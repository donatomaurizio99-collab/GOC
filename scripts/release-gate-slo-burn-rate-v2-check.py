from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_REQUIRED_REPORTS = [
    "artifacts/failure-budget-dashboard-release-gate.json",
    "artifacts/release-gate-staging-soak-readiness-release-gate.json",
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


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _criterion(name: str, passed: bool, details: str) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _extract_worst_burn_rate_percent(payload: dict[str, Any]) -> float:
    metrics = payload.get("metrics")
    if isinstance(metrics, dict):
        direct = metrics.get("error_budget_burn_rate_percent")
        if direct is not None:
            return max(0.0, _coerce_float(direct, 0.0))
    reports = payload.get("reports")
    if isinstance(reports, list):
        observed: list[float] = []
        for item in reports:
            if not isinstance(item, dict):
                continue
            item_metrics = item.get("metrics")
            if not isinstance(item_metrics, dict):
                continue
            value = item_metrics.get("error_budget_burn_rate_percent")
            if value is None:
                continue
            observed.append(max(0.0, _coerce_float(value, 0.0)))
        if observed:
            return max(observed)
    return 0.0


def run_check(
    *,
    label: str,
    project_root: Path,
    policy_file: Path,
    required_reports: list[str],
    required_label: str,
    output_file: Path,
    allow_missing_reports: bool,
    allow_not_ready: bool,
) -> dict[str, Any]:
    started = time.perf_counter()
    _expect(required_reports, "At least one required report must be configured.")

    policy = _read_json_object(policy_file)
    windows_raw = policy.get("burn_rate_windows")
    windows = windows_raw if isinstance(windows_raw, list) else []
    _expect(windows, f"Policy must define non-empty burn_rate_windows: {policy_file}")

    normalized_windows: list[dict[str, Any]] = []
    policy_invalid_entries = 0
    for idx, item in enumerate(windows):
        if not isinstance(item, dict):
            policy_invalid_entries += 1
            continue
        name = str(item.get("name") or "").strip() or f"window-{idx + 1}"
        max_burn_rate_percent = _coerce_float(item.get("max_burn_rate_percent"), -1.0)
        max_non_ok_seconds = int(_coerce_float(item.get("max_non_ok_seconds"), -1.0))
        if max_burn_rate_percent < 0 or max_non_ok_seconds < 0:
            policy_invalid_entries += 1
            continue
        normalized_windows.append(
            {
                "name": name,
                "max_burn_rate_percent": float(max_burn_rate_percent),
                "max_non_ok_seconds": int(max_non_ok_seconds),
            }
        )

    _expect(normalized_windows, f"Policy windows invalid: {policy_file}")

    resolved_required_reports = [_resolve_path(project_root, value) for value in required_reports]
    missing_reports: list[str] = []
    report_records: list[dict[str, Any]] = []
    non_green_reports: list[dict[str, Any]] = []
    label_mismatch_reports: list[dict[str, Any]] = []
    observed_burn_rates: list[float] = []
    observed_non_ok_seconds: list[int] = []

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

        worst_burn_rate_percent = _extract_worst_burn_rate_percent(payload)
        decision_release_blocked = bool(((payload.get("decision") or {}).get("release_blocked")))
        non_ok_seconds = 0 if not decision_release_blocked else 3600
        observed_burn_rates.append(worst_burn_rate_percent)
        observed_non_ok_seconds.append(non_ok_seconds)

        record = {
            "path": str(report_path),
            "label": report_label,
            "success": bool(success_value) if has_success_flag else None,
            "has_success_flag": bool(has_success_flag),
            "label_matches_required": bool(label_matches),
            "worst_burn_rate_percent": float(worst_burn_rate_percent),
            "non_ok_seconds": int(non_ok_seconds),
        }
        report_records.append(record)
        if is_non_green:
            non_green_reports.append(record)
        if not label_matches:
            label_mismatch_reports.append(record)

    worst_burn_rate_percent = max(observed_burn_rates) if observed_burn_rates else 0.0
    worst_non_ok_seconds = max(observed_non_ok_seconds) if observed_non_ok_seconds else 0

    burn_rate_violations = []
    non_ok_window_violations = []
    for window in normalized_windows:
        if worst_burn_rate_percent > float(window["max_burn_rate_percent"]):
            burn_rate_violations.append(
                {
                    "window": window["name"],
                    "observed_burn_rate_percent": worst_burn_rate_percent,
                    "max_burn_rate_percent": float(window["max_burn_rate_percent"]),
                }
            )
        if worst_non_ok_seconds > int(window["max_non_ok_seconds"]):
            non_ok_window_violations.append(
                {
                    "window": window["name"],
                    "observed_non_ok_seconds": int(worst_non_ok_seconds),
                    "max_non_ok_seconds": int(window["max_non_ok_seconds"]),
                }
            )

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
            "burn_rate_budget",
            len(burn_rate_violations) == 0,
            f"burn_rate_violations={len(burn_rate_violations)}",
        ),
        _criterion(
            "non_ok_window_budget",
            len(non_ok_window_violations) == 0,
            f"non_ok_window_violations={len(non_ok_window_violations)}",
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
            "burn_rate_windows": normalized_windows,
            "policy_invalid_entries": int(policy_invalid_entries),
        },
        "metrics": {
            "required_reports_total": len(required_reports),
            "required_reports_present": len(report_records),
            "required_reports_missing": len(missing_reports),
            "slo_burn_rate_non_green": len(non_green_reports),
            "label_mismatch_reports": len(label_mismatch_reports),
            "worst_burn_rate_percent": float(round(worst_burn_rate_percent, 3)),
            "worst_non_ok_seconds": int(worst_non_ok_seconds),
            "burn_rate_violations": len(burn_rate_violations),
            "non_ok_window_violations": len(non_ok_window_violations),
            "criteria_failed": len(failed_criteria),
        },
        "decision": {
            "release_blocked": not bool(success),
            "recommended_action": "block_release" if not success else "proceed",
        },
        "criteria": criteria,
        "failed_criteria": failed_criteria,
        "reports": report_records,
        "missing_reports": missing_reports,
        "non_green_reports": non_green_reports,
        "label_mismatch_reports": label_mismatch_reports,
        "burn_rate_violations": burn_rate_violations,
        "non_ok_window_violations": non_ok_window_violations,
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2), encoding="utf-8")

    if not success and not allow_not_ready:
        raise RuntimeError(f"Release-gate SLO burn-rate v2 check failed: {json.dumps(report, sort_keys=True)}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate multi-window SLO burn-rate budgets (5m/1h/6h style) and emit deterministic release gate signal."
        )
    )
    parser.add_argument("--label", default="release-gate-slo-burn-rate-v2-check")
    parser.add_argument("--project-root")
    parser.add_argument("--policy-file", default="docs/release-gate-slo-burn-rate-v2-policy.json")
    parser.add_argument("--required-reports", default=",".join(DEFAULT_REQUIRED_REPORTS))
    parser.add_argument("--required-label", default="release-gate")
    parser.add_argument("--output-file", default="artifacts/release-gate-slo-burn-rate-v2-release-gate.json")
    parser.add_argument("--allow-missing-reports", action="store_true")
    parser.add_argument("--allow-not-ready", action="store_true")
    args = parser.parse_args(argv)

    inferred_project_root = Path(__file__).resolve().parents[1]
    project_root = _resolve_path(inferred_project_root, args.project_root) if args.project_root else inferred_project_root
    policy_file = _resolve_path(project_root, args.policy_file)
    required_reports = _parse_csv_list(args.required_reports)
    output_file = _resolve_path(project_root, args.output_file)

    try:
        report = run_check(
            label=str(args.label),
            project_root=project_root,
            policy_file=policy_file,
            required_reports=required_reports,
            required_label=str(args.required_label),
            output_file=output_file,
            allow_missing_reports=bool(args.allow_missing_reports),
            allow_not_ready=bool(args.allow_not_ready),
        )
    except Exception as exc:
        print(f"[release-gate-slo-burn-rate-v2-check] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
