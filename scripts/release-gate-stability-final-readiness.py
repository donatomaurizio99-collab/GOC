from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]


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


def run_check(
    *,
    label: str,
    project_root: Path,
    required_reports: list[str],
    required_label: str,
    output_file: Path,
    allow_missing_reports: bool,
) -> dict[str, Any]:
    started = time.perf_counter()
    _expect(required_reports, "At least one required report is required.")

    resolved_reports = [_resolve_path(project_root, value) for value in required_reports]
    missing_reports: list[str] = []
    non_green_reports: list[dict[str, Any]] = []
    label_mismatch_reports: list[dict[str, Any]] = []
    reports: list[dict[str, Any]] = []

    for report_path in resolved_reports:
        if not report_path.exists():
            missing_reports.append(str(report_path))
            continue
        payload = _read_json_object(report_path)
        report_label = str(payload.get("label") or "")
        success_value = payload.get("success")
        has_success = isinstance(success_value, bool)
        non_green = has_success and bool(success_value is False)
        label_matches = (not required_label) or (report_label == required_label)
        report_entry = {
            "path": str(report_path),
            "label": report_label,
            "has_success_flag": bool(has_success),
            "success": bool(success_value) if has_success else None,
            "label_matches_required": bool(label_matches),
        }
        reports.append(report_entry)
        if non_green or not has_success:
            non_green_reports.append(report_entry)
        if not label_matches:
            label_mismatch_reports.append(report_entry)

    criteria: list[dict[str, Any]] = [
        {
            "name": "required_reports_present",
            "passed": bool(allow_missing_reports or not missing_reports),
            "details": f"missing_reports={len(missing_reports)}",
        },
        {
            "name": "required_reports_green",
            "passed": not non_green_reports,
            "details": f"non_green_reports={len(non_green_reports)}",
        },
        {
            "name": "required_reports_label_match",
            "passed": not label_mismatch_reports,
            "details": f"label_mismatch_reports={len(label_mismatch_reports)}",
        },
    ]
    failed_criteria = [item for item in criteria if not bool(item.get("passed"))]

    success = not failed_criteria
    report = {
        "label": label,
        "success": bool(success),
        "paths": {
            "project_root": str(project_root),
            "output_file": str(output_file),
        },
        "config": {
            "required_reports": required_reports,
            "required_label": required_label,
            "allow_missing_reports": bool(allow_missing_reports),
        },
        "metrics": {
            "required_reports_total": len(required_reports),
            "reports_present": len(reports),
            "required_reports_missing": len(missing_reports),
            "required_reports_non_green": len(non_green_reports),
            "label_mismatch_reports": len(label_mismatch_reports),
            "criteria_failed": len(failed_criteria),
        },
        "decision": {
            "release_blocked": not bool(success),
            "recommended_action": "block_release" if not success else "proceed",
        },
        "criteria": criteria,
        "failed_criteria": failed_criteria,
        "reports": reports,
        "missing_reports": missing_reports,
        "non_green_reports": non_green_reports,
        "label_mismatch_reports": label_mismatch_reports,
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(
        json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2),
        encoding="utf-8",
    )

    if not success:
        raise RuntimeError(f"Release-gate stability final readiness check failed: {json.dumps(report, sort_keys=True)}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Final release-gate stability readiness gate across Stage L-P evidence artifacts."
        )
    )
    parser.add_argument("--label", default="release-gate-stability-final-readiness")
    parser.add_argument("--project-root")
    parser.add_argument("--required-reports", default="")
    parser.add_argument("--required-label", default="release-gate")
    parser.add_argument("--output-file", default="artifacts/release-gate-stability-final-readiness-release-gate.json")
    parser.add_argument("--allow-missing-reports", action="store_true")
    args = parser.parse_args(argv)

    inferred_project_root = Path(__file__).resolve().parents[1]
    project_root = _resolve_path(inferred_project_root, args.project_root) if args.project_root else inferred_project_root
    output_file = _resolve_path(project_root, args.output_file)
    required_reports = _parse_csv_list(args.required_reports)
    if not required_reports:
        print("[release-gate-stability-final-readiness] ERROR: --required-reports must not be empty.", file=sys.stderr)
        return 2

    try:
        report = run_check(
            label=str(args.label),
            project_root=project_root,
            required_reports=required_reports,
            required_label=str(args.required_label),
            output_file=output_file,
            allow_missing_reports=bool(args.allow_missing_reports),
        )
    except Exception as exc:
        print(f"[release-gate-stability-final-readiness] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
