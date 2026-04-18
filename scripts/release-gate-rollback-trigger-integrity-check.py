from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_REQUIRED_REPORTS = [
    "artifacts/release-gate-hypercare-activation-release-gate.json",
    "artifacts/auto-rollback-policy-release-gate.json",
    "artifacts/incident-rollback-release-gate.json",
    "artifacts/release-gate-slo-burn-rate-v2-release-gate.json",
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
    auto_rollback_report_file: Path,
    incident_rollback_report_file: Path,
    hypercare_report_file: Path,
    required_label: str,
    output_file: Path,
    allow_missing_reports: bool,
    allow_not_ready: bool,
) -> dict[str, Any]:
    started = time.perf_counter()
    _expect(required_reports, "At least one required report must be configured.")

    policy = _read_json_object(policy_file)
    required_trigger_reasons_raw = policy.get("required_trigger_reasons")
    required_trigger_reasons = (
        [str(item).strip() for item in required_trigger_reasons_raw]
        if isinstance(required_trigger_reasons_raw, list)
        else []
    )
    required_trigger_reasons = [item for item in required_trigger_reasons if item]
    runbook_section = str(policy.get("runbook_section") or "").strip()
    max_expected_reason_mismatches = max(0, _coerce_int(policy.get("max_expected_reason_mismatches"), 0))
    max_trigger_reason_violations = max(0, _coerce_int(policy.get("max_trigger_reason_violations"), 0))
    policy_errors: list[str] = []
    if not required_trigger_reasons:
        policy_errors.append("required_trigger_reasons_missing")
    if not runbook_section:
        policy_errors.append("runbook_section_missing")

    resolved_required_reports = [_resolve_path(project_root, value) for value in required_reports]
    missing_reports: list[str] = []
    report_records: list[dict[str, Any]] = []
    non_green_reports: list[dict[str, Any]] = []
    label_mismatch_reports: list[dict[str, Any]] = []
    release_block_signal_reports: list[dict[str, Any]] = []

    for report_path in resolved_required_reports:
        if not report_path.exists():
            missing_reports.append(str(report_path))
            continue
        payload = _read_json_object(report_path)
        report_label = str(payload.get("label") or "")
        success_value = payload.get("success")
        has_success_flag = isinstance(success_value, bool)
        is_non_green = (not has_success_flag) or (success_value is False)
        label_matches = (not required_label) or (report_label == required_label)
        release_blocked_value = (payload.get("decision") or {}).get("release_blocked")
        has_release_block_signal = isinstance(release_blocked_value, bool) and bool(release_blocked_value)
        record = {
            "path": str(report_path),
            "label": report_label,
            "success": bool(success_value) if has_success_flag else None,
            "has_success_flag": bool(has_success_flag),
            "label_matches_required": bool(label_matches),
            "release_blocked": bool(release_blocked_value) if isinstance(release_blocked_value, bool) else None,
        }
        report_records.append(record)
        if is_non_green:
            non_green_reports.append(record)
        if not label_matches:
            label_mismatch_reports.append(record)
        if has_release_block_signal:
            release_block_signal_reports.append(record)

    auto_rollback_payload = _read_json_object(auto_rollback_report_file) if auto_rollback_report_file.exists() else {}
    incident_rollback_payload = (
        _read_json_object(incident_rollback_report_file) if incident_rollback_report_file.exists() else {}
    )
    hypercare_payload = _read_json_object(hypercare_report_file) if hypercare_report_file.exists() else {}

    observed_trigger_reason = str(
        (auto_rollback_payload.get("decision") or {}).get("observed_trigger_reason")
        or (auto_rollback_payload.get("observation") or {}).get("trigger_reason")
        or ""
    ).strip()
    expected_reason_matched = bool((auto_rollback_payload.get("decision") or {}).get("expected_reason_matched"))
    auto_rollback_executed = bool((auto_rollback_payload.get("rollback") or {}).get("executed"))
    incident_rollback_ok = bool(
        (incident_rollback_payload.get("rollback") or {}).get("ok")
        or incident_rollback_payload.get("success")
    )
    rollback_integrity_execution_ok = auto_rollback_executed and incident_rollback_ok
    hypercare_action = str((hypercare_payload.get("decision") or {}).get("recommended_action") or "")
    hypercare_signal_ok = hypercare_action in {"proceed_to_stage_ae", "rollback_integrity_ready"}

    expected_reason_mismatches = 0 if expected_reason_matched else 1
    trigger_reason_violations = 0 if observed_trigger_reason in required_trigger_reasons else 1

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
            "release_block_signals_cleared",
            len(release_block_signal_reports) == 0,
            f"reports_with_release_block_signal={len(release_block_signal_reports)}",
        ),
        _criterion(
            "rollback_integrity_policy_valid",
            len(policy_errors) == 0,
            f"policy_errors={len(policy_errors)}",
        ),
        _criterion(
            "rollback_expected_reason_budget",
            expected_reason_mismatches <= max_expected_reason_mismatches,
            (
                f"expected_reason_mismatches={expected_reason_mismatches}, "
                f"max_allowed={max_expected_reason_mismatches}"
            ),
        ),
        _criterion(
            "rollback_trigger_reason_budget",
            trigger_reason_violations <= max_trigger_reason_violations,
            (
                f"trigger_reason_violations={trigger_reason_violations}, "
                f"max_allowed={max_trigger_reason_violations}"
            ),
        ),
        _criterion(
            "rollback_execution_proven",
            bool(rollback_integrity_execution_ok),
            (
                f"auto_rollback_executed={auto_rollback_executed}, "
                f"incident_rollback_ok={incident_rollback_ok}"
            ),
        ),
        _criterion(
            "hypercare_signal",
            bool(hypercare_signal_ok),
            f"recommended_action={hypercare_action!r}",
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
            "auto_rollback_report_file": str(auto_rollback_report_file),
            "incident_rollback_report_file": str(incident_rollback_report_file),
            "hypercare_report_file": str(hypercare_report_file),
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
            "required_trigger_reasons": required_trigger_reasons,
            "max_expected_reason_mismatches": int(max_expected_reason_mismatches),
            "max_trigger_reason_violations": int(max_trigger_reason_violations),
            "runbook_section": runbook_section,
        },
        "metrics": {
            "required_reports_total": len(required_reports),
            "required_reports_present": len(report_records),
            "required_reports_missing": len(missing_reports),
            "rollback_integrity_reports_non_green": len(non_green_reports),
            "label_mismatch_reports": len(label_mismatch_reports),
            "rollback_integrity_release_block_signals": len(release_block_signal_reports),
            "rollback_integrity_policy_invalid": len(policy_errors),
            "rollback_integrity_expected_reason_mismatches": int(expected_reason_mismatches),
            "rollback_integrity_trigger_reason_violations": int(trigger_reason_violations),
            "rollback_integrity_execution_failed": 0 if rollback_integrity_execution_ok else 1,
            "rollback_integrity_hypercare_signal_failed": 0 if hypercare_signal_ok else 1,
            "criteria_failed": len(failed_criteria),
        },
        "decision": {
            "release_blocked": not bool(success),
            "recommended_action": "block_release" if not success else "proceed_to_stage_af",
        },
        "criteria": criteria,
        "failed_criteria": failed_criteria,
        "reports": report_records,
        "missing_reports": missing_reports,
        "non_green_reports": non_green_reports,
        "label_mismatch_reports": label_mismatch_reports,
        "reports_with_release_block_signal": release_block_signal_reports,
        "policy_errors": policy_errors,
        "trigger_context": {
            "observed_trigger_reason": observed_trigger_reason,
            "expected_reason_matched": expected_reason_matched,
            "auto_rollback_executed": auto_rollback_executed,
            "incident_rollback_ok": incident_rollback_ok,
            "hypercare_action": hypercare_action,
        },
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2), encoding="utf-8")

    if not success and not allow_not_ready:
        raise RuntimeError(f"Release-gate rollback trigger integrity check failed: {json.dumps(report, sort_keys=True)}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate rollback trigger integrity from Stage-AD hypercare plus auto-rollback and incident rollback evidence."
        )
    )
    parser.add_argument("--label", default="release-gate-rollback-trigger-integrity-check")
    parser.add_argument("--project-root")
    parser.add_argument("--policy-file", default="docs/release-gate-rollback-trigger-integrity-policy.json")
    parser.add_argument("--required-reports", default=",".join(DEFAULT_REQUIRED_REPORTS))
    parser.add_argument("--auto-rollback-report-file", default="artifacts/auto-rollback-policy-release-gate.json")
    parser.add_argument("--incident-rollback-report-file", default="artifacts/incident-rollback-release-gate.json")
    parser.add_argument("--hypercare-report-file", default="artifacts/release-gate-hypercare-activation-release-gate.json")
    parser.add_argument("--required-label", default="release-gate")
    parser.add_argument("--output-file", default="artifacts/release-gate-rollback-trigger-integrity-release-gate.json")
    parser.add_argument("--allow-missing-reports", action="store_true")
    parser.add_argument("--allow-not-ready", action="store_true")
    args = parser.parse_args(argv)

    inferred_project_root = Path(__file__).resolve().parents[1]
    project_root = _resolve_path(inferred_project_root, args.project_root) if args.project_root else inferred_project_root
    policy_file = _resolve_path(project_root, args.policy_file)
    required_reports = _parse_csv_list(args.required_reports)
    auto_rollback_report_file = _resolve_path(project_root, args.auto_rollback_report_file)
    incident_rollback_report_file = _resolve_path(project_root, args.incident_rollback_report_file)
    hypercare_report_file = _resolve_path(project_root, args.hypercare_report_file)
    output_file = _resolve_path(project_root, args.output_file)

    try:
        report = run_check(
            label=str(args.label),
            project_root=project_root,
            policy_file=policy_file,
            required_reports=required_reports,
            auto_rollback_report_file=auto_rollback_report_file,
            incident_rollback_report_file=incident_rollback_report_file,
            hypercare_report_file=hypercare_report_file,
            required_label=str(args.required_label),
            output_file=output_file,
            allow_missing_reports=bool(args.allow_missing_reports),
            allow_not_ready=bool(args.allow_not_ready),
        )
    except Exception as exc:
        print(f"[release-gate-rollback-trigger-integrity-check] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
