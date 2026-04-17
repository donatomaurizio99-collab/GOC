from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_REQUIRED_REPORTS = [
    "artifacts/canary-guardrails-release-gate.json",
    "artifacts/auto-rollback-policy-release-gate.json",
    "artifacts/p0-disaster-recovery-rehearsal-pack-release-gate.json",
    "artifacts/failure-budget-dashboard-release-gate.json",
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


def _criterion(name: str, passed: bool, details: str) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def run_check(
    *,
    label: str,
    project_root: Path,
    required_reports: list[str],
    canary_report_file: Path,
    rollback_report_file: Path,
    disaster_recovery_report_file: Path,
    failure_budget_report_file: Path,
    required_label: str,
    required_canary_stage_count: int,
    output_file: Path,
    allow_missing_reports: bool,
    allow_not_ready: bool,
) -> dict[str, Any]:
    started = time.perf_counter()
    _expect(required_reports, "At least one required report must be configured.")

    resolved_required_reports = [_resolve_path(project_root, value) for value in required_reports]

    missing_reports: list[str] = []
    report_records: list[dict[str, Any]] = []
    non_green_reports: list[dict[str, Any]] = []
    label_mismatch_reports: list[dict[str, Any]] = []

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

    def read_optional(path: Path) -> dict[str, Any]:
        if path.exists():
            return _read_json_object(path)
        return {}

    canary_payload = read_optional(canary_report_file)
    rollback_payload = read_optional(rollback_report_file)
    dr_payload = read_optional(disaster_recovery_report_file)
    failure_payload = read_optional(failure_budget_report_file)

    canary_stage_evaluations = (
        canary_payload.get("stage_evaluations") if isinstance(canary_payload.get("stage_evaluations"), list) else []
    )
    canary_decision_result = str((canary_payload.get("decision") or {}).get("result") or "")
    canary_freeze_active = bool(
        (((canary_payload.get("rings") or {}).get("post_state") or {}).get("release_freeze") or {}).get("active")
    )

    rollback_triggered = bool(((rollback_payload.get("decision") or {}).get("triggered")))
    rollback_executed = bool(((rollback_payload.get("rollback") or {}).get("executed")))
    rollback_expected_reason_matched = bool(
        ((rollback_payload.get("decision") or {}).get("expected_reason_matched"))
    )

    dr_release_blocked = bool(((dr_payload.get("decision") or {}).get("release_blocked")))
    dr_duration_budget_exceeded = bool(((dr_payload.get("metrics") or {}).get("duration_budget_exceeded")))

    failure_release_blocked = bool(((failure_payload.get("decision") or {}).get("release_blocked")))

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
            "canary_stage_count",
            len(canary_stage_evaluations) >= int(required_canary_stage_count),
            f"observed={len(canary_stage_evaluations)}, required={required_canary_stage_count}",
        ),
        _criterion(
            "canary_decision_recorded",
            canary_decision_result in {"halt", "promote"},
            f"decision_result={canary_decision_result!r}",
        ),
        _criterion(
            "canary_halt_path_verified",
            canary_decision_result == "halt" and canary_freeze_active,
            f"decision_result={canary_decision_result!r}, freeze_active={canary_freeze_active}",
        ),
        _criterion(
            "incident_rollback_proven",
            rollback_triggered and rollback_executed,
            f"triggered={rollback_triggered}, executed={rollback_executed}",
        ),
        _criterion(
            "incident_rollback_reason_match",
            rollback_expected_reason_matched,
            f"expected_reason_matched={rollback_expected_reason_matched}",
        ),
        _criterion(
            "disaster_recovery_not_blocked",
            not dr_release_blocked,
            f"release_blocked={dr_release_blocked}",
        ),
        _criterion(
            "disaster_recovery_duration_budget",
            not dr_duration_budget_exceeded,
            f"duration_budget_exceeded={dr_duration_budget_exceeded}",
        ),
        _criterion(
            "failure_budget_not_blocked",
            not failure_release_blocked,
            f"release_blocked={failure_release_blocked}",
        ),
    ]

    failed_criteria = [item for item in criteria if not bool(item.get("passed"))]
    success = len(failed_criteria) == 0

    report = {
        "label": label,
        "success": bool(success),
        "paths": {
            "project_root": str(project_root),
            "canary_report_file": str(canary_report_file),
            "rollback_report_file": str(rollback_report_file),
            "disaster_recovery_report_file": str(disaster_recovery_report_file),
            "failure_budget_report_file": str(failure_budget_report_file),
            "output_file": str(output_file),
        },
        "config": {
            "required_reports": required_reports,
            "required_label": required_label,
            "required_canary_stage_count": int(required_canary_stage_count),
            "allow_missing_reports": bool(allow_missing_reports),
            "allow_not_ready": bool(allow_not_ready),
        },
        "metrics": {
            "required_reports_total": len(required_reports),
            "required_reports_present": len(report_records),
            "required_reports_missing": len(missing_reports),
            "staging_reports_non_green": len(non_green_reports),
            "label_mismatch_reports": len(label_mismatch_reports),
            "canary_stage_count": len(canary_stage_evaluations),
            "incident_rollback_proof_failed": 0 if (rollback_triggered and rollback_executed) else 1,
            "restore_proof_failed": 0 if ((not dr_release_blocked) and (not dr_duration_budget_exceeded)) else 1,
            "criteria_failed": len(failed_criteria),
        },
        "criteria": criteria,
        "failed_criteria": failed_criteria,
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
        raise RuntimeError(f"Release-gate staging soak readiness check failed: {json.dumps(report, sort_keys=True)}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Consolidate staging soak and incident/restore release criteria into a deterministic release-gate report."
        )
    )
    parser.add_argument("--label", default="release-gate-staging-soak-readiness")
    parser.add_argument("--project-root")
    parser.add_argument("--required-reports", default=",".join(DEFAULT_REQUIRED_REPORTS))
    parser.add_argument("--canary-report-file", default="artifacts/canary-guardrails-release-gate.json")
    parser.add_argument("--rollback-report-file", default="artifacts/auto-rollback-policy-release-gate.json")
    parser.add_argument("--disaster-recovery-report-file", default="artifacts/p0-disaster-recovery-rehearsal-pack-release-gate.json")
    parser.add_argument("--failure-budget-report-file", default="artifacts/failure-budget-dashboard-release-gate.json")
    parser.add_argument("--required-label", default="release-gate")
    parser.add_argument("--required-canary-stage-count", type=int, default=4)
    parser.add_argument("--output-file", default="artifacts/release-gate-staging-soak-readiness-release-gate.json")
    parser.add_argument("--allow-missing-reports", action="store_true")
    parser.add_argument("--allow-not-ready", action="store_true")
    args = parser.parse_args(argv)

    inferred_project_root = Path(__file__).resolve().parents[1]
    project_root = _resolve_path(inferred_project_root, args.project_root) if args.project_root else inferred_project_root

    required_reports = _parse_csv_list(args.required_reports)
    canary_report_file = _resolve_path(project_root, args.canary_report_file)
    rollback_report_file = _resolve_path(project_root, args.rollback_report_file)
    disaster_recovery_report_file = _resolve_path(project_root, args.disaster_recovery_report_file)
    failure_budget_report_file = _resolve_path(project_root, args.failure_budget_report_file)
    output_file = _resolve_path(project_root, args.output_file)

    try:
        report = run_check(
            label=str(args.label),
            project_root=project_root,
            required_reports=required_reports,
            canary_report_file=canary_report_file,
            rollback_report_file=rollback_report_file,
            disaster_recovery_report_file=disaster_recovery_report_file,
            failure_budget_report_file=failure_budget_report_file,
            required_label=str(args.required_label),
            required_canary_stage_count=int(args.required_canary_stage_count),
            output_file=output_file,
            allow_missing_reports=bool(args.allow_missing_reports),
            allow_not_ready=bool(args.allow_not_ready),
        )
    except Exception as exc:
        print(f"[release-gate-staging-soak-readiness] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
