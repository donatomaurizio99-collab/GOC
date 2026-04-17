from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_REQUIRED_REPORTS = [
    "artifacts/release-gate-supply-chain-artifact-trust-release-gate.json",
    "artifacts/release-gate-operations-handoff-readiness-release-gate.json",
    "artifacts/release-gate-evidence-hash-manifest-release-gate.json",
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
    manifest_file: Path,
    required_label: str,
    output_file: Path,
    allow_missing_reports: bool,
    allow_not_ready: bool,
) -> dict[str, Any]:
    started = time.perf_counter()
    _expect(required_reports, "At least one required report must be configured.")

    policy = _read_json_object(policy_file)
    required_manifest_entries_raw = policy.get("required_manifest_entries")
    required_manifest_entries = (
        [str(item).strip() for item in required_manifest_entries_raw]
        if isinstance(required_manifest_entries_raw, list)
        else []
    )
    required_manifest_entries = [item for item in required_manifest_entries if item]
    require_sha256 = bool(policy.get("require_sha256", True))
    max_missing_entries = max(0, _coerce_int(policy.get("max_missing_entries"), 0))
    max_unverified_entries = max(0, _coerce_int(policy.get("max_unverified_entries"), 0))
    _expect(required_manifest_entries, f"Policy must define required_manifest_entries: {policy_file}")

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

    manifest_missing_entries: list[str] = []
    unverified_entries: list[str] = []
    if manifest_file.exists():
        manifest_payload = _read_json_object(manifest_file)
        raw_manifest_files = manifest_payload.get("files")
        manifest_files = raw_manifest_files if isinstance(raw_manifest_files, list) else []
        manifest_by_name: dict[str, dict[str, Any]] = {}
        for entry in manifest_files:
            if not isinstance(entry, dict):
                continue
            path_value = str(entry.get("path") or "")
            if not path_value:
                continue
            file_name = Path(path_value.replace("\\", "/")).name
            if file_name:
                manifest_by_name[file_name] = entry
        for required_entry in required_manifest_entries:
            required_name = Path(required_entry).name
            manifest_entry = manifest_by_name.get(required_name)
            if manifest_entry is None:
                manifest_missing_entries.append(required_entry)
                continue
            if require_sha256 and not str(manifest_entry.get("sha256") or "").strip():
                unverified_entries.append(required_entry)
    else:
        manifest_missing_entries = list(required_manifest_entries)

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
            "evidence_attestation_manifest_coverage",
            len(manifest_missing_entries) <= max_missing_entries,
            f"manifest_missing_entries={len(manifest_missing_entries)}, max_allowed={max_missing_entries}",
        ),
        _criterion(
            "evidence_attestation_sha256_verified",
            len(unverified_entries) <= max_unverified_entries,
            f"unverified_entries={len(unverified_entries)}, max_allowed={max_unverified_entries}",
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
            "manifest_file": str(manifest_file),
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
            "required_manifest_entries": required_manifest_entries,
            "require_sha256": bool(require_sha256),
            "max_missing_entries": int(max_missing_entries),
            "max_unverified_entries": int(max_unverified_entries),
        },
        "metrics": {
            "required_reports_total": len(required_reports),
            "required_reports_present": len(report_records),
            "required_reports_missing": len(missing_reports),
            "evidence_attestation_reports_non_green": len(non_green_reports),
            "label_mismatch_reports": len(label_mismatch_reports),
            "evidence_attestation_missing_entries": len(manifest_missing_entries),
            "evidence_attestation_unverified_entries": len(unverified_entries),
            "criteria_failed": len(failed_criteria),
        },
        "decision": {
            "release_blocked": not bool(success),
            "recommended_action": "block_release" if not success else "proceed_to_stage_aa",
        },
        "criteria": criteria,
        "failed_criteria": failed_criteria,
        "reports": report_records,
        "missing_reports": missing_reports,
        "non_green_reports": non_green_reports,
        "label_mismatch_reports": label_mismatch_reports,
        "manifest_missing_entries": manifest_missing_entries,
        "unverified_entries": unverified_entries,
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2), encoding="utf-8")

    if not success and not allow_not_ready:
        raise RuntimeError(f"Release-gate evidence attestation check failed: {json.dumps(report, sort_keys=True)}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate Stage-U/V/W/X/Y manifest attestation coverage and digest integrity contract before final release-train decision."
        )
    )
    parser.add_argument("--label", default="release-gate-evidence-attestation-check")
    parser.add_argument("--project-root")
    parser.add_argument("--policy-file", default="docs/release-gate-evidence-attestation-policy.json")
    parser.add_argument("--required-reports", default=",".join(DEFAULT_REQUIRED_REPORTS))
    parser.add_argument("--manifest-file", default="artifacts/release-gate-evidence-manifest-release-gate.json")
    parser.add_argument("--required-label", default="release-gate")
    parser.add_argument("--output-file", default="artifacts/release-gate-evidence-attestation-release-gate.json")
    parser.add_argument("--allow-missing-reports", action="store_true")
    parser.add_argument("--allow-not-ready", action="store_true")
    args = parser.parse_args(argv)

    inferred_project_root = Path(__file__).resolve().parents[1]
    project_root = _resolve_path(inferred_project_root, args.project_root) if args.project_root else inferred_project_root
    policy_file = _resolve_path(project_root, args.policy_file)
    required_reports = _parse_csv_list(args.required_reports)
    manifest_file = _resolve_path(project_root, args.manifest_file)
    output_file = _resolve_path(project_root, args.output_file)

    try:
        report = run_check(
            label=str(args.label),
            project_root=project_root,
            policy_file=policy_file,
            required_reports=required_reports,
            manifest_file=manifest_file,
            required_label=str(args.required_label),
            output_file=output_file,
            allow_missing_reports=bool(args.allow_missing_reports),
            allow_not_ready=bool(args.allow_not_ready),
        )
    except Exception as exc:
        print(f"[release-gate-evidence-attestation-check] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
