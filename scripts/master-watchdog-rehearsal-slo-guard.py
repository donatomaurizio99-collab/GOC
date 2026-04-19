from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_MTTR_TARGET_SECONDS = 300.0
DEFAULT_DRILL_ARTIFACT_NAME = "master-guard-workflow-health-rehearsal-drill"
DEFAULT_DRILL_REPORT_FILENAME = "master-guard-workflow-health-rehearsal-drill.json"


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
    _expect(isinstance(artifacts, list), f"Invalid artifacts payload for run {run_id}.")
    return [item for item in artifacts if isinstance(item, dict)]


def _load_fixture_runs(*, payload: dict[str, Any], workflow_name: str) -> list[dict[str, Any]]:
    if isinstance(payload.get("workflow_runs"), list):
        runs = payload.get("workflow_runs") or []
    elif isinstance(payload.get("workflow_runs"), dict):
        runs = (payload.get("workflow_runs") or {}).get(workflow_name) or []
    else:
        runs = payload.get("runs") or []
    _expect(isinstance(runs, list), "Invalid runs fixture payload.")
    return [item for item in runs if isinstance(item, dict)]


def _load_fixture_artifacts(*, payload: dict[str, Any], run_id: int) -> list[dict[str, Any]]:
    run_artifacts = payload.get("run_artifacts") if isinstance(payload.get("run_artifacts"), dict) else {}
    value = run_artifacts.get(str(run_id)) if isinstance(run_artifacts, dict) else None
    if value is None:
        return []
    if isinstance(value, dict):
        artifacts = value.get("artifacts") or []
    else:
        artifacts = value
    _expect(isinstance(artifacts, list), f"fixtures.run_artifacts['{run_id}'] must resolve to a list.")
    return [item for item in artifacts if isinstance(item, dict)]


def _extract_artifact_names(artifacts: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    available: list[str] = []
    expired: list[str] = []
    for artifact in artifacts:
        name = str(artifact.get("name") or "").strip()
        if not name:
            continue
        if bool(artifact.get("expired")):
            expired.append(name)
        else:
            available.append(name)
    return sorted(available), sorted(expired)


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


def _parse_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _resolve_mttr_from_drill_report(report: dict[str, Any]) -> tuple[float | None, float | None, str | None]:
    metrics = report.get("metrics") if isinstance(report.get("metrics"), dict) else {}
    mttr_seconds = _parse_float(metrics.get("alert_chain_mttr_seconds"))
    mttr_value_source: str | None = None
    if mttr_seconds is not None and mttr_seconds >= 0.0:
        mttr_value_source = "metrics.alert_chain_mttr_seconds"
    else:
        mttr_seconds = None

    # Backward-compatibility for older rehearsal drill artifacts.
    if mttr_seconds is None:
        duration_ms = _parse_float(report.get("duration_ms"))
        if duration_ms is not None and duration_ms >= 0.0:
            mttr_seconds = float(duration_ms) / 1000.0
            mttr_value_source = "duration_ms_fallback"

    reported_target = _parse_float(metrics.get("mttr_target_seconds"))
    return mttr_seconds, reported_target, mttr_value_source


def _load_drill_report_from_artifact(
    *,
    repo: str,
    run_id: int,
    artifact_name: str,
) -> dict[str, Any]:
    _expect(run_id > 0, "run_id must be > 0 for artifact download.")
    _expect(str(artifact_name).strip(), "artifact_name must be non-empty.")
    with tempfile.TemporaryDirectory(prefix="master-watchdog-rehearsal-slo-artifact-") as temp_root_text:
        temp_root = Path(temp_root_text)
        command = [
            "gh",
            "run",
            "download",
            str(run_id),
            "-R",
            repo,
            "-n",
            str(artifact_name),
            "-D",
            str(temp_root),
        ]
        completed = subprocess.run(command, capture_output=True, text=True)
        _expect(
            completed.returncode == 0,
            f"gh run download failed ({completed.returncode}) for run #{run_id}: {completed.stderr.strip()}",
        )
        candidates = sorted(
            item
            for item in temp_root.rglob("*.json")
            if item.is_file() and item.name.lower() == DEFAULT_DRILL_REPORT_FILENAME.lower()
        )
        _expect(
            len(candidates) >= 1,
            (
                f"Downloaded artifact '{artifact_name}' for run #{run_id} but did not find "
                f"'{DEFAULT_DRILL_REPORT_FILENAME}'."
            ),
        )
        return _load_json_file(candidates[0])


def run_watchdog_rehearsal_slo_guard(
    *,
    label: str,
    repo: str,
    branch: str,
    workflow_name: str,
    max_age_hours: float,
    mttr_target_seconds: float,
    per_page: int,
    runs_file: Path | None,
    drill_report_file: Path | None,
    drill_artifact_name: str,
    now_utc: datetime,
    allow_breach: bool,
    output_file: Path,
) -> dict[str, Any]:
    _expect(float(max_age_hours) > 0.0, "max_age_hours must be > 0.")
    _expect(float(mttr_target_seconds) > 0.0, "mttr_target_seconds must be > 0.")
    _expect(int(per_page) > 0, "per_page must be > 0.")
    _expect(str(drill_artifact_name).strip(), "drill_artifact_name must be non-empty.")

    started = time.perf_counter()
    fixtures_payload = _load_json_file(runs_file) if runs_file is not None else None
    fixture_artifacts_available = bool(
        fixtures_payload is not None and isinstance(fixtures_payload.get("run_artifacts"), dict)
    )
    runs = (
        _load_fixture_runs(payload=fixtures_payload or {}, workflow_name=workflow_name)
        if fixtures_payload is not None
        else _fetch_runs_from_github(
            repo=repo,
            branch=branch,
            workflow_name=workflow_name,
            per_page=per_page,
        )
    )

    artifacts_cache: dict[int, list[dict[str, Any]]] = {}

    def load_artifacts_for_run(run_id: int) -> list[dict[str, Any]]:
        if run_id <= 0:
            return []
        if int(run_id) in artifacts_cache:
            return artifacts_cache[int(run_id)]
        if fixtures_payload is not None:
            loaded = _load_fixture_artifacts(payload=fixtures_payload, run_id=run_id)
        else:
            loaded = _fetch_run_artifacts_from_github(repo=repo, run_id=run_id)
        artifacts_cache[int(run_id)] = loaded
        return loaded

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

    latest_run_id = int(latest_run.get("id") or 0) if latest_run is not None else 0
    latest_run_artifacts = load_artifacts_for_run(latest_run_id) if latest_run_id > 0 else []
    latest_run_available_artifacts, latest_run_expired_artifacts = _extract_artifact_names(latest_run_artifacts)
    required_artifacts = [str(drill_artifact_name)]
    artifact_inventory_known = bool(fixtures_payload is None or fixture_artifacts_available)
    latest_run_missing_required_artifacts = (
        [artifact_name for artifact_name in required_artifacts if artifact_name not in latest_run_available_artifacts]
        if artifact_inventory_known
        else []
    )

    latest_run_snapshot = (
        {
            "run_id": latest_run_id,
            "status": str(latest_run.get("status") or ""),
            "conclusion": str(latest_run.get("conclusion") or ""),
            "updated_at": str(latest_run.get("updated_at") or ""),
            "updated_at_parsed_utc": _format_utc(latest_run_updated_at),
            "url": _resolve_latest_run_url(repo=repo, run=latest_run),
            "required_artifacts": required_artifacts,
            "artifact_inventory_known": bool(artifact_inventory_known),
            "available_artifacts": latest_run_available_artifacts,
            "expired_artifacts": latest_run_expired_artifacts,
            "missing_required_artifacts": latest_run_missing_required_artifacts,
        }
        if latest_run is not None
        else None
    )

    mttr_report_loaded = False
    mttr_report_source = "none"
    mttr_report_load_error: str | None = None
    mttr_report: dict[str, Any] | None = None
    mttr_seconds: float | None = None
    mttr_value_source: str | None = None
    mttr_target_effective_seconds: float = float(mttr_target_seconds)
    mttr_evaluated = False
    mttr_breach = False
    mttr_report_unavailable = False

    should_attempt_mttr = bool(
        (not stale_breach)
        and (not failed_breach)
        and latest_run_id > 0
        and (drill_report_file is not None or runs_file is None)
        and (not artifact_inventory_known or len(latest_run_missing_required_artifacts) == 0)
    )
    if (
        not should_attempt_mttr
        and runs_file is not None
        and drill_report_file is None
        and not artifact_inventory_known
    ):
        mttr_report_source = "skipped_fixture_mode"
    elif (
        not should_attempt_mttr
        and artifact_inventory_known
        and len(latest_run_missing_required_artifacts) > 0
        and not stale_breach
        and not failed_breach
    ):
        mttr_report_source = "missing_required_artifact"
        mttr_report_load_error = (
            "Latest rehearsal run missing required artifact(s): "
            + ", ".join(latest_run_missing_required_artifacts)
        )
        mttr_report_unavailable = True

    if should_attempt_mttr:
        mttr_evaluated = True
        try:
            if drill_report_file is not None:
                mttr_report = _load_json_file(drill_report_file)
                mttr_report_source = "file"
            else:
                mttr_report = _load_drill_report_from_artifact(
                    repo=repo,
                    run_id=latest_run_id,
                    artifact_name=drill_artifact_name,
                )
                mttr_report_source = "artifact"
            mttr_report_loaded = True
        except Exception as exc:  # noqa: BLE001
            mttr_report_load_error = str(exc)
            mttr_report_loaded = False

        if mttr_report_loaded and mttr_report is not None:
            mttr_seconds, reported_target, mttr_value_source = _resolve_mttr_from_drill_report(mttr_report)
            if reported_target is not None and reported_target > 0:
                mttr_target_effective_seconds = float(reported_target)
            if mttr_seconds is None:
                mttr_report_loaded = False
                mttr_report_source = f"{mttr_report_source}-invalid"
                mttr_report_load_error = (
                    "Drill report missing numeric metrics.alert_chain_mttr_seconds and duration_ms fallback."
                )

        if not mttr_report_loaded:
            mttr_report_unavailable = True
        elif mttr_seconds is not None:
            mttr_breach = bool(float(mttr_seconds) > float(mttr_target_effective_seconds))

    if stale_breach:
        breach_reason = "stale"
    elif failed_breach:
        breach_reason = "failed"
    elif mttr_report_unavailable:
        breach_reason = "mttr_report_unavailable"
    elif mttr_breach:
        breach_reason = "mttr"
    else:
        breach_reason = "none"
    breached = breach_reason != "none"

    criteria = [
        {
            "name": "watchdog_rehearsal_recent_enough",
            "passed": bool((not stale_breach) or allow_breach),
            "details": (
                f"stale_breach={stale_breach}, latest_run_age_hours={latest_run_age_hours}, "
                f"max_age_hours={max_age_hours}, allow_breach={allow_breach}"
            ),
        },
        {
            "name": "watchdog_rehearsal_latest_run_success",
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
            "name": "watchdog_rehearsal_mttr_within_target",
            "passed": bool(
                (
                    (not mttr_report_unavailable)
                    and (not mttr_breach)
                )
                or (not mttr_evaluated)
                or allow_breach
            ),
            "details": (
                f"mttr_evaluated={mttr_evaluated}, "
                f"mttr_report_loaded={mttr_report_loaded}, "
                f"mttr_report_unavailable={mttr_report_unavailable}, "
                f"mttr_seconds={mttr_seconds}, "
                f"mttr_target_effective_seconds={mttr_target_effective_seconds}, "
                f"mttr_breach={mttr_breach}, "
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
            "max_age_hours": float(max_age_hours),
            "mttr_target_seconds": float(mttr_target_seconds),
            "per_page": int(per_page),
            "runs_file": str(runs_file) if runs_file is not None else None,
            "drill_report_file": str(drill_report_file) if drill_report_file is not None else None,
            "drill_artifact_name": str(drill_artifact_name),
            "allow_breach": bool(allow_breach),
            "now_utc": _format_utc(now_utc),
            "output_file": str(output_file),
        },
        "metrics": {
            "runs_total_fetched": int(len(runs)),
            "max_age_hours": float(max_age_hours),
            "latest_run_age_hours": float(latest_run_age_hours) if latest_run_age_hours is not None else None,
            "latest_run_artifact_inventory_known": bool(artifact_inventory_known),
            "latest_run_available_artifacts_total": int(len(latest_run_available_artifacts)),
            "latest_run_expired_artifacts_total": int(len(latest_run_expired_artifacts)),
            "required_artifacts_missing_total": int(len(latest_run_missing_required_artifacts)),
            "latest_run_missing_required_artifacts": latest_run_missing_required_artifacts,
            "mttr_evaluated": bool(mttr_evaluated),
            "mttr_seconds": float(mttr_seconds) if mttr_seconds is not None else None,
            "mttr_target_seconds": float(mttr_target_effective_seconds),
            "mttr_breach": bool(mttr_breach),
            "mttr_report_loaded": bool(mttr_report_loaded),
            "mttr_report_unavailable": bool(mttr_report_unavailable),
            "mttr_value_source": mttr_value_source,
            "mttr_report_source": mttr_report_source,
            "mttr_report_load_error": mttr_report_load_error,
            "criteria_failed": int(len(failed_criteria)),
        },
        "criteria": criteria,
        "failed_criteria": failed_criteria,
        "latest_run": latest_run_snapshot,
        "drill_report": {
            "loaded": bool(mttr_report_loaded),
            "source": mttr_report_source,
            "artifact_name": str(drill_artifact_name),
            "required_artifacts": required_artifacts,
            "artifact_inventory_known": bool(artifact_inventory_known),
            "missing_required_artifacts": latest_run_missing_required_artifacts,
            "report_file": str(drill_report_file) if drill_report_file is not None else None,
            "load_error": mttr_report_load_error,
            "mttr_seconds": float(mttr_seconds) if mttr_seconds is not None else None,
            "mttr_value_source": mttr_value_source,
            "mttr_target_seconds": float(mttr_target_effective_seconds),
        },
        "decision": {
            "watchdog_rehearsal_slo_breached": bool(breached),
            "breach_reason": breach_reason,
            "recommended_action": (
                "watchdog_rehearsal_slo_healthy" if not breached else f"watchdog_rehearsal_slo_{breach_reason}"
            ),
        },
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2), encoding="utf-8")

    if breached and not allow_breach:
        raise RuntimeError(f"Master watchdog rehearsal SLO guard failed: {json.dumps(report, sort_keys=True)}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify that the watchdog rehearsal drill remains healthy and fresh on master "
            "(at least one successful run within the configured SLO window)."
        )
    )
    parser.add_argument("--label", default="master-watchdog-rehearsal-slo-guard")
    parser.add_argument("--repo", default="donatomaurizio99-collab/GOC")
    parser.add_argument("--branch", default="master")
    parser.add_argument("--workflow-name", default="Master Watchdog Rehearsal Drill")
    parser.add_argument("--max-age-hours", type=float, default=192.0)
    parser.add_argument("--mttr-target-seconds", type=float, default=DEFAULT_MTTR_TARGET_SECONDS)
    parser.add_argument("--per-page", type=int, default=20)
    parser.add_argument("--runs-file")
    parser.add_argument("--drill-report-file")
    parser.add_argument("--drill-artifact-name", default=DEFAULT_DRILL_ARTIFACT_NAME)
    parser.add_argument("--now-utc")
    parser.add_argument("--allow-breach", action="store_true")
    parser.add_argument("--output-file", default="artifacts/master-watchdog-rehearsal-slo-guard.json")
    args = parser.parse_args(argv)

    if float(args.max_age_hours) <= 0:
        print("[master-watchdog-rehearsal-slo-guard] ERROR: --max-age-hours must be > 0.", file=sys.stderr)
        return 2
    if int(args.per_page) <= 0:
        print("[master-watchdog-rehearsal-slo-guard] ERROR: --per-page must be > 0.", file=sys.stderr)
        return 2
    if float(args.mttr_target_seconds) <= 0:
        print("[master-watchdog-rehearsal-slo-guard] ERROR: --mttr-target-seconds must be > 0.", file=sys.stderr)
        return 2
    if not str(args.drill_artifact_name or "").strip():
        print("[master-watchdog-rehearsal-slo-guard] ERROR: --drill-artifact-name must be non-empty.", file=sys.stderr)
        return 2

    runs_file = Path(str(args.runs_file)).expanduser() if args.runs_file else None
    drill_report_file = Path(str(args.drill_report_file)).expanduser() if args.drill_report_file else None
    output_file = Path(str(args.output_file)).expanduser()
    try:
        report = run_watchdog_rehearsal_slo_guard(
            label=str(args.label),
            repo=str(args.repo),
            branch=str(args.branch),
            workflow_name=str(args.workflow_name),
            max_age_hours=float(args.max_age_hours),
            mttr_target_seconds=float(args.mttr_target_seconds),
            per_page=int(args.per_page),
            runs_file=runs_file,
            drill_report_file=drill_report_file,
            drill_artifact_name=str(args.drill_artifact_name),
            now_utc=_resolve_now_utc(args.now_utc),
            allow_breach=bool(args.allow_breach),
            output_file=output_file,
        )
    except Exception as exc:
        print(f"[master-watchdog-rehearsal-slo-guard] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
