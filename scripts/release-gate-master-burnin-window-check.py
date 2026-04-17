from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _resolve_path(project_root: Path, value: str) -> Path:
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


def _read_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    _expect(isinstance(payload, dict), f"Expected JSON object in {path}")
    return payload


def _normalize_failing_jobs(payload: dict[str, Any]) -> list[dict[str, str]]:
    raw_jobs = payload.get("failing_jobs")
    jobs = raw_jobs if isinstance(raw_jobs, list) else []
    normalized: list[dict[str, str]] = []
    for item in jobs:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        conclusion = str(item.get("conclusion") or "").strip()
        if not name:
            continue
        normalized.append({"name": name, "conclusion": conclusion})
    return normalized


def run_check(
    *,
    label: str,
    burnin_report_file: Path,
    min_consecutive: int,
    target_consecutive: int,
    max_failing_jobs: int,
    output_file: Path,
    allow_target_not_met: bool,
    allow_flaky_jobs: bool,
) -> dict[str, Any]:
    started = time.perf_counter()
    _expect(min_consecutive > 0, "min_consecutive must be > 0")
    _expect(target_consecutive >= min_consecutive, "target_consecutive must be >= min_consecutive")
    _expect(max_failing_jobs >= 0, "max_failing_jobs must be >= 0")

    burnin_payload = _read_json_object(burnin_report_file)
    burnin_metrics = burnin_payload.get("metrics") if isinstance(burnin_payload.get("metrics"), dict) else {}
    consecutive_green = int(burnin_metrics.get("consecutive_green") or 0)
    evaluated_runs = int(burnin_metrics.get("evaluated_runs") or 0)

    first_non_green = burnin_payload.get("first_non_green")
    first_non_green_payload = first_non_green if isinstance(first_non_green, dict) else {}
    failing_jobs = _normalize_failing_jobs(first_non_green_payload)
    unique_failing_job_names = sorted({item["name"] for item in failing_jobs})

    minimum_met = consecutive_green >= min_consecutive
    target_met = consecutive_green >= target_consecutive
    failing_jobs_within_budget = len(unique_failing_job_names) <= max_failing_jobs

    criteria: list[dict[str, Any]] = [
        {
            "name": "burnin_minimum_consecutive_met",
            "passed": bool(minimum_met),
            "details": f"consecutive_green={consecutive_green}, min={min_consecutive}",
        },
        {
            "name": "burnin_target_consecutive_met",
            "passed": bool(target_met or allow_target_not_met),
            "details": (
                f"consecutive_green={consecutive_green}, target={target_consecutive}, "
                f"allow_target_not_met={allow_target_not_met}"
            ),
        },
        {
            "name": "flake_cleanup_budget",
            "passed": bool(failing_jobs_within_budget or allow_flaky_jobs),
            "details": (
                f"unique_failing_jobs={len(unique_failing_job_names)}, "
                f"max_failing_jobs={max_failing_jobs}, allow_flaky_jobs={allow_flaky_jobs}"
            ),
        },
        {
            "name": "evaluated_runs_recorded",
            "passed": bool(evaluated_runs > 0),
            "details": f"evaluated_runs={evaluated_runs}",
        },
    ]

    cleanup_actions = [
        {
            "job": job_name,
            "action": "Inspect latest non-green run logs for this required CI job and eliminate flakes.",
        }
        for job_name in unique_failing_job_names
    ]

    failed_criteria = [item for item in criteria if not bool(item.get("passed"))]
    success = len(failed_criteria) == 0
    report = {
        "label": label,
        "success": bool(success),
        "paths": {
            "burnin_report_file": str(burnin_report_file),
            "output_file": str(output_file),
        },
        "config": {
            "min_consecutive": int(min_consecutive),
            "target_consecutive": int(target_consecutive),
            "max_failing_jobs": int(max_failing_jobs),
            "allow_target_not_met": bool(allow_target_not_met),
            "allow_flaky_jobs": bool(allow_flaky_jobs),
        },
        "metrics": {
            "consecutive_green": int(consecutive_green),
            "evaluated_runs": int(evaluated_runs),
            "target_consecutive_met": 1 if target_met else 0,
            "minimum_consecutive_met": 1 if minimum_met else 0,
            "first_non_green_failing_jobs": len(failing_jobs),
            "unique_failing_jobs": len(unique_failing_job_names),
            "criteria_failed": len(failed_criteria),
        },
        "criteria": criteria,
        "failed_criteria": failed_criteria,
        "first_non_green": first_non_green_payload if first_non_green_payload else None,
        "failing_jobs": failing_jobs,
        "flake_cleanup_actions": cleanup_actions,
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2), encoding="utf-8")

    if not success:
        raise RuntimeError(f"Release-gate master burn-in window check failed: {json.dumps(report, sort_keys=True)}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate release-gate burn-in history for the master branch and emit a flake-cleanup aware window report."
        )
    )
    parser.add_argument("--label", default="release-gate-master-burnin-window-check")
    parser.add_argument("--project-root")
    parser.add_argument("--burnin-report-file", default="artifacts/p0-burnin-consecutive-green-release-gate.json")
    parser.add_argument("--min-consecutive", type=int, default=3)
    parser.add_argument("--target-consecutive", type=int, default=5)
    parser.add_argument("--max-failing-jobs", type=int, default=0)
    parser.add_argument("--output-file", default="artifacts/release-gate-master-burnin-window-release-gate.json")
    parser.add_argument("--allow-target-not-met", action="store_true")
    parser.add_argument("--allow-flaky-jobs", action="store_true")
    args = parser.parse_args(argv)

    inferred_project_root = Path(__file__).resolve().parents[1]
    project_root = _resolve_path(inferred_project_root, args.project_root) if args.project_root else inferred_project_root
    burnin_report_file = _resolve_path(project_root, args.burnin_report_file)
    output_file = _resolve_path(project_root, args.output_file)

    try:
        report = run_check(
            label=str(args.label),
            burnin_report_file=burnin_report_file,
            min_consecutive=int(args.min_consecutive),
            target_consecutive=int(args.target_consecutive),
            max_failing_jobs=int(args.max_failing_jobs),
            output_file=output_file,
            allow_target_not_met=bool(args.allow_target_not_met),
            allow_flaky_jobs=bool(args.allow_flaky_jobs),
        )
    except Exception as exc:
        print(f"[release-gate-master-burnin-window-check] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
