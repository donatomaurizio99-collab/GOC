from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


DEFAULT_REQUIRED_JOBS = [
    "Release Gate (Windows)",
    "Pytest (Python 3.11)",
    "Pytest (Python 3.12)",
    "Desktop Smoke (Windows)",
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
    payload = json.loads(path.read_text(encoding="utf-8"))
    _expect(isinstance(payload, dict), f"Expected JSON object in file: {path}")
    return payload


def _parse_required_jobs(text: str) -> list[str]:
    jobs = [item.strip() for item in str(text).split(",") if item.strip()]
    _expect(jobs, "At least one required job must be configured.")
    return jobs


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
    run_id = int(run.get("id") or 0)
    run_name = str(run.get("name") or "")
    run_conclusion = str(run.get("conclusion") or "")
    run_status = str(run.get("status") or "")
    run_head_sha = str(run.get("head_sha") or "")
    run_updated_at = str(run.get("updated_at") or "")

    job_map: dict[str, str] = {}
    for job in jobs:
        name = str(job.get("name") or "")
        if not name:
            continue
        job_map[name] = str(job.get("conclusion") or "")

    missing_jobs = [job_name for job_name in required_jobs if job_name not in job_map]
    failing_jobs = [
        {"name": job_name, "conclusion": job_map.get(job_name, "")}
        for job_name in required_jobs
        if job_name in job_map and str(job_map.get(job_name) or "") != "success"
    ]

    is_green = (
        run_status == "completed"
        and run_conclusion == "success"
        and not missing_jobs
        and not failing_jobs
    )

    return {
        "run_id": run_id,
        "run_name": run_name,
        "run_status": run_status,
        "run_conclusion": run_conclusion,
        "head_sha": run_head_sha,
        "updated_at": run_updated_at,
        "required_jobs": {name: job_map.get(name, "") for name in required_jobs},
        "missing_jobs": missing_jobs,
        "failing_jobs": failing_jobs,
        "is_green": bool(is_green),
    }


def run_burnin_check(
    *,
    label: str,
    repo: str,
    branch: str,
    workflow_name: str,
    required_jobs: list[str],
    required_consecutive: int,
    per_page: int,
    runs_file: Path | None,
    jobs_dir: Path | None,
    allow_not_met: bool,
) -> dict[str, Any]:
    _expect(required_consecutive > 0, "required_consecutive must be > 0.")
    _expect(per_page > 0, "per_page must be > 0.")
    _expect(per_page >= required_consecutive, "per_page should be >= required_consecutive.")

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

    _expect(run_items, "No workflow runs available for burn-in evaluation.")
    evaluations: list[dict[str, Any]] = []
    consecutive_green = 0
    first_non_green: dict[str, Any] | None = None

    for run in run_items:
        run_id = int(run.get("id") or 0)
        _expect(run_id > 0, f"Invalid run id in workflow runs payload: {run!r}")
        if jobs_dir is not None:
            jobs = _load_run_jobs_from_dir(jobs_dir=jobs_dir, run_id=run_id)
        else:
            jobs = _fetch_run_jobs_from_github(repo=repo, run_id=run_id)
        evaluation = _evaluate_run(run=run, required_jobs=required_jobs, jobs=jobs)
        evaluations.append(evaluation)
        if evaluation["is_green"]:
            consecutive_green += 1
            if consecutive_green >= required_consecutive:
                break
        else:
            first_non_green = evaluation
            break

    success = consecutive_green >= required_consecutive
    report = {
        "label": label,
        "success": bool(success),
        "config": {
            "repo": repo,
            "branch": branch,
            "workflow_name": workflow_name,
            "required_jobs": required_jobs,
            "required_consecutive": int(required_consecutive),
            "per_page": int(per_page),
            "allow_not_met": bool(allow_not_met),
            "fixture_runs_file": str(runs_file) if runs_file is not None else None,
            "fixture_jobs_dir": str(jobs_dir) if jobs_dir is not None else None,
        },
        "metrics": {
            "consecutive_green": int(consecutive_green),
            "required_consecutive": int(required_consecutive),
            "evaluated_runs": int(len(evaluations)),
        },
        "first_non_green": first_non_green,
        "evaluations": evaluations,
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }
    if (not success) and (not allow_not_met):
        raise RuntimeError(f"P0 burn-in consecutive-green check not met: {json.dumps(report, sort_keys=True)}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Check P0 burn-in criteria by verifying required CI jobs are green for N consecutive "
            "completed runs on a branch."
        )
    )
    parser.add_argument("--label", default="p0-burnin-consecutive-green")
    parser.add_argument("--repo", default="donatomaurizio99-collab/GOC")
    parser.add_argument("--branch", default="master")
    parser.add_argument("--workflow-name", default="CI")
    parser.add_argument("--required-jobs", default=",".join(DEFAULT_REQUIRED_JOBS))
    parser.add_argument("--required-consecutive", type=int, default=10)
    parser.add_argument("--per-page", type=int, default=50)
    parser.add_argument("--runs-file")
    parser.add_argument("--jobs-dir")
    parser.add_argument("--output-file")
    parser.add_argument("--allow-not-met", action="store_true")
    args = parser.parse_args(argv)

    if int(args.required_consecutive) <= 0:
        print("[p0-burnin-consecutive-green] ERROR: --required-consecutive must be > 0.", file=sys.stderr)
        return 2
    if int(args.per_page) <= 0:
        print("[p0-burnin-consecutive-green] ERROR: --per-page must be > 0.", file=sys.stderr)
        return 2

    runs_file = Path(str(args.runs_file)).expanduser() if args.runs_file else None
    jobs_dir = Path(str(args.jobs_dir)).expanduser() if args.jobs_dir else None
    output_file = Path(str(args.output_file)).expanduser() if args.output_file else None

    try:
        report = run_burnin_check(
            label=str(args.label),
            repo=str(args.repo),
            branch=str(args.branch),
            workflow_name=str(args.workflow_name),
            required_jobs=_parse_required_jobs(str(args.required_jobs)),
            required_consecutive=int(args.required_consecutive),
            per_page=int(args.per_page),
            runs_file=runs_file,
            jobs_dir=jobs_dir,
            allow_not_met=bool(args.allow_not_met),
        )
    except Exception as exc:
        print(f"[p0-burnin-consecutive-green] ERROR: {exc}", file=sys.stderr)
        return 1

    if output_file is not None:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2), encoding="utf-8")

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
