from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable


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
        "workflow_file": "master-release-gate-runtime-slo-guard.yml",
        "workflow_name": "Master Release Gate Runtime SLO Guard",
        "required_artifacts": [
            "release-gate-runtime-slo-guard",
            "release-gate-runtime-slo-guard-issue-upsert",
            "release-gate-runtime-slo-guard-selftest",
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

DEFAULT_BURNIN_WINDOW_DAYS = 14
DEFAULT_MTTR_TARGET_SECONDS = 300.0
DEFAULT_MTTR_POLICY_FILE = "docs/watchdog-rehearsal-mttr-policy.json"
DEFAULT_WATCHDOG_SLO_WORKFLOW_NAME = "Master Watchdog Rehearsal SLO Guard"
DEFAULT_WATCHDOG_SLO_ARTIFACT_NAME = "master-watchdog-rehearsal-slo-guard"
DEFAULT_WATCHDOG_SLO_REPORT_FILENAME = "master-watchdog-rehearsal-slo-guard.json"
BURNIN_BREACH_REASONS = ("stale", "failed", "mttr")


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


def _parse_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _percentile(values: list[float], percentile: float) -> float:
    _expect(values, "Cannot compute percentile for empty values.")
    sorted_values = sorted(float(item) for item in values)
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    p = max(0.0, min(100.0, float(percentile)))
    rank = (len(sorted_values) - 1) * p / 100.0
    lower = int(math.floor(rank))
    upper = int(math.ceil(rank))
    if lower == upper:
        return float(sorted_values[lower])
    weight = rank - lower
    return float((sorted_values[lower] * (1.0 - weight)) + (sorted_values[upper] * weight))


def _percentile_snapshot(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"p50": None, "p95": None, "p99": None}
    return {
        "p50": round(_percentile(values, 50.0), 3),
        "p95": round(_percentile(values, 95.0), 3),
        "p99": round(_percentile(values, 99.0), 3),
    }


def _delta(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None:
        return None
    return round(float(current) - float(previous), 3)


def _run_successful(run: dict[str, Any]) -> bool:
    return bool(
        str(run.get("status") or "") == "completed"
        and str(run.get("conclusion") or "") == "success"
    )


def _resolve_run_url(*, repo: str, run: dict[str, Any]) -> str:
    direct = str(run.get("html_url") or run.get("url") or "").strip()
    if direct:
        return direct
    run_id = int(run.get("id") or 0)
    if run_id <= 0:
        return ""
    return f"https://github.com/{repo}/actions/runs/{run_id}"


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


def _load_run_report_from_artifact(
    *,
    repo: str,
    run_id: int,
    artifact_name: str,
    report_filename: str,
) -> dict[str, Any]:
    _expect(run_id > 0, "run_id must be > 0 for artifact download.")
    _expect(str(artifact_name).strip(), "artifact_name must be non-empty.")
    _expect(str(report_filename).strip(), "report_filename must be non-empty.")
    with tempfile.TemporaryDirectory(prefix="master-guard-burnin-watchdog-report-") as temp_root_text:
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
            if item.is_file() and item.name.lower() == str(report_filename).strip().lower()
        )
        _expect(
            len(candidates) >= 1,
            (
                f"Downloaded artifact '{artifact_name}' for run #{run_id} but did not find "
                f"'{report_filename}'."
            ),
        )
        return _load_json_file(candidates[0])


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


def _load_fixture_run_report(
    *,
    payload: dict[str, Any],
    run_id: int,
    artifact_name: str,
) -> dict[str, Any] | None:
    run_reports = payload.get("run_reports")
    if not isinstance(run_reports, dict):
        return None
    entry = run_reports.get(str(run_id))
    if entry is None:
        return None

    if isinstance(entry, dict):
        by_artifact = entry.get("artifacts")
        if isinstance(by_artifact, dict):
            artifact_report = by_artifact.get(str(artifact_name))
            if isinstance(artifact_report, dict):
                return artifact_report
        report_payload = entry.get("report")
        if isinstance(report_payload, dict):
            return report_payload
        if "label" in entry or "metrics" in entry or "decision" in entry:
            return entry
    return None


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
    load_artifacts_for_run: Callable[[int], list[dict[str, Any]]],
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
        run_success = bool(run_status == "completed" and run_conclusion == "success")
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


def _collect_burnin_window_stats(
    *,
    repo: str,
    workflow_specs: list[dict[str, Any]],
    runs_by_workflow: dict[str, list[dict[str, Any]]],
    window_start_utc: datetime,
    window_end_utc: datetime,
    watchdog_slo_workflow_name: str,
    load_artifacts_for_run: Callable[[int], list[dict[str, Any]]],
    load_watchdog_report_for_run: Callable[[int], dict[str, Any] | None],
) -> dict[str, Any]:
    runs_in_window_total = 0
    watchdog_report_runs_total = 0
    mttr_samples: list[float] = []
    breach_counts = {reason: 0 for reason in BURNIN_BREACH_REASONS}
    missing_artifact_items: list[dict[str, Any]] = []
    report_load_errors: list[dict[str, Any]] = []

    for spec in workflow_specs:
        workflow_name = str(spec.get("workflow_name") or "")
        workflow_file = str(spec.get("workflow_file") or "")
        required_artifacts = [str(item) for item in spec.get("required_artifacts") or []]
        runs = runs_by_workflow.get(workflow_name) or []

        for run in runs:
            run_updated_at = _parse_utc_timestamp(str(run.get("updated_at") or ""))
            if run_updated_at is None:
                continue
            if run_updated_at < window_start_utc or run_updated_at >= window_end_utc:
                continue

            runs_in_window_total += 1
            run_id = int(run.get("id") or 0)
            run_url = _resolve_run_url(repo=repo, run=run)
            available_artifacts: list[str] = []
            if run_id > 0:
                run_artifacts = load_artifacts_for_run(run_id)
                available_artifacts, _ = _extract_artifact_names(run_artifacts)

            missing_required_artifacts = [
                artifact_name
                for artifact_name in required_artifacts
                if artifact_name not in available_artifacts
            ]
            for artifact_name in missing_required_artifacts:
                missing_artifact_items.append(
                    {
                        "workflow_name": workflow_name,
                        "workflow_file": workflow_file,
                        "run_id": run_id if run_id > 0 else None,
                        "run_url": run_url or None,
                        "artifact_name": artifact_name,
                    }
                )

            if workflow_name != watchdog_slo_workflow_name or run_id <= 0:
                continue
            if missing_required_artifacts:
                continue

            try:
                watchdog_report = load_watchdog_report_for_run(run_id)
            except Exception as exc:  # noqa: BLE001
                report_load_errors.append(
                    {
                        "run_id": run_id,
                        "run_url": run_url or None,
                        "error": str(exc),
                    }
                )
                continue

            if not isinstance(watchdog_report, dict):
                report_load_errors.append(
                    {
                        "run_id": run_id,
                        "run_url": run_url or None,
                        "error": "watchdog report payload was not a JSON object",
                    }
                )
                continue

            watchdog_report_runs_total += 1
            decision = watchdog_report.get("decision") if isinstance(watchdog_report.get("decision"), dict) else {}
            metrics = watchdog_report.get("metrics") if isinstance(watchdog_report.get("metrics"), dict) else {}
            breach_reason = str(decision.get("breach_reason") or "").strip().lower()
            breached = bool(decision.get("watchdog_rehearsal_slo_breached")) or breach_reason in BURNIN_BREACH_REASONS
            if breached and breach_reason in breach_counts:
                breach_counts[breach_reason] += 1

            mttr_seconds = _parse_float(metrics.get("mttr_seconds"))
            if mttr_seconds is None:
                mttr_seconds = _parse_float(metrics.get("alert_chain_mttr_seconds"))
            if mttr_seconds is not None and mttr_seconds >= 0.0:
                mttr_samples.append(float(mttr_seconds))

    deduped_missing_items: list[dict[str, Any]] = []
    seen_missing: set[tuple[str, int | None, str]] = set()
    for item in sorted(
        missing_artifact_items,
        key=lambda entry: (
            str(entry.get("workflow_name") or ""),
            int(entry.get("run_id") or 0),
            str(entry.get("artifact_name") or ""),
        ),
    ):
        key = (
            str(item.get("workflow_name") or ""),
            int(item.get("run_id") or 0) if item.get("run_id") is not None else None,
            str(item.get("artifact_name") or ""),
        )
        if key in seen_missing:
            continue
        seen_missing.add(key)
        deduped_missing_items.append(item)

    breach_total = int(sum(int(breach_counts[reason]) for reason in BURNIN_BREACH_REASONS))
    percentiles = _percentile_snapshot(mttr_samples)
    return {
        "window_start_utc": _format_utc(window_start_utc),
        "window_end_utc": _format_utc(window_end_utc),
        "runs_in_window_total": int(runs_in_window_total),
        "watchdog_report_runs_total": int(watchdog_report_runs_total),
        "samples_total": int(len(mttr_samples)),
        "mttr_samples_seconds": [round(float(item), 3) for item in sorted(mttr_samples)],
        "mttr_percentiles_seconds": percentiles,
        "breach_counts": {
            "total": int(breach_total),
            "by_reason": {reason: int(breach_counts[reason]) for reason in BURNIN_BREACH_REASONS},
        },
        "unjustified_breaches": int(breach_total),
        "missing_artifacts": {
            "total": int(len(deduped_missing_items)),
            "items": deduped_missing_items,
        },
        "report_load_errors_total": int(len(report_load_errors)),
        "report_load_errors": report_load_errors,
    }


def _resolve_mttr_target_seconds(
    *,
    mttr_target_seconds: float | None,
    mttr_policy_file: Path | None,
) -> dict[str, Any]:
    if mttr_target_seconds is not None:
        _expect(float(mttr_target_seconds) > 0.0, "mttr_target_seconds must be > 0 when provided.")
        return {
            "target_mttr_seconds": round(float(mttr_target_seconds), 3),
            "source": "argument",
            "policy_file": str(mttr_policy_file) if mttr_policy_file is not None else None,
            "load_error": None,
        }

    if mttr_policy_file is not None and mttr_policy_file.exists():
        try:
            policy_payload = _load_json_file(mttr_policy_file)
            policy_target = _parse_float(policy_payload.get("target_mttr_seconds"))
            if policy_target is not None and policy_target > 0.0:
                return {
                    "target_mttr_seconds": round(float(policy_target), 3),
                    "source": "policy_file",
                    "policy_file": str(mttr_policy_file),
                    "load_error": None,
                }
            return {
                "target_mttr_seconds": round(float(DEFAULT_MTTR_TARGET_SECONDS), 3),
                "source": "default",
                "policy_file": str(mttr_policy_file),
                "load_error": "Policy file missing numeric positive 'target_mttr_seconds'; using default.",
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "target_mttr_seconds": round(float(DEFAULT_MTTR_TARGET_SECONDS), 3),
                "source": "default",
                "policy_file": str(mttr_policy_file),
                "load_error": f"Failed to load policy file: {exc}",
            }

    return {
        "target_mttr_seconds": round(float(DEFAULT_MTTR_TARGET_SECONDS), 3),
        "source": "default",
        "policy_file": str(mttr_policy_file) if mttr_policy_file is not None else None,
        "load_error": None,
    }


def run_guard_burnin_check(
    *,
    label: str,
    repo: str,
    branch: str,
    per_page: int,
    required_successful_runs: int,
    digest_required_successful_runs: int,
    drill_required_successful_runs: int,
    burnin_window_days: int,
    mttr_target_seconds: float | None,
    mttr_policy_file: Path | None,
    watchdog_slo_workflow_name: str,
    watchdog_slo_artifact_name: str,
    watchdog_slo_report_filename: str,
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
    _expect(burnin_window_days > 0, "burnin_window_days must be > 0.")
    _expect(str(watchdog_slo_workflow_name).strip(), "watchdog_slo_workflow_name must be non-empty.")
    _expect(str(watchdog_slo_artifact_name).strip(), "watchdog_slo_artifact_name must be non-empty.")
    _expect(str(watchdog_slo_report_filename).strip(), "watchdog_slo_report_filename must be non-empty.")

    started = time.perf_counter()
    fixtures_payload = _load_json_file(fixtures_file) if fixtures_file is not None else None
    workflow_specs = (
        _load_workflow_specs_file(workflow_specs_file)
        if workflow_specs_file is not None
        else _normalize_workflow_specs(DEFAULT_GUARD_BURNIN_WORKFLOW_SPECS)
    )

    artifacts_cache: dict[int, list[dict[str, Any]]] = {}
    watchdog_report_cache: dict[int, dict[str, Any] | None] = {}
    runs_by_workflow: dict[str, list[dict[str, Any]]] = {}

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
        if run_id <= 0:
            return []
        cached = artifacts_cache.get(int(run_id))
        if cached is not None:
            return cached
        if fixtures_payload is not None:
            loaded = _load_fixture_artifacts(payload=fixtures_payload, run_id=run_id)
        else:
            loaded = _fetch_run_artifacts_from_github(repo=repo, run_id=run_id)
        artifacts_cache[int(run_id)] = loaded
        return loaded

    def load_watchdog_report_for_run(run_id: int) -> dict[str, Any] | None:
        if run_id <= 0:
            return None
        if int(run_id) in watchdog_report_cache:
            return watchdog_report_cache[int(run_id)]
        loaded_report: dict[str, Any] | None
        if fixtures_payload is not None:
            loaded_report = _load_fixture_run_report(
                payload=fixtures_payload,
                run_id=run_id,
                artifact_name=watchdog_slo_artifact_name,
            )
        else:
            loaded_report = _load_run_report_from_artifact(
                repo=repo,
                run_id=run_id,
                artifact_name=watchdog_slo_artifact_name,
                report_filename=watchdog_slo_report_filename,
            )
        watchdog_report_cache[int(run_id)] = loaded_report
        return loaded_report

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
        runs_by_workflow[workflow_name] = runs
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

    current_window_end = now_utc
    current_window_start = now_utc - timedelta(days=burnin_window_days)
    previous_window_start = current_window_start - timedelta(days=burnin_window_days)

    current_window = _collect_burnin_window_stats(
        repo=repo,
        workflow_specs=workflow_specs,
        runs_by_workflow=runs_by_workflow,
        window_start_utc=current_window_start,
        window_end_utc=current_window_end,
        watchdog_slo_workflow_name=watchdog_slo_workflow_name,
        load_artifacts_for_run=load_artifacts_for_run,
        load_watchdog_report_for_run=load_watchdog_report_for_run,
    )
    previous_window = _collect_burnin_window_stats(
        repo=repo,
        workflow_specs=workflow_specs,
        runs_by_workflow=runs_by_workflow,
        window_start_utc=previous_window_start,
        window_end_utc=current_window_start,
        watchdog_slo_workflow_name=watchdog_slo_workflow_name,
        load_artifacts_for_run=load_artifacts_for_run,
        load_watchdog_report_for_run=load_watchdog_report_for_run,
    )

    mttr_target_resolution = _resolve_mttr_target_seconds(
        mttr_target_seconds=mttr_target_seconds,
        mttr_policy_file=mttr_policy_file,
    )
    mttr_target_effective_seconds = float(mttr_target_resolution["target_mttr_seconds"])

    current_percentiles = current_window.get("mttr_percentiles_seconds") if isinstance(
        current_window.get("mttr_percentiles_seconds"), dict
    ) else {}
    previous_percentiles = previous_window.get("mttr_percentiles_seconds") if isinstance(
        previous_window.get("mttr_percentiles_seconds"), dict
    ) else {}
    current_p95 = _parse_float(current_percentiles.get("p95"))
    current_unjustified_breaches = int(current_window.get("unjustified_breaches") or 0)
    current_missing_artifacts_total = int(
        ((current_window.get("missing_artifacts") or {}) if isinstance(current_window.get("missing_artifacts"), dict) else {}).get(
            "total"
        )
        or 0
    )

    exit_criteria = [
        {
            "name": "missing_artifacts_zero",
            "passed": bool(current_missing_artifacts_total == 0),
            "details": f"missing_artifacts_total={current_missing_artifacts_total}",
        },
        {
            "name": "unjustified_breaches_zero",
            "passed": bool(current_unjustified_breaches == 0),
            "details": f"unjustified_breaches={current_unjustified_breaches}",
        },
        {
            "name": "p95_mttr_within_target",
            "passed": bool(current_p95 is not None and float(current_p95) <= float(mttr_target_effective_seconds)),
            "details": (
                f"p95_mttr_seconds={current_p95}, "
                f"mttr_target_seconds={mttr_target_effective_seconds}"
            ),
        },
    ]
    failed_exit_criteria = [item for item in exit_criteria if not bool(item.get("passed"))]
    exit_criteria_met = len(failed_exit_criteria) == 0

    trend_available = bool(
        int(previous_window.get("samples_total") or 0) > 0
        or int((previous_window.get("breach_counts") or {}).get("total") or 0) > 0
        or int(((previous_window.get("missing_artifacts") or {}).get("total") or 0)) > 0
    )
    trend = {
        "available": bool(trend_available),
        "sample_count_delta": int(int(current_window.get("samples_total") or 0) - int(previous_window.get("samples_total") or 0)),
        "breach_total_delta": int(
            int((current_window.get("breach_counts") or {}).get("total") or 0)
            - int((previous_window.get("breach_counts") or {}).get("total") or 0)
        ),
        "missing_artifacts_total_delta": int(
            int(((current_window.get("missing_artifacts") or {}).get("total") or 0))
            - int(((previous_window.get("missing_artifacts") or {}).get("total") or 0))
        ),
        "mttr_percentile_delta_seconds": {
            "p50": _delta(
                _parse_float(current_percentiles.get("p50")),
                _parse_float(previous_percentiles.get("p50")),
            ),
            "p95": _delta(
                _parse_float(current_percentiles.get("p95")),
                _parse_float(previous_percentiles.get("p95")),
            ),
            "p99": _delta(
                _parse_float(current_percentiles.get("p99")),
                _parse_float(previous_percentiles.get("p99")),
            ),
        },
    }

    guard_burnin_degraded = bool(len(degraded_workflows) > 0 or not exit_criteria_met)
    non_green_reasons: list[str] = []
    if len(degraded_workflows) > 0:
        non_green_reasons.append("workflow_window_degraded")
    for failed in failed_exit_criteria:
        non_green_reasons.append(f"hard_exit_{str(failed.get('name') or 'unknown')}")

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
        {
            "name": "guard_burnin_hard_exit_criteria",
            "passed": bool(exit_criteria_met or allow_degraded),
            "details": (
                f"hard_exit_criteria_failed_total={len(failed_exit_criteria)}, "
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
            "burnin_window_days": int(burnin_window_days),
            "mttr_target_seconds_override": (
                round(float(mttr_target_seconds), 3) if mttr_target_seconds is not None else None
            ),
            "mttr_policy_file": str(mttr_policy_file) if mttr_policy_file is not None else None,
            "watchdog_slo_workflow_name": watchdog_slo_workflow_name,
            "watchdog_slo_artifact_name": watchdog_slo_artifact_name,
            "watchdog_slo_report_filename": watchdog_slo_report_filename,
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
            "burnin_window_days": int(burnin_window_days),
            "burnin_window_samples_total": int(current_window.get("samples_total") or 0),
            "burnin_unjustified_breaches_total": int(current_unjustified_breaches),
            "burnin_missing_artifacts_total": int(current_missing_artifacts_total),
            "burnin_mttr_p95_seconds": float(current_p95) if current_p95 is not None else None,
            "burnin_mttr_target_seconds": round(float(mttr_target_effective_seconds), 3),
            "hard_exit_criteria_failed": int(len(failed_exit_criteria)),
            "criteria_failed": int(len(failed_criteria)),
        },
        "criteria": criteria,
        "failed_criteria": failed_criteria,
        "evaluations": evaluations,
        "degraded_workflow_names": degraded_workflow_names,
        "burnin_window": {
            "window_days": int(burnin_window_days),
            "current_window": current_window,
            "previous_window": previous_window,
            "trend": trend,
            "mttr_target_seconds": round(float(mttr_target_effective_seconds), 3),
            "mttr_target_source": str(mttr_target_resolution.get("source") or "default"),
            "mttr_target_policy_file": mttr_target_resolution.get("policy_file"),
            "mttr_target_load_error": mttr_target_resolution.get("load_error"),
            "exit_criteria": {
                "criteria": exit_criteria,
                "failed": failed_exit_criteria,
                "met": bool(exit_criteria_met),
            },
        },
        "decision": {
            "guard_burnin_degraded": bool(guard_burnin_degraded),
            "burnin_status": "green" if not guard_burnin_degraded else "non_green",
            "non_green": bool(guard_burnin_degraded),
            "non_green_reasons": non_green_reasons,
            "hard_exit_criteria_met": bool(exit_criteria_met),
            "hard_exit_criteria_failed": [str(item.get("name") or "") for item in failed_exit_criteria],
            "recommended_action": (
                "guard_burnin_healthy"
                if not guard_burnin_degraded
                else (
                    "guard_burnin_rehearsal_required_and_exit_criteria_failed"
                    if len(degraded_workflows) > 0 and not exit_criteria_met
                    else (
                        "guard_burnin_rehearsal_required"
                        if len(degraded_workflows) > 0
                        else "guard_burnin_exit_criteria_failed"
                    )
                )
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
            "successful runs with required artifacts, plus a 14-day watchdog MTTR/breach hard-exit window."
        )
    )
    parser.add_argument("--label", default="master-guard-burnin-check")
    parser.add_argument("--repo", default="donatomaurizio99-collab/GOC")
    parser.add_argument("--branch", default="master")
    parser.add_argument("--per-page", type=int, default=20)
    parser.add_argument("--required-successful-runs", type=int, default=3)
    parser.add_argument("--digest-required-successful-runs", type=int, default=1)
    parser.add_argument("--drill-required-successful-runs", type=int, default=1)
    parser.add_argument("--burnin-window-days", type=int, default=DEFAULT_BURNIN_WINDOW_DAYS)
    parser.add_argument("--mttr-target-seconds", type=float)
    parser.add_argument("--mttr-policy-file", default=DEFAULT_MTTR_POLICY_FILE)
    parser.add_argument("--watchdog-slo-workflow-name", default=DEFAULT_WATCHDOG_SLO_WORKFLOW_NAME)
    parser.add_argument("--watchdog-slo-artifact-name", default=DEFAULT_WATCHDOG_SLO_ARTIFACT_NAME)
    parser.add_argument("--watchdog-slo-report-filename", default=DEFAULT_WATCHDOG_SLO_REPORT_FILENAME)
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
    if int(args.burnin_window_days) <= 0:
        print("[master-guard-burnin-check] ERROR: --burnin-window-days must be > 0.", file=sys.stderr)
        return 2
    if args.mttr_target_seconds is not None and float(args.mttr_target_seconds) <= 0.0:
        print("[master-guard-burnin-check] ERROR: --mttr-target-seconds must be > 0 when provided.", file=sys.stderr)
        return 2
    if not str(args.watchdog_slo_workflow_name or "").strip():
        print("[master-guard-burnin-check] ERROR: --watchdog-slo-workflow-name must be non-empty.", file=sys.stderr)
        return 2
    if not str(args.watchdog_slo_artifact_name or "").strip():
        print("[master-guard-burnin-check] ERROR: --watchdog-slo-artifact-name must be non-empty.", file=sys.stderr)
        return 2
    if not str(args.watchdog_slo_report_filename or "").strip():
        print("[master-guard-burnin-check] ERROR: --watchdog-slo-report-filename must be non-empty.", file=sys.stderr)
        return 2

    fixtures_file = Path(str(args.fixtures_file)).expanduser() if args.fixtures_file else None
    workflow_specs_file = Path(str(args.workflow_specs_file)).expanduser() if args.workflow_specs_file else None
    mttr_policy_file = Path(str(args.mttr_policy_file)).expanduser() if args.mttr_policy_file else None
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
            burnin_window_days=int(args.burnin_window_days),
            mttr_target_seconds=float(args.mttr_target_seconds) if args.mttr_target_seconds is not None else None,
            mttr_policy_file=mttr_policy_file,
            watchdog_slo_workflow_name=str(args.watchdog_slo_workflow_name),
            watchdog_slo_artifact_name=str(args.watchdog_slo_artifact_name),
            watchdog_slo_report_filename=str(args.watchdog_slo_report_filename),
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
