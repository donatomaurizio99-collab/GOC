from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_REQUIRED_REPORTS = [
    "artifacts/release-gate-production-readiness-certification-release-gate.json",
    "artifacts/release-gate-rc-canary-rollout-release-gate.json",
    "artifacts/auto-rollback-policy-release-gate.json",
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


def _criterion(name: str, passed: bool, details: str) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def run_check(
    *,
    label: str,
    project_root: Path,
    policy_file: Path,
    required_reports: list[str],
    rollback_report_file: Path,
    disaster_recovery_report_file: Path,
    required_label: str,
    output_file: Path,
    allow_missing_reports: bool,
    allow_not_ready: bool,
) -> dict[str, Any]:
    started = time.perf_counter()
    _expect(required_reports, "At least one required report must be configured.")

    policy = _read_json_object(policy_file)
    raw_steps = policy.get("required_rehearsal_steps")
    rehearsal_steps = [str(item).strip() for item in raw_steps] if isinstance(raw_steps, list) else []
    rehearsal_steps = [item for item in rehearsal_steps if item]
    policy_invalid = 0 if rehearsal_steps else 1
    _expect(rehearsal_steps, f"Policy must define required_rehearsal_steps: {policy_file}")

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

    rollback_payload = loaded_payload_by_path.get(str(rollback_report_file), {})
    if not rollback_payload and rollback_report_file.exists():
        rollback_payload = _read_json_object(rollback_report_file)
    dr_payload = loaded_payload_by_path.get(str(disaster_recovery_report_file), {})
    if not dr_payload and disaster_recovery_report_file.exists():
        dr_payload = _read_json_object(disaster_recovery_report_file)

    rollback_triggered = bool(((rollback_payload.get("decision") or {}).get("triggered")))
    rollback_executed = bool(((rollback_payload.get("rollback") or {}).get("executed")))
    rollback_expected_reason_matched = bool(((rollback_payload.get("decision") or {}).get("expected_reason_matched")))
    restore_release_blocked = bool(((dr_payload.get("decision") or {}).get("release_blocked")))
    restore_duration_budget_exceeded = bool(((dr_payload.get("metrics") or {}).get("duration_budget_exceeded")))

    step_results = []
    for step_name in rehearsal_steps:
        passed = True
        if step_name == "rollback_rehearsal":
            passed = rollback_triggered and rollback_executed and rollback_expected_reason_matched
        if step_name == "restore_validation":
            passed = (not restore_release_blocked) and (not restore_duration_budget_exceeded)
        step_results.append({"name": step_name, "passed": bool(passed)})

    covered_steps = [item for item in step_results if bool(item.get("passed"))]

    criteria = [
        _criterion(
            "policy_valid",
            policy_invalid == 0,
            f"policy_invalid={policy_invalid}",
        ),
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
            "deploy_rehearsal_steps_covered",
            len(covered_steps) == len(rehearsal_steps),
            f"covered_steps={len(covered_steps)}, required_steps={len(rehearsal_steps)}",
        ),
        _criterion(
            "deploy_rehearsal_rollback_proven",
            rollback_triggered and rollback_executed and rollback_expected_reason_matched,
            (
                f"triggered={rollback_triggered}, executed={rollback_executed}, "
                f"expected_reason_matched={rollback_expected_reason_matched}"
            ),
        ),
        _criterion(
            "deploy_rehearsal_restore_proven",
            (not restore_release_blocked) and (not restore_duration_budget_exceeded),
            (
                f"restore_release_blocked={restore_release_blocked}, "
                f"restore_duration_budget_exceeded={restore_duration_budget_exceeded}"
            ),
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
            "rollback_report_file": str(rollback_report_file),
            "disaster_recovery_report_file": str(disaster_recovery_report_file),
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
            "required_rehearsal_steps": rehearsal_steps,
        },
        "metrics": {
            "required_reports_total": len(required_reports),
            "required_reports_present": len(report_records),
            "required_reports_missing": len(missing_reports),
            "deploy_rehearsal_non_green": len(non_green_reports),
            "label_mismatch_reports": len(label_mismatch_reports),
            "deploy_rehearsal_policy_invalid": int(policy_invalid),
            "deploy_rehearsal_steps_total": len(rehearsal_steps),
            "deploy_rehearsal_steps_passed": len(covered_steps),
            "deploy_rehearsal_rollback_failed": 0 if (rollback_triggered and rollback_executed) else 1,
            "deploy_rehearsal_restore_failed": 0 if ((not restore_release_blocked) and (not restore_duration_budget_exceeded)) else 1,
            "criteria_failed": len(failed_criteria),
        },
        "decision": {
            "release_blocked": not bool(success),
            "recommended_action": "block_release" if not success else "proceed",
        },
        "criteria": criteria,
        "failed_criteria": failed_criteria,
        "step_results": step_results,
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
        raise RuntimeError(f"Release-gate deploy rehearsal check failed: {json.dumps(report, sort_keys=True)}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate deterministic deploy and rollback rehearsal evidence for production release promotion."
        )
    )
    parser.add_argument("--label", default="release-gate-deploy-rehearsal-check")
    parser.add_argument("--project-root")
    parser.add_argument("--policy-file", default="docs/release-gate-deploy-rehearsal-policy.json")
    parser.add_argument("--required-reports", default=",".join(DEFAULT_REQUIRED_REPORTS))
    parser.add_argument("--rollback-report-file", default="artifacts/auto-rollback-policy-release-gate.json")
    parser.add_argument("--disaster-recovery-report-file", default="artifacts/p0-disaster-recovery-rehearsal-pack-release-gate.json")
    parser.add_argument("--required-label", default="release-gate")
    parser.add_argument("--output-file", default="artifacts/release-gate-deploy-rehearsal-release-gate.json")
    parser.add_argument("--allow-missing-reports", action="store_true")
    parser.add_argument("--allow-not-ready", action="store_true")
    args = parser.parse_args(argv)

    inferred_project_root = Path(__file__).resolve().parents[1]
    project_root = _resolve_path(inferred_project_root, args.project_root) if args.project_root else inferred_project_root
    policy_file = _resolve_path(project_root, args.policy_file)
    required_reports = _parse_csv_list(args.required_reports)
    rollback_report_file = _resolve_path(project_root, args.rollback_report_file)
    disaster_recovery_report_file = _resolve_path(project_root, args.disaster_recovery_report_file)
    output_file = _resolve_path(project_root, args.output_file)

    try:
        report = run_check(
            label=str(args.label),
            project_root=project_root,
            policy_file=policy_file,
            required_reports=required_reports,
            rollback_report_file=rollback_report_file,
            disaster_recovery_report_file=disaster_recovery_report_file,
            required_label=str(args.required_label),
            output_file=output_file,
            allow_missing_reports=bool(args.allow_missing_reports),
            allow_not_ready=bool(args.allow_not_ready),
        )
    except Exception as exc:
        print(f"[release-gate-deploy-rehearsal-check] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
