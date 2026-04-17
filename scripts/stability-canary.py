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


def _write_json_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True, sort_keys=True), encoding="utf-8")


def _prepare_p0_burnin_fixtures(*, runs_file: Path, jobs_dir: Path, required_jobs: list[str]) -> None:
    runs_payload = {
        "workflow_runs": [
            {
                "id": 9203,
                "name": "CI",
                "status": "completed",
                "conclusion": "success",
                "head_sha": "sha-9203",
                "updated_at": "2026-04-17T00:00:03Z",
            },
            {
                "id": 9202,
                "name": "CI",
                "status": "completed",
                "conclusion": "success",
                "head_sha": "sha-9202",
                "updated_at": "2026-04-17T00:00:02Z",
            },
            {
                "id": 9201,
                "name": "CI",
                "status": "completed",
                "conclusion": "success",
                "head_sha": "sha-9201",
                "updated_at": "2026-04-17T00:00:01Z",
            },
            {
                "id": 9200,
                "name": "CI",
                "status": "completed",
                "conclusion": "failure",
                "head_sha": "sha-9200",
                "updated_at": "2026-04-17T00:00:00Z",
            },
        ]
    }
    _write_json_file(runs_file, runs_payload)

    for run_id in (9203, 9202, 9201, 9200):
        conclusion = "success" if run_id != 9200 else "failure"
        jobs_payload = {
            "jobs": (
                [{"name": job_name, "conclusion": conclusion} for job_name in required_jobs]
                + [{"name": "Auxiliary Check", "conclusion": "success"}]
            )
        }
        _write_json_file(jobs_dir / f"{run_id}.json", jobs_payload)


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
    safe_mode_report_file = (
        PROJECT_ROOT / ".tmp" / "stability-canary-safe-mode-ux" / "safe-mode-ux-degradation-report.json"
    )
    a11y_report_file = PROJECT_ROOT / ".tmp" / "stability-canary-a11y" / "a11y-test-harness-report.json"
    p0_schema_artifacts_dir = PROJECT_ROOT / ".tmp" / "stability-canary-p0-schema"
    p0_schema_report_file = p0_schema_artifacts_dir / "p0-report-schema-contract-report.json"
    p0_runbook_contract_report_file = (
        PROJECT_ROOT / ".tmp" / "stability-canary-p0-runbook-contract" / "p0-runbook-contract-check-report.json"
    )
    p0_burnin_artifacts_dir = PROJECT_ROOT / ".tmp" / "stability-canary-p0-burnin"
    p0_burnin_report_file = p0_burnin_artifacts_dir / "p0-burnin-consecutive-green-report.json"
    p0_burnin_fixtures_dir = p0_burnin_artifacts_dir / "fixtures"
    p0_burnin_runs_file = p0_burnin_fixtures_dir / "runs.json"
    p0_burnin_jobs_dir = p0_burnin_fixtures_dir / "jobs"
    p0_closure_report_file = PROJECT_ROOT / ".tmp" / "stability-canary-p0-closure" / "p0-closure-report.json"
    p0_evidence_artifacts_dir = PROJECT_ROOT / ".tmp" / "stability-canary-p0-evidence"
    p0_evidence_bundle_file = p0_evidence_artifacts_dir / "p0-release-evidence-bundle-report.json"
    p0_evidence_bundle_dir = p0_evidence_artifacts_dir / "p0-release-evidence-files"
    canary_determinism_workspace = PROJECT_ROOT / ".tmp" / "stability-canary-determinism"
    canary_determinism_report_file = canary_determinism_workspace / "canary-determinism-flake-report.json"
    p0_burnin_required_jobs = [
        "Release Gate (Windows)",
        "Pytest (Python 3.11)",
        "Pytest (Python 3.12)",
        "Desktop Smoke (Windows)",
    ]
    _prepare_p0_burnin_fixtures(
        runs_file=p0_burnin_runs_file,
        jobs_dir=p0_burnin_jobs_dir,
        required_jobs=p0_burnin_required_jobs,
    )
    p0_schema_required_files = ",".join(
        [
            str(safe_mode_report_file),
            str(a11y_report_file),
        ]
    )
    p0_evidence_required_files = ",".join(
        [
            str(safe_mode_report_file),
            str(a11y_report_file),
            str(p0_burnin_report_file),
            str(p0_schema_report_file),
            str(p0_runbook_contract_report_file),
        ]
    )
    p0_closure_required_evidence_reports = ",".join(
        [
            str(safe_mode_report_file),
            str(a11y_report_file),
            str(p0_burnin_report_file),
            str(p0_schema_report_file),
            str(p0_runbook_contract_report_file),
        ]
    )

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
        "power_loss_durability": [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "power-loss-durability-drill.py"),
            "--workspace",
            str(PROJECT_ROOT / ".tmp" / "stability-canary-power-loss"),
            "--label",
            "stability-canary",
            "--transaction-rows",
            "180",
            "--payload-bytes",
            "192",
            "--startup-timeout-seconds",
            "15",
        ],
        "upgrade_downgrade_compatibility": [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "upgrade-downgrade-compatibility-drill.py"),
            "--workspace",
            str(PROJECT_ROOT / ".tmp" / "stability-canary-upgrade-downgrade"),
            "--label",
            "stability-canary",
            "--n-minus-1-runs",
            "300",
            "--payload-bytes",
            "256",
            "--max-upgrade-ms",
            "15000",
            "--max-rollback-restore-ms",
            "15000",
            "--max-reupgrade-ms",
            "15000",
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
        "safe_mode_ux_degradation": [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "safe-mode-ux-degradation-check.py"),
            "--label",
            "stability-canary",
            "--output-file",
            str(safe_mode_report_file),
        ],
        "a11y_test_harness": [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "a11y-test-harness-check.py"),
            "--label",
            "stability-canary",
            "--output-file",
            str(a11y_report_file),
        ],
        "canary_determinism_flake_intelligence": [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "canary-determinism-flake-check.py"),
            "--label",
            "stability-canary",
            "--project-root",
            str(PROJECT_ROOT),
            "--policy-file",
            str(PROJECT_ROOT / "docs" / "canary-determinism-policy.json"),
            "--quarantine-file",
            str(PROJECT_ROOT / "docs" / "canary-determinism-quarantine.json"),
            "--runbook-file",
            str(PROJECT_ROOT / "docs" / "production-runbook.md"),
            "--workspace",
            str(canary_determinism_workspace),
            "--required-label",
            "stability-canary",
            "--probe-repeats",
            "2",
            "--output-file",
            str(canary_determinism_report_file),
        ],
        "p0_report_schema_contract": [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "p0-report-schema-contract-check.py"),
            "--label",
            "stability-canary",
            "--project-root",
            str(PROJECT_ROOT),
            "--artifacts-dir",
            str(p0_schema_artifacts_dir),
            "--include-glob",
            "*-report.json",
            "--required-files",
            p0_schema_required_files,
            "--required-label",
            "stability-canary",
            "--required-top-level-keys",
            "label,success",
            "--output-file",
            str(p0_schema_report_file),
        ],
        "p0_runbook_contract": [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "p0-runbook-contract-check.py"),
            "--label",
            "stability-canary",
            "--project-root",
            str(PROJECT_ROOT),
            "--output-file",
            str(p0_runbook_contract_report_file),
        ],
        "p0_burnin_consecutive_green": [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "p0-burnin-consecutive-green.py"),
            "--label",
            "stability-canary",
            "--repo",
            "donatomaurizio99-collab/GOC",
            "--branch",
            "master",
            "--workflow-name",
            "CI",
            "--required-jobs",
            ",".join(p0_burnin_required_jobs),
            "--required-consecutive",
            "3",
            "--per-page",
            "10",
            "--runs-file",
            str(p0_burnin_runs_file),
            "--jobs-dir",
            str(p0_burnin_jobs_dir),
            "--output-file",
            str(p0_burnin_report_file),
        ],
        "p0_release_evidence_bundle": [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "p0-release-evidence-bundle.py"),
            "--label",
            "stability-canary",
            "--project-root",
            str(PROJECT_ROOT),
            "--artifacts-dir",
            str(p0_evidence_artifacts_dir),
            "--include-glob",
            "*-report.json",
            "--required-files",
            p0_evidence_required_files,
            "--required-label",
            "stability-canary",
            "--output-file",
            str(p0_evidence_bundle_file),
            "--bundle-dir",
            str(p0_evidence_bundle_dir),
        ],
        "p0_closure_report": [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "p0-closure-report.py"),
            "--label",
            "stability-canary",
            "--project-root",
            str(PROJECT_ROOT),
            "--required-consecutive",
            "3",
            "--required-evidence-reports",
            p0_closure_required_evidence_reports,
            "--evidence-bundle-file",
            str(p0_evidence_bundle_file),
            "--burnin-file",
            str(p0_burnin_report_file),
            "--runbook-contract-file",
            str(p0_runbook_contract_report_file),
            "--output-file",
            str(p0_closure_report_file),
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
        baseline_config = baseline_drills.get(drill_name)
        if not isinstance(baseline_config, dict):
            regressions.append(
                {
                    "type": "missing_baseline_entry",
                    "drill": drill_name,
                    "message": "No baseline drill configuration found.",
                }
            )
            continue
        baseline_duration = float(baseline_config.get("baseline_duration_seconds", 0.0))
        if baseline_duration <= 0:
            regressions.append(
                {
                    "type": "invalid_baseline_duration",
                    "drill": drill_name,
                    "baseline_duration_seconds": baseline_duration,
                    "message": "baseline_duration_seconds must be > 0.",
                }
            )
            continue
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
    long_soak_baseline = baseline_drills.get("long_soak_budget")
    if not isinstance(long_soak_baseline, dict):
        regressions.append(
            {
                "type": "missing_baseline_entry",
                "drill": "long_soak_budget",
                "message": "No baseline drill configuration found.",
            }
        )
        max_429_rate = 1.0
        max_error_rate = 1.0
    else:
        max_429_rate = float(long_soak_baseline.get("max_http_429_rate_percent", 1.0))
        max_error_rate = float(long_soak_baseline.get("max_error_rate_percent", 1.0))
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
