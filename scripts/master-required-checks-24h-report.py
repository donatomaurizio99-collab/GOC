from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


DEFAULT_REQUIRED_JOBS = [
    "Release Gate (Windows)",
    "Security CI Lane",
    "Pytest (Python 3.11)",
    "Pytest (Python 3.12)",
    "Desktop Smoke (Windows)",
]

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


def _parse_required_jobs(text: str) -> list[str]:
    jobs = [item.strip() for item in str(text).split(",") if item.strip()]
    _expect(jobs, "At least one required job must be configured.")
    return jobs


def _dedupe_ordered_strings(values: list[str]) -> tuple[list[str], list[str]]:
    deduped: list[str] = []
    duplicates: list[str] = []
    seen: set[str] = set()
    for raw in values:
        token = str(raw).strip()
        if not token:
            continue
        if token in seen:
            duplicates.append(token)
            continue
        seen.add(token)
        deduped.append(token)
    return deduped, duplicates


def _collapse_job_conclusion(conclusions: list[str]) -> str:
    normalized = [str(item).strip().lower() for item in conclusions if str(item).strip()]
    if not normalized:
        return ""
    return max(normalized, key=lambda item: CONCLUSION_SEVERITY.get(item, 6))


def _build_job_conclusion_map(jobs: list[dict[str, Any]]) -> tuple[dict[str, str], list[str], int, int]:
    conclusions_by_name: dict[str, list[str]] = {}
    named_job_entries_total = 0
    for job in jobs:
        name = str(job.get("name") or "").strip()
        if not name:
            continue
        named_job_entries_total += 1
        conclusions_by_name.setdefault(name, []).append(str(job.get("conclusion") or ""))

    job_map: dict[str, str] = {}
    duplicate_job_names: list[str] = []
    for name, conclusions in conclusions_by_name.items():
        if len(conclusions) > 1:
            duplicate_job_names.append(name)
        job_map[name] = _collapse_job_conclusion(conclusions)

    duplicate_job_entries_dropped = max(0, named_job_entries_total - len(job_map))
    return job_map, sorted(duplicate_job_names), named_job_entries_total, duplicate_job_entries_dropped


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
    required_jobs: list[str],
    jobs: list[dict[str, Any]],
) -> dict[str, Any]:
    run_status = str(run.get("status") or "")
    run_conclusion = str(run.get("conclusion") or "")
    job_map, duplicate_job_names, named_job_entries_total, duplicate_job_entries_dropped = _build_job_conclusion_map(
        jobs
    )

    missing_jobs = [job_name for job_name in required_jobs if job_name not in job_map]
    failing_jobs = [
        {"name": job_name, "conclusion": job_map.get(job_name, "")}
        for job_name in required_jobs
        if job_name in job_map and str(job_map.get(job_name) or "") != "success"
    ]
    is_green = run_status == "completed" and run_conclusion == "success" and not missing_jobs and not failing_jobs

    return {
        "run_id": int(run.get("id") or 0),
        "run_name": str(run.get("name") or ""),
        "run_status": run_status,
        "run_conclusion": run_conclusion,
        "head_sha": str(run.get("head_sha") or ""),
        "updated_at": str(run.get("updated_at") or ""),
        "required_jobs": {name: job_map.get(name, "") for name in required_jobs},
        "job_dedupe": {
            "named_job_entries_total": int(named_job_entries_total),
            "unique_job_names_total": int(len(job_map)),
            "duplicate_job_name_total": int(len(duplicate_job_names)),
            "duplicate_job_entries_dropped": int(duplicate_job_entries_dropped),
            "duplicate_job_names": duplicate_job_names,
        },
        "missing_jobs": missing_jobs,
        "failing_jobs": failing_jobs,
        "is_green": bool(is_green),
    }


def run_required_checks_report(
    *,
    label: str,
    repo: str,
    branch: str,
    workflow_name: str,
    required_jobs: list[str],
    lookback_hours: int,
    per_page: int,
    max_non_green_runs: int,
    runs_file: Path | None,
    jobs_dir: Path | None,
    now_utc: datetime,
    allow_non_green: bool,
    output_file: Path,
) -> dict[str, Any]:
    _expect(lookback_hours > 0, "lookback_hours must be > 0.")
    _expect(per_page > 0, "per_page must be > 0.")
    _expect(max_non_green_runs >= 0, "max_non_green_runs must be >= 0.")
    resolved_required_jobs, duplicate_required_jobs = _dedupe_ordered_strings(required_jobs)
    _expect(resolved_required_jobs, "At least one required job must be configured.")

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

    _expect(run_items, "No workflow runs available for required-checks evaluation.")

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

        evaluation = _evaluate_run(run=run, required_jobs=resolved_required_jobs, jobs=jobs)
        evaluation["updated_at_parsed_utc"] = _format_utc(updated_at)
        evaluation["within_window"] = True
        evaluations.append(evaluation)

    non_green_runs = [item for item in evaluations if not bool(item.get("is_green"))]
    green_runs_total = len(evaluations) - len(non_green_runs)
    duplicate_job_name_observations = sum(
        int(((evaluation.get("job_dedupe") or {}).get("duplicate_job_name_total") or 0))
        for evaluation in evaluations
    )
    duplicate_job_entries_dropped_total = sum(
        int(((evaluation.get("job_dedupe") or {}).get("duplicate_job_entries_dropped") or 0))
        for evaluation in evaluations
    )
    unique_non_green_required_jobs = sorted(
        {
            str(item.get("name") or "").strip()
            for evaluation in non_green_runs
            for item in (evaluation.get("failing_jobs") or [])
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        }
    )

    criteria = [
        {
            "name": "runs_within_window_recorded",
            "passed": bool(len(evaluations) > 0),
            "details": f"evaluated_runs_total={len(evaluations)}, lookback_hours={lookback_hours}",
        },
        {
            "name": "non_green_runs_within_budget",
            "passed": bool(
                int(len(non_green_runs)) <= int(max_non_green_runs) or bool(allow_non_green)
            ),
            "details": (
                f"non_green_runs_total={len(non_green_runs)}, max_non_green_runs={max_non_green_runs}, "
                f"allow_non_green={allow_non_green}"
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
            "required_jobs": resolved_required_jobs,
            "required_jobs_declared_total": int(len(required_jobs)),
            "required_jobs_unique_total": int(len(resolved_required_jobs)),
            "required_jobs_duplicates_removed": duplicate_required_jobs,
            "lookback_hours": int(lookback_hours),
            "per_page": int(per_page),
            "max_non_green_runs": int(max_non_green_runs),
            "allow_non_green": bool(allow_non_green),
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
            "evaluated_runs_total": int(len(evaluations)),
            "green_runs_total": int(green_runs_total),
            "non_green_runs_total": int(len(non_green_runs)),
            "required_jobs_duplicates_removed_total": int(len(duplicate_required_jobs)),
            "duplicate_job_name_observations": int(duplicate_job_name_observations),
            "duplicate_job_entries_dropped_total": int(duplicate_job_entries_dropped_total),
            "unique_non_green_required_jobs_total": int(len(unique_non_green_required_jobs)),
            "criteria_failed": int(len(failed_criteria)),
        },
        "criteria": criteria,
        "failed_criteria": failed_criteria,
        "non_green_runs": non_green_runs,
        "evaluations": evaluations,
        "non_green_unique_required_jobs": unique_non_green_required_jobs,
        "decision": {
            "release_blocked": not bool(success),
            "recommended_action": "required_checks_window_green" if success else "required_checks_window_non_green",
        },
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2), encoding="utf-8")

    if not success:
        raise RuntimeError(f"Master required-checks 24h report failed: {json.dumps(report, sort_keys=True)}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect CI runs on master for the last N hours and enforce that required checks stay green "
            "without duplicate-name ambiguity."
        )
    )
    parser.add_argument("--label", default="master-required-checks-24h-report")
    parser.add_argument("--repo", default="donatomaurizio99-collab/GOC")
    parser.add_argument("--branch", default="master")
    parser.add_argument("--workflow-name", default="CI")
    parser.add_argument("--required-jobs", default=",".join(DEFAULT_REQUIRED_JOBS))
    parser.add_argument("--lookback-hours", type=int, default=24)
    parser.add_argument("--per-page", type=int, default=100)
    parser.add_argument("--max-non-green-runs", type=int, default=0)
    parser.add_argument("--runs-file")
    parser.add_argument("--jobs-dir")
    parser.add_argument("--now-utc")
    parser.add_argument("--allow-non-green", action="store_true")
    parser.add_argument("--output-file", default="artifacts/master-required-checks-24h-report.json")
    args = parser.parse_args(argv)

    if int(args.lookback_hours) <= 0:
        print("[master-required-checks-24h-report] ERROR: --lookback-hours must be > 0.", file=sys.stderr)
        return 2
    if int(args.per_page) <= 0:
        print("[master-required-checks-24h-report] ERROR: --per-page must be > 0.", file=sys.stderr)
        return 2
    if int(args.max_non_green_runs) < 0:
        print("[master-required-checks-24h-report] ERROR: --max-non-green-runs must be >= 0.", file=sys.stderr)
        return 2

    runs_file = Path(str(args.runs_file)).expanduser() if args.runs_file else None
    jobs_dir = Path(str(args.jobs_dir)).expanduser() if args.jobs_dir else None
    output_file = Path(str(args.output_file)).expanduser()

    try:
        report = run_required_checks_report(
            label=str(args.label),
            repo=str(args.repo),
            branch=str(args.branch),
            workflow_name=str(args.workflow_name),
            required_jobs=_parse_required_jobs(str(args.required_jobs)),
            lookback_hours=int(args.lookback_hours),
            per_page=int(args.per_page),
            max_non_green_runs=int(args.max_non_green_runs),
            runs_file=runs_file,
            jobs_dir=jobs_dir,
            now_utc=_resolve_now_utc(args.now_utc),
            allow_non_green=bool(args.allow_non_green),
            output_file=output_file,
        )
    except Exception as exc:
        print(f"[master-required-checks-24h-report] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
