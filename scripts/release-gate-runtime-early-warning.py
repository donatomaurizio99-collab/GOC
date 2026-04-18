from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


CONCLUSION_SEVERITY = {
    "success": 0,
    "neutral": 1,
    "skipped": 1,
    "cancelled": 2,
    "timed_out": 3,
    "failure": 4,
    "action_required": 5,
}


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


def _seconds_between(started_at: datetime | None, completed_at: datetime | None) -> float | None:
    if started_at is None or completed_at is None:
        return None
    delta = (completed_at - started_at).total_seconds()
    return max(0.0, float(delta))


def _collapse_job_conclusion(conclusions: list[str]) -> str:
    normalized = [str(item).strip().lower() for item in conclusions if str(item).strip()]
    if not normalized:
        return ""
    return max(normalized, key=lambda item: CONCLUSION_SEVERITY.get(item, 6))


def _fetch_workflow_id(*, repo: str, workflow_name: str) -> int:
    payload = _run_gh_api(f"repos/{repo}/actions/workflows?per_page=100")
    workflows = payload.get("workflows") or []
    _expect(isinstance(workflows, list), "Invalid workflows payload format.")
    for workflow in workflows:
        if not isinstance(workflow, dict):
            continue
        if str(workflow.get("name") or "") == str(workflow_name):
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
    _expect(isinstance(runs, list), "Invalid workflow runs payload format.")
    return [item for item in runs if isinstance(item, dict)]


def _fetch_run_jobs_from_github(*, repo: str, run_id: int) -> list[dict[str, Any]]:
    payload = _run_gh_api(f"repos/{repo}/actions/runs/{run_id}/jobs?per_page=100")
    jobs = payload.get("jobs") or []
    _expect(isinstance(jobs, list), f"Invalid jobs payload format for run {run_id}.")
    return [item for item in jobs if isinstance(item, dict)]


def _load_run_jobs_from_dir(*, jobs_dir: Path, run_id: int) -> list[dict[str, Any]]:
    candidate_paths = [
        jobs_dir / f"{run_id}.json",
        jobs_dir / f"run-{run_id}.json",
        jobs_dir / f"run-{run_id}-jobs.json",
    ]
    file_path = next((path for path in candidate_paths if path.exists()), None)
    _expect(file_path is not None, f"No jobs fixture file found for run id {run_id} in {jobs_dir}")
    payload = _load_json_file(file_path)
    jobs = payload.get("jobs") or []
    _expect(isinstance(jobs, list), f"Invalid jobs fixture payload format for run {run_id}: {file_path}")
    return [item for item in jobs if isinstance(item, dict)]


def _evaluate_run(
    *,
    run: dict[str, Any],
    jobs: list[dict[str, Any]],
    release_gate_job_name: str,
    threshold_seconds: int,
) -> dict[str, Any]:
    matching_jobs = [
        job
        for job in jobs
        if str(job.get("name") or "").strip() == release_gate_job_name
    ]
    durations: list[float] = []
    conclusions: list[str] = []
    for job in matching_jobs:
        started_at = _parse_utc_timestamp(str(job.get("started_at") or ""))
        completed_at = _parse_utc_timestamp(str(job.get("completed_at") or ""))
        duration = _seconds_between(started_at, completed_at)
        if duration is not None:
            durations.append(float(duration))
        conclusion = str(job.get("conclusion") or "").strip()
        if conclusion:
            conclusions.append(conclusion)

    selected_duration = max(durations) if durations else None
    selected_conclusion = _collapse_job_conclusion(conclusions)
    over_threshold = bool(
        selected_duration is not None and float(selected_duration) >= float(threshold_seconds)
    )
    return {
        "run_id": int(run.get("id") or 0),
        "run_name": str(run.get("name") or ""),
        "run_status": str(run.get("status") or ""),
        "run_conclusion": str(run.get("conclusion") or ""),
        "head_sha": str(run.get("head_sha") or ""),
        "updated_at": str(run.get("updated_at") or ""),
        "release_gate_job_name": release_gate_job_name,
        "release_gate_job_matches_total": int(len(matching_jobs)),
        "release_gate_duration_samples_total": int(len(durations)),
        "release_gate_duration_seconds": round(float(selected_duration), 3) if selected_duration is not None else None,
        "release_gate_conclusion": selected_conclusion,
        "over_threshold": bool(over_threshold),
    }


def run_runtime_early_warning(
    *,
    label: str,
    repo: str,
    branch: str,
    workflow_name: str,
    release_gate_job_name: str,
    lookback_hours: int,
    per_page: int,
    threshold_seconds: int,
    sustained_runs: int,
    runs_file: Path | None,
    jobs_dir: Path | None,
    now_utc: datetime,
    fail_on_warning: bool,
    output_file: Path,
) -> dict[str, Any]:
    _expect(lookback_hours > 0, "lookback_hours must be > 0.")
    _expect(per_page > 0, "per_page must be > 0.")
    _expect(threshold_seconds > 0, "threshold_seconds must be > 0.")
    _expect(sustained_runs > 0, "sustained_runs must be > 0.")

    started = time.perf_counter()
    if runs_file is not None:
        runs_payload = _load_json_file(runs_file)
        runs = runs_payload.get("workflow_runs") or []
        _expect(isinstance(runs, list), f"Invalid runs fixture payload format in {runs_file}")
        run_items = [item for item in runs if isinstance(item, dict)]
    else:
        run_items = _fetch_runs_from_github(
            repo=repo,
            branch=branch,
            workflow_name=workflow_name,
            per_page=per_page,
        )
    _expect(run_items, "No workflow runs available for runtime early warning.")

    cutoff_utc = now_utc - timedelta(hours=lookback_hours)
    evaluations: list[dict[str, Any]] = []
    runs_outside_window_ignored_total = 0
    runs_with_invalid_updated_at_total = 0

    for run in run_items:
        run_id = int(run.get("id") or 0)
        _expect(run_id > 0, f"Invalid run id in workflow runs payload: {run!r}")
        updated_at_value = str(run.get("updated_at") or "")
        updated_at = _parse_utc_timestamp(updated_at_value)
        if updated_at is not None and updated_at < cutoff_utc:
            runs_outside_window_ignored_total += 1
            continue
        if updated_at is None:
            runs_with_invalid_updated_at_total += 1

        if jobs_dir is not None:
            jobs = _load_run_jobs_from_dir(jobs_dir=jobs_dir, run_id=run_id)
        else:
            jobs = _fetch_run_jobs_from_github(repo=repo, run_id=run_id)
        evaluation = _evaluate_run(
            run=run,
            jobs=jobs,
            release_gate_job_name=release_gate_job_name,
            threshold_seconds=threshold_seconds,
        )
        evaluation["updated_at_parsed_utc"] = _format_utc(updated_at)
        evaluations.append(evaluation)

    duration_evaluations = [item for item in evaluations if item.get("release_gate_duration_seconds") is not None]
    runs_over_threshold = [item for item in duration_evaluations if bool(item.get("over_threshold"))]

    consecutive_runs_over_threshold = 0
    for item in duration_evaluations:
        if bool(item.get("over_threshold")):
            consecutive_runs_over_threshold += 1
        else:
            break

    warning_triggered = bool(
        consecutive_runs_over_threshold >= sustained_runs and len(duration_evaluations) >= sustained_runs
    )
    warning_message = (
        f"Release Gate runtime early warning: {consecutive_runs_over_threshold} consecutive master CI runs "
        f"at or above {threshold_seconds}s for '{release_gate_job_name}'."
        if warning_triggered
        else ""
    )
    if warning_triggered:
        print(f"::warning::{warning_message}")

    criteria = [
        {
            "name": "release_gate_duration_samples_recorded",
            "passed": bool(len(duration_evaluations) > 0),
            "details": (
                f"duration_samples={len(duration_evaluations)}, "
                f"runs_in_window={len(evaluations)}, lookback_hours={lookback_hours}"
            ),
        },
        {
            "name": "warning_policy",
            "passed": bool((not warning_triggered) or (not fail_on_warning)),
            "details": (
                f"warning_triggered={warning_triggered}, fail_on_warning={fail_on_warning}, "
                f"consecutive_runs_over_threshold={consecutive_runs_over_threshold}, sustained_runs={sustained_runs}"
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
            "release_gate_job_name": release_gate_job_name,
            "lookback_hours": int(lookback_hours),
            "per_page": int(per_page),
            "threshold_seconds": int(threshold_seconds),
            "sustained_runs": int(sustained_runs),
            "fail_on_warning": bool(fail_on_warning),
            "now_utc": _format_utc(now_utc),
            "cutoff_utc": _format_utc(cutoff_utc),
            "fixture_runs_file": str(runs_file) if runs_file is not None else None,
            "fixture_jobs_dir": str(jobs_dir) if jobs_dir is not None else None,
            "output_file": str(output_file),
        },
        "metrics": {
            "runs_total_fetched": int(len(run_items)),
            "runs_outside_window_ignored_total": int(runs_outside_window_ignored_total),
            "runs_with_invalid_updated_at_total": int(runs_with_invalid_updated_at_total),
            "runs_in_window_total": int(len(evaluations)),
            "runs_with_release_gate_duration_total": int(len(duration_evaluations)),
            "runs_over_threshold_total": int(len(runs_over_threshold)),
            "consecutive_runs_over_threshold": int(consecutive_runs_over_threshold),
            "warning_triggered": 1 if warning_triggered else 0,
            "criteria_failed": int(len(failed_criteria)),
        },
        "criteria": criteria,
        "failed_criteria": failed_criteria,
        "warning_message": warning_message if warning_triggered else None,
        "decision": {
            "warning_triggered": bool(warning_triggered),
            "release_blocked": bool(warning_triggered and fail_on_warning),
            "recommended_action": (
                "investigate_release_gate_runtime_regression"
                if warning_triggered
                else "runtime_within_warning_budget"
            ),
        },
        "evaluations": evaluations,
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2), encoding="utf-8")

    if not success:
        raise RuntimeError(f"Release-gate runtime early warning failed: {json.dumps(report, sort_keys=True)}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Track Release Gate job runtime trend on master CI and emit an early warning when "
            "runtime stays above threshold for consecutive runs."
        )
    )
    parser.add_argument("--label", default="release-gate-runtime-early-warning")
    parser.add_argument("--repo", default="donatomaurizio99-collab/GOC")
    parser.add_argument("--branch", default="master")
    parser.add_argument("--workflow-name", default="CI")
    parser.add_argument("--release-gate-job-name", default="Release Gate (Windows)")
    parser.add_argument("--lookback-hours", type=int, default=72)
    parser.add_argument("--per-page", type=int, default=80)
    parser.add_argument("--threshold-seconds", type=int, default=540)
    parser.add_argument("--sustained-runs", type=int, default=3)
    parser.add_argument("--runs-file")
    parser.add_argument("--jobs-dir")
    parser.add_argument("--now-utc")
    parser.add_argument("--fail-on-warning", action="store_true")
    parser.add_argument("--output-file", default="artifacts/release-gate-runtime-early-warning.json")
    args = parser.parse_args(argv)

    if int(args.lookback_hours) <= 0:
        print("[release-gate-runtime-early-warning] ERROR: --lookback-hours must be > 0.", file=sys.stderr)
        return 2
    if int(args.per_page) <= 0:
        print("[release-gate-runtime-early-warning] ERROR: --per-page must be > 0.", file=sys.stderr)
        return 2
    if int(args.threshold_seconds) <= 0:
        print("[release-gate-runtime-early-warning] ERROR: --threshold-seconds must be > 0.", file=sys.stderr)
        return 2
    if int(args.sustained_runs) <= 0:
        print("[release-gate-runtime-early-warning] ERROR: --sustained-runs must be > 0.", file=sys.stderr)
        return 2

    runs_file = Path(str(args.runs_file)).expanduser() if args.runs_file else None
    jobs_dir = Path(str(args.jobs_dir)).expanduser() if args.jobs_dir else None
    output_file = Path(str(args.output_file)).expanduser()
    try:
        report = run_runtime_early_warning(
            label=str(args.label),
            repo=str(args.repo),
            branch=str(args.branch),
            workflow_name=str(args.workflow_name),
            release_gate_job_name=str(args.release_gate_job_name),
            lookback_hours=int(args.lookback_hours),
            per_page=int(args.per_page),
            threshold_seconds=int(args.threshold_seconds),
            sustained_runs=int(args.sustained_runs),
            runs_file=runs_file,
            jobs_dir=jobs_dir,
            now_utc=_resolve_now_utc(args.now_utc),
            fail_on_warning=bool(args.fail_on_warning),
            output_file=output_file,
        )
    except Exception as exc:
        print(f"[release-gate-runtime-early-warning] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
