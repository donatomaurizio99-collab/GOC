from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_CRITICAL_TEST_NAMES = [
    "test_79_recovery_hard_abort_drill_reports_success",
    "test_98_power_loss_durability_drill_reports_success",
    "test_99_disk_pressure_fault_injection_drill_reports_success",
    "test_100_sqlite_real_full_drill_reports_success",
    "test_101_wal_checkpoint_crash_drill_reports_success",
    "test_102_recovery_idempotence_drill_reports_success",
    "test_103_fsync_io_stall_drill_reports_success",
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


def _tail_lines(text: str, *, max_lines: int = 25) -> list[str]:
    lines = [line.rstrip() for line in str(text or "").splitlines() if line.strip()]
    return lines[-max(1, int(max_lines)) :]


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def run_gate(
    *,
    repeats: int,
    max_failed_iterations: int,
    target_file: Path,
    keyword_expression: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    _expect(target_file.exists(), f"Target test file does not exist: {target_file}")

    iterations: list[dict[str, Any]] = []
    started = time.perf_counter()
    for index in range(1, max(1, int(repeats)) + 1):
        cmd = [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            str(target_file),
            "-k",
            str(keyword_expression),
        ]
        run_started = time.perf_counter()
        completed = subprocess.run(
            cmd,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=max(1.0, float(timeout_seconds)),
        )
        duration_ms = int((time.perf_counter() - run_started) * 1000)
        success = int(completed.returncode) == 0
        iterations.append(
            {
                "iteration": int(index),
                "success": bool(success),
                "return_code": int(completed.returncode),
                "duration_ms": duration_ms,
                "stdout_tail": _tail_lines(completed.stdout, max_lines=25),
                "stderr_tail": _tail_lines(completed.stderr, max_lines=25),
            }
        )

    failed = [item for item in iterations if not bool(item["success"])]
    passed = [item for item in iterations if bool(item["success"])]
    success = len(failed) <= int(max_failed_iterations)

    return {
        "success": bool(success),
        "config": {
            "repeats": int(repeats),
            "max_failed_iterations": int(max_failed_iterations),
            "target_file": str(target_file),
            "keyword_expression": str(keyword_expression),
            "timeout_seconds": float(timeout_seconds),
        },
        "summary": {
            "passed_iterations": len(passed),
            "failed_iterations": len(failed),
            "failure_budget_remaining": int(max_failed_iterations) - len(failed),
        },
        "iterations": iterations,
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Flake gate for critical stability drills: rerun a targeted pytest drill subset "
            "for N iterations and fail when failed-iteration budget is exceeded."
        )
    )
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--max-failed-iterations", type=int, default=0)
    parser.add_argument("--target-file", default=str(PROJECT_ROOT / "tests" / "test_goal_ops.py"))
    parser.add_argument("--keyword-expression", default=DEFAULT_KEYWORD_EXPRESSION)
    parser.add_argument("--timeout-seconds", type=float, default=600.0)
    parser.add_argument("--output-file")
    args = parser.parse_args(argv)

    if int(args.repeats) <= 0:
        print("[critical-drill-flake-gate] ERROR: --repeats must be > 0.", file=sys.stderr)
        return 2
    if int(args.max_failed_iterations) < 0:
        print("[critical-drill-flake-gate] ERROR: --max-failed-iterations must be >= 0.", file=sys.stderr)
        return 2
    if float(args.timeout_seconds) <= 0:
        print("[critical-drill-flake-gate] ERROR: --timeout-seconds must be > 0.", file=sys.stderr)
        return 2
    if not str(args.keyword_expression).strip():
        print("[critical-drill-flake-gate] ERROR: --keyword-expression must not be empty.", file=sys.stderr)
        return 2

    target_file = Path(str(args.target_file)).expanduser()
    if not target_file.is_absolute():
        target_file = (PROJECT_ROOT / target_file).resolve()

    try:
        report = run_gate(
            repeats=int(args.repeats),
            max_failed_iterations=int(args.max_failed_iterations),
            target_file=target_file,
            keyword_expression=str(args.keyword_expression),
            timeout_seconds=float(args.timeout_seconds),
        )
    except subprocess.TimeoutExpired as exc:
        print(
            f"[critical-drill-flake-gate] ERROR: pytest iteration timed out after {exc.timeout}s.",
            file=sys.stderr,
        )
        return 1
    except Exception as exc:
        print(f"[critical-drill-flake-gate] ERROR: {exc}", file=sys.stderr)
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

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0 if bool(report.get("success")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
