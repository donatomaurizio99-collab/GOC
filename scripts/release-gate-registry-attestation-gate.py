from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any

def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _resolve_path(project_root: Path, value: str) -> Path:
    candidate = Path(str(value)).expanduser()
    if not candidate.is_absolute():
        candidate = project_root / candidate
    return candidate.resolve()


def _read_json_object(path: Path) -> dict[str, Any]:
    _expect(path.exists(), f"JSON file not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    _expect(isinstance(payload, dict), f"Expected JSON object in file: {path}")
    return payload


def _normalize_artifact_path(value: str) -> str:
    return str(value).strip().replace("\\", "/")


def _load_registry_sync_module(module_path: Path) -> ModuleType:
    _expect(module_path.exists(), f"Registry sync module not found: {module_path}")
    spec = importlib.util.spec_from_file_location("release_gate_registry_sync_module", module_path)
    _expect(spec is not None and spec.loader is not None, f"Unable to load module spec from: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    _expect(hasattr(module, "load_registry"), "Registry sync module missing required function: load_registry")
    _expect(
        hasattr(module, "build_registry_lock_payload"),
        "Registry sync module missing required function: build_registry_lock_payload",
    )
    return module


def _path_matches_report_value(payload: dict[str, Any], key: str, expected: Path) -> bool:
    raw = payload.get(key)
    if not isinstance(raw, str) or not raw.strip():
        return False
    try:
        resolved_payload_path = Path(raw).expanduser().resolve()
    except Exception:
        return False
    return resolved_payload_path == expected.resolve()


def run_attestation(
    *,
    label: str,
    project_root: Path,
    registry_sync_report_file: Path,
    registry_file: Path,
    lock_file: Path,
    ci_workflow_file: Path,
    required_mode: str,
    expected_registry_sync_report_path: str,
    output_file: Path | None,
) -> dict[str, Any]:
    started = time.perf_counter()
    report = _read_json_object(registry_sync_report_file)
    lock_payload = _read_json_object(lock_file)

    module_path = project_root / "scripts" / "release-gate-registry-sync.py"
    registry_sync_module = _load_registry_sync_module(module_path)
    registry = registry_sync_module.load_registry(registry_file)
    expected_lock_payload = registry_sync_module.build_registry_lock_payload(registry)

    normalized_expected_sync_report_path = _normalize_artifact_path(expected_registry_sync_report_path)
    reported_sync_path = _normalize_artifact_path(str(report.get("registry_sync_report_path") or ""))

    attestation_checks = {
        "report_success_true": report.get("success") is True,
        "report_mode_matches_required": str(report.get("mode") or "") == required_mode,
        "report_changed_false": report.get("changed") is False,
        "report_lock_changed_false": report.get("lock_changed") is False,
        "report_registry_path_matches": _path_matches_report_value(report, "registry_file", registry_file),
        "report_lock_path_matches": _path_matches_report_value(report, "lock_file", lock_file),
        "report_ci_workflow_path_matches": _path_matches_report_value(report, "ci_workflow_file", ci_workflow_file),
        "report_sync_path_matches_expected": reported_sync_path == normalized_expected_sync_report_path,
        "registry_sync_report_path_covered_total_is_one": int(report.get("registry_sync_report_path_covered_total") or 0)
        == 1,
        "strict_flags_missing_in_release_gate_total_is_zero": int(
            report.get("strict_flags_missing_in_release_gate_total") or 0
        )
        == 0,
        "strict_switches_missing_in_registry_total_is_zero": int(
            report.get("strict_switches_missing_in_registry_total") or 0
        )
        == 0,
        "declared_strict_switches_without_runtime_usage_total_is_zero": int(
            report.get("declared_strict_switches_without_runtime_usage_total") or 0
        )
        == 0,
        "p0_runbook_default_list_mismatch_total_is_zero": int(
            report.get("p0_runbook_default_list_mismatch_total") or 0
        )
        == 0,
        "lock_payload_matches_registry": lock_payload == expected_lock_payload,
    }

    failed_check_names = [name for name, passed in attestation_checks.items() if not bool(passed)]
    success = len(failed_check_names) == 0
    report_payload = {
        "label": label,
        "success": bool(success),
        "paths": {
            "project_root": str(project_root),
            "registry_sync_report_file": str(registry_sync_report_file),
            "registry_file": str(registry_file),
            "lock_file": str(lock_file),
            "ci_workflow_file": str(ci_workflow_file),
            "output_file": str(output_file) if output_file is not None else None,
        },
        "config": {
            "required_mode": required_mode,
            "expected_registry_sync_report_path": normalized_expected_sync_report_path,
        },
        "metrics": {
            "checks_total": len(attestation_checks),
            "checks_failed": len(failed_check_names),
            "report_strict_flags_total": int(report.get("strict_flags_total") or 0),
            "report_artifact_paths_total": int(report.get("artifact_paths_total") or 0),
            "report_p0_runbook_default_lists_checked_total": int(
                report.get("p0_runbook_default_lists_checked_total") or 0
            ),
            "report_p0_runbook_default_list_mismatch_total": int(
                report.get("p0_runbook_default_list_mismatch_total") or 0
            ),
            "report_registry_sync_path_matches_expected": 1
            if attestation_checks["report_sync_path_matches_expected"]
            else 0,
            "registry_lock_payload_match": 1 if attestation_checks["lock_payload_matches_registry"] else 0,
        },
        "checks": attestation_checks,
        "failed_checks": failed_check_names,
        "decision": {
            "release_blocked": not bool(success),
            "recommended_action": "registry_attestation_passed" if success else "registry_attestation_failed",
        },
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }
    if not success:
        raise RuntimeError(f"Release-gate registry attestation gate failed: {json.dumps(report_payload, sort_keys=True)}")
    return report_payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Attest release-gate registry sync output against lockfile and wiring invariants before release-gate run."
        )
    )
    parser.add_argument("--label", default="release-gate-registry-attestation-gate")
    parser.add_argument("--project-root")
    parser.add_argument("--registry-sync-report-file", default="artifacts/release-gate-registry-sync-ci.json")
    parser.add_argument("--registry-file", default="docs/release-gate-registry.json")
    parser.add_argument("--lock-file", default="docs/release-gate-registry.lock.json")
    parser.add_argument("--ci-workflow-file", default=".github/workflows/ci.yml")
    parser.add_argument("--required-mode", default="check")
    parser.add_argument("--expected-registry-sync-report-path", default="artifacts/release-gate-registry-sync-ci.json")
    parser.add_argument("--output-file")
    args = parser.parse_args(argv)

    inferred_project_root = Path(__file__).resolve().parents[1]
    project_root = _resolve_path(inferred_project_root, args.project_root) if args.project_root else inferred_project_root
    registry_sync_report_file = _resolve_path(project_root, args.registry_sync_report_file)
    registry_file = _resolve_path(project_root, args.registry_file)
    lock_file = _resolve_path(project_root, args.lock_file)
    ci_workflow_file = _resolve_path(project_root, args.ci_workflow_file)
    output_file = _resolve_path(project_root, args.output_file) if args.output_file else None

    try:
        payload = run_attestation(
            label=str(args.label),
            project_root=project_root,
            registry_sync_report_file=registry_sync_report_file,
            registry_file=registry_file,
            lock_file=lock_file,
            ci_workflow_file=ci_workflow_file,
            required_mode=str(args.required_mode),
            expected_registry_sync_report_path=str(args.expected_registry_sync_report_path),
            output_file=output_file,
        )
    except Exception as exc:
        print(f"[release-gate-registry-attestation-gate] ERROR: {exc}", file=sys.stderr)
        return 1

    if output_file is not None:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2), encoding="utf-8")

    print(json.dumps(payload, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
