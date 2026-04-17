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


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    _expect(isinstance(payload, dict), f"Expected JSON object in {path}")
    return payload


def _extract_metric_snippet(payload: dict[str, Any]) -> dict[str, Any]:
    metrics = payload.get("metrics")
    if isinstance(metrics, dict):
        keys = [
            "criteria_failed",
            "criteria_passed",
            "drills_failed",
            "drills_total",
            "duration_budget_exceeded",
            "consecutive_green",
            "required_consecutive",
            "error_budget_burn_rate_percent",
            "http_429_rate_percent",
            "error_rate_percent",
            "max_restore_duration_ms",
            "bounded_rows_lost",
        ]
        snippet = {key: metrics[key] for key in keys if key in metrics}
        if snippet:
            return snippet
    return {}


def build_dashboard(
    *,
    label: str,
    project_root: Path,
    runbook_file: Path,
    budget_report_files: list[str],
    allow_missing_reports: bool,
    output_file: Path,
) -> dict[str, Any]:
    started = time.perf_counter()
    _expect(runbook_file.exists(), f"Runbook file not found: {runbook_file}")
    _expect(budget_report_files, "At least one --budget-report-files entry is required.")

    resolved_paths = [_resolve_path(project_root, value) for value in budget_report_files]
    missing_reports: list[str] = []
    invalid_reports: list[dict[str, Any]] = []
    evaluated: list[dict[str, Any]] = []

    for path in resolved_paths:
        if not path.exists():
            missing_reports.append(str(path))
            continue
        try:
            payload = _read_json(path)
        except Exception as exc:
            invalid_reports.append({"path": str(path), "error": str(exc)})
            continue

        success_value = payload.get("success")
        has_success = isinstance(success_value, bool)
        report_success = bool(success_value) if has_success else False
        evaluated.append(
            {
                "path": str(path),
                "label": str(payload.get("label") or ""),
                "success": report_success if has_success else None,
                "has_success_flag": has_success,
                "decision": payload.get("decision") if isinstance(payload.get("decision"), dict) else {},
                "metrics": _extract_metric_snippet(payload),
            }
        )

    failed_reports = [item for item in evaluated if item.get("success") is False]
    reports_without_success_flag = [item for item in evaluated if item.get("success") is None]
    success = (
        (allow_missing_reports or not missing_reports)
        and not invalid_reports
        and not failed_reports
        and not reports_without_success_flag
    )
    report = {
        "label": label,
        "success": bool(success),
        "paths": {
            "project_root": str(project_root),
            "runbook_file": str(runbook_file),
            "output_file": str(output_file),
        },
        "config": {
            "budget_report_files": budget_report_files,
            "allow_missing_reports": bool(allow_missing_reports),
        },
        "metrics": {
            "reports_expected": len(budget_report_files),
            "reports_present": len(evaluated),
            "reports_missing": len(missing_reports),
            "reports_invalid": len(invalid_reports),
            "reports_failed": len(failed_reports),
            "reports_without_success_flag": len(reports_without_success_flag),
        },
        "decision": {
            "release_blocked": not bool(success),
            "recommended_action": "block_release" if not success else "proceed",
            "runbook_path": "docs/production-runbook.md#341-failure-budget-dashboard-and-release-blocker",
        },
        "reports": evaluated,
        "missing_reports": missing_reports,
        "invalid_reports": invalid_reports,
        "failed_reports": failed_reports,
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(
        json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    if not success:
        raise RuntimeError(f"Failure budget dashboard is red: {json.dumps(report, sort_keys=True)}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Aggregate critical release budget reports into one dashboard and deterministic "
            "release-blocker decision."
        )
    )
    parser.add_argument("--label", default="failure-budget-dashboard")
    parser.add_argument("--project-root")
    parser.add_argument("--runbook-file", default="docs/production-runbook.md")
    parser.add_argument(
        "--budget-report-files",
        default=(
            "artifacts/load-profile-framework-release-gate.json,"
            "artifacts/rto-rpo-assertion-release-gate.json,"
            "artifacts/canary-guardrails-release-gate.json,"
            "artifacts/auto-rollback-policy-release-gate.json,"
            "artifacts/p0-disaster-recovery-rehearsal-pack-release-gate.json"
        ),
    )
    parser.add_argument("--allow-missing-reports", action="store_true")
    parser.add_argument("--output-file", default="artifacts/failure-budget-dashboard-release-gate.json")
    args = parser.parse_args(argv)

    inferred_project_root = Path(__file__).resolve().parents[1]
    project_root = _resolve_path(inferred_project_root, args.project_root) if args.project_root else inferred_project_root
    runbook_file = _resolve_path(project_root, args.runbook_file)
    output_file = _resolve_path(project_root, args.output_file)
    budget_report_files = _parse_csv_list(args.budget_report_files)

    if not budget_report_files:
        print("[failure-budget-dashboard] ERROR: --budget-report-files must not be empty.", file=sys.stderr)
        return 2

    try:
        report = build_dashboard(
            label=str(args.label),
            project_root=project_root,
            runbook_file=runbook_file,
            budget_report_files=budget_report_files,
            allow_missing_reports=bool(args.allow_missing_reports),
            output_file=output_file,
        )
    except Exception as exc:
        print(f"[failure-budget-dashboard] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
