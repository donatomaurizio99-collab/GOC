from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_REQUIRED_REPORTS = [
    "artifacts/release-gate-stability-final-readiness-release-gate.json",
    "artifacts/release-gate-staging-soak-readiness-release-gate.json",
    "artifacts/release-gate-rc-canary-rollout-release-gate.json",
    "artifacts/p0-closure-report-release-gate.json",
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


def _parse_utc_timestamp(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None
    return dt.timestamp()


def _read_mtime_epoch(path: Path) -> float | None:
    try:
        return float(path.stat().st_mtime)
    except OSError:
        return None


def run_check(
    *,
    label: str,
    project_root: Path,
    required_reports: list[str],
    manifest_file: Path,
    required_label: str,
    max_report_timestamp_skew_seconds: int,
    output_file: Path,
    allow_missing_reports: bool,
    allow_not_ready: bool,
) -> dict[str, Any]:
    started = time.perf_counter()
    _expect(required_reports, "At least one required report must be configured.")
    _expect(max_report_timestamp_skew_seconds >= 0, "--max-report-timestamp-skew-seconds must be >= 0.")

    resolved_required_reports = [_resolve_path(project_root, value) for value in required_reports]

    missing_reports: list[str] = []
    report_records: list[dict[str, Any]] = []
    non_green_reports: list[dict[str, Any]] = []
    label_mismatch_reports: list[dict[str, Any]] = []
    invalid_timestamp_reports: list[dict[str, Any]] = []
    parsed_timestamps: list[float] = []
    report_timestamp_by_path: dict[str, float | None] = {}
    report_mtime_by_path: dict[str, float | None] = {}

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
        generated_at = payload.get("generated_at_utc")
        parsed_timestamp = _parse_utc_timestamp(generated_at)
        has_valid_timestamp = parsed_timestamp is not None
        report_mtime = _read_mtime_epoch(report_path)

        record = {
            "path": str(report_path),
            "label": report_label,
            "success": bool(success_value) if has_success_flag else None,
            "has_success_flag": bool(has_success_flag),
            "label_matches_required": bool(label_matches),
            "generated_at_utc": str(generated_at or ""),
            "has_valid_generated_at_utc": bool(has_valid_timestamp),
        }
        report_records.append(record)
        if is_non_green:
            non_green_reports.append(record)
        if not label_matches:
            label_mismatch_reports.append(record)
        if has_valid_timestamp:
            parsed_timestamps.append(float(parsed_timestamp))
        else:
            invalid_timestamp_reports.append(record)
        report_timestamp_by_path[str(report_path)] = float(parsed_timestamp) if has_valid_timestamp else None
        report_mtime_by_path[str(report_path)] = report_mtime

    manifest_present = manifest_file.exists()
    manifest_payload: dict[str, Any] = _read_json_object(manifest_file) if manifest_present else {}
    manifest_mtime_epoch = _read_mtime_epoch(manifest_file) if manifest_present else None
    manifest_entries_raw = manifest_payload.get("files")
    manifest_entries = manifest_entries_raw if isinstance(manifest_entries_raw, list) else []
    manifest_paths = {
        str(_resolve_path(project_root, str(item.get("path") or "")))
        for item in manifest_entries
        if isinstance(item, dict) and str(item.get("path") or "").strip()
    }
    manifest_generated_at_epoch = _parse_utc_timestamp(manifest_payload.get("generated_at_utc"))
    reports_expected_in_manifest: list[str] = []
    reports_generated_after_manifest: list[str] = []
    for report_path in resolved_required_reports:
        report_key = str(report_path)
        if report_key in missing_reports:
            continue
        report_timestamp = report_timestamp_by_path.get(report_key)
        report_generated_after_manifest = False
        if (
            manifest_generated_at_epoch is not None
            and report_timestamp is not None
            and float(report_timestamp) > float(manifest_generated_at_epoch)
        ):
            report_generated_after_manifest = True
        elif (
            manifest_generated_at_epoch is not None
            and report_timestamp is not None
            and float(report_timestamp) == float(manifest_generated_at_epoch)
        ):
            report_mtime = report_mtime_by_path.get(report_key)
            if (
                manifest_mtime_epoch is not None
                and report_mtime is not None
                and float(report_mtime) > float(manifest_mtime_epoch)
            ):
                report_generated_after_manifest = True

        if report_generated_after_manifest:
            reports_generated_after_manifest.append(report_key)
            continue
        reports_expected_in_manifest.append(report_key)

    manifest_missing_entries = [
        report_path for report_path in reports_expected_in_manifest if report_path not in manifest_paths
    ]

    timestamp_skew_seconds = 0.0
    if len(parsed_timestamps) >= 2:
        timestamp_skew_seconds = max(parsed_timestamps) - min(parsed_timestamps)

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
            "required_reports_have_valid_timestamp",
            len(invalid_timestamp_reports) == 0,
            f"invalid_timestamp_reports={len(invalid_timestamp_reports)}",
        ),
        _criterion(
            "report_timestamp_skew_within_budget",
            float(timestamp_skew_seconds) <= float(max_report_timestamp_skew_seconds),
            (
                f"observed={int(round(timestamp_skew_seconds))}, "
                f"max_allowed={int(max_report_timestamp_skew_seconds)}"
            ),
        ),
        _criterion(
            "manifest_present",
            bool(manifest_present),
            f"manifest_present={manifest_present}",
        ),
        _criterion(
            "manifest_covers_required_reports",
            len(manifest_missing_entries) == 0,
            f"manifest_missing_entries={len(manifest_missing_entries)}",
        ),
    ]

    failed_criteria = [item for item in criteria if not bool(item.get("passed"))]
    success = len(failed_criteria) == 0

    report = {
        "label": label,
        "success": bool(success),
        "paths": {
            "project_root": str(project_root),
            "manifest_file": str(manifest_file),
            "output_file": str(output_file),
        },
        "config": {
            "required_reports": required_reports,
            "required_label": required_label,
            "max_report_timestamp_skew_seconds": int(max_report_timestamp_skew_seconds),
            "allow_missing_reports": bool(allow_missing_reports),
            "allow_not_ready": bool(allow_not_ready),
        },
        "metrics": {
            "required_reports_total": len(required_reports),
            "required_reports_present": len(report_records),
            "required_reports_missing": len(missing_reports),
            "lineage_reports_non_green": len(non_green_reports),
            "label_mismatch_reports": len(label_mismatch_reports),
            "invalid_timestamp_reports": len(invalid_timestamp_reports),
            "manifest_missing_entries": len(manifest_missing_entries),
            "manifest_expected_reports": len(reports_expected_in_manifest),
            "reports_generated_after_manifest": len(reports_generated_after_manifest),
            "max_report_timestamp_skew_seconds": int(round(timestamp_skew_seconds)),
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
        "invalid_timestamp_reports": invalid_timestamp_reports,
        "manifest_missing_entries": manifest_missing_entries,
        "reports_expected_in_manifest": reports_expected_in_manifest,
        "reports_generated_after_manifest": reports_generated_after_manifest,
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2), encoding="utf-8")

    if not success and not allow_not_ready:
        raise RuntimeError(f"Release-gate evidence lineage check failed: {json.dumps(report, sort_keys=True)}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate cross-report evidence lineage coherence over Stage-P/Q/R outputs and the evidence hash manifest."
        )
    )
    parser.add_argument("--label", default="release-gate-evidence-lineage-check")
    parser.add_argument("--project-root")
    parser.add_argument("--required-reports", default=",".join(DEFAULT_REQUIRED_REPORTS))
    parser.add_argument("--manifest-file", default="artifacts/release-gate-evidence-manifest-release-gate.json")
    parser.add_argument("--required-label", default="release-gate")
    parser.add_argument("--max-report-timestamp-skew-seconds", type=int, default=900)
    parser.add_argument("--output-file", default="artifacts/release-gate-evidence-lineage-release-gate.json")
    parser.add_argument("--allow-missing-reports", action="store_true")
    parser.add_argument("--allow-not-ready", action="store_true")
    args = parser.parse_args(argv)

    inferred_project_root = Path(__file__).resolve().parents[1]
    project_root = _resolve_path(inferred_project_root, args.project_root) if args.project_root else inferred_project_root
    required_reports = _parse_csv_list(args.required_reports)
    manifest_file = _resolve_path(project_root, args.manifest_file)
    output_file = _resolve_path(project_root, args.output_file)

    try:
        report = run_check(
            label=str(args.label),
            project_root=project_root,
            required_reports=required_reports,
            manifest_file=manifest_file,
            required_label=str(args.required_label),
            max_report_timestamp_skew_seconds=int(args.max_report_timestamp_skew_seconds),
            output_file=output_file,
            allow_missing_reports=bool(args.allow_missing_reports),
            allow_not_ready=bool(args.allow_not_ready),
        )
    except Exception as exc:
        print(f"[release-gate-evidence-lineage-check] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
