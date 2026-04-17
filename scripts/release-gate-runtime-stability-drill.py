from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_CRITICAL_TEST_NAMES = [
    "test_105_storage_corruption_hardening_drill_reports_success",
    "test_106_backup_restore_stress_drill_reports_success",
    "test_107_snapshot_restore_crash_consistency_drill_reports_success",
    "test_108_multi_db_atomic_switch_drill_reports_success",
    "test_144_dashboard_template_contains_runtime_rail_contract",
    "test_145_safe_mode_ux_degradation_check_reports_success",
    "test_147_a11y_test_harness_check_reports_success",
    "test_149_dashboard_template_exposes_keyboard_and_screen_reader_baseline",
]
DEFAULT_KEYWORD_EXPRESSION = " or ".join(DEFAULT_CRITICAL_TEST_NAMES)


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _tail_lines(text: str, *, max_lines: int = 30) -> list[str]:
    lines = [line.rstrip() for line in str(text or "").splitlines() if line.strip()]
    return lines[-max(1, int(max_lines)) :]


def _load_last_json_line(stdout_text: str) -> dict[str, Any]:
    for raw in reversed(str(stdout_text or "").splitlines()):
        line = raw.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise RuntimeError("No JSON payload found in critical-drill-flake-gate output.")


def run_stability_drill(
    *,
    label: str,
    samples: int,
    repeats_per_sample: int,
    target_file: Path,
    keyword_expression: str,
    timeout_seconds: float,
    max_mean_duration_ms: int,
    max_stddev_ms: int,
    max_iteration_duration_ms: int,
) -> dict[str, Any]:
    _expect(target_file.exists(), f"Target test file does not exist: {target_file}")

    started = time.perf_counter()
    sample_reports: list[dict[str, Any]] = []
    iteration_durations: list[int] = []
    failed_iterations = 0

    for sample_index in range(1, max(1, int(samples)) + 1):
        cmd = [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "critical-drill-flake-gate.py"),
            "--repeats",
            str(max(1, int(repeats_per_sample))),
            "--max-failed-iterations",
            "0",
            "--target-file",
            str(target_file),
            "--keyword-expression",
            str(keyword_expression),
            "--timeout-seconds",
            str(float(timeout_seconds)),
        ]
        run_started = time.perf_counter()
        completed = subprocess.run(
            cmd,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=max(1.0, float(timeout_seconds)),
        )
        run_duration_ms = int((time.perf_counter() - run_started) * 1000)
        payload: dict[str, Any] = {}
        parse_error = ""
        try:
            payload = _load_last_json_line(completed.stdout)
        except Exception as exc:
            parse_error = str(exc)

        sample_success = bool(completed.returncode == 0 and payload and payload.get("success") is True)
        sample_iterations = payload.get("iterations") if isinstance(payload, dict) else []
        if not isinstance(sample_iterations, list):
            sample_iterations = []

        sample_failed = 0
        sample_durations: list[int] = []
        for item in sample_iterations:
            if not isinstance(item, dict):
                continue
            duration_ms = int(item.get("duration_ms") or 0)
            sample_durations.append(duration_ms)
            iteration_durations.append(duration_ms)
            if not bool(item.get("success")):
                sample_failed += 1
        failed_iterations += sample_failed

        sample_reports.append(
            {
                "sample": int(sample_index),
                "success": bool(sample_success),
                "return_code": int(completed.returncode),
                "duration_ms": int(run_duration_ms),
                "parse_error": parse_error,
                "iterations": sample_iterations,
                "sample_iteration_durations_ms": sample_durations,
                "sample_failed_iterations": int(sample_failed),
                "stdout_tail": _tail_lines(completed.stdout, max_lines=25),
                "stderr_tail": _tail_lines(completed.stderr, max_lines=25),
                "summary": payload.get("summary") if isinstance(payload, dict) else {},
            }
        )

    _expect(iteration_durations, "No iteration durations collected from runtime stability drill.")
    mean_duration = float(statistics.mean(iteration_durations))
    stddev_duration = float(statistics.pstdev(iteration_durations)) if len(iteration_durations) > 1 else 0.0
    max_duration = int(max(iteration_durations))

    failures: list[str] = []
    if any(not bool(report["success"]) for report in sample_reports):
        failures.append("At least one sampled critical flake gate run failed.")
    if int(failed_iterations) > 0:
        failures.append(f"Observed failed critical iterations: {failed_iterations}.")
    if mean_duration > float(max_mean_duration_ms):
        failures.append(
            f"Mean duration {mean_duration:.1f}ms exceeded budget {int(max_mean_duration_ms)}ms."
        )
    if stddev_duration > float(max_stddev_ms):
        failures.append(
            f"Duration stddev {stddev_duration:.1f}ms exceeded budget {int(max_stddev_ms)}ms."
        )
    if max_duration > int(max_iteration_duration_ms):
        failures.append(
            f"Max iteration duration {max_duration}ms exceeded budget {int(max_iteration_duration_ms)}ms."
        )

    success = not failures
    report = {
        "label": label,
        "success": bool(success),
        "config": {
            "samples": int(samples),
            "repeats_per_sample": int(repeats_per_sample),
            "target_file": str(target_file),
            "keyword_expression": str(keyword_expression),
            "timeout_seconds": float(timeout_seconds),
            "max_mean_duration_ms": int(max_mean_duration_ms),
            "max_stddev_ms": int(max_stddev_ms),
            "max_iteration_duration_ms": int(max_iteration_duration_ms),
        },
        "metrics": {
            "iterations_total": int(len(iteration_durations)),
            "failed_iterations": int(failed_iterations),
            "mean_duration_ms": int(round(mean_duration)),
            "stddev_duration_ms": int(round(stddev_duration)),
            "max_duration_ms": int(max_duration),
        },
        "samples": sample_reports,
        "failures": failures,
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run runtime-stability sampling of critical drills and enforce duration budgets "
            "for release-gate predictability."
        )
    )
    parser.add_argument("--label", default="release-gate-runtime-stability-drill")
    parser.add_argument("--samples", type=int, default=2)
    parser.add_argument("--repeats-per-sample", type=int, default=1)
    parser.add_argument("--target-file", default=str(PROJECT_ROOT / "tests" / "test_goal_ops.py"))
    parser.add_argument("--keyword-expression", default=DEFAULT_KEYWORD_EXPRESSION)
    parser.add_argument("--timeout-seconds", type=float, default=900.0)
    parser.add_argument("--max-mean-duration-ms", type=int, default=120000)
    parser.add_argument("--max-stddev-ms", type=int, default=60000)
    parser.add_argument("--max-iteration-duration-ms", type=int, default=180000)
    parser.add_argument("--output-file")
    args = parser.parse_args(argv)

    if int(args.samples) <= 0:
        print("[release-gate-runtime-stability-drill] ERROR: --samples must be > 0.", file=sys.stderr)
        return 2
    if int(args.repeats_per_sample) <= 0:
        print("[release-gate-runtime-stability-drill] ERROR: --repeats-per-sample must be > 0.", file=sys.stderr)
        return 2
    if float(args.timeout_seconds) <= 0:
        print("[release-gate-runtime-stability-drill] ERROR: --timeout-seconds must be > 0.", file=sys.stderr)
        return 2
    if int(args.max_mean_duration_ms) <= 0:
        print("[release-gate-runtime-stability-drill] ERROR: --max-mean-duration-ms must be > 0.", file=sys.stderr)
        return 2
    if int(args.max_stddev_ms) < 0:
        print("[release-gate-runtime-stability-drill] ERROR: --max-stddev-ms must be >= 0.", file=sys.stderr)
        return 2
    if int(args.max_iteration_duration_ms) <= 0:
        print("[release-gate-runtime-stability-drill] ERROR: --max-iteration-duration-ms must be > 0.", file=sys.stderr)
        return 2

    try:
        report = run_stability_drill(
            label=str(args.label),
            samples=int(args.samples),
            repeats_per_sample=int(args.repeats_per_sample),
            target_file=Path(str(args.target_file)).expanduser(),
            keyword_expression=str(args.keyword_expression),
            timeout_seconds=float(args.timeout_seconds),
            max_mean_duration_ms=int(args.max_mean_duration_ms),
            max_stddev_ms=int(args.max_stddev_ms),
            max_iteration_duration_ms=int(args.max_iteration_duration_ms),
        )
    except Exception as exc:
        print(f"[release-gate-runtime-stability-drill] ERROR: {exc}", file=sys.stderr)
        return 1

    if args.output_file:
        output_file = Path(str(args.output_file)).expanduser()
        if not output_file.is_absolute():
            output_file = (PROJECT_ROOT / output_file).resolve()
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(
            json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2),
            encoding="utf-8",
        )

    if not bool(report.get("success")):
        print(
            "[release-gate-runtime-stability-drill] ERROR: "
            + f"Release-gate runtime stability drill failed: {json.dumps(report, sort_keys=True)}",
            file=sys.stderr,
        )
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
