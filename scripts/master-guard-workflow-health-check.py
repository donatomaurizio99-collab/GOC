from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


DEFAULT_GUARD_WORKFLOW_SPECS = [
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
        ],
    },
    {
        "workflow_file": "master-watchdog-rehearsal-slo-guard.yml",
        "workflow_name": "Master Watchdog Rehearsal SLO Guard",
        "required_artifacts": [
            "master-watchdog-rehearsal-slo-guard",
            "master-watchdog-rehearsal-slo-guard-issue-upsert",
        ],
    },
]

GUARD_WORKFLOW_DISCOVERY_TOKENS = (
    "guard",
    "warning",
    "required-checks",
    "workflow-health",
)


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
    payload = json.loads(path.read_text(encoding="utf-8"))
    _expect(isinstance(payload, dict), f"Expected JSON object in file: {path}")
    return payload


def _parse_contract_workflow_files(text: str) -> list[str]:
    items = [str(item).strip() for item in str(text or "").split(",") if str(item).strip()]
    deduped: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def _discover_relevant_master_guard_workflow_files(*, project_root: Path) -> list[str]:
    workflows_dir = project_root / ".github" / "workflows"
    if not workflows_dir.exists():
        return []
    discovered: list[str] = []
    for path in sorted(workflows_dir.glob("master-*.yml")):
        filename = path.name
        lower = filename.lower()
        if any(token in lower for token in GUARD_WORKFLOW_DISCOVERY_TOKENS):
            discovered.append(filename)
    return discovered


def _parse_workflow_name_from_file(path: Path) -> str:
    if not path.exists():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped.startswith("name:"):
            continue
        return stripped.split("name:", 1)[1].strip()
    return ""


def _evaluate_workflow_coverage_contract(
    *,
    project_root: Path,
    contract_workflow_files: list[str] | None,
) -> dict[str, Any]:
    coverage_files = (
        list(contract_workflow_files)
        if contract_workflow_files is not None
        else _discover_relevant_master_guard_workflow_files(project_root=project_root)
    )
    specs_by_file: dict[str, dict[str, Any]] = {}
    duplicate_spec_files: list[str] = []
    for spec in DEFAULT_GUARD_WORKFLOW_SPECS:
        workflow_file = str(spec.get("workflow_file") or "").strip()
        if not workflow_file:
            continue
        if workflow_file in specs_by_file:
            duplicate_spec_files.append(workflow_file)
            continue
        specs_by_file[workflow_file] = spec

    spec_files = sorted(specs_by_file.keys())
    coverage_set = set(coverage_files)
    spec_set = set(spec_files)

    uncovered_guard_workflow_files = sorted(coverage_set - spec_set)
    stale_spec_guard_workflow_files = sorted(spec_set - coverage_set)

    workflows_dir = project_root / ".github" / "workflows"
    missing_coverage_files_on_disk = sorted(
        file_name for file_name in coverage_files if not (workflows_dir / file_name).exists()
    )
    missing_spec_files_on_disk = sorted(
        file_name for file_name in spec_files if not (workflows_dir / file_name).exists()
    )

    workflow_name_mismatches: list[dict[str, str]] = []
    for workflow_file, spec in specs_by_file.items():
        expected_workflow_name = str(spec.get("workflow_name") or "")
        if not expected_workflow_name:
            continue
        parsed_name = _parse_workflow_name_from_file(workflows_dir / workflow_file)
        if parsed_name and parsed_name != expected_workflow_name:
            workflow_name_mismatches.append(
                {
                    "workflow_file": workflow_file,
                    "expected_workflow_name": expected_workflow_name,
                    "observed_workflow_name": parsed_name,
                }
            )

    coverage_contract_ok = bool(
        len(uncovered_guard_workflow_files) == 0
        and len(duplicate_spec_files) == 0
        and len(missing_coverage_files_on_disk) == 0
        and len(missing_spec_files_on_disk) == 0
        and len(workflow_name_mismatches) == 0
    )
    return {
        "coverage_files": coverage_files,
        "spec_files": spec_files,
        "uncovered_guard_workflow_files": uncovered_guard_workflow_files,
        "stale_spec_guard_workflow_files": stale_spec_guard_workflow_files,
        "duplicate_spec_files": sorted(duplicate_spec_files),
        "missing_coverage_files_on_disk": missing_coverage_files_on_disk,
        "missing_spec_files_on_disk": missing_spec_files_on_disk,
        "workflow_name_mismatches": workflow_name_mismatches,
        "coverage_contract_ok": coverage_contract_ok,
    }


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


def _load_fixture_runs(*, fixtures_payload: dict[str, Any], workflow_name: str) -> list[dict[str, Any]]:
    workflow_runs = fixtures_payload.get("workflow_runs")
    _expect(isinstance(workflow_runs, dict), "fixtures file must include object field 'workflow_runs'.")
    runs = workflow_runs.get(workflow_name) or []
    _expect(
        isinstance(runs, list),
        f"fixtures.workflow_runs['{workflow_name}'] must be a list when present.",
    )
    return [item for item in runs if isinstance(item, dict)]


def _load_fixture_artifacts(*, fixtures_payload: dict[str, Any], run_id: int) -> list[dict[str, Any]]:
    run_artifacts = fixtures_payload.get("run_artifacts")
    _expect(isinstance(run_artifacts, dict), "fixtures file must include object field 'run_artifacts'.")
    value = run_artifacts.get(str(run_id))
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
    return available, expired


def _evaluate_guard_workflow(
    *,
    workflow_name: str,
    required_artifacts: list[str],
    runs: list[dict[str, Any]],
    cutoff_utc: datetime,
    load_artifacts_for_run: Any,
) -> tuple[dict[str, Any], dict[str, int]]:
    runs_in_window: list[tuple[dict[str, Any], datetime | None]] = []
    runs_outside_window_ignored = 0
    runs_with_invalid_updated_at = 0

    for run in runs:
        updated_at = _parse_utc_timestamp(str(run.get("updated_at") or ""))
        if updated_at is not None and updated_at < cutoff_utc:
            runs_outside_window_ignored += 1
            continue
        if updated_at is None:
            runs_with_invalid_updated_at += 1
        runs_in_window.append((run, updated_at))

    runs_sorted = sorted(
        runs_in_window,
        key=lambda item: item[1] or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    latest_run = runs_sorted[0][0] if runs_sorted else None
    latest_run_updated_at = runs_sorted[0][1] if runs_sorted else None

    latest_run_id = int(latest_run.get("id") or 0) if latest_run else 0
    latest_run_status = str(latest_run.get("status") or "") if latest_run else ""
    latest_run_conclusion = str(latest_run.get("conclusion") or "") if latest_run else ""
    latest_run_success = bool(
        latest_run is not None and latest_run_status == "completed" and latest_run_conclusion == "success"
    )

    artifacts: list[dict[str, Any]] = []
    available_artifact_names: list[str] = []
    expired_artifact_names: list[str] = []
    if latest_run_id > 0:
        artifacts = load_artifacts_for_run(latest_run_id)
        available_artifact_names, expired_artifact_names = _extract_artifact_names(artifacts)

    missing_required_artifacts = [item for item in required_artifacts if item not in available_artifact_names]
    has_recent_run = latest_run is not None
    is_healthy = bool(has_recent_run and latest_run_success and not missing_required_artifacts)

    reasons: list[str] = []
    if not has_recent_run:
        reasons.append("no_recent_completed_run_in_window")
    elif not latest_run_success:
        reasons.append("latest_run_not_success")
    if missing_required_artifacts:
        reasons.append("required_artifacts_missing")

    evaluation = {
        "workflow_name": workflow_name,
        "required_artifacts": required_artifacts,
        "runs_total_fetched": int(len(runs)),
        "runs_in_window_total": int(len(runs_in_window)),
        "runs_outside_window_ignored_total": int(runs_outside_window_ignored),
        "runs_with_invalid_updated_at_total": int(runs_with_invalid_updated_at),
        "latest_run": (
            {
                "run_id": latest_run_id,
                "status": latest_run_status,
                "conclusion": latest_run_conclusion,
                "updated_at": str(latest_run.get("updated_at") or ""),
                "updated_at_parsed_utc": _format_utc(latest_run_updated_at),
            }
            if latest_run
            else None
        ),
        "artifact_inventory": {
            "available_artifacts": available_artifact_names,
            "expired_artifacts": expired_artifact_names,
            "artifacts_total": int(len(artifacts)),
        },
        "missing_required_artifacts": missing_required_artifacts,
        "has_recent_run": bool(has_recent_run),
        "latest_run_success": bool(latest_run_success),
        "is_healthy": bool(is_healthy),
        "degraded_reasons": reasons,
    }
    counters = {
        "runs_outside_window_ignored_total": int(runs_outside_window_ignored),
        "runs_with_invalid_updated_at_total": int(runs_with_invalid_updated_at),
    }
    return evaluation, counters


def run_guard_workflow_health_check(
    *,
    label: str,
    repo: str,
    branch: str,
    lookback_hours: int,
    per_page: int,
    fixtures_file: Path | None,
    contract_workflow_files: list[str] | None,
    now_utc: datetime,
    allow_degraded: bool,
    output_file: Path,
) -> dict[str, Any]:
    _expect(lookback_hours > 0, "lookback_hours must be > 0.")
    _expect(per_page > 0, "per_page must be > 0.")

    started = time.perf_counter()
    cutoff_utc = now_utc - timedelta(hours=lookback_hours)
    fixtures_payload = _load_json_file(fixtures_file) if fixtures_file is not None else None
    project_root = Path(__file__).resolve().parents[1]
    coverage_contract = _evaluate_workflow_coverage_contract(
        project_root=project_root,
        contract_workflow_files=contract_workflow_files,
    )

    def load_runs_for_workflow(workflow_name: str) -> list[dict[str, Any]]:
        if fixtures_payload is not None:
            return _load_fixture_runs(fixtures_payload=fixtures_payload, workflow_name=workflow_name)
        return _fetch_runs_from_github(
            repo=repo,
            branch=branch,
            workflow_name=workflow_name,
            per_page=per_page,
        )

    def load_artifacts_for_run(run_id: int) -> list[dict[str, Any]]:
        if fixtures_payload is not None:
            return _load_fixture_artifacts(fixtures_payload=fixtures_payload, run_id=run_id)
        return _fetch_run_artifacts_from_github(repo=repo, run_id=run_id)

    evaluations: list[dict[str, Any]] = []
    aggregate_runs_outside_window_ignored_total = 0
    aggregate_runs_with_invalid_updated_at_total = 0

    for spec in DEFAULT_GUARD_WORKFLOW_SPECS:
        workflow_name = str(spec["workflow_name"])
        required_artifacts = [str(item) for item in spec.get("required_artifacts") or []]
        runs = load_runs_for_workflow(workflow_name)
        evaluation, counters = _evaluate_guard_workflow(
            workflow_name=workflow_name,
            required_artifacts=required_artifacts,
            runs=runs,
            cutoff_utc=cutoff_utc,
            load_artifacts_for_run=load_artifacts_for_run,
        )
        evaluations.append(evaluation)
        aggregate_runs_outside_window_ignored_total += int(counters["runs_outside_window_ignored_total"])
        aggregate_runs_with_invalid_updated_at_total += int(counters["runs_with_invalid_updated_at_total"])

    degraded_workflows = [item for item in evaluations if not bool(item.get("is_healthy"))]
    workflows_without_recent_run = [item for item in evaluations if not bool(item.get("has_recent_run"))]
    workflows_with_non_success_latest_run = [
        item
        for item in evaluations
        if bool(item.get("has_recent_run")) and not bool(item.get("latest_run_success"))
    ]
    workflows_missing_required_artifacts = [
        item for item in evaluations if bool(item.get("missing_required_artifacts"))
    ]

    missing_required_artifacts_total = sum(
        int(len(item.get("missing_required_artifacts") or [])) for item in evaluations
    )
    degraded_workflow_names = [str(item.get("workflow_name") or "") for item in degraded_workflows]

    criteria = [
        {
            "name": "guard_workflow_coverage_contract",
            "passed": bool(coverage_contract.get("coverage_contract_ok") or allow_degraded),
            "details": (
                f"uncovered_total={len(coverage_contract.get('uncovered_guard_workflow_files') or [])}, "
                f"name_mismatch_total={len(coverage_contract.get('workflow_name_mismatches') or [])}, "
                f"allow_degraded={allow_degraded}"
            ),
        },
        {
            "name": "guard_workflows_evaluated",
            "passed": bool(len(evaluations) > 0),
            "details": f"guard_workflows_total={len(evaluations)}",
        },
        {
            "name": "guard_workflows_healthy",
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
            "lookback_hours": int(lookback_hours),
            "per_page": int(per_page),
            "allow_degraded": bool(allow_degraded),
            "now_utc": _format_utc(now_utc),
            "cutoff_utc": _format_utc(cutoff_utc),
            "fixtures_file": str(fixtures_file) if fixtures_file is not None else None,
            "coverage_contract_files_source": (
                "override" if contract_workflow_files is not None else "discovered"
            ),
            "coverage_contract_files": coverage_contract.get("coverage_files") or [],
            "guard_workflow_names": [str(item["workflow_name"]) for item in DEFAULT_GUARD_WORKFLOW_SPECS],
            "output_file": str(output_file),
        },
        "metrics": {
            "guard_workflows_total": int(len(evaluations)),
            "guard_workflows_healthy_total": int(len(evaluations) - len(degraded_workflows)),
            "guard_workflows_degraded_total": int(len(degraded_workflows)),
            "guard_workflows_without_recent_run_total": int(len(workflows_without_recent_run)),
            "guard_workflows_non_success_total": int(len(workflows_with_non_success_latest_run)),
            "guard_workflows_missing_required_artifacts_total": int(len(workflows_missing_required_artifacts)),
            "missing_required_artifacts_total": int(missing_required_artifacts_total),
            "runs_outside_window_ignored_total": int(aggregate_runs_outside_window_ignored_total),
            "runs_with_invalid_updated_at_total": int(aggregate_runs_with_invalid_updated_at_total),
            "coverage_contract_guard_workflow_files_total": int(len(coverage_contract.get("coverage_files") or [])),
            "coverage_contract_uncovered_files_total": int(
                len(coverage_contract.get("uncovered_guard_workflow_files") or [])
            ),
            "coverage_contract_stale_spec_files_total": int(
                len(coverage_contract.get("stale_spec_guard_workflow_files") or [])
            ),
            "coverage_contract_duplicate_spec_files_total": int(
                len(coverage_contract.get("duplicate_spec_files") or [])
            ),
            "coverage_contract_missing_files_on_disk_total": int(
                len(coverage_contract.get("missing_coverage_files_on_disk") or [])
                + len(coverage_contract.get("missing_spec_files_on_disk") or [])
            ),
            "coverage_contract_name_mismatches_total": int(
                len(coverage_contract.get("workflow_name_mismatches") or [])
            ),
            "criteria_failed": int(len(failed_criteria)),
        },
        "criteria": criteria,
        "failed_criteria": failed_criteria,
        "coverage_contract": coverage_contract,
        "evaluations": evaluations,
        "degraded_workflows": degraded_workflows,
        "degraded_workflow_names": degraded_workflow_names,
        "decision": {
            "guard_workflow_health_degraded": bool(len(degraded_workflows) > 0),
            "guard_workflow_coverage_contract_ok": bool(coverage_contract.get("coverage_contract_ok")),
            "recommended_action": (
                "guard_workflow_health_green"
                if len(degraded_workflows) == 0 and bool(coverage_contract.get("coverage_contract_ok"))
                else "guard_workflow_health_degraded"
            ),
        },
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2), encoding="utf-8")

    if not success:
        raise RuntimeError(f"Master guard-workflow health check failed: {json.dumps(report, sort_keys=True)}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Nightly watchdog for guard workflows on master. Ensures each guard workflow has "
            "a recent successful run with expected artifacts."
        )
    )
    parser.add_argument("--label", default="master-guard-workflow-health-check")
    parser.add_argument("--repo", default="donatomaurizio99-collab/GOC")
    parser.add_argument("--branch", default="master")
    parser.add_argument("--lookback-hours", type=int, default=30)
    parser.add_argument("--per-page", type=int, default=50)
    parser.add_argument("--fixtures-file")
    parser.add_argument(
        "--contract-workflow-files",
        help=(
            "Optional comma-separated override for relevant guard workflow files. "
            "Defaults to auto-discovery over master-* guard/warning/required-checks/workflow-health files."
        ),
    )
    parser.add_argument("--now-utc")
    parser.add_argument("--allow-degraded", action="store_true")
    parser.add_argument("--output-file", default="artifacts/master-guard-workflow-health-check.json")
    args = parser.parse_args(argv)

    if int(args.lookback_hours) <= 0:
        print("[master-guard-workflow-health-check] ERROR: --lookback-hours must be > 0.", file=sys.stderr)
        return 2
    if int(args.per_page) <= 0:
        print("[master-guard-workflow-health-check] ERROR: --per-page must be > 0.", file=sys.stderr)
        return 2

    fixtures_file = Path(str(args.fixtures_file)).expanduser() if args.fixtures_file else None
    contract_workflow_files = (
        _parse_contract_workflow_files(str(args.contract_workflow_files))
        if args.contract_workflow_files
        else None
    )
    output_file = Path(str(args.output_file)).expanduser()
    try:
        report = run_guard_workflow_health_check(
            label=str(args.label),
            repo=str(args.repo),
            branch=str(args.branch),
            lookback_hours=int(args.lookback_hours),
            per_page=int(args.per_page),
            fixtures_file=fixtures_file,
            contract_workflow_files=contract_workflow_files,
            now_utc=_resolve_now_utc(args.now_utc),
            allow_degraded=bool(args.allow_degraded),
            output_file=output_file,
        )
    except Exception as exc:
        print(f"[master-guard-workflow-health-check] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
