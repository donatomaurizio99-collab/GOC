from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _resolve_path(project_root: Path, value: str) -> Path:
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


def _read_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    _expect(isinstance(payload, dict), f"Expected JSON object in {path}")
    return payload


def _parse_iso_utc(value: str) -> datetime | None:
    candidate = str(value or "").strip()
    if not candidate:
        return None
    try:
        parsed = datetime.strptime(candidate, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc)


def _age_hours(now_utc: datetime, then_utc: datetime) -> float:
    return max(0.0, (now_utc - then_utc).total_seconds() / 3600.0)


def run_check(
    *,
    label: str,
    project_root: Path,
    policy_file: Path,
    required_label: str,
    output_file: Path,
    allow_missing_reports: bool,
) -> dict[str, Any]:
    started = time.perf_counter()
    now_utc = datetime.now(timezone.utc)
    policy = _read_json_object(policy_file)
    required_reports_raw = policy.get("required_reports")
    _expect(
        isinstance(required_reports_raw, list) and required_reports_raw,
        f"Policy file requires non-empty 'required_reports': {policy_file}",
    )
    max_report_age_hours = float(policy.get("max_report_age_hours", 24.0))
    _expect(max_report_age_hours > 0.0, "Policy key max_report_age_hours must be > 0.")

    required_paths = [_resolve_path(project_root, str(item)) for item in required_reports_raw]
    missing_reports: list[str] = []
    non_green_reports: list[dict[str, Any]] = []
    label_mismatch_reports: list[dict[str, Any]] = []
    stale_reports: list[dict[str, Any]] = []
    reports: list[dict[str, Any]] = []

    for report_path in required_paths:
        if not report_path.exists():
            missing_reports.append(str(report_path))
            continue
        payload = _read_json_object(report_path)
        success_value = payload.get("success")
        has_success = isinstance(success_value, bool)
        report_label = str(payload.get("label") or "")
        label_matches = (not required_label) or (report_label == required_label)
        generated_at_utc_value = str(payload.get("generated_at_utc") or "")
        generated_at_utc = _parse_iso_utc(generated_at_utc_value)
        if generated_at_utc is None:
            generated_at_utc = datetime.fromtimestamp(report_path.stat().st_mtime, tz=timezone.utc)
            generated_at_utc_value = generated_at_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

        age_hours = _age_hours(now_utc, generated_at_utc)
        stale = age_hours > max_report_age_hours
        non_green = has_success and bool(success_value is False)

        report_entry = {
            "path": str(report_path),
            "label": report_label,
            "has_success_flag": bool(has_success),
            "success": bool(success_value) if has_success else None,
            "label_matches_required": bool(label_matches),
            "generated_at_utc": generated_at_utc_value,
            "age_hours": round(age_hours, 3),
            "stale": bool(stale),
        }
        reports.append(report_entry)

        if non_green:
            non_green_reports.append(report_entry)
        if not label_matches:
            label_mismatch_reports.append(report_entry)
        if stale:
            stale_reports.append(report_entry)

    success = (
        (allow_missing_reports or not missing_reports)
        and not non_green_reports
        and not label_mismatch_reports
        and not stale_reports
    )
    report = {
        "label": label,
        "success": bool(success),
        "paths": {
            "project_root": str(project_root),
            "policy_file": str(policy_file),
            "output_file": str(output_file),
        },
        "config": {
            "required_label": required_label,
            "allow_missing_reports": bool(allow_missing_reports),
            "max_report_age_hours": max_report_age_hours,
            "required_reports": [str(path) for path in required_paths],
        },
        "metrics": {
            "required_reports_total": len(required_paths),
            "reports_present": len(reports),
            "required_reports_missing": len(missing_reports),
            "non_green_reports": len(non_green_reports),
            "label_mismatch_reports": len(label_mismatch_reports),
            "stale_reports": len(stale_reports),
        },
        "decision": {
            "release_blocked": not bool(success),
            "recommended_action": "block_release" if not success else "proceed",
        },
        "reports": reports,
        "missing_reports": missing_reports,
        "non_green_reports": non_green_reports,
        "label_mismatch_reports": label_mismatch_reports,
        "stale_reports": stale_reports,
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(
        json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2),
        encoding="utf-8",
    )

    if not success:
        raise RuntimeError(f"Release-gate evidence freshness check failed: {json.dumps(report, sort_keys=True)}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate freshness and green status of required release-gate evidence reports."
        )
    )
    parser.add_argument("--label", default="release-gate-evidence-freshness-check")
    parser.add_argument("--project-root")
    parser.add_argument("--policy-file", default="docs/release-gate-evidence-freshness-policy.json")
    parser.add_argument("--required-label", default="release-gate")
    parser.add_argument("--output-file", default="artifacts/release-gate-evidence-freshness-release-gate.json")
    parser.add_argument("--allow-missing-reports", action="store_true")
    args = parser.parse_args(argv)

    inferred_project_root = Path(__file__).resolve().parents[1]
    project_root = _resolve_path(inferred_project_root, args.project_root) if args.project_root else inferred_project_root
    policy_file = _resolve_path(project_root, args.policy_file)
    output_file = _resolve_path(project_root, args.output_file)

    try:
        report = run_check(
            label=str(args.label),
            project_root=project_root,
            policy_file=policy_file,
            required_label=str(args.required_label),
            output_file=output_file,
            allow_missing_reports=bool(args.allow_missing_reports),
        )
    except Exception as exc:
        print(f"[release-gate-evidence-freshness-check] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
