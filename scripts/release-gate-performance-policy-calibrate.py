from __future__ import annotations

import argparse
import json
import math
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


def _parse_csv_list(text: str) -> list[str]:
    return [item.strip() for item in str(text).split(",") if item.strip()]


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


def _discover_timing_files(project_root: Path, explicit_files: list[str], glob_pattern: str) -> list[Path]:
    discovered: list[Path] = []
    for value in explicit_files:
        candidate = _resolve_path(project_root, value)
        if candidate.exists() and candidate.is_file():
            discovered.append(candidate)
    if glob_pattern:
        discovered.extend(
            sorted(
                path.resolve()
                for path in project_root.glob(glob_pattern)
                if path.is_file()
            )
        )
    deduped: list[Path] = []
    seen: set[str] = set()
    for path in discovered:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path)
    return deduped


def run_calibration(
    *,
    label: str,
    project_root: Path,
    step_timing_files: list[Path],
    required_label: str,
    min_samples: int,
    baseline_percentile: float,
    max_duration_percentile: float,
    headroom_percent: float,
    max_regression_percent: float,
    trend_top_n: int,
    output_file: Path,
    policy_output_file: Path,
    history_baseline_output_file: Path,
    write_updates: bool,
    allow_insufficient_samples: bool,
) -> dict[str, Any]:
    started = time.perf_counter()
    _expect(min_samples > 0, "min_samples must be > 0")
    _expect(trend_top_n > 0, "trend_top_n must be > 0")

    per_step_durations: dict[str, list[float]] = {}
    total_durations: list[float] = []
    ignored_label_mismatch: list[str] = []
    invalid_files: list[dict[str, str]] = []
    used_files: list[str] = []

    for path in step_timing_files:
        try:
            payload = _read_json_object(path)
        except Exception as exc:
            invalid_files.append({"path": str(path), "error": str(exc)})
            continue

        observed_label = str(payload.get("label") or "")
        if required_label and observed_label != required_label:
            ignored_label_mismatch.append(str(path))
            continue

        raw_steps = payload.get("steps")
        steps = raw_steps if isinstance(raw_steps, list) else []
        normalized_steps: list[dict[str, Any]] = []
        for item in steps:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            try:
                duration = float(item.get("duration_seconds") or 0.0)
            except (TypeError, ValueError):
                duration = 0.0
            if duration < 0.0:
                duration = 0.0
            normalized_steps.append({"name": name, "duration_seconds": duration})
            per_step_durations.setdefault(name, []).append(duration)

        if not normalized_steps:
            invalid_files.append({"path": str(path), "error": "No valid step durations found."})
            continue

        used_files.append(str(path))
        total_duration = sum(float(item["duration_seconds"]) for item in normalized_steps)
        total_durations.append(total_duration)

    calibrated_steps: list[dict[str, Any]] = []
    insufficient_steps: list[dict[str, Any]] = []

    for step_name in sorted(per_step_durations.keys()):
        durations = per_step_durations[step_name]
        sample_count = len(durations)
        if sample_count < min_samples:
            insufficient_steps.append({
                "name": step_name,
                "sample_count": sample_count,
                "required_min_samples": min_samples,
            })
            continue
        baseline_duration = _percentile(durations, baseline_percentile)
        max_duration = _percentile(durations, max_duration_percentile) * (1.0 + (headroom_percent / 100.0))
        if max_duration < baseline_duration:
            max_duration = baseline_duration

        calibrated_steps.append(
            {
                "name": step_name,
                "sample_count": sample_count,
                "baseline_duration_seconds": round(baseline_duration, 3),
                "max_duration_seconds": round(max_duration, 3),
            }
        )

    total_samples = len(total_durations)
    total_duration_baseline = 0.0
    total_duration_max = 0.0
    if total_samples >= min_samples:
        total_duration_baseline = _percentile(total_durations, baseline_percentile)
        total_duration_max = _percentile(total_durations, max_duration_percentile) * (1.0 + (headroom_percent / 100.0))
        if total_duration_max < total_duration_baseline:
            total_duration_max = total_duration_baseline

    policy_payload = {
        "version": "1.1.0",
        "calibrated_from_samples": int(total_samples),
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "max_total_duration_seconds": round(total_duration_max, 3),
        "max_step_regression_percent": round(float(max_regression_percent), 3),
        "trend_top_n": int(trend_top_n),
        "steps": [
            {
                "name": str(item["name"]),
                "baseline_duration_seconds": float(item["baseline_duration_seconds"]),
                "max_duration_seconds": float(item["max_duration_seconds"]),
            }
            for item in calibrated_steps
        ],
    }

    history_baseline_payload = {
        "version": "1.1.0",
        "calibrated_from_samples": int(total_samples),
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "baseline_total_duration_seconds": round(total_duration_baseline, 3),
        "max_total_duration_regression_percent": round(float(max_regression_percent), 3),
        "max_step_regression_percent": round(float(max_regression_percent), 3),
        "baseline_step_durations": {
            str(item["name"]): float(item["baseline_duration_seconds"]) for item in calibrated_steps
        },
    }

    sample_requirements_met = (
        total_samples >= min_samples and len(calibrated_steps) > 0 and len(insufficient_steps) == 0
    )
    success = bool(sample_requirements_met or allow_insufficient_samples)

    report = {
        "label": label,
        "success": bool(success),
        "paths": {
            "project_root": str(project_root),
            "output_file": str(output_file),
            "policy_output_file": str(policy_output_file),
            "history_baseline_output_file": str(history_baseline_output_file),
        },
        "config": {
            "required_label": required_label,
            "min_samples": int(min_samples),
            "baseline_percentile": float(baseline_percentile),
            "max_duration_percentile": float(max_duration_percentile),
            "headroom_percent": float(headroom_percent),
            "max_regression_percent": float(max_regression_percent),
            "trend_top_n": int(trend_top_n),
            "write_updates": bool(write_updates),
            "allow_insufficient_samples": bool(allow_insufficient_samples),
        },
        "metrics": {
            "timing_files_discovered": len(step_timing_files),
            "timing_files_used": len(used_files),
            "invalid_timing_files": len(invalid_files),
            "label_mismatch_files": len(ignored_label_mismatch),
            "total_samples": int(total_samples),
            "calibrated_steps": len(calibrated_steps),
            "insufficient_steps": len(insufficient_steps),
            "sample_requirements_met": 1 if sample_requirements_met else 0,
        },
        "timing_files_used": used_files,
        "ignored_label_mismatch_files": ignored_label_mismatch,
        "invalid_timing_files": invalid_files,
        "insufficient_steps": insufficient_steps,
        "recommended_policy": policy_payload,
        "recommended_history_baseline": history_baseline_payload,
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2), encoding="utf-8")

    if write_updates:
        policy_output_file.parent.mkdir(parents=True, exist_ok=True)
        history_baseline_output_file.parent.mkdir(parents=True, exist_ok=True)
        policy_output_file.write_text(
            json.dumps(policy_payload, ensure_ascii=True, sort_keys=True, indent=2),
            encoding="utf-8",
        )
        history_baseline_output_file.write_text(
            json.dumps(history_baseline_payload, ensure_ascii=True, sort_keys=True, indent=2),
            encoding="utf-8",
        )

    if not success:
        raise RuntimeError(
            "Release-gate performance policy calibration failed due to insufficient valid samples: "
            + json.dumps(report, sort_keys=True)
        )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Calibrate release-gate performance budget/history policies from real step-timing reports."
        )
    )
    parser.add_argument("--label", default="release-gate-performance-policy-calibration")
    parser.add_argument("--project-root")
    parser.add_argument("--step-timings-files", default="")
    parser.add_argument("--step-timings-glob", default="artifacts/release-gate-step-timings*.json")
    parser.add_argument("--required-label", default="release-gate")
    parser.add_argument("--min-samples", type=int, default=3)
    parser.add_argument("--baseline-percentile", type=float, default=50.0)
    parser.add_argument("--max-duration-percentile", type=float, default=95.0)
    parser.add_argument("--headroom-percent", type=float, default=25.0)
    parser.add_argument("--max-regression-percent", type=float, default=40.0)
    parser.add_argument("--trend-top-n", type=int, default=8)
    parser.add_argument("--output-file", default="artifacts/release-gate-performance-policy-calibration-release-gate.json")
    parser.add_argument("--policy-output-file", default="docs/release-gate-performance-budget-policy.json")
    parser.add_argument("--history-baseline-output-file", default="docs/release-gate-performance-history-baseline.json")
    parser.add_argument("--write-updates", action="store_true")
    parser.add_argument("--allow-insufficient-samples", action="store_true")
    args = parser.parse_args(argv)

    inferred_project_root = Path(__file__).resolve().parents[1]
    project_root = _resolve_path(inferred_project_root, args.project_root) if args.project_root else inferred_project_root

    explicit_files = _parse_csv_list(args.step_timings_files)
    step_timing_files = _discover_timing_files(project_root, explicit_files, str(args.step_timings_glob))
    output_file = _resolve_path(project_root, args.output_file)
    policy_output_file = _resolve_path(project_root, args.policy_output_file)
    history_baseline_output_file = _resolve_path(project_root, args.history_baseline_output_file)

    try:
        report = run_calibration(
            label=str(args.label),
            project_root=project_root,
            step_timing_files=step_timing_files,
            required_label=str(args.required_label),
            min_samples=int(args.min_samples),
            baseline_percentile=float(args.baseline_percentile),
            max_duration_percentile=float(args.max_duration_percentile),
            headroom_percent=float(args.headroom_percent),
            max_regression_percent=float(args.max_regression_percent),
            trend_top_n=int(args.trend_top_n),
            output_file=output_file,
            policy_output_file=policy_output_file,
            history_baseline_output_file=history_baseline_output_file,
            write_updates=bool(args.write_updates),
            allow_insufficient_samples=bool(args.allow_insufficient_samples),
        )
    except Exception as exc:
        print(f"[release-gate-performance-policy-calibration] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
