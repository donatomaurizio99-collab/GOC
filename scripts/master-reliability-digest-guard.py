from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _run_gh_api(path: str) -> dict[str, Any]:
    command = ["gh", "api", path]
    completed = subprocess.run(command, capture_output=True, text=True)
    _expect(
        completed.returncode == 0,
        f"gh api failed ({completed.returncode}) for '{path}': {completed.stderr.strip()}",
    )
    payload = json.loads(completed.stdout)
    _expect(isinstance(payload, dict), f"Expected JSON object from gh api path '{path}'.")
    return payload


def _load_json_file(path: Path) -> dict[str, Any]:
    _expect(path.exists(), f"JSON file not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    _expect(isinstance(payload, dict), f"Expected JSON object in file: {path}")
    return payload


def _parse_utc_timestamp(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _format_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _resolve_now_utc(now_utc_text: str | None) -> datetime:
    if not now_utc_text:
        return datetime.now(timezone.utc)
    parsed = _parse_utc_timestamp(now_utc_text)
    _expect(parsed is not None, f"Invalid --now-utc timestamp: {now_utc_text}")
    return parsed


def _fetch_workflow_id(*, repo: str, workflow_name: str) -> int:
    payload = _run_gh_api(f"repos/{repo}/actions/workflows?per_page=100")
    workflows = payload.get("workflows") or []
    _expect(isinstance(workflows, list), "Invalid workflows payload format.")
    for workflow in workflows:
        if not isinstance(workflow, dict):
            continue
        if str(workflow.get("name") or "").strip() == workflow_name:
            workflow_id = int(workflow.get("id") or 0)
            _expect(workflow_id > 0, f"Workflow '{workflow_name}' has invalid id.")
            return workflow_id
    raise RuntimeError(f"Workflow '{workflow_name}' not found in repo '{repo}'.")


def _fetch_runs_from_github(
    *,
    repo: str,
    branch: str,
    workflow_name: str,
    per_page: int,
) -> list[dict[str, Any]]:
    workflow_id = _fetch_workflow_id(repo=repo, workflow_name=workflow_name)
    payload = _run_gh_api(
        f"repos/{repo}/actions/workflows/{workflow_id}/runs?branch={branch}&status=completed&per_page={per_page}"
    )
    runs = payload.get("workflow_runs") or []
    _expect(isinstance(runs, list), f"Invalid workflow runs payload for workflow '{workflow_name}'.")
    return [item for item in runs if isinstance(item, dict)]


def _fetch_run_artifacts_from_github(*, repo: str, run_id: int) -> list[dict[str, Any]]:
    payload = _run_gh_api(f"repos/{repo}/actions/runs/{run_id}/artifacts?per_page=100")
    artifacts = payload.get("artifacts") or []
    _expect(isinstance(artifacts, list), f"Invalid run artifacts payload for run {run_id}.")
    return [item for item in artifacts if isinstance(item, dict)]


def _load_fixture_runs(*, payload: dict[str, Any], workflow_name: str) -> list[dict[str, Any]]:
    if isinstance(payload.get("workflow_runs"), list):
        runs = payload.get("workflow_runs") or []
    elif isinstance(payload.get("workflow_runs"), dict):
        runs = (payload.get("workflow_runs") or {}).get(workflow_name) or []
    else:
        runs = payload.get("runs") or []
    _expect(isinstance(runs, list), "Invalid runs fixture payload format.")
    return [item for item in runs if isinstance(item, dict)]


def _load_fixture_run_artifacts(*, payload: dict[str, Any], run_id: int) -> list[dict[str, Any]]:
    run_artifacts = payload.get("run_artifacts") if isinstance(payload.get("run_artifacts"), dict) else {}
    entry = run_artifacts.get(str(run_id)) if isinstance(run_artifacts, dict) else None
    if not isinstance(entry, dict):
        return []
    artifacts = entry.get("artifacts") if isinstance(entry.get("artifacts"), list) else []
    return [item for item in artifacts if isinstance(item, dict)]


def _run_timestamp_for_sort(run: dict[str, Any]) -> datetime:
    for key in ("updated_at", "run_started_at", "created_at"):
        parsed = _parse_utc_timestamp(str(run.get(key) or ""))
        if parsed is not None:
            return parsed
    return datetime.min.replace(tzinfo=timezone.utc)


def _resolve_latest_run_url(*, repo: str, run: dict[str, Any]) -> str:
    direct = str(run.get("html_url") or run.get("url") or "").strip()
    if direct:
        return direct
    run_id = int(run.get("id") or 0)
    if run_id <= 0:
        return ""
    return f"https://github.com/{repo}/actions/runs/{run_id}"


def run_reliability_digest_guard(
    *,
    label: str,
    repo: str,
    branch: str,
    workflow_name: str,
    max_age_hours: float,
    per_page: int,
    required_artifact: str,
    runs_file: Path | None,
    now_utc: datetime,
    allow_breach: bool,
    output_file: Path,
) -> dict[str, Any]:
    _expect(float(max_age_hours) > 0.0, "max_age_hours must be > 0.")
    _expect(int(per_page) > 0, "per_page must be > 0.")
    _expect(str(required_artifact).strip() != "", "required_artifact must be non-empty.")

    started = time.perf_counter()
    fixture_payload = _load_json_file(runs_file) if runs_file is not None else None
    runs = (
        _load_fixture_runs(payload=fixture_payload or {}, workflow_name=workflow_name)
        if fixture_payload is not None
        else _fetch_runs_from_github(
            repo=repo,
            branch=branch,
            workflow_name=workflow_name,
            per_page=per_page,
        )
    )
    sorted_runs = sorted(runs, key=_run_timestamp_for_sort, reverse=True)
    latest_run = sorted_runs[0] if sorted_runs else None

    latest_run_updated_at = (
        _parse_utc_timestamp(str(latest_run.get("updated_at") or ""))
        if latest_run is not None
        else None
    )
    latest_run_age_hours = (
        max(0.0, float((now_utc - latest_run_updated_at).total_seconds()) / 3600.0)
        if latest_run_updated_at is not None
        else None
    )
    stale_breach = bool(
        latest_run is None
        or latest_run_updated_at is None
        or (latest_run_age_hours is not None and float(latest_run_age_hours) > float(max_age_hours))
    )
    latest_run_success = bool(
        latest_run is not None
        and str(latest_run.get("status") or "") == "completed"
        and str(latest_run.get("conclusion") or "") == "success"
    )
    failed_breach = bool(latest_run is not None and not latest_run_success)

    latest_run_artifacts: list[dict[str, Any]] = []
    if latest_run is not None:
        latest_run_id = int(latest_run.get("id") or 0)
        if latest_run_id > 0:
            latest_run_artifacts = (
                _load_fixture_run_artifacts(payload=fixture_payload or {}, run_id=latest_run_id)
                if fixture_payload is not None
                else _fetch_run_artifacts_from_github(repo=repo, run_id=latest_run_id)
            )

    available_artifacts = sorted(
        {
            str(item.get("name") or "").strip()
            for item in latest_run_artifacts
            if str(item.get("name") or "").strip() and not bool(item.get("expired"))
        }
    )
    required_artifact_present = str(required_artifact) in available_artifacts
    required_artifact_missing = bool(latest_run is not None and not required_artifact_present)

    breach_reason = (
        "stale"
        if stale_breach
        else ("failed" if failed_breach else ("artifact_missing" if required_artifact_missing else "none"))
    )
    breached = breach_reason in {"stale", "failed", "artifact_missing"}

    latest_run_snapshot = (
        {
            "run_id": int(latest_run.get("id") or 0),
            "status": str(latest_run.get("status") or ""),
            "conclusion": str(latest_run.get("conclusion") or ""),
            "updated_at": str(latest_run.get("updated_at") or ""),
            "updated_at_parsed_utc": _format_utc(latest_run_updated_at),
            "url": _resolve_latest_run_url(repo=repo, run=latest_run),
            "required_artifact_name": str(required_artifact),
            "required_artifact_present": bool(required_artifact_present),
            "available_non_expired_artifacts": available_artifacts,
        }
        if latest_run is not None
        else None
    )

    criteria = [
        {
            "name": "digest_workflow_recent_enough",
            "passed": bool((not stale_breach) or allow_breach),
            "details": (
                f"stale_breach={stale_breach}, latest_run_age_hours={latest_run_age_hours}, "
                f"max_age_hours={max_age_hours}, allow_breach={allow_breach}"
            ),
        },
        {
            "name": "digest_workflow_latest_run_success",
            "passed": bool((not failed_breach) or allow_breach),
            "details": (
                f"failed_breach={failed_breach}, latest_run_status="
                f"{(latest_run_snapshot or {}).get('status') if latest_run_snapshot else 'none'}, "
                f"latest_run_conclusion="
                f"{(latest_run_snapshot or {}).get('conclusion') if latest_run_snapshot else 'none'}, "
                f"allow_breach={allow_breach}"
            ),
        },
        {
            "name": "digest_workflow_required_artifact_present",
            "passed": bool((not required_artifact_missing) or allow_breach),
            "details": (
                f"required_artifact={required_artifact}, "
                f"required_artifact_missing={required_artifact_missing}, "
                f"allow_breach={allow_breach}"
            ),
        },
    ]
    failed_criteria = [item for item in criteria if not bool(item.get("passed"))]
    success = len(failed_criteria) == 0

    report = {
        "label": label,
        "success": bool(success),
        "config": {
            "repo": repo,
            "branch": branch,
            "workflow_name": workflow_name,
            "required_artifact": str(required_artifact),
            "max_age_hours": float(max_age_hours),
            "per_page": int(per_page),
            "runs_file": str(runs_file) if runs_file is not None else None,
            "allow_breach": bool(allow_breach),
            "now_utc": _format_utc(now_utc),
            "output_file": str(output_file),
        },
        "metrics": {
            "runs_total_fetched": int(len(runs)),
            "max_age_hours": float(max_age_hours),
            "latest_run_age_hours": float(latest_run_age_hours) if latest_run_age_hours is not None else None,
            "required_artifact_missing": 1 if required_artifact_missing else 0,
            "available_non_expired_artifacts_total": int(len(available_artifacts)),
            "criteria_failed": int(len(failed_criteria)),
        },
        "criteria": criteria,
        "failed_criteria": failed_criteria,
        "latest_run": latest_run_snapshot,
        "decision": {
            "reliability_digest_guard_breached": bool(breached),
            "breach_reason": breach_reason,
            "recommended_action": (
                "reliability_digest_guard_healthy"
                if not breached
                else f"reliability_digest_guard_{breach_reason}"
            ),
        },
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2), encoding="utf-8")

    if breached and not allow_breach:
        raise RuntimeError(f"Master reliability digest guard failed: {json.dumps(report, sort_keys=True)}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify that the master reliability digest workflow remains fresh/healthy and "
            "publishes the required digest artifact."
        )
    )
    parser.add_argument("--label", default="master-reliability-digest-guard")
    parser.add_argument("--repo", default="donatomaurizio99-collab/GOC")
    parser.add_argument("--branch", default="master")
    parser.add_argument("--workflow-name", default="Master Reliability Digest")
    parser.add_argument("--required-artifact", default="master-reliability-digest")
    parser.add_argument("--max-age-hours", type=float, default=192.0)
    parser.add_argument("--per-page", type=int, default=20)
    parser.add_argument("--runs-file")
    parser.add_argument("--now-utc")
    parser.add_argument("--allow-breach", action="store_true")
    parser.add_argument("--output-file", default="artifacts/master-reliability-digest-guard.json")
    args = parser.parse_args(argv)

    if float(args.max_age_hours) <= 0:
        print("[master-reliability-digest-guard] ERROR: --max-age-hours must be > 0.", file=sys.stderr)
        return 2
    if int(args.per_page) <= 0:
        print("[master-reliability-digest-guard] ERROR: --per-page must be > 0.", file=sys.stderr)
        return 2
    if not str(args.required_artifact or "").strip():
        print("[master-reliability-digest-guard] ERROR: --required-artifact must be non-empty.", file=sys.stderr)
        return 2

    runs_file = Path(str(args.runs_file)).expanduser() if args.runs_file else None
    output_file = Path(str(args.output_file)).expanduser()
    try:
        report = run_reliability_digest_guard(
            label=str(args.label),
            repo=str(args.repo),
            branch=str(args.branch),
            workflow_name=str(args.workflow_name),
            required_artifact=str(args.required_artifact),
            max_age_hours=float(args.max_age_hours),
            per_page=int(args.per_page),
            runs_file=runs_file,
            now_utc=_resolve_now_utc(args.now_utc),
            allow_breach=bool(args.allow_breach),
            output_file=output_file,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[master-reliability-digest-guard] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
