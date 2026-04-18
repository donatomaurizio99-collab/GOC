from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_REQUIRED_REPORTS = [
    "artifacts/release-gate-post-cutover-finalization-release-gate.json",
    "artifacts/release-gate-slo-burn-rate-v2-release-gate.json",
    "artifacts/release-gate-chaos-matrix-continuous-release-gate.json",
    "artifacts/release-gate-operations-handoff-readiness-release-gate.json",
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
    finalization_report_file: Path,
    burn_rate_report_file: Path,
    chaos_report_file: Path,
    required_label: str,
    output_file: Path,
    allow_missing_reports: bool,
    allow_not_ready: bool,
) -> dict[str, Any]:
    started = time.perf_counter()
    _expect(required_reports, "At least one required report must be configured.")

    policy = _read_json_object(policy_file)
    required_watch_windows_raw = policy.get("required_watch_windows")
    required_watch_windows = (
        [str(item).strip() for item in required_watch_windows_raw]
        if isinstance(required_watch_windows_raw, list)
        else []
    )
    required_watch_windows = [item for item in required_watch_windows if item]
    max_non_ok_window_violations = max(0, _coerce_int(policy.get("max_non_ok_window_violations"), 0))
    max_chaos_regression_violations = max(0, _coerce_int(policy.get("max_chaos_regression_violations"), 0))
    min_required_green_reports = max(1, _coerce_int(policy.get("min_required_green_reports"), len(required_reports)))
    runbook_section = str(policy.get("runbook_section") or "").strip()

    policy_errors: list[str] = []
    if not required_watch_windows:
        policy_errors.append("required_watch_windows_missing")
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

    finalization_payload = _read_json_object(finalization_report_file) if finalization_report_file.exists() else {}
    burn_rate_payload = _read_json_object(burn_rate_report_file) if burn_rate_report_file.exists() else {}
    chaos_payload = _read_json_object(chaos_report_file) if chaos_report_file.exists() else {}

    finalization_action = str((finalization_payload.get("decision") or {}).get("recommended_action") or "")
    finalization_signal_ok = finalization_action in {"production_ready", "production_ready_finalized"}
    observed_non_ok_window_violations = _coerce_int((burn_rate_payload.get("metrics") or {}).get("non_ok_window_violations"), 0)
    observed_chaos_regression_violations = _coerce_int(
        (chaos_payload.get("metrics") or {}).get("chaos_regression_violations"),
        0,
    )

    non_ok_window_budget_ok = observed_non_ok_window_violations <= max_non_ok_window_violations
    chaos_regression_budget_ok = observed_chaos_regression_violations <= max_chaos_regression_violations
    green_reports_count = len(report_records) - len(non_green_reports)
    required_green_reports_met = int(green_reports_count) >= int(min_required_green_reports)

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
            "post_release_watch_policy_valid",
            len(policy_errors) == 0,
            f"policy_errors={len(policy_errors)}",
        ),
        _criterion(
            "post_release_watch_finalization_signal",
            bool(finalization_signal_ok),
            f"recommended_action={finalization_action!r}",
        ),
        _criterion(
            "post_release_watch_non_ok_window_budget",
            bool(non_ok_window_budget_ok),
            (
                f"observed_non_ok_window_violations={observed_non_ok_window_violations}, "
                f"max_non_ok_window_violations={max_non_ok_window_violations}"
            ),
        ),
        _criterion(
            "post_release_watch_chaos_regression_budget",
            bool(chaos_regression_budget_ok),
            (
                f"observed_chaos_regression_violations={observed_chaos_regression_violations}, "
                f"max_chaos_regression_violations={max_chaos_regression_violations}"
            ),
        ),
        _criterion(
            "post_release_watch_green_report_quorum",
            bool(required_green_reports_met),
            (
                f"green_reports_count={green_reports_count}, "
                f"min_required_green_reports={min_required_green_reports}"
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
            "finalization_report_file": str(finalization_report_file),
            "burn_rate_report_file": str(burn_rate_report_file),
            "chaos_report_file": str(chaos_report_file),
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
            "required_watch_windows": required_watch_windows,
            "max_non_ok_window_violations": int(max_non_ok_window_violations),
            "max_chaos_regression_violations": int(max_chaos_regression_violations),
            "min_required_green_reports": int(min_required_green_reports),
            "runbook_section": runbook_section,
        },
        "metrics": {
            "required_reports_total": len(required_reports),
            "required_reports_present": len(report_records),
            "required_reports_missing": len(missing_reports),
            "post_release_watch_reports_non_green": len(non_green_reports),
            "label_mismatch_reports": len(label_mismatch_reports),
            "post_release_watch_release_block_signals": len(release_block_signal_reports),
            "post_release_watch_policy_invalid": len(policy_errors),
            "post_release_watch_windows_total": len(required_watch_windows),
            "post_release_watch_green_reports_count": int(green_reports_count),
            "post_release_watch_required_green_reports_failed": 0 if required_green_reports_met else 1,
            "post_release_watch_finalization_signal_failed": 0 if finalization_signal_ok else 1,
            "post_release_watch_non_ok_window_violations": 0 if non_ok_window_budget_ok else 1,
            "post_release_watch_chaos_regression_violations": 0 if chaos_regression_budget_ok else 1,
            "post_release_watch_observed_non_ok_window_violations": int(observed_non_ok_window_violations),
            "post_release_watch_observed_chaos_regression_violations": int(observed_chaos_regression_violations),
            "criteria_failed": len(failed_criteria),
        },
        "decision": {
            "release_blocked": not bool(success),
            "recommended_action": "block_release" if not success else "proceed_to_stage_ah",
        },
        "criteria": criteria,
        "failed_criteria": failed_criteria,
        "reports": report_records,
        "missing_reports": missing_reports,
        "non_green_reports": non_green_reports,
        "label_mismatch_reports": label_mismatch_reports,
        "reports_with_release_block_signal": release_block_signal_reports,
        "policy_errors": policy_errors,
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2), encoding="utf-8")

    if not success and not allow_not_ready:
        raise RuntimeError(f"Release-gate post-release watch check failed: {json.dumps(report, sort_keys=True)}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate post-release watch stability contract from post-cutover/failure-budget/chaos chain "
            "before steady-state certification."
        )
    )
    parser.add_argument("--label", default="release-gate-post-release-watch-check")
    parser.add_argument("--project-root")
    parser.add_argument("--policy-file", default="docs/release-gate-post-release-watch-policy.json")
    parser.add_argument("--required-reports", default=",".join(DEFAULT_REQUIRED_REPORTS))
    parser.add_argument(
        "--finalization-report-file",
        default="artifacts/release-gate-post-cutover-finalization-release-gate.json",
    )
    parser.add_argument(
        "--burn-rate-report-file",
        default="artifacts/release-gate-slo-burn-rate-v2-release-gate.json",
    )
    parser.add_argument(
        "--chaos-report-file",
        default="artifacts/release-gate-chaos-matrix-continuous-release-gate.json",
    )
    parser.add_argument("--required-label", default="release-gate")
    parser.add_argument("--output-file", default="artifacts/release-gate-post-release-watch-release-gate.json")
    parser.add_argument("--allow-missing-reports", action="store_true")
    parser.add_argument("--allow-not-ready", action="store_true")
    args = parser.parse_args(argv)

    inferred_project_root = PROJECT_ROOT
    project_root = _resolve_path(inferred_project_root, args.project_root) if args.project_root else inferred_project_root
    policy_file = _resolve_path(project_root, args.policy_file)
    required_reports = _parse_csv_list(args.required_reports)
    finalization_report_file = _resolve_path(project_root, args.finalization_report_file)
    burn_rate_report_file = _resolve_path(project_root, args.burn_rate_report_file)
    chaos_report_file = _resolve_path(project_root, args.chaos_report_file)
    output_file = _resolve_path(project_root, args.output_file)

    try:
        report = run_check(
            label=str(args.label),
            project_root=project_root,
            policy_file=policy_file,
            required_reports=required_reports,
            finalization_report_file=finalization_report_file,
            burn_rate_report_file=burn_rate_report_file,
            chaos_report_file=chaos_report_file,
            required_label=str(args.required_label),
            output_file=output_file,
            allow_missing_reports=bool(args.allow_missing_reports),
            allow_not_ready=bool(args.allow_not_ready),
        )
    except Exception as exc:
        print(f"[release-gate-post-release-watch-check] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
