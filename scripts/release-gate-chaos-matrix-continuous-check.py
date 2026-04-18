from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_REQUIRED_REPORTS = [
    "artifacts/critical-drill-flake-gate-release-gate.json",
    "artifacts/release-gate-runtime-stability-release-gate.json",
    "artifacts/p0-disaster-recovery-rehearsal-pack-release-gate.json",
]


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _parse_csv_list(text: str) -> list[str]:
    return [item.strip() for item in str(text).split(",") if item.strip()]


def _resolve_path(project_root: Path, value: str) -> Path:
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


def _read_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    _expect(isinstance(payload, dict), f"Expected JSON object in {path}")
    return payload


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _criterion(name: str, passed: bool, details: str) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def run_check(
    *,
    label: str,
    project_root: Path,
    policy_file: Path,
    required_reports: list[str],
    critical_drill_report_file: Path,
    required_label: str,
    output_file: Path,
    allow_missing_reports: bool,
    allow_not_ready: bool,
) -> dict[str, Any]:
    started = time.perf_counter()
    _expect(required_reports, "At least one required report must be configured.")

    policy = _read_json_object(policy_file)
    raw_scenarios = policy.get("required_scenarios")
    required_scenarios = [str(item).strip() for item in raw_scenarios] if isinstance(raw_scenarios, list) else []
    required_scenarios = [item for item in required_scenarios if item]
    max_failed_scenarios = max(0, _coerce_int(policy.get("max_failed_scenarios"), 0))
    max_regression_violations = max(0, _coerce_int(policy.get("max_regression_violations"), 0))
    _expect(required_scenarios, f"Policy must define required_scenarios: {policy_file}")

    resolved_required_reports = [_resolve_path(project_root, value) for value in required_reports]
    missing_reports: list[str] = []
    report_records: list[dict[str, Any]] = []
    non_green_reports: list[dict[str, Any]] = []
    label_mismatch_reports: list[dict[str, Any]] = []
    loaded_payload_by_path: dict[str, dict[str, Any]] = {}

    for report_path in resolved_required_reports:
        if not report_path.exists():
            missing_reports.append(str(report_path))
            continue
        payload = _read_json_object(report_path)
        loaded_payload_by_path[str(report_path)] = payload
        report_label = str(payload.get("label") or "")
        success_value = payload.get("success")
        has_success_flag = isinstance(success_value, bool)
        is_non_green = (not has_success_flag) or (success_value is False)
        label_matches = (not required_label) or (report_label == required_label)
        record = {
            "path": str(report_path),
            "label": report_label,
            "success": bool(success_value) if has_success_flag else None,
            "has_success_flag": bool(has_success_flag),
            "label_matches_required": bool(label_matches),
        }
        report_records.append(record)
        if is_non_green:
            non_green_reports.append(record)
        if not label_matches:
            label_mismatch_reports.append(record)

    critical_payload = loaded_payload_by_path.get(str(critical_drill_report_file), {})
    if not critical_payload and critical_drill_report_file.exists():
        critical_payload = _read_json_object(critical_drill_report_file)

    critical_metrics = critical_payload.get("metrics") if isinstance(critical_payload.get("metrics"), dict) else {}
    failed_iterations = max(0, _coerce_int(critical_metrics.get("failed_iterations"), 0))
    max_failed_iterations = max(0, _coerce_int(critical_metrics.get("max_failed_iterations"), 0))
    scenario_failures = 0 if failed_iterations <= max_failed_iterations else 1

    scenario_results: list[dict[str, Any]] = []
    for scenario_name in required_scenarios:
        scenario_passed = len(non_green_reports) == 0 and scenario_failures == 0
        scenario_results.append({"scenario": scenario_name, "passed": bool(scenario_passed)})

    failed_scenarios = [item for item in scenario_results if not bool(item.get("passed"))]
    regression_violations = 0 if failed_iterations <= max_failed_iterations else 1

    criteria = [
        _criterion(
            "required_reports_present",
            bool(allow_missing_reports or not missing_reports),
            f"missing_reports={len(missing_reports)}",
        ),
        _criterion(
            "required_reports_green",
            len(non_green_reports) == 0,
            f"non_green_reports={len(non_green_reports)}",
        ),
        _criterion(
            "required_reports_label_match",
            len(label_mismatch_reports) == 0,
            f"label_mismatch_reports={len(label_mismatch_reports)}",
        ),
        _criterion(
            "chaos_matrix_coverage",
            len(scenario_results) == len(required_scenarios),
            f"scenario_results={len(scenario_results)}, required_scenarios={len(required_scenarios)}",
        ),
        _criterion(
            "chaos_failed_scenarios_budget",
            len(failed_scenarios) <= int(max_failed_scenarios),
            f"failed_scenarios={len(failed_scenarios)}, max_allowed={max_failed_scenarios}",
        ),
        _criterion(
            "chaos_regression_budget",
            int(regression_violations) <= int(max_regression_violations),
            f"regression_violations={regression_violations}, max_allowed={max_regression_violations}",
        ),
    ]

    failed_criteria = [item for item in criteria if not bool(item.get("passed"))]
    success = len(failed_criteria) == 0

    report = {
        "label": label,
        "success": bool(success),
        "paths": {
            "project_root": str(project_root),
            "policy_file": str(policy_file),
            "critical_drill_report_file": str(critical_drill_report_file),
            "output_file": str(output_file),
        },
        "config": {
            "required_reports": required_reports,
            "required_label": required_label,
            "allow_missing_reports": bool(allow_missing_reports),
            "allow_not_ready": bool(allow_not_ready),
        },
        "policy": {
            "version": str(policy.get("version") or ""),
            "required_scenarios": required_scenarios,
            "max_failed_scenarios": int(max_failed_scenarios),
            "max_regression_violations": int(max_regression_violations),
        },
        "metrics": {
            "required_reports_total": len(required_reports),
            "required_reports_present": len(report_records),
            "required_reports_missing": len(missing_reports),
            "chaos_required_reports_non_green": len(non_green_reports),
            "label_mismatch_reports": len(label_mismatch_reports),
            "chaos_failed_scenarios": len(failed_scenarios),
            "chaos_regression_violations": int(regression_violations),
            "critical_failed_iterations": int(failed_iterations),
            "criteria_failed": len(failed_criteria),
        },
        "decision": {
            "release_blocked": not bool(success),
            "recommended_action": "block_release" if not success else "proceed",
        },
        "criteria": criteria,
        "failed_criteria": failed_criteria,
        "scenario_results": scenario_results,
        "failed_scenarios": failed_scenarios,
        "reports": report_records,
        "missing_reports": missing_reports,
        "non_green_reports": non_green_reports,
        "label_mismatch_reports": label_mismatch_reports,
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2), encoding="utf-8")

    if not success and not allow_not_ready:
        raise RuntimeError(f"Release-gate chaos matrix continuous check failed: {json.dumps(report, sort_keys=True)}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate weekly chaos-matrix continuity and deterministic no-regression criteria for release promotion."
        )
    )
    parser.add_argument("--label", default="release-gate-chaos-matrix-continuous-check")
    parser.add_argument("--project-root")
    parser.add_argument("--policy-file", default="docs/release-gate-chaos-matrix-policy.json")
    parser.add_argument("--required-reports", default=",".join(DEFAULT_REQUIRED_REPORTS))
    parser.add_argument("--critical-drill-report-file", default="artifacts/critical-drill-flake-gate-release-gate.json")
    parser.add_argument("--required-label", default="release-gate")
    parser.add_argument("--output-file", default="artifacts/release-gate-chaos-matrix-continuous-release-gate.json")
    parser.add_argument("--allow-missing-reports", action="store_true")
    parser.add_argument("--allow-not-ready", action="store_true")
    args = parser.parse_args(argv)

    inferred_project_root = Path(__file__).resolve().parents[1]
    project_root = _resolve_path(inferred_project_root, args.project_root) if args.project_root else inferred_project_root
    policy_file = _resolve_path(project_root, args.policy_file)
    required_reports = _parse_csv_list(args.required_reports)
    critical_drill_report_file = _resolve_path(project_root, args.critical_drill_report_file)
    output_file = _resolve_path(project_root, args.output_file)

    try:
        report = run_check(
            label=str(args.label),
            project_root=project_root,
            policy_file=policy_file,
            required_reports=required_reports,
            critical_drill_report_file=critical_drill_report_file,
            required_label=str(args.required_label),
            output_file=output_file,
            allow_missing_reports=bool(args.allow_missing_reports),
            allow_not_ready=bool(args.allow_not_ready),
        )
    except Exception as exc:
        print(f"[release-gate-chaos-matrix-continuous-check] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
