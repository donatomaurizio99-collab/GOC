from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any


def _parse_csv_list(text: str) -> list[str]:
    return [item.strip() for item in str(text).split(",") if item.strip()]


def _resolve_path(project_root: Path, value: str) -> Path:
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"Expected JSON object in {path}")
    return payload


def _criterion(name: str, passed: bool, details: str) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def build_closure_report(
    *,
    label: str,
    required_consecutive: int,
    required_evidence_reports: list[str],
    evidence_bundle_file: Path,
    burnin_file: Path,
    runbook_contract_file: Path,
    output_file: Path,
) -> dict[str, Any]:
    started = time.perf_counter()
    criteria: list[dict[str, Any]] = []
    missing_files: list[str] = []
    missing_required_evidence_reports: list[str] = []
    non_green_required_evidence_reports: list[dict[str, Any]] = []
    evidence_label_mismatch_reports = 0

    def load_or_missing(path: Path) -> dict[str, Any] | None:
        if not path.exists():
            missing_files.append(str(path))
            return None
        return _read_json(path)

    evidence_payload = load_or_missing(evidence_bundle_file)
    burnin_payload = load_or_missing(burnin_file)
    runbook_payload = load_or_missing(runbook_contract_file)

    if evidence_payload is None:
        criteria.append(_criterion("evidence_bundle_present", False, "Missing evidence bundle report file"))
        missing_required_evidence_reports = list(required_evidence_reports)
    else:
        criteria.append(
            _criterion(
                "evidence_bundle_success",
                bool(evidence_payload.get("success") is True),
                f"success={evidence_payload.get('success')!r}",
            )
        )
        evidence_metrics = evidence_payload.get("metrics") or {}
        evidence_label_mismatch_reports = int(evidence_metrics.get("label_mismatch_reports") or 0)
        criteria.append(
            _criterion(
                "evidence_bundle_label_mismatch_zero",
                evidence_label_mismatch_reports == 0,
                f"label_mismatch_reports={evidence_label_mismatch_reports}",
            )
        )
        if required_evidence_reports:
            evidence_reports_raw = evidence_payload.get("reports")
            evidence_reports = evidence_reports_raw if isinstance(evidence_reports_raw, list) else []
            indexed_reports: dict[str, dict[str, Any]] = {}
            for entry in evidence_reports:
                if not isinstance(entry, dict):
                    continue
                report_path = str(entry.get("path") or "")
                if not report_path:
                    continue
                indexed_reports[report_path] = entry

            for required_path in required_evidence_reports:
                matched_entry = indexed_reports.get(required_path)
                if matched_entry is None:
                    missing_required_evidence_reports.append(required_path)
                    continue
                if matched_entry.get("success") is not True:
                    non_green_required_evidence_reports.append(
                        {
                            "path": required_path,
                            "success": matched_entry.get("success"),
                        }
                    )
            criteria.append(
                _criterion(
                    "evidence_bundle_required_reports_present",
                    len(missing_required_evidence_reports) == 0,
                    f"missing_required_reports={len(missing_required_evidence_reports)}",
                )
            )
            criteria.append(
                _criterion(
                    "evidence_bundle_required_reports_green",
                    len(non_green_required_evidence_reports) == 0,
                    f"non_green_required_reports={len(non_green_required_evidence_reports)}",
                )
            )

    if burnin_payload is None:
        criteria.append(_criterion("burnin_report_present", False, "Missing burn-in report file"))
    else:
        burnin_success = bool(burnin_payload.get("success") is True)
        metrics = burnin_payload.get("metrics") or {}
        consecutive_green = int(metrics.get("consecutive_green") or 0)
        criteria.append(_criterion("burnin_success", burnin_success, f"success={burnin_payload.get('success')!r}"))
        criteria.append(
            _criterion(
                "burnin_consecutive_threshold",
                consecutive_green >= required_consecutive,
                f"consecutive_green={consecutive_green}, required={required_consecutive}",
            )
        )

    if runbook_payload is None:
        criteria.append(_criterion("runbook_contract_present", False, "Missing runbook contract report file"))
    else:
        criteria.append(
            _criterion(
                "runbook_contract_success",
                bool(runbook_payload.get("success") is True),
                f"success={runbook_payload.get('success')!r}",
            )
        )

    failed_criteria = [item for item in criteria if not item["passed"]]
    success = len(failed_criteria) == 0
    report = {
        "label": label,
        "success": bool(success),
        "config": {
            "required_consecutive": int(required_consecutive),
            "required_evidence_reports": required_evidence_reports,
            "evidence_bundle_file": str(evidence_bundle_file),
            "burnin_file": str(burnin_file),
            "runbook_contract_file": str(runbook_contract_file),
        },
        "metrics": {
            "criteria_total": len(criteria),
            "criteria_passed": len(criteria) - len(failed_criteria),
            "criteria_failed": len(failed_criteria),
            "required_evidence_reports": len(required_evidence_reports),
            "required_evidence_reports_missing": len(missing_required_evidence_reports),
            "required_evidence_reports_non_green": len(non_green_required_evidence_reports),
            "evidence_label_mismatch_reports": evidence_label_mismatch_reports,
        },
        "criteria": criteria,
        "failed_criteria": failed_criteria,
        "missing_files": missing_files,
        "missing_required_evidence_reports": missing_required_evidence_reports,
        "non_green_required_evidence_reports": non_green_required_evidence_reports,
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2), encoding="utf-8")
    if not success:
        raise RuntimeError(f"P0 closure report is not green: {json.dumps(report, sort_keys=True)}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a P0 closure go/no-go report from release-gate evidence reports."
    )
    parser.add_argument("--label", default="p0-closure-report")
    parser.add_argument("--project-root")
    parser.add_argument("--required-consecutive", type=int, default=10)
    parser.add_argument("--required-evidence-reports", default="")
    parser.add_argument("--evidence-bundle-file", default="artifacts/p0-release-evidence-bundle-release-gate.json")
    parser.add_argument("--burnin-file", default="artifacts/p0-burnin-consecutive-green-release-gate.json")
    parser.add_argument("--runbook-contract-file", default="artifacts/p0-runbook-contract-check-release-gate.json")
    parser.add_argument("--output-file", default="artifacts/p0-closure-report-release-gate.json")
    parser.add_argument("--allow-not-ready", action="store_true")
    args = parser.parse_args(argv)

    if int(args.required_consecutive) <= 0:
        print("[p0-closure-report] ERROR: --required-consecutive must be > 0.", file=sys.stderr)
        return 2

    inferred_project_root = Path(__file__).resolve().parents[1]
    project_root = _resolve_path(inferred_project_root, args.project_root) if args.project_root else inferred_project_root
    evidence_bundle_file = _resolve_path(project_root, args.evidence_bundle_file)
    burnin_file = _resolve_path(project_root, args.burnin_file)
    runbook_contract_file = _resolve_path(project_root, args.runbook_contract_file)
    output_file = _resolve_path(project_root, args.output_file)
    required_evidence_reports = _parse_csv_list(args.required_evidence_reports)

    try:
        report = build_closure_report(
            label=str(args.label),
            required_consecutive=int(args.required_consecutive),
            required_evidence_reports=required_evidence_reports,
            evidence_bundle_file=evidence_bundle_file,
            burnin_file=burnin_file,
            runbook_contract_file=runbook_contract_file,
            output_file=output_file,
        )
    except Exception as exc:
        if args.allow_not_ready and output_file.exists():
            report = _read_json(output_file)
            print(json.dumps(report, ensure_ascii=True, sort_keys=True))
            return 0
        print(f"[p0-closure-report] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
