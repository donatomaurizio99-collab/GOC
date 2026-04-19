from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _load_json_file(path: Path) -> dict[str, Any]:
    _expect(path.exists(), f"JSON file not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    _expect(isinstance(payload, dict), f"Expected JSON object in file: {path}")
    return payload


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _safe_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "y", "on"}:
            return True
        if lowered in {"false", "0", "no", "n", "off"}:
            return False
    return bool(default)


def run_production_readiness_gate(
    *,
    label: str,
    required_checks_report_file: Path,
    branch_protection_report_file: Path,
    guard_health_report_file: Path,
    guard_burnin_report_file: Path,
    watchdog_rehearsal_guard_report_file: Path,
    allow_not_ready: bool,
    output_file: Path,
) -> dict[str, Any]:
    started = time.perf_counter()
    errors: list[str] = []

    def load(path: Path, key: str) -> dict[str, Any] | None:
        try:
            return _load_json_file(path)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{key}: {exc}")
            return None

    required_checks_payload = load(required_checks_report_file, "required_checks")
    branch_protection_payload = load(branch_protection_report_file, "branch_protection")
    guard_health_payload = load(guard_health_report_file, "guard_health")
    guard_burnin_payload = load(guard_burnin_report_file, "guard_burnin")
    watchdog_rehearsal_guard_payload = load(watchdog_rehearsal_guard_report_file, "watchdog_rehearsal_guard")

    reports_present = bool(
        required_checks_payload is not None
        and branch_protection_payload is not None
        and guard_health_payload is not None
        and guard_burnin_payload is not None
        and watchdog_rehearsal_guard_payload is not None
    )

    required_checks_non_green_total = _safe_int(
        ((required_checks_payload or {}).get("metrics") or {}).get("non_green_runs_total"),
        0,
    )
    required_checks_release_blocked = _safe_bool(
        ((required_checks_payload or {}).get("decision") or {}).get("release_blocked"),
        False,
    )
    required_checks_green = bool(
        required_checks_payload is not None
        and required_checks_non_green_total == 0
        and not required_checks_release_blocked
    )

    branch_protection_drift_detected = _safe_bool(
        ((branch_protection_payload or {}).get("decision") or {}).get("branch_protection_drift_detected"),
        True,
    )
    branch_protection_integrity = bool(
        branch_protection_payload is not None and not branch_protection_drift_detected
    )

    guard_workflow_health_degraded = _safe_bool(
        ((guard_health_payload or {}).get("decision") or {}).get("guard_workflow_health_degraded"),
        True,
    )
    guard_workflow_coverage_contract_ok = _safe_bool(
        ((guard_health_payload or {}).get("decision") or {}).get("guard_workflow_coverage_contract_ok"),
        False,
    )
    guard_workflow_health_integrity = bool(
        guard_health_payload is not None
        and not guard_workflow_health_degraded
        and guard_workflow_coverage_contract_ok
    )

    guard_burnin_degraded = _safe_bool(
        ((guard_burnin_payload or {}).get("decision") or {}).get("guard_burnin_degraded"),
        True,
    )
    guard_burnin_integrity = bool(
        guard_burnin_payload is not None and not guard_burnin_degraded
    )

    watchdog_rehearsal_slo_breached = _safe_bool(
        ((watchdog_rehearsal_guard_payload or {}).get("decision") or {}).get("watchdog_rehearsal_slo_breached"),
        True,
    )
    watchdog_rehearsal_guard_integrity = bool(
        watchdog_rehearsal_guard_payload is not None and not watchdog_rehearsal_slo_breached
    )

    production_ready = bool(
        reports_present
        and required_checks_green
        and branch_protection_integrity
        and guard_workflow_health_integrity
        and guard_burnin_integrity
        and watchdog_rehearsal_guard_integrity
    )

    criteria = [
        {
            "name": "readiness_reports_present",
            "passed": bool(reports_present or allow_not_ready),
            "details": f"reports_present={reports_present}, allow_not_ready={allow_not_ready}",
        },
        {
            "name": "required_checks_integrity",
            "passed": bool(required_checks_green or allow_not_ready),
            "details": (
                f"required_checks_non_green_total={required_checks_non_green_total}, "
                f"required_checks_release_blocked={required_checks_release_blocked}, "
                f"allow_not_ready={allow_not_ready}"
            ),
        },
        {
            "name": "branch_protection_integrity",
            "passed": bool(branch_protection_integrity or allow_not_ready),
            "details": (
                f"branch_protection_drift_detected={branch_protection_drift_detected}, "
                f"allow_not_ready={allow_not_ready}"
            ),
        },
        {
            "name": "guard_workflow_health_integrity",
            "passed": bool(guard_workflow_health_integrity or allow_not_ready),
            "details": (
                f"guard_workflow_health_degraded={guard_workflow_health_degraded}, "
                f"guard_workflow_coverage_contract_ok={guard_workflow_coverage_contract_ok}, "
                f"allow_not_ready={allow_not_ready}"
            ),
        },
        {
            "name": "guard_burnin_integrity",
            "passed": bool(guard_burnin_integrity or allow_not_ready),
            "details": (
                f"guard_burnin_degraded={guard_burnin_degraded}, "
                f"allow_not_ready={allow_not_ready}"
            ),
        },
        {
            "name": "watchdog_rehearsal_guard_integrity",
            "passed": bool(watchdog_rehearsal_guard_integrity or allow_not_ready),
            "details": (
                f"watchdog_rehearsal_slo_breached={watchdog_rehearsal_slo_breached}, "
                f"allow_not_ready={allow_not_ready}"
            ),
        },
    ]
    failed_criteria = [item for item in criteria if not bool(item.get("passed"))]
    success = len(failed_criteria) == 0

    summary_lines = [
        f"production_ready={str(production_ready).lower()}",
        f"required_checks_non_green_total={required_checks_non_green_total}",
        f"branch_protection_drift_detected={str(branch_protection_drift_detected).lower()}",
        f"guard_workflow_health_degraded={str(guard_workflow_health_degraded).lower()}",
        f"guard_burnin_degraded={str(guard_burnin_degraded).lower()}",
        f"watchdog_rehearsal_slo_breached={str(watchdog_rehearsal_slo_breached).lower()}",
    ]

    report = {
        "label": label,
        "success": bool(success),
        "config": {
            "required_checks_report_file": str(required_checks_report_file),
            "branch_protection_report_file": str(branch_protection_report_file),
            "guard_health_report_file": str(guard_health_report_file),
            "guard_burnin_report_file": str(guard_burnin_report_file),
            "watchdog_rehearsal_guard_report_file": str(watchdog_rehearsal_guard_report_file),
            "allow_not_ready": bool(allow_not_ready),
            "output_file": str(output_file),
        },
        "metrics": {
            "required_checks_non_green_total": int(required_checks_non_green_total),
            "required_checks_release_blocked": bool(required_checks_release_blocked),
            "branch_protection_drift_detected": bool(branch_protection_drift_detected),
            "guard_workflow_health_degraded": bool(guard_workflow_health_degraded),
            "guard_workflow_coverage_contract_ok": bool(guard_workflow_coverage_contract_ok),
            "guard_burnin_degraded": bool(guard_burnin_degraded),
            "watchdog_rehearsal_slo_breached": bool(watchdog_rehearsal_slo_breached),
            "reports_load_errors_total": int(len(errors)),
            "criteria_failed": int(len(failed_criteria)),
        },
        "criteria": criteria,
        "failed_criteria": failed_criteria,
        "load_errors": errors,
        "decision": {
            "production_ready": bool(production_ready),
            "release_blocked": not bool(production_ready),
            "allow_not_ready_effective": bool(allow_not_ready and not production_ready),
            "recommended_action": (
                "production_readiness_gate_green"
                if production_ready
                else "production_readiness_gate_not_ready"
            ),
        },
        "summary_lines": summary_lines,
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2), encoding="utf-8")

    if not success:
        raise RuntimeError(f"Master production readiness gate failed: {json.dumps(report, sort_keys=True)}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Aggregate required-check integrity, branch-protection drift, guard-workflow health, "
            "guard burn-in, and watchdog rehearsal guard integrity into a single production-readiness go/no-go signal."
        )
    )
    parser.add_argument("--label", default="master-production-readiness-gate")
    parser.add_argument(
        "--required-checks-report-file",
        default="artifacts/master-required-checks-24h-report-readiness.json",
    )
    parser.add_argument(
        "--branch-protection-report-file",
        default="artifacts/master-branch-protection-drift-guard-readiness.json",
    )
    parser.add_argument(
        "--guard-health-report-file",
        default="artifacts/master-guard-workflow-health-check-readiness.json",
    )
    parser.add_argument(
        "--guard-burnin-report-file",
        default="artifacts/master-guard-burnin-check-readiness.json",
    )
    parser.add_argument(
        "--watchdog-rehearsal-guard-report-file",
        default="artifacts/master-watchdog-rehearsal-slo-guard-readiness.json",
    )
    parser.add_argument("--allow-not-ready", action="store_true")
    parser.add_argument("--output-file", default="artifacts/master-production-readiness-gate.json")
    args = parser.parse_args(argv)

    output_file = Path(str(args.output_file)).expanduser()
    try:
        report = run_production_readiness_gate(
            label=str(args.label),
            required_checks_report_file=Path(str(args.required_checks_report_file)).expanduser(),
            branch_protection_report_file=Path(str(args.branch_protection_report_file)).expanduser(),
            guard_health_report_file=Path(str(args.guard_health_report_file)).expanduser(),
            guard_burnin_report_file=Path(str(args.guard_burnin_report_file)).expanduser(),
            watchdog_rehearsal_guard_report_file=Path(str(args.watchdog_rehearsal_guard_report_file)).expanduser(),
            allow_not_ready=bool(args.allow_not_ready),
            output_file=output_file,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[master-production-readiness-gate] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
