from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_REQUIRED_REPORTS = [
    "artifacts/release-gate-post-release-continuity-release-gate.json",
    "artifacts/release-gate-steady-state-certification-release-gate.json",
    "artifacts/release-gate-post-release-watch-release-gate.json",
    "artifacts/p0-burnin-consecutive-green-release-gate.json",
    "artifacts/p0-closure-report-release-gate.json",
    "artifacts/release-gate-production-final-attestation-release-gate.json",
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
    post_release_continuity_report_file: Path,
    steady_state_report_file: Path,
    production_final_report_file: Path,
    burnin_report_file: Path,
    closure_report_file: Path,
    required_label: str,
    output_file: Path,
    allow_missing_reports: bool,
    allow_not_ready: bool,
) -> dict[str, Any]:
    started = time.perf_counter()
    _expect(required_reports, "At least one required report must be configured.")

    policy = _read_json_object(policy_file)
    required_signals_raw = policy.get("required_sustainability_signals")
    required_signals = [str(item).strip() for item in required_signals_raw] if isinstance(required_signals_raw, list) else []
    required_signals = [item for item in required_signals if item]
    min_required_green_reports = max(1, _coerce_int(policy.get("min_required_green_reports"), len(required_reports)))
    runbook_section = str(policy.get("runbook_section") or "").strip()

    policy_errors: list[str] = []
    if not required_signals:
        policy_errors.append("required_sustainability_signals_missing")
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

    continuity_payload = (
        _read_json_object(post_release_continuity_report_file)
        if post_release_continuity_report_file.exists()
        else {}
    )
    steady_state_payload = _read_json_object(steady_state_report_file) if steady_state_report_file.exists() else {}
    production_final_payload = (
        _read_json_object(production_final_report_file) if production_final_report_file.exists() else {}
    )
    burnin_payload = _read_json_object(burnin_report_file) if burnin_report_file.exists() else {}
    closure_payload = _read_json_object(closure_report_file) if closure_report_file.exists() else {}

    continuity_action = str((continuity_payload.get("decision") or {}).get("recommended_action") or "")
    steady_state_action = str((steady_state_payload.get("decision") or {}).get("recommended_action") or "")
    production_final_action = str((production_final_payload.get("decision") or {}).get("recommended_action") or "")

    continuity_signal_ok = continuity_action in {"proceed_to_stage_aj", "production_ready_sustained"}
    steady_state_signal_ok = steady_state_action in {"production_ready_steady_state", "production_ready_finalized"}
    production_final_signal_ok = production_final_action in {
        "production_ready",
        "production_ready_finalized",
        "proceed_to_stage_ac",
    }

    burnin_metrics = burnin_payload.get("metrics") if isinstance(burnin_payload.get("metrics"), dict) else {}
    burnin_consecutive_green = _coerce_int(burnin_metrics.get("consecutive_green"), 0)
    burnin_required_consecutive = max(1, _coerce_int(burnin_metrics.get("required_consecutive"), 1))
    burnin_threshold_ok = bool(burnin_payload.get("success") is True) and (
        burnin_consecutive_green >= burnin_required_consecutive
    )

    closure_release_blocked = bool((closure_payload.get("decision") or {}).get("release_blocked"))
    closure_signal_ok = bool(closure_payload.get("success") is True) and (not closure_release_blocked)

    signal_status = {
        "post_release_continuity": bool(continuity_signal_ok),
        "steady_state_certification": bool(steady_state_signal_ok),
        "production_final_attestation": bool(production_final_signal_ok),
        "burnin_consecutive_green": bool(burnin_threshold_ok),
        "p0_closure_go_no_go": bool(closure_signal_ok),
    }

    unknown_required_signals = [name for name in required_signals if name not in signal_status]
    if unknown_required_signals:
        policy_errors.append("required_sustainability_signals_unknown")
    failed_required_signals = [name for name in required_signals if signal_status.get(name) is False]

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
            "production_sustainability_policy_valid",
            len(policy_errors) == 0,
            f"policy_errors={len(policy_errors)}",
        ),
        _criterion(
            "production_sustainability_required_signal_coverage",
            len(failed_required_signals) == 0 and not unknown_required_signals,
            (
                f"failed_required_signals={len(failed_required_signals)}, "
                f"unknown_required_signals={len(unknown_required_signals)}"
            ),
        ),
        _criterion(
            "production_sustainability_green_report_quorum",
            bool(required_green_reports_met),
            (
                f"green_reports_count={green_reports_count}, "
                f"min_required_green_reports={min_required_green_reports}"
            ),
        ),
        _criterion(
            "production_sustainability_continuity_signal",
            bool(continuity_signal_ok),
            f"recommended_action={continuity_action!r}",
        ),
        _criterion(
            "production_sustainability_steady_state_signal",
            bool(steady_state_signal_ok),
            f"recommended_action={steady_state_action!r}",
        ),
        _criterion(
            "production_sustainability_production_final_signal",
            bool(production_final_signal_ok),
            f"recommended_action={production_final_action!r}",
        ),
        _criterion(
            "production_sustainability_burnin_threshold",
            bool(burnin_threshold_ok),
            (
                f"consecutive_green={burnin_consecutive_green}, "
                f"required_consecutive={burnin_required_consecutive}"
            ),
        ),
        _criterion(
            "production_sustainability_closure_signal",
            bool(closure_signal_ok),
            f"closure_release_blocked={closure_release_blocked}",
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
            "post_release_continuity_report_file": str(post_release_continuity_report_file),
            "steady_state_report_file": str(steady_state_report_file),
            "production_final_report_file": str(production_final_report_file),
            "burnin_report_file": str(burnin_report_file),
            "closure_report_file": str(closure_report_file),
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
            "required_sustainability_signals": required_signals,
            "min_required_green_reports": int(min_required_green_reports),
            "runbook_section": runbook_section,
        },
        "metrics": {
            "required_reports_total": len(required_reports),
            "required_reports_present": len(report_records),
            "required_reports_missing": len(missing_reports),
            "production_sustainability_reports_non_green": len(non_green_reports),
            "label_mismatch_reports": len(label_mismatch_reports),
            "production_sustainability_release_block_signals": len(release_block_signal_reports),
            "production_sustainability_policy_invalid": len(policy_errors),
            "production_sustainability_required_signals_total": len(required_signals),
            "production_sustainability_required_signals_failed": len(failed_required_signals) + len(unknown_required_signals),
            "production_sustainability_green_reports_count": int(green_reports_count),
            "production_sustainability_required_green_reports_failed": 0 if required_green_reports_met else 1,
            "production_sustainability_continuity_signal_failed": 0 if continuity_signal_ok else 1,
            "production_sustainability_steady_state_signal_failed": 0 if steady_state_signal_ok else 1,
            "production_sustainability_production_final_signal_failed": 0 if production_final_signal_ok else 1,
            "production_sustainability_burnin_threshold_failed": 0 if burnin_threshold_ok else 1,
            "production_sustainability_closure_signal_failed": 0 if closure_signal_ok else 1,
            "criteria_failed": len(failed_criteria),
        },
        "decision": {
            "release_blocked": not bool(success),
            "recommended_action": "block_release" if not success else "production_ready_sustained",
        },
        "criteria": criteria,
        "failed_criteria": failed_criteria,
        "signal_status": signal_status,
        "failed_required_signals": failed_required_signals,
        "unknown_required_signals": unknown_required_signals,
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
        raise RuntimeError(
            "Release-gate production sustainability certification check failed: "
            f"{json.dumps(report, sort_keys=True)}"
        )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate sustained production-ready certification from post-release continuity + "
            "steady-state + burn-in + closure evidence."
        )
    )
    parser.add_argument("--label", default="release-gate-production-sustainability-certification-check")
    parser.add_argument("--project-root")
    parser.add_argument("--policy-file", default="docs/release-gate-production-sustainability-certification-policy.json")
    parser.add_argument("--required-reports", default=",".join(DEFAULT_REQUIRED_REPORTS))
    parser.add_argument(
        "--post-release-continuity-report-file",
        default="artifacts/release-gate-post-release-continuity-release-gate.json",
    )
    parser.add_argument(
        "--steady-state-report-file",
        default="artifacts/release-gate-steady-state-certification-release-gate.json",
    )
    parser.add_argument(
        "--production-final-report-file",
        default="artifacts/release-gate-production-final-attestation-release-gate.json",
    )
    parser.add_argument(
        "--burnin-report-file",
        default="artifacts/p0-burnin-consecutive-green-release-gate.json",
    )
    parser.add_argument(
        "--closure-report-file",
        default="artifacts/p0-closure-report-release-gate.json",
    )
    parser.add_argument("--required-label", default="release-gate")
    parser.add_argument(
        "--output-file",
        default="artifacts/release-gate-production-sustainability-certification-release-gate.json",
    )
    parser.add_argument("--allow-missing-reports", action="store_true")
    parser.add_argument("--allow-not-ready", action="store_true")
    args = parser.parse_args(argv)

    inferred_project_root = PROJECT_ROOT
    project_root = _resolve_path(inferred_project_root, args.project_root) if args.project_root else inferred_project_root
    policy_file = _resolve_path(project_root, args.policy_file)
    required_reports = _parse_csv_list(args.required_reports)
    post_release_continuity_report_file = _resolve_path(project_root, args.post_release_continuity_report_file)
    steady_state_report_file = _resolve_path(project_root, args.steady_state_report_file)
    production_final_report_file = _resolve_path(project_root, args.production_final_report_file)
    burnin_report_file = _resolve_path(project_root, args.burnin_report_file)
    closure_report_file = _resolve_path(project_root, args.closure_report_file)
    output_file = _resolve_path(project_root, args.output_file)

    try:
        report = run_check(
            label=str(args.label),
            project_root=project_root,
            policy_file=policy_file,
            required_reports=required_reports,
            post_release_continuity_report_file=post_release_continuity_report_file,
            steady_state_report_file=steady_state_report_file,
            production_final_report_file=production_final_report_file,
            burnin_report_file=burnin_report_file,
            closure_report_file=closure_report_file,
            required_label=str(args.required_label),
            output_file=output_file,
            allow_missing_reports=bool(args.allow_missing_reports),
            allow_not_ready=bool(args.allow_not_ready),
        )
    except Exception as exc:
        print(f"[release-gate-production-sustainability-certification-check] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
