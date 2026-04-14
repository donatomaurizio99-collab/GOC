from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _load_json_file(path: Path) -> dict[str, Any]:
    _expect(path.exists(), f"JSON file not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    _expect(isinstance(data, dict), f"JSON file must contain object: {path}")
    return data


def _run_json_command(command: list[str]) -> tuple[dict[str, Any], float]:
    started = time.perf_counter()
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    duration_seconds = time.perf_counter() - started
    _expect(
        completed.returncode == 0,
        (
            f"Command failed (exit={completed.returncode}): {' '.join(command)}\n"
            f"STDERR:\n{completed.stderr}\nSTDOUT:\n{completed.stdout}"
        ),
    )
    output_lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    _expect(output_lines, f"Command produced no output: {' '.join(command)}")
    payload = json.loads(output_lines[-1])
    _expect(isinstance(payload, dict), f"Expected JSON object output: {' '.join(command)}")
    return payload, duration_seconds


def _regression_percent(current: float, baseline: float) -> float:
    if baseline <= 0:
        return 0.0
    return ((current - baseline) / baseline) * 100.0


def run_canary(
    *,
    baseline_file: Path,
    output_file: Path,
    long_soak_duration_seconds: int,
) -> dict[str, Any]:
    baseline = _load_json_file(baseline_file)
    max_duration_regression_percent = float(baseline.get("max_duration_regression_percent", 25.0))

    drill_commands: dict[str, list[str]] = {
        "release_freeze_policy": [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "release-freeze-policy.py"),
            "--workspace",
            str(PROJECT_ROOT / ".tmp" / "stability-canary-release-freeze"),
            "--label",
            "stability-canary",
            "--manifest-path",
            str(PROJECT_ROOT / ".tmp" / "stability-canary-release-freeze" / "desktop-rings.json"),
            "--ring",
            "stable",
            "--mock-slo-statuses",
            "degraded,critical,critical,critical",
            "--mock-error-budget-burn-rates",
            "0.5,1.0,2.5,2.5",
            "--non-ok-window-seconds",
            "2",
            "--poll-interval-seconds",
            "1",
            "--max-observation-seconds",
            "8",
            "--max-error-budget-burn-rate-percent",
            "2.0",
            "--seed-previous-version",
            "0.0.1",
            "--seed-incident-version",
            "0.0.2",
            "--promotion-test-version",
            "0.0.3",
        ],
        "db_corruption_quarantine": [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "db-corruption-quarantine-drill.py"),
            "--workspace",
            str(PROJECT_ROOT / ".tmp" / "stability-canary-db-corruption"),
            "--label",
            "stability-canary",
            "--corruption-bytes",
            "256",
        ],
        "db_safe_mode_watchdog": [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "db-safe-mode-watchdog-drill.py"),
            "--lock-error-injections",
            "4",
        ],
        "invariant_monitor_watchdog": [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "invariant-monitor-watchdog-drill.py"),
            "--timeout-seconds",
            "8",
        ],
        "event_consumer_recovery_chaos": [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "event-consumer-recovery-chaos-drill.py"),
            "--goal-count",
            "30",
            "--stale-processing-count",
            "10",
            "--drain-batch-size",
            "100",
            "--timeout-seconds",
            "15",
        ],
        "invariant_burst": [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "invariant-burst-drill.py"),
            "--goal-count",
            "36",
        ],
        "long_soak_budget": [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "long-soak-budget-drill.py"),
            "--duration-seconds",
            str(int(long_soak_duration_seconds)),
            "--max-p95-latency-ms",
            "300",
            "--max-p99-latency-ms",
            "500",
            "--max-max-latency-ms",
            "10000",
            "--max-http-429-rate-percent",
            "1.0",
            "--max-error-rate-percent",
            "1.0",
            "--min-requests",
            "250",
            "--drain-batch-size",
            "180",
            "--workflow-start-every-cycles",
            "0",
        ],
    }

    drill_results: dict[str, dict[str, Any]] = {}
    for drill_name, command in drill_commands.items():
        payload, duration_seconds = _run_json_command(command)
        drill_results[drill_name] = {
            "duration_seconds": round(float(duration_seconds), 3),
            "payload": payload,
        }

    regressions: list[dict[str, Any]] = []
    baseline_drills = baseline.get("drills") or {}
    for drill_name, result in drill_results.items():
        baseline_config = baseline_drills.get(drill_name) or {}
        baseline_duration = float(baseline_config.get("baseline_duration_seconds", 0.0))
        if baseline_duration > 0:
            regression_percent = _regression_percent(result["duration_seconds"], baseline_duration)
            if regression_percent > max_duration_regression_percent:
                regressions.append(
                    {
                        "type": "duration_regression",
                        "drill": drill_name,
                        "current_duration_seconds": result["duration_seconds"],
                        "baseline_duration_seconds": baseline_duration,
                        "regression_percent": round(regression_percent, 3),
                        "max_allowed_regression_percent": max_duration_regression_percent,
                    }
                )

    long_soak_payload = drill_results["long_soak_budget"]["payload"]
    max_429_rate = float((baseline_drills.get("long_soak_budget") or {}).get("max_http_429_rate_percent", 1.0))
    max_error_rate = float((baseline_drills.get("long_soak_budget") or {}).get("max_error_rate_percent", 1.0))
    observed_429_rate = float(long_soak_payload["observed_rates_percent"]["http_429_rate"])
    observed_error_rate = float(long_soak_payload["observed_rates_percent"]["error_rate"])
    if observed_429_rate > max_429_rate:
        regressions.append(
            {
                "type": "http_429_rate_regression",
                "drill": "long_soak_budget",
                "observed": observed_429_rate,
                "max_allowed": max_429_rate,
            }
        )
    if observed_error_rate > max_error_rate:
        regressions.append(
            {
                "type": "error_rate_regression",
                "drill": "long_soak_budget",
                "observed": observed_error_rate,
                "max_allowed": max_error_rate,
            }
        )

    report = {
        "success": len(regressions) == 0,
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "baseline_file": str(baseline_file),
        "output_file": str(output_file),
        "drills": drill_results,
        "regressions": regressions,
    }
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(
        json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    if regressions:
        raise RuntimeError(f"Stability canary regressions detected: {json.dumps(regressions, sort_keys=True)}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Nightly stability canary: run core stability drills and compare duration/rate trends against baseline."
        )
    )
    parser.add_argument(
        "--baseline-file",
        default=str(PROJECT_ROOT / "docs" / "stability-canary-baseline.json"),
    )
    parser.add_argument(
        "--output-file",
        default=str(PROJECT_ROOT / "artifacts" / "stability-canary-report.json"),
    )
    parser.add_argument("--long-soak-duration-seconds", type=int, default=120)
    args = parser.parse_args(argv)

    if int(args.long_soak_duration_seconds) <= 0:
        print("[stability-canary] ERROR: --long-soak-duration-seconds must be > 0.", file=sys.stderr)
        return 2

    try:
        report = run_canary(
            baseline_file=Path(args.baseline_file).expanduser(),
            output_file=Path(args.output_file).expanduser(),
            long_soak_duration_seconds=int(args.long_soak_duration_seconds),
        )
    except Exception as exc:
        print(f"[stability-canary] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
