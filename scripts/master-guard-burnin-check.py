from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_GUARD_BURNIN_WORKFLOW_SPECS = [
    {
        "workflow_file": "master-required-checks-24h.yml",
        "workflow_name": "Master Required Checks 24h",
        "required_artifacts": ["master-required-checks-24h-report"],
    },
    {
        "workflow_file": "master-branch-protection-drift-guard.yml",
        "workflow_name": "Master Branch Protection Drift Guard",
        "required_artifacts": [
            "master-branch-protection-drift-guard",
            "master-branch-protection-drift-issue-upsert",
        ],
    },
    {
        "workflow_file": "master-release-gate-runtime-early-warning.yml",
        "workflow_name": "Master Release Gate Runtime Early Warning",
        "required_artifacts": [
            "release-gate-runtime-early-warning",
            "release-gate-runtime-early-warning-issue-upsert",
            "release-gate-runtime-alert-age-slo-issue-upsert",
        ],
    },
    {
        "workflow_file": "master-guard-workflow-health.yml",
        "workflow_name": "Master Guard Workflow Health",
        "required_artifacts": [
            "master-guard-workflow-health-check",
            "master-guard-workflow-health-issue-upsert",
            "master-guard-workflow-health-selftest",
        ],
    },
    {
        "workflow_file": "master-watchdog-rehearsal-slo-guard.yml",
        "workflow_name": "Master Watchdog Rehearsal SLO Guard",
        "required_artifacts": [
            "master-watchdog-rehearsal-slo-guard",
            "master-watchdog-rehearsal-slo-guard-issue-upsert",
            "master-watchdog-rehearsal-slo-guard-selftest",
        ],
    },
    {
        "workflow_file": "master-watchdog-rehearsal-drill.yml",
        "workflow_name": "Master Watchdog Rehearsal Drill",
        "required_artifacts": [
            "master-guard-workflow-health-rehearsal-check",
            "master-guard-workflow-health-rehearsal-issue-upsert",
            "master-guard-workflow-health-rehearsal-drill",
        ],
        "required_successful_runs": 1,
    },
    {
        "workflow_file": "master-reliability-digest.yml",
        "workflow_name": "Master Reliability Digest",
        "required_artifacts": [
            "master-reliability-digest",
            "master-reliability-digest-warning-issue-upsert",
        ],
        "required_successful_runs": 1,
    },
    {
        "workflow_file": "master-reliability-digest-guard.yml",
        "workflow_name": "Master Reliability Digest Guard",
        "required_artifacts": [
            "master-reliability-digest-guard",
            "master-reliability-digest-guard-issue-upsert",
            "master-reliability-digest-guard-selftest",
        ],
    },
]


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
    workflow_runs = payload.get("workflow_runs")
    if isinstance(workflow_runs, dict):
        runs = workflow_runs.get(workflow_name) or []
    elif isinstance(workflow_runs, list):
        runs = workflow_runs
    else:
        runs = payload.get("runs") or []
    _expect(isinstance(runs, list), f"fixtures.workflow_runs['{workflow_name}'] must be a list when present.")
    return [item for item in runs if isinstance(item, dict)]


def _load_fixture_artifacts(*, payload: dict[str, Any], run_id: int) -> list[dict[str, Any]]:
    run_artifacts = payload.get("run_artifacts") if isinstance(payload.get("run_artifacts"), dict) else {}
    entry = run_artifacts.get(str(run_id)) if isinstance(run_artifacts, dict) else None
    if entry is None:
        return []
    if isinstance(entry, dict):
        artifacts = entry.get("artifacts") or []
    else:
        artifacts = entry
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


def _normalize_workflow_specs(raw_specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_specs):
        _expect(isinstance(raw, dict), f"workflow spec #{index + 1} must be an object.")
        workflow_name = str(raw.get("workflow_name") or "").strip()
        _expect(workflow_name != "", f"workflow spec #{index + 1} missing non-empty 'workflow_name'.")
        workflow_file = str(raw.get("workflow_file") or "").strip()
        required_artifacts_raw = raw.get("required_artifacts") or []
        _expect(
            isinstance(required_artifacts_raw, list),
            f"workflow spec '{workflow_name}' field 'required_artifacts' must be a list.",
        )
        required_artifacts = sorted(
            {
                str(item).strip()
                for item in required_artifacts_raw
                if str(item).strip()
            }
        )
        required_successful_runs_raw = raw.get("required_successful_runs")
        required_successful_runs: int | None = None
        if required_successful_runs_raw is not None:
            required_successful_runs = int(required_successful_runs_raw)
            _expect(
                required_successful_runs > 0,
                f"workflow spec '{workflow_name}' required_successful_runs must be > 0 when set.",
            )
        normalized.append(
            {
                "workflow_name": workflow_name,
                "workflow_file": workflow_file,
                "required_artifacts": required_artifacts,
                "required_successful_runs": required_successful_runs,
            }
        )
    _expect(normalized, "At least one workflow spec must be configured.")
    return normalized


def _load_workflow_specs_file(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(payload, dict):
        raw_specs = payload.get("workflow_specs") or []
    else:
        raw_specs = payload
    _expect(isinstance(raw_specs, list), "workflow-specs file must be a list or object with 'workflow_specs'.")
    raw_objects = [item for item in raw_specs if isinstance(item, dict)]
    _expect(len(raw_objects) == len(raw_specs), "workflow-specs file contains non-object entries.")
    return _normalize_workflow_specs(raw_objects)


def _resolve_required_successful_runs(
    *,
    spec: dict[str, Any],
    required_successful_runs: int,
    digest_required_successful_runs: int,
    drill_required_successful_runs: int,
) -> int:
    from_spec = spec.get("required_successful_runs")
    if from_spec is not None:
        resolved = int(from_spec)
        _expect(resolved > 0, "resolved required_successful_runs must be > 0.")
        return resolved
    workflow_name = str(spec.get("workflow_name") or "")
    if workflow_name == "Master Reliability Digest":
        return int(digest_required_successful_runs)
    if workflow_name == "Master Watchdog Rehearsal Drill":
        return int(drill_required_successful_runs)
    return int(required_successful_runs)


def _evaluate_workflow_burnin(
    *,
    spec: dict[str, Any],
    runs: list[dict[str, Any]],
    required_successful_runs: int,
    load_artifacts_for_run: Any,
) -> tuple[dict[str, Any], dict[str, int]]:
    sorted_runs = sorted(
        runs,
        key=lambda item: _parse_utc_timestamp(str(item.get("updated_at") or ""))
        or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    considered_runs = sorted_runs[:required_successful_runs]
    has_sufficient_runs = len(considered_runs) >= required_successful_runs
    required_artifacts = [str(item) for item in spec.get("required_artifacts") or []]

    run_snapshots: list[dict[str, Any]] = []
    non_success_runs_total = 0
    missing_artifacts_total = 0
    healthy_runs_total = 0
    for run in considered_runs:
        run_id = int(run.get("id") or 0)
        run_status = str(run.get("status") or "")
        run_conclusion = str(run.get("conclusion") or "")
        run_updated_at = _parse_utc_timestamp(str(run.get("updated_at") or ""))
        run_artifacts = load_artifacts_for_run(run_id) if run_id > 0 else []
        available_artifacts, expired_artifacts = _extract_artifact_names(run_artifacts)
        missing_required_artifacts = [item for item in required_artifacts if item not in available_artifacts]
        run_success = run_status == "completed" and run_conclusion == "success"
        run_healthy = bool(run_success and not missing_required_artifacts)
        if not run_success:
            non_success_runs_total += 1
        if missing_required_artifacts:
            missing_artifacts_total += len(missing_required_artifacts)
        if run_healthy:
            healthy_runs_total += 1
        run_snapshots.append(
            {
                "run_id": run_id,
                "status": run_status,
                "conclusion": run_conclusion,
                "updated_at": str(run.get("updated_at") or ""),
                "updated_at_parsed_utc": _format_utc(run_updated_at),
                "url": str(run.get("html_url") or ""),
                "required_artifacts": required_artifacts,
                "available_artifacts": available_artifacts,
                "expired_artifacts": expired_artifacts,
                "missing_required_artifacts": missing_required_artifacts,
                "run_healthy": bool(run_healthy),
            }
        )

    degraded_reasons: list[str] = []
    if not has_sufficient_runs:
        degraded_reasons.append("insufficient_completed_runs")
    if non_success_runs_total > 0:
        degraded_reasons.append("non_success_run_in_burnin_window")
    if missing_artifacts_total > 0:
        degraded_reasons.append("required_artifacts_missing_in_burnin_window")

    is_healthy = bool(
        has_sufficient_runs and healthy_runs_total == required_successful_runs and non_success_runs_total == 0
    )
    evaluation = {
        "workflow_name": str(spec.get("workflow_name") or ""),
        "workflow_file": str(spec.get("workflow_file") or ""),
        "required_successful_runs": int(required_successful_runs),
        "required_artifacts": required_artifacts,
        "runs_total_fetched": int(len(runs)),
        "burnin_runs_observed": int(len(considered_runs)),
        "healthy_runs_in_burnin_window": int(healthy_runs_total),
        "non_success_runs_in_burnin_window": int(non_success_runs_total),
        "missing_required_artifacts_in_burnin_window": int(missing_artifacts_total),
        "has_sufficient_runs": bool(has_sufficient_runs),
        "is_healthy": bool(is_healthy),
        "degraded_reasons": degraded_reasons,
        "runs": run_snapshots,
    }
    counters = {
        "non_success_runs_total": int(non_success_runs_total),
        "missing_required_artifacts_total": int(missing_artifacts_total),
    }
    return evaluation, counters


def run_guard_burnin_check(
    *,
    label: str,
    repo: str,
    branch: str,
    per_page: int,
    required_successful_runs: int,
    digest_required_successful_runs: int,
    drill_required_successful_runs: int,
    fixtures_file: Path | None,
    workflow_specs_file: Path | None,
    now_utc: datetime,
    allow_degraded: bool,
    output_file: Path,
) -> dict[str, Any]:
    _expect(per_page > 0, "per_page must be > 0.")
    _expect(required_successful_runs > 0, "required_successful_runs must be > 0.")
    _expect(digest_required_successful_runs > 0, "digest_required_successful_runs must be > 0.")
    _expect(drill_required_successful_runs > 0, "drill_required_successful_runs must be > 0.")

    started = time.perf_counter()
    fixtures_payload = _load_json_file(fixtures_file) if fixtures_file is not None else None
    workflow_specs = (
        _load_workflow_specs_file(workflow_specs_file)
        if workflow_specs_file is not None
        else _normalize_workflow_specs(DEFAULT_GUARD_BURNIN_WORKFLOW_SPECS)
    )

    def load_runs_for_workflow(workflow_name: str) -> list[dict[str, Any]]:
        if fixtures_payload is not None:
            return _load_fixture_runs(payload=fixtures_payload, workflow_name=workflow_name)
        return _fetch_runs_from_github(
            repo=repo,
            branch=branch,
            workflow_name=workflow_name,
            per_page=per_page,
        )

    def load_artifacts_for_run(run_id: int) -> list[dict[str, Any]]:
        if fixtures_payload is not None:
            return _load_fixture_artifacts(payload=fixtures_payload, run_id=run_id)
        return _fetch_run_artifacts_from_github(repo=repo, run_id=run_id)

    evaluations: list[dict[str, Any]] = []
    aggregate_non_success_runs_total = 0
    aggregate_missing_required_artifacts_total = 0
    for spec in workflow_specs:
        workflow_name = str(spec.get("workflow_name") or "")
        resolved_required_runs = _resolve_required_successful_runs(
            spec=spec,
            required_successful_runs=required_successful_runs,
            digest_required_successful_runs=digest_required_successful_runs,
            drill_required_successful_runs=drill_required_successful_runs,
        )
        runs = load_runs_for_workflow(workflow_name)
        evaluation, counters = _evaluate_workflow_burnin(
            spec=spec,
            runs=runs,
            required_successful_runs=resolved_required_runs,
            load_artifacts_for_run=load_artifacts_for_run,
        )
        evaluations.append(evaluation)
        aggregate_non_success_runs_total += int(counters["non_success_runs_total"])
        aggregate_missing_required_artifacts_total += int(counters["missing_required_artifacts_total"])

    degraded_workflows = [item for item in evaluations if not bool(item.get("is_healthy"))]
    workflows_below_burnin = [item for item in evaluations if not bool(item.get("has_sufficient_runs"))]
    degraded_workflow_names = [str(item.get("workflow_name") or "") for item in degraded_workflows]
    runs_evaluated_total = sum(int(item.get("burnin_runs_observed") or 0) for item in evaluations)

    criteria = [
        {
            "name": "workflows_configured_for_guard_burnin",
            "passed": bool(len(workflow_specs) > 0),
            "details": f"workflow_specs_total={len(workflow_specs)}",
        },
        {
            "name": "guard_burnin_window_healthy",
            "passed": bool((len(degraded_workflows) == 0) or allow_degraded),
            "details": (
                f"degraded_workflows_total={len(degraded_workflows)}, "
                f"allow_degraded={allow_degraded}"
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
            "per_page": int(per_page),
            "required_successful_runs": int(required_successful_runs),
            "digest_required_successful_runs": int(digest_required_successful_runs),
            "drill_required_successful_runs": int(drill_required_successful_runs),
            "allow_degraded": bool(allow_degraded),
            "now_utc": _format_utc(now_utc),
            "fixtures_file": str(fixtures_file) if fixtures_file is not None else None,
            "workflow_specs_file": str(workflow_specs_file) if workflow_specs_file is not None else None,
            "workflow_specs": workflow_specs,
            "output_file": str(output_file),
        },
        "metrics": {
            "workflows_total": int(len(evaluations)),
            "workflows_healthy_total": int(len(evaluations) - len(degraded_workflows)),
            "workflows_degraded_total": int(len(degraded_workflows)),
            "workflows_below_burnin_total": int(len(workflows_below_burnin)),
            "runs_evaluated_total": int(runs_evaluated_total),
            "non_success_runs_total": int(aggregate_non_success_runs_total),
            "missing_required_artifacts_total": int(aggregate_missing_required_artifacts_total),
            "criteria_failed": int(len(failed_criteria)),
        },
        "criteria": criteria,
        "failed_criteria": failed_criteria,
        "evaluations": evaluations,
        "degraded_workflow_names": degraded_workflow_names,
        "decision": {
            "guard_burnin_degraded": bool(len(degraded_workflows) > 0),
            "recommended_action": (
                "guard_burnin_healthy"
                if len(degraded_workflows) == 0
                else "guard_burnin_rehearsal_required"
            ),
        },
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2), encoding="utf-8")

    if not success:
        raise RuntimeError(f"Master guard burn-in check failed: {json.dumps(report, sort_keys=True)}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate burn-in health for master guard/digest workflows by requiring N recent "
            "successful runs with required artifacts."
        )
    )
    parser.add_argument("--label", default="master-guard-burnin-check")
    parser.add_argument("--repo", default="donatomaurizio99-collab/GOC")
    parser.add_argument("--branch", default="master")
    parser.add_argument("--per-page", type=int, default=20)
    parser.add_argument("--required-successful-runs", type=int, default=3)
    parser.add_argument("--digest-required-successful-runs", type=int, default=1)
    parser.add_argument("--drill-required-successful-runs", type=int, default=1)
    parser.add_argument("--fixtures-file")
    parser.add_argument("--workflow-specs-file")
    parser.add_argument("--now-utc")
    parser.add_argument("--allow-degraded", action="store_true")
    parser.add_argument("--output-file", default="artifacts/master-guard-burnin-check.json")
    args = parser.parse_args(argv)

    if int(args.per_page) <= 0:
        print("[master-guard-burnin-check] ERROR: --per-page must be > 0.", file=sys.stderr)
        return 2
    if int(args.required_successful_runs) <= 0:
        print("[master-guard-burnin-check] ERROR: --required-successful-runs must be > 0.", file=sys.stderr)
        return 2
    if int(args.digest_required_successful_runs) <= 0:
        print(
            "[master-guard-burnin-check] ERROR: --digest-required-successful-runs must be > 0.",
            file=sys.stderr,
        )
        return 2
    if int(args.drill_required_successful_runs) <= 0:
        print(
            "[master-guard-burnin-check] ERROR: --drill-required-successful-runs must be > 0.",
            file=sys.stderr,
        )
        return 2

    fixtures_file = Path(str(args.fixtures_file)).expanduser() if args.fixtures_file else None
    workflow_specs_file = Path(str(args.workflow_specs_file)).expanduser() if args.workflow_specs_file else None
    output_file = Path(str(args.output_file)).expanduser()
    try:
        report = run_guard_burnin_check(
            label=str(args.label),
            repo=str(args.repo),
            branch=str(args.branch),
            per_page=int(args.per_page),
            required_successful_runs=int(args.required_successful_runs),
            digest_required_successful_runs=int(args.digest_required_successful_runs),
            drill_required_successful_runs=int(args.drill_required_successful_runs),
            fixtures_file=fixtures_file,
            workflow_specs_file=workflow_specs_file,
            now_utc=_resolve_now_utc(args.now_utc),
            allow_degraded=bool(args.allow_degraded),
            output_file=output_file,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[master-guard-burnin-check] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
