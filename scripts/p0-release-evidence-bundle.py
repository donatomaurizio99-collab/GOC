from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any


DEFAULT_REQUIRED_LABEL = ""
DEFAULT_REGISTRY_FILE = "docs/release-gate-registry.json"


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _read_text(path: Path) -> str:
    _expect(path.exists(), f"Required file not found: {path}")
    return path.read_text(encoding="utf-8")


def _parse_csv_list(text: str) -> list[str]:
    return [item.strip() for item in str(text).split(",") if item.strip()]


def _resolve_path(project_root: Path, value: str) -> Path:
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(_read_text(path))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Failed to parse JSON file {path}: {exc}") from exc
    _expect(isinstance(payload, dict), f"Expected JSON object in report file: {path}")
    return payload


def _normalize_registry_string_list(
    payload: dict[str, Any],
    key: str,
    *,
    context: str,
    allow_empty: bool = False,
) -> list[str]:
    raw = payload.get(key)
    _expect(isinstance(raw, list), f"Registry key '{key}' must be a list in {context}.")

    normalized: list[str] = []
    seen: set[str] = set()
    duplicates: list[str] = []
    for item in raw:
        _expect(isinstance(item, str), f"Registry key '{key}' must contain only strings in {context}.")
        token = item.strip()
        _expect(token, f"Registry key '{key}' contains an empty token in {context}.")
        if token in seen:
            duplicates.append(token)
            continue
        seen.add(token)
        normalized.append(token)

    _expect(not duplicates, f"Registry key '{key}' contains duplicate tokens in {context}: {duplicates}")
    if not allow_empty:
        _expect(normalized, f"Registry key '{key}' must contain at least one token in {context}.")
    return normalized


def _load_registry_defaults(registry_file: Path) -> dict[str, Any]:
    payload = _read_json_file(registry_file)
    contract = payload.get("p0_release_evidence_bundle")
    _expect(isinstance(contract, dict), "Registry key 'p0_release_evidence_bundle' must be an object.")

    required_label_raw = contract.get("required_label")
    _expect(
        isinstance(required_label_raw, str),
        "Registry key 'required_label' must be a string in p0_release_evidence_bundle.",
    )
    required_label = required_label_raw.strip()
    _expect(required_label, "Registry key 'required_label' must not be empty in p0_release_evidence_bundle.")

    required_files: list[str] = []
    if "required_files" in contract:
        required_files = _normalize_registry_string_list(
            contract,
            "required_files",
            context="p0_release_evidence_bundle",
            allow_empty=True,
        )

    return {
        "required_label": required_label,
        "required_files": required_files,
    }


def run_bundle(
    *,
    label: str,
    project_root: Path,
    artifacts_dir: Path,
    include_glob: str,
    required_files: list[str],
    required_label: str,
    output_file: Path,
    bundle_dir: Path,
    allow_empty: bool,
) -> dict[str, Any]:
    started = time.perf_counter()
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    bundle_dir.mkdir(parents=True, exist_ok=True)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    discovered_paths = sorted(
        [
            path.resolve()
            for path in artifacts_dir.glob(include_glob)
            if path.is_file() and path.resolve() != output_file.resolve()
        ]
    )
    required_paths = sorted([_resolve_path(project_root, rel_path) for rel_path in required_files])
    required_missing = [str(path) for path in required_paths if not path.exists()]

    discovered_map = {str(path): path for path in discovered_paths}
    for path in required_paths:
        discovered_map.setdefault(str(path), path)

    evaluated_reports: list[dict[str, Any]] = []
    invalid_reports: list[dict[str, Any]] = []
    copied_files: list[str] = []
    for file_path in sorted(discovered_map.values(), key=lambda item: str(item).lower()):
        if not file_path.exists():
            continue
        copy_target = bundle_dir / file_path.name
        shutil.copy2(file_path, copy_target)
        copied_files.append(str(copy_target))
        try:
            payload = _read_json_file(file_path)
            success_value = payload.get("success")
            has_success_flag = isinstance(success_value, bool)
            report_label = str(payload.get("label") or "")
            label_matches_required = True
            if required_label:
                label_matches_required = report_label == required_label
            report_entry = {
                "path": str(file_path),
                "label": report_label,
                "success": bool(success_value) if has_success_flag else None,
                "has_success_flag": bool(has_success_flag),
            }
            if required_label:
                report_entry["required_label"] = required_label
                report_entry["label_matches_required"] = bool(label_matches_required)
            if has_success_flag and success_value is False:
                report_entry["failure_excerpt"] = str(payload.get("error") or "")[:200]
            evaluated_reports.append(report_entry)
        except Exception as exc:  # pragma: no cover - defensive path
            invalid_reports.append({"path": str(file_path), "error": str(exc)})

    parsed_success_reports = [item for item in evaluated_reports if item.get("success") is True]
    parsed_failed_reports = [item for item in evaluated_reports if item.get("success") is False]
    parsed_without_flag = [item for item in evaluated_reports if item.get("success") is None]
    label_mismatch_reports = (
        [item for item in evaluated_reports if item.get("label_matches_required") is False]
        if required_label
        else []
    )

    success = (
        not required_missing
        and not invalid_reports
        and not parsed_failed_reports
        and not label_mismatch_reports
        and (allow_empty or bool(evaluated_reports))
    )
    report = {
        "label": label,
        "success": bool(success),
        "paths": {
            "project_root": str(project_root),
            "artifacts_dir": str(artifacts_dir),
            "bundle_dir": str(bundle_dir),
            "output_file": str(output_file),
        },
        "config": {
            "include_glob": include_glob,
            "required_files": required_files,
            "required_label": required_label,
            "allow_empty": bool(allow_empty),
        },
        "metrics": {
            "discovered_reports": len(evaluated_reports),
            "required_missing_reports": len(required_missing),
            "invalid_reports": len(invalid_reports),
            "failed_reports": len(parsed_failed_reports),
            "success_reports": len(parsed_success_reports),
            "reports_without_success_flag": len(parsed_without_flag),
            "label_mismatch_reports": len(label_mismatch_reports),
        },
        "reports": evaluated_reports,
        "required_missing": required_missing,
        "invalid_reports": invalid_reports,
        "label_mismatch_reports": label_mismatch_reports,
        "copied_files": copied_files,
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }
    output_file.write_text(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2), encoding="utf-8")
    if not success:
        raise RuntimeError(f"P0 release evidence bundle check failed: {json.dumps(report, sort_keys=True)}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build and validate a P0 release evidence bundle from report JSON files "
            "written during release-gate execution."
        )
    )
    parser.add_argument("--label", default="p0-release-evidence-bundle")
    parser.add_argument("--project-root")
    parser.add_argument("--artifacts-dir", default="artifacts")
    parser.add_argument("--include-glob", default="*-release-gate.json")
    parser.add_argument("--registry-file", default=DEFAULT_REGISTRY_FILE)
    parser.add_argument("--required-files", default="")
    parser.add_argument("--required-label", default="")
    parser.add_argument("--output-file", default="artifacts/p0-release-evidence-bundle.json")
    parser.add_argument("--bundle-dir", default="artifacts/p0-release-evidence-files")
    parser.add_argument("--allow-empty", action="store_true")
    args = parser.parse_args(argv)

    inferred_project_root = Path(__file__).resolve().parents[1]
    project_root = _resolve_path(inferred_project_root, args.project_root) if args.project_root else inferred_project_root
    artifacts_dir = _resolve_path(project_root, args.artifacts_dir)
    registry_file = _resolve_path(project_root, args.registry_file)
    output_file = _resolve_path(project_root, args.output_file)
    bundle_dir = _resolve_path(project_root, args.bundle_dir)
    required_files = _parse_csv_list(args.required_files)
    required_label = str(args.required_label).strip()

    registry_defaults: dict[str, Any] = {}
    if registry_file.exists():
        try:
            registry_defaults = _load_registry_defaults(registry_file)
        except Exception as exc:
            print(
                f"[p0-release-evidence-bundle] ERROR: Invalid registry defaults in {registry_file}: {exc}",
                file=sys.stderr,
            )
            return 2

    if not required_files and registry_defaults.get("required_files"):
        required_files = list(registry_defaults["required_files"])
    if not required_label:
        required_label = str(
            registry_defaults.get("required_label", DEFAULT_REQUIRED_LABEL)
        ).strip()

    try:
        report = run_bundle(
            label=str(args.label),
            project_root=project_root,
            artifacts_dir=artifacts_dir,
            include_glob=str(args.include_glob),
            required_files=required_files,
            required_label=required_label,
            output_file=output_file,
            bundle_dir=bundle_dir,
            allow_empty=bool(args.allow_empty),
        )
    except Exception as exc:
        print(f"[p0-release-evidence-bundle] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
