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


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _regression_percent(current: float, baseline: float) -> float:
    if baseline <= 0.0:
        return 0.0
    return ((current - baseline) / baseline) * 100.0


def run_check(
    *,
    label: str,
    history_baseline_file: Path,
    step_timings_file: Path,
    required_label: str,
    output_file: Path,
    allow_missing_baseline: bool,
) -> dict[str, Any]:
    started = time.perf_counter()
    timings = _read_json_object(step_timings_file)

    observed_label = str(timings.get("label") or "")
    label_matches = (not required_label) or (observed_label == required_label)
    steps_raw = timings.get("steps")
    _expect(isinstance(steps_raw, list) and steps_raw, f"Step timings file requires non-empty 'steps': {step_timings_file}")
    observed_steps: dict[str, float] = {}
    for raw in steps_raw:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        duration = _to_float(raw.get("duration_seconds"), default=0.0)
        if duration < 0.0:
            duration = 0.0
        observed_steps[name] = duration
    _expect(observed_steps, f"No valid observed step durations found: {step_timings_file}")
    observed_total_duration = sum(observed_steps.values())

    baseline_missing = False
    history_regression_violations: list[dict[str, Any]] = []
    baseline_step_missing_in_observed: list[str] = []
    top_regressions: list[dict[str, Any]] = []

    if not history_baseline_file.exists():
        baseline_missing = True
        if not allow_missing_baseline:
            history_regression_violations.append(
                {
                    "type": "missing_baseline_file",
                    "path": str(history_baseline_file),
                }
            )
        baseline_total_duration = 0.0
        max_total_regression_percent = 0.0
        max_step_regression_percent = 0.0
        baseline_step_durations: dict[str, Any] = {}
    else:
        baseline_payload = _read_json_object(history_baseline_file)
        baseline_total_duration = _to_float(baseline_payload.get("baseline_total_duration_seconds"), default=0.0)
        max_total_regression_percent = _to_float(
            baseline_payload.get("max_total_duration_regression_percent"),
            default=0.0,
        )
        max_step_regression_percent = _to_float(
            baseline_payload.get("max_step_regression_percent"),
            default=0.0,
        )
        baseline_step_durations_raw = baseline_payload.get("baseline_step_durations")
        baseline_step_durations = baseline_step_durations_raw if isinstance(baseline_step_durations_raw, dict) else {}

        _expect(baseline_total_duration > 0.0, "Baseline key baseline_total_duration_seconds must be > 0.")
        _expect(max_total_regression_percent >= 0.0, "Baseline key max_total_duration_regression_percent must be >= 0.")
        _expect(max_step_regression_percent >= 0.0, "Baseline key max_step_regression_percent must be >= 0.")

        total_regression = _regression_percent(observed_total_duration, baseline_total_duration)
        if total_regression > max_total_regression_percent:
            history_regression_violations.append(
                {
                    "type": "total_duration_regression",
                    "observed_total_duration_seconds": round(observed_total_duration, 3),
                    "baseline_total_duration_seconds": round(baseline_total_duration, 3),
                    "regression_percent": round(total_regression, 3),
                    "max_total_duration_regression_percent": round(max_total_regression_percent, 3),
                }
            )

        for step_name, baseline_duration_raw in baseline_step_durations.items():
            baseline_duration = _to_float(baseline_duration_raw, default=0.0)
            if baseline_duration <= 0.0:
                continue
            if step_name not in observed_steps:
                baseline_step_missing_in_observed.append(str(step_name))
                continue
            observed_duration = float(observed_steps[step_name])
            regression = _regression_percent(observed_duration, baseline_duration)
            trend_row = {
                "name": str(step_name),
                "observed_duration_seconds": round(observed_duration, 3),
                "baseline_duration_seconds": round(baseline_duration, 3),
                "regression_percent": round(regression, 3),
            }
            top_regressions.append(trend_row)
            if regression > max_step_regression_percent:
                history_regression_violations.append(
                    {
                        "type": "step_duration_regression",
                        "name": str(step_name),
                        "observed_duration_seconds": round(observed_duration, 3),
                        "baseline_duration_seconds": round(baseline_duration, 3),
                        "regression_percent": round(regression, 3),
                        "max_step_regression_percent": round(max_step_regression_percent, 3),
                    }
                )

    top_regressions = sorted(top_regressions, key=lambda row: float(row.get("regression_percent", 0.0)), reverse=True)[:10]
    success = label_matches and not history_regression_violations
    report = {
        "label": label,
        "success": bool(success),
        "paths": {
            "history_baseline_file": str(history_baseline_file),
            "step_timings_file": str(step_timings_file),
            "output_file": str(output_file),
        },
        "config": {
            "required_label": required_label,
            "allow_missing_baseline": bool(allow_missing_baseline),
        },
        "metrics": {
            "observed_step_count": len(observed_steps),
            "observed_total_duration_seconds": round(observed_total_duration, 3),
            "baseline_missing": 1 if baseline_missing else 0,
            "history_regression_violations": len(history_regression_violations),
            "baseline_step_missing_in_observed": len(baseline_step_missing_in_observed),
            "label_mismatch_reports": 0 if label_matches else 1,
        },
        "decision": {
            "release_blocked": not bool(success),
            "recommended_action": "block_release" if not success else "proceed",
        },
        "checks": {
            "required_label": required_label,
            "observed_label": observed_label,
            "label_matches_required": bool(label_matches),
            "history_regression_violations": history_regression_violations,
            "baseline_step_missing_in_observed": baseline_step_missing_in_observed,
            "top_regressions": top_regressions,
        },
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(
        json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2),
        encoding="utf-8",
    )

    if not success:
        raise RuntimeError(f"Release-gate performance history check failed: {json.dumps(report, sort_keys=True)}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Compare current release-gate step timings against baseline history regression budgets."
        )
    )
    parser.add_argument("--label", default="release-gate-performance-history-check")
    parser.add_argument("--project-root")
    parser.add_argument("--history-baseline-file", default="docs/release-gate-performance-history-baseline.json")
    parser.add_argument("--step-timings-file", default="artifacts/release-gate-step-timings-release-gate.json")
    parser.add_argument("--required-label", default="release-gate")
    parser.add_argument("--output-file", default="artifacts/release-gate-performance-history-release-gate.json")
    parser.add_argument("--allow-missing-baseline", action="store_true")
    args = parser.parse_args(argv)

    inferred_project_root = Path(__file__).resolve().parents[1]
    project_root = _resolve_path(inferred_project_root, args.project_root) if args.project_root else inferred_project_root
    history_baseline_file = _resolve_path(project_root, args.history_baseline_file)
    step_timings_file = _resolve_path(project_root, args.step_timings_file)
    output_file = _resolve_path(project_root, args.output_file)

    try:
        report = run_check(
            label=str(args.label),
            history_baseline_file=history_baseline_file,
            step_timings_file=step_timings_file,
            required_label=str(args.required_label),
            output_file=output_file,
            allow_missing_baseline=bool(args.allow_missing_baseline),
        )
    except Exception as exc:
        print(f"[release-gate-performance-history-check] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
