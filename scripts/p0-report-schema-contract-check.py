from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any


DEFAULT_REQUIRED_TOP_LEVEL_KEYS = [
    "label",
    "success",
    "generated_at_utc",
    "duration_ms",
    "paths",
    "metrics",
    "decision",
]

DEFAULT_REQUIRED_DECISION_KEYS = [
    "release_blocked",
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
    payload = json.loads(path.read_text(encoding="utf-8"))
    _expect(isinstance(payload, dict), f"Expected JSON object in report file: {path}")
    return payload


def _validate_report_schema(
    payload: dict[str, Any],
    required_top_level_keys: list[str],
    required_decision_keys: list[str],
) -> dict[str, Any]:
    missing_top_level_keys = [key for key in required_top_level_keys if key not in payload]
    type_errors: list[str] = []

    label_value = payload.get("label")
    if "label" in payload and not isinstance(label_value, str):
        type_errors.append("label_must_be_string")

    success_value = payload.get("success")
    if "success" in payload and not isinstance(success_value, bool):
        type_errors.append("success_must_be_boolean")

    generated_at_value = payload.get("generated_at_utc")
    if "generated_at_utc" in payload and not isinstance(generated_at_value, str):
        type_errors.append("generated_at_utc_must_be_string")

    duration_value = payload.get("duration_ms")
    if "duration_ms" in payload:
        if not isinstance(duration_value, (int, float)):
            type_errors.append("duration_ms_must_be_number")
        elif float(duration_value) < 0:
            type_errors.append("duration_ms_must_be_non_negative")

    paths_value = payload.get("paths")
    if "paths" in payload and not isinstance(paths_value, dict):
        type_errors.append("paths_must_be_object")

    metrics_value = payload.get("metrics")
    if "metrics" in payload and not isinstance(metrics_value, dict):
        type_errors.append("metrics_must_be_object")

    decision_value = payload.get("decision")
    decision_is_object = isinstance(decision_value, dict)
    if "decision" in payload and not decision_is_object:
        type_errors.append("decision_must_be_object")

    missing_decision_keys = [
        key for key in required_decision_keys if not decision_is_object or key not in decision_value
    ]
    if decision_is_object and "release_blocked" in decision_value and not isinstance(decision_value["release_blocked"], bool):
        type_errors.append("decision.release_blocked_must_be_boolean")

    schema_valid = not missing_top_level_keys and not missing_decision_keys and not type_errors
    return {
        "schema_valid": bool(schema_valid),
        "missing_top_level_keys": missing_top_level_keys,
        "missing_decision_keys": missing_decision_keys,
        "type_errors": type_errors,
    }


def run_check(
    *,
    label: str,
    project_root: Path,
    artifacts_dir: Path,
    include_glob: str,
    required_files: list[str],
    required_label: str,
    required_top_level_keys: list[str],
    required_decision_keys: list[str],
    output_file: Path,
    allow_empty: bool,
) -> dict[str, Any]:
    started = time.perf_counter()
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    discovered_paths = sorted(
        [
            path.resolve()
            for path in artifacts_dir.glob(include_glob)
            if path.is_file() and path.resolve() != output_file.resolve()
        ]
    )
    required_paths = sorted([_resolve_path(project_root, rel_path) for rel_path in required_files])
    missing_required_files = [str(path) for path in required_paths if not path.exists()]

    discovered_map = {str(path): path for path in discovered_paths}
    for path in required_paths:
        discovered_map.setdefault(str(path), path)

    # In strict gate mode we pass explicit required files. In that case, validate only
    # required files so unrelated legacy report schemas do not create false blockers.
    required_path_set = {str(item) for item in required_paths}
    if required_paths:
        paths_to_evaluate = sorted(
            [path for path in required_paths if path.exists()],
            key=lambda item: str(item).lower(),
        )
    else:
        paths_to_evaluate = sorted(
            [path for path in discovered_map.values() if path.exists()],
            key=lambda item: str(item).lower(),
        )

    out_of_scope_paths = [
        str(path)
        for path in discovered_paths
        if required_paths and str(path) not in required_path_set
    ]

    evaluated_reports: list[dict[str, Any]] = []
    invalid_reports: list[dict[str, Any]] = []
    schema_failed_reports: list[dict[str, Any]] = []
    label_mismatch_reports: list[dict[str, Any]] = []

    for file_path in paths_to_evaluate:
        try:
            payload = _read_json_object(file_path)
        except Exception as exc:
            invalid_entry = {"path": str(file_path), "error": str(exc)}
            invalid_reports.append(invalid_entry)
            continue

        schema_check = _validate_report_schema(
            payload=payload,
            required_top_level_keys=required_top_level_keys,
            required_decision_keys=required_decision_keys,
        )
        report_label = str(payload.get("label") or "")
        label_matches_required = True
        if required_label:
            label_matches_required = report_label == required_label

        success_value = payload.get("success")
        has_success_flag = isinstance(success_value, bool)
        report_entry: dict[str, Any] = {
            "path": str(file_path),
            "label": report_label,
            "success": bool(success_value) if has_success_flag else None,
            "has_success_flag": bool(has_success_flag),
            "schema_valid": bool(schema_check["schema_valid"]),
            "missing_top_level_keys": list(schema_check["missing_top_level_keys"]),
            "missing_decision_keys": list(schema_check["missing_decision_keys"]),
            "type_errors": list(schema_check["type_errors"]),
        }
        if required_label:
            report_entry["required_label"] = required_label
            report_entry["label_matches_required"] = bool(label_matches_required)
        evaluated_reports.append(report_entry)

        if not schema_check["schema_valid"]:
            schema_failed_reports.append(report_entry)
        if required_label and not label_matches_required:
            label_mismatch_reports.append(report_entry)

    success = (
        not missing_required_files
        and not invalid_reports
        and not schema_failed_reports
        and not label_mismatch_reports
        and (allow_empty or bool(evaluated_reports))
    )
    report = {
        "label": label,
        "success": bool(success),
        "paths": {
            "project_root": str(project_root),
            "artifacts_dir": str(artifacts_dir),
            "output_file": str(output_file),
        },
        "config": {
            "include_glob": include_glob,
            "required_files": required_files,
            "required_label": required_label,
            "required_top_level_keys": required_top_level_keys,
            "required_decision_keys": required_decision_keys,
            "allow_empty": bool(allow_empty),
        },
        "metrics": {
            "reports_discovered": len(discovered_paths),
            "reports_evaluated": len(evaluated_reports),
            "reports_out_of_scope": len(out_of_scope_paths),
            "missing_required_files": len(missing_required_files),
            "invalid_reports": len(invalid_reports),
            "schema_failed_reports": len(schema_failed_reports),
            "label_mismatch_reports": len(label_mismatch_reports),
        },
        "decision": {
            "release_blocked": not bool(success),
            "recommended_action": "block_release" if not success else "proceed",
        },
        "reports": evaluated_reports,
        "out_of_scope_reports": out_of_scope_paths,
        "missing_required_files": missing_required_files,
        "invalid_reports": invalid_reports,
        "schema_failed_reports": schema_failed_reports,
        "label_mismatch_reports": label_mismatch_reports,
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }
    output_file.write_text(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2), encoding="utf-8")
    if not success:
        raise RuntimeError(f"P0 report schema contract check failed: {json.dumps(report, sort_keys=True)}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate minimal JSON schema contracts for release-gate reports so evidence parsing "
            "remains deterministic across stability-first gate evolution."
        )
    )
    parser.add_argument("--label", default="p0-report-schema-contract-check")
    parser.add_argument("--project-root")
    parser.add_argument("--artifacts-dir", default="artifacts")
    parser.add_argument("--include-glob", default="*-release-gate.json")
    parser.add_argument("--required-files", default="")
    parser.add_argument("--required-label", default="")
    parser.add_argument("--required-top-level-keys", default=",".join(DEFAULT_REQUIRED_TOP_LEVEL_KEYS))
    parser.add_argument("--required-decision-keys", default=",".join(DEFAULT_REQUIRED_DECISION_KEYS))
    parser.add_argument("--output-file", default="artifacts/p0-report-schema-contract-release-gate.json")
    parser.add_argument("--allow-empty", action="store_true")
    args = parser.parse_args(argv)

    inferred_project_root = Path(__file__).resolve().parents[1]
    project_root = _resolve_path(inferred_project_root, args.project_root) if args.project_root else inferred_project_root
    artifacts_dir = _resolve_path(project_root, args.artifacts_dir)
    output_file = _resolve_path(project_root, args.output_file)

    required_files = _parse_csv_list(args.required_files)
    required_top_level_keys = _parse_csv_list(args.required_top_level_keys)
    required_decision_keys = _parse_csv_list(args.required_decision_keys)

    if not required_top_level_keys:
        print("[p0-report-schema-contract-check] ERROR: --required-top-level-keys must not be empty.", file=sys.stderr)
        return 2
    if not required_decision_keys:
        print("[p0-report-schema-contract-check] ERROR: --required-decision-keys must not be empty.", file=sys.stderr)
        return 2

    try:
        report = run_check(
            label=str(args.label),
            project_root=project_root,
            artifacts_dir=artifacts_dir,
            include_glob=str(args.include_glob),
            required_files=required_files,
            required_label=str(args.required_label),
            required_top_level_keys=required_top_level_keys,
            required_decision_keys=required_decision_keys,
            output_file=output_file,
            allow_empty=bool(args.allow_empty),
        )
    except Exception as exc:
        print(f"[p0-report-schema-contract-check] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
