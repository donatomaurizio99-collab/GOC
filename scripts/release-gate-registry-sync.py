from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


P0_CONTRACT_LIST_KEYS = [
    "required_runbook_scripts",
    "required_canary_drills",
    "required_release_gate_tokens",
    "required_ci_artifact_paths",
    "required_runbook_tokens",
]


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _read_text(path: Path) -> str:
    _expect(path.exists(), f"Required file not found: {path}")
    return path.read_text(encoding="utf-8")


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(_read_text(path))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Failed to parse JSON file {path}: {exc}") from exc
    _expect(isinstance(payload, dict), f"JSON file must contain an object: {path}")
    return payload


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _render_json_file(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2) + "\n"


def _normalize_string_list(
    payload: dict[str, Any],
    key: str,
    *,
    context: str,
    value_pattern: str | None = None,
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
        if value_pattern is not None:
            _expect(
                re.fullmatch(value_pattern, token) is not None,
                f"Registry key '{key}' contains invalid token '{token}' in {context}.",
            )
        if token in seen:
            duplicates.append(token)
            continue
        seen.add(token)
        normalized.append(token)

    _expect(not duplicates, f"Registry key '{key}' contains duplicate tokens in {context}: {duplicates}")
    if not allow_empty:
        _expect(normalized, f"Registry key '{key}' must contain at least one token in {context}.")
    return normalized


def _normalize_artifact_token(token: str) -> str:
    return str(token).strip().replace("\\", "/")


def _artifact_path_is_covered(path: str, patterns: list[str]) -> bool:
    normalized_path = _normalize_artifact_token(path)
    for pattern in patterns:
        normalized_pattern = _normalize_artifact_token(pattern)
        if fnmatch.fnmatch(normalized_path, normalized_pattern):
            return True
    return False


def _validate_cross_contracts(
    *,
    release_evidence_artifact_paths: list[str],
    p0_runbook_contract: dict[str, list[str]],
    p0_report_schema_contract: dict[str, Any],
    p0_release_evidence_bundle: dict[str, Any],
) -> dict[str, int]:
    schema_required_label = str(p0_report_schema_contract["required_label"])
    bundle_required_label = str(p0_release_evidence_bundle["required_label"])
    _expect(
        schema_required_label == bundle_required_label,
        (
            "Registry contract mismatch: "
            "p0_report_schema_contract.required_label must match "
            "p0_release_evidence_bundle.required_label."
        ),
    )

    schema_required_top_level_keys = list(p0_report_schema_contract["required_top_level_keys"])
    _expect(
        "label" in schema_required_top_level_keys and "success" in schema_required_top_level_keys,
        (
            "Registry contract mismatch: "
            "p0_report_schema_contract.required_top_level_keys must include 'label' and 'success'."
        ),
    )

    uncovered_ci_artifact_paths = [
        token
        for token in p0_runbook_contract["required_ci_artifact_paths"]
        if not _artifact_path_is_covered(token, release_evidence_artifact_paths)
    ]
    _expect(
        not uncovered_ci_artifact_paths,
        (
            "Registry contract mismatch: p0_runbook_contract.required_ci_artifact_paths contains "
            "entries not covered by release_gate_ci.release_evidence_artifact_paths: "
            f"{uncovered_ci_artifact_paths}"
        ),
    )

    schema_required_files = list(p0_report_schema_contract["required_files"])
    uncovered_schema_required_files = [
        token
        for token in schema_required_files
        if not _artifact_path_is_covered(token, release_evidence_artifact_paths)
    ]
    _expect(
        not uncovered_schema_required_files,
        (
            "Registry contract mismatch: p0_report_schema_contract.required_files contains entries "
            "not covered by release_gate_ci.release_evidence_artifact_paths: "
            f"{uncovered_schema_required_files}"
        ),
    )

    bundle_required_files = list(p0_release_evidence_bundle["required_files"])
    uncovered_bundle_required_files = [
        token
        for token in bundle_required_files
        if not _artifact_path_is_covered(token, release_evidence_artifact_paths)
    ]
    _expect(
        not uncovered_bundle_required_files,
        (
            "Registry contract mismatch: p0_release_evidence_bundle.required_files contains entries "
            "not covered by release_gate_ci.release_evidence_artifact_paths: "
            f"{uncovered_bundle_required_files}"
        ),
    )

    return {
        "ci_artifact_paths_checked_total": len(p0_runbook_contract["required_ci_artifact_paths"]),
        "schema_required_files_checked_total": len(schema_required_files),
        "bundle_required_files_checked_total": len(bundle_required_files),
    }


def load_registry(registry_file: Path) -> dict[str, Any]:
    payload = _read_json_object(registry_file)
    registry_version_raw = payload.get("version")
    _expect(isinstance(registry_version_raw, str), "Registry key 'version' must be a string.")
    registry_version = registry_version_raw.strip()
    _expect(registry_version, "Registry key 'version' must not be empty.")

    release_gate_ci = payload.get("release_gate_ci")
    _expect(isinstance(release_gate_ci, dict), "Registry key 'release_gate_ci' must be an object.")
    strict_flags = _normalize_string_list(
        release_gate_ci,
        "strict_flags",
        context="release_gate_ci",
        value_pattern=r"Strict[A-Za-z0-9]+",
    )
    artifact_paths = _normalize_string_list(
        release_gate_ci,
        "release_evidence_artifact_paths",
        context="release_gate_ci",
    )

    p0_contract = payload.get("p0_runbook_contract")
    _expect(isinstance(p0_contract, dict), "Registry key 'p0_runbook_contract' must be an object.")
    p0_lists = {
        key: _normalize_string_list(p0_contract, key, context="p0_runbook_contract")
        for key in P0_CONTRACT_LIST_KEYS
    }

    p0_schema = payload.get("p0_report_schema_contract")
    _expect(isinstance(p0_schema, dict), "Registry key 'p0_report_schema_contract' must be an object.")
    p0_schema_top_level = _normalize_string_list(
        p0_schema,
        "required_top_level_keys",
        context="p0_report_schema_contract",
    )
    p0_schema_decision = _normalize_string_list(
        p0_schema,
        "required_decision_keys",
        context="p0_report_schema_contract",
        allow_empty=True,
    )
    p0_schema_label_raw = p0_schema.get("required_label")
    _expect(
        isinstance(p0_schema_label_raw, str),
        "Registry key 'required_label' must be a string in p0_report_schema_contract.",
    )
    p0_schema_label = p0_schema_label_raw.strip()
    _expect(
        p0_schema_label,
        "Registry key 'required_label' must not be empty in p0_report_schema_contract.",
    )
    p0_schema_required_files = _normalize_string_list(
        p0_schema,
        "required_files",
        context="p0_report_schema_contract",
        allow_empty=True,
    )

    p0_bundle = payload.get("p0_release_evidence_bundle")
    _expect(isinstance(p0_bundle, dict), "Registry key 'p0_release_evidence_bundle' must be an object.")
    p0_bundle_label_raw = p0_bundle.get("required_label")
    _expect(
        isinstance(p0_bundle_label_raw, str),
        "Registry key 'required_label' must be a string in p0_release_evidence_bundle.",
    )
    p0_bundle_label = p0_bundle_label_raw.strip()
    _expect(
        p0_bundle_label,
        "Registry key 'required_label' must not be empty in p0_release_evidence_bundle.",
    )
    p0_bundle_required_files = _normalize_string_list(
        p0_bundle,
        "required_files",
        context="p0_release_evidence_bundle",
        allow_empty=True,
    )

    registry = {
        "registry_version": registry_version,
        "strict_flags": strict_flags,
        "release_evidence_artifact_paths": artifact_paths,
        "p0_contract": p0_lists,
        "p0_report_schema_contract": {
            "required_top_level_keys": p0_schema_top_level,
            "required_decision_keys": p0_schema_decision,
            "required_label": p0_schema_label,
            "required_files": p0_schema_required_files,
        },
        "p0_release_evidence_bundle": {
            "required_label": p0_bundle_label,
            "required_files": p0_bundle_required_files,
        },
    }
    registry["cross_contract_metrics"] = _validate_cross_contracts(
        release_evidence_artifact_paths=registry["release_evidence_artifact_paths"],
        p0_runbook_contract=registry["p0_contract"],
        p0_report_schema_contract=registry["p0_report_schema_contract"],
        p0_release_evidence_bundle=registry["p0_release_evidence_bundle"],
    )
    return registry


def build_registry_lock_payload(registry: dict[str, Any]) -> dict[str, Any]:
    registry_core = {
        "registry_version": registry["registry_version"],
        "release_gate_ci": {
            "strict_flags": registry["strict_flags"],
            "release_evidence_artifact_paths": registry["release_evidence_artifact_paths"],
        },
        "p0_runbook_contract": registry["p0_contract"],
        "p0_report_schema_contract": registry["p0_report_schema_contract"],
        "p0_release_evidence_bundle": registry["p0_release_evidence_bundle"],
    }

    strict_flags_canonical = _canonical_json(registry["strict_flags"])
    artifact_paths_canonical = _canonical_json(registry["release_evidence_artifact_paths"])
    p0_contract_canonical = _canonical_json(registry["p0_contract"])
    p0_schema_contract_canonical = _canonical_json(registry["p0_report_schema_contract"])
    p0_bundle_contract_canonical = _canonical_json(registry["p0_release_evidence_bundle"])
    registry_core_canonical = _canonical_json(registry_core)

    return {
        "version": "1.0.0",
        "registry_version": registry["registry_version"],
        "hashes": {
            "registry_core_sha256": _sha256_text(registry_core_canonical),
            "strict_flags_sha256": _sha256_text(strict_flags_canonical),
            "release_evidence_artifact_paths_sha256": _sha256_text(artifact_paths_canonical),
            "p0_runbook_contract_sha256": _sha256_text(p0_contract_canonical),
            "p0_report_schema_contract_sha256": _sha256_text(p0_schema_contract_canonical),
            "p0_release_evidence_bundle_sha256": _sha256_text(p0_bundle_contract_canonical),
        },
        "counts": {
            "strict_flags_total": len(registry["strict_flags"]),
            "release_evidence_artifact_paths_total": len(registry["release_evidence_artifact_paths"]),
            "p0_runbook_required_scripts_total": len(registry["p0_contract"]["required_runbook_scripts"]),
            "p0_runbook_required_ci_artifact_paths_total": len(
                registry["p0_contract"]["required_ci_artifact_paths"]
            ),
            "p0_schema_required_top_level_keys_total": len(
                registry["p0_report_schema_contract"]["required_top_level_keys"]
            ),
            "p0_schema_required_decision_keys_total": len(
                registry["p0_report_schema_contract"]["required_decision_keys"]
            ),
            "p0_schema_required_files_total": len(registry["p0_report_schema_contract"]["required_files"]),
            "p0_bundle_required_files_total": len(registry["p0_release_evidence_bundle"]["required_files"]),
        },
    }


def _leading_whitespace(text: str) -> str:
    return text[: len(text) - len(text.lstrip(" "))]


def _update_release_gate_command_line(ci_text: str, strict_flags: list[str]) -> tuple[str, bool]:
    lines = ci_text.splitlines()
    command_indexes = [idx for idx, line in enumerate(lines) if ".\\scripts\\release-gate.ps1" in line]
    _expect(command_indexes, "Unable to find '.\\scripts\\release-gate.ps1' command in CI workflow.")
    _expect(
        len(command_indexes) == 1,
        f"Expected exactly one release-gate command in CI workflow, found {len(command_indexes)}.",
    )

    index = command_indexes[0]
    indent = _leading_whitespace(lines[index])
    generated = indent + ".\\scripts\\release-gate.ps1 " + " ".join(f"-{flag}" for flag in strict_flags)
    changed = lines[index] != generated
    lines[index] = generated
    return ("\n".join(lines) + ("\n" if ci_text.endswith("\n") else "")), changed


def _find_release_evidence_upload_block(lines: list[str]) -> tuple[int, int]:
    upload_step_index = -1
    for idx, line in enumerate(lines):
        if line.strip() == "- name: Upload release evidence artifacts":
            upload_step_index = idx
            break
    _expect(upload_step_index >= 0, "Unable to find 'Upload release evidence artifacts' step in CI workflow.")

    path_index = -1
    end_index = -1
    for idx in range(upload_step_index, len(lines)):
        stripped = lines[idx].strip()
        if stripped == "path: |":
            path_index = idx
            continue
        if path_index >= 0 and stripped.startswith("if-no-files-found:"):
            end_index = idx
            break
    _expect(path_index >= 0, "Unable to find release evidence artifact 'path: |' block in CI workflow.")
    _expect(end_index >= 0, "Unable to find end of release evidence artifact path block in CI workflow.")
    return path_index, end_index


def _update_release_evidence_paths(ci_text: str, artifact_paths: list[str]) -> tuple[str, bool]:
    lines = ci_text.splitlines()
    path_index, end_index = _find_release_evidence_upload_block(lines)

    existing_path_lines = lines[path_index + 1 : end_index]
    path_indent = "            "
    for line in existing_path_lines:
        if line.strip():
            path_indent = _leading_whitespace(line)
            break

    generated_lines = [f"{path_indent}{path}" for path in artifact_paths]
    changed = existing_path_lines != generated_lines
    updated_lines = lines[: path_index + 1] + generated_lines + lines[end_index:]
    return ("\n".join(updated_lines) + ("\n" if ci_text.endswith("\n") else "")), changed


def validate_registry_wiring(
    *,
    release_gate_text: str,
    schema_wrapper_text: str,
    bundle_wrapper_text: str,
) -> dict[str, int]:
    release_gate_registry_arg = '"--registry-file", "docs/release-gate-registry.json"'
    release_gate_registry_arg_count = release_gate_text.count(release_gate_registry_arg)
    _expect(
        release_gate_registry_arg_count >= 2,
        (
            "Release-gate script must pass "
            "'--registry-file\", \"docs/release-gate-registry.json\"' to both "
            "P0 schema and bundle checks."
        ),
    )

    _expect(
        '".\\scripts\\p0-report-schema-contract-check.py"' in release_gate_text,
        "Unable to find P0 report schema contract step in release-gate script.",
    )
    _expect(
        '".\\scripts\\p0-release-evidence-bundle.py"' in release_gate_text,
        "Unable to find P0 release evidence bundle step in release-gate script.",
    )

    schema_wrapper_registry_default = '[string]$RegistryFile = "docs\\\\release-gate-registry.json"'
    bundle_wrapper_registry_default = '[string]$RegistryFile = "docs\\\\release-gate-registry.json"'
    wrapper_registry_arg = '"--registry-file", $RegistryFile'

    _expect(
        schema_wrapper_registry_default in schema_wrapper_text,
        "P0 schema wrapper must define RegistryFile default to docs\\\\release-gate-registry.json.",
    )
    _expect(
        wrapper_registry_arg in schema_wrapper_text,
        "P0 schema wrapper must pass --registry-file to the Python checker.",
    )
    _expect(
        bundle_wrapper_registry_default in bundle_wrapper_text,
        "P0 bundle wrapper must define RegistryFile default to docs\\\\release-gate-registry.json.",
    )
    _expect(
        wrapper_registry_arg in bundle_wrapper_text,
        "P0 bundle wrapper must pass --registry-file to the Python checker.",
    )

    return {
        "release_gate_registry_argument_occurrences": release_gate_registry_arg_count,
        "schema_wrapper_registry_argument_occurrences": schema_wrapper_text.count(wrapper_registry_arg),
        "bundle_wrapper_registry_argument_occurrences": bundle_wrapper_text.count(wrapper_registry_arg),
    }


def synchronize_ci_workflow(
    ci_text: str,
    *,
    strict_flags: list[str],
    artifact_paths: list[str],
) -> tuple[str, bool]:
    updated_text, changed_command = _update_release_gate_command_line(ci_text, strict_flags)
    updated_text, changed_paths = _update_release_evidence_paths(updated_text, artifact_paths)
    return updated_text, bool(changed_command or changed_paths)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Synchronize and validate CI release-gate strict flags/artifact path block from "
            "docs/release-gate-registry.json and validate registry contract sections."
        )
    )
    parser.add_argument("--project-root")
    parser.add_argument("--registry-file", default="docs/release-gate-registry.json")
    parser.add_argument("--lock-file", default="docs/release-gate-registry.lock.json")
    parser.add_argument("--ci-workflow-file", default=".github/workflows/ci.yml")
    parser.add_argument("--release-gate-file", default="scripts/release-gate.ps1")
    parser.add_argument("--schema-wrapper-file", default="scripts/run-p0-report-schema-contract-check.ps1")
    parser.add_argument("--bundle-wrapper-file", default="scripts/run-p0-release-evidence-bundle.ps1")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)

    inferred_project_root = Path(__file__).resolve().parents[1]
    project_root = (
        Path(str(args.project_root)).expanduser().resolve()
        if args.project_root
        else inferred_project_root
    )
    registry_file = (project_root / str(args.registry_file)).resolve()
    lock_file = (project_root / str(args.lock_file)).resolve()
    ci_workflow_file = (project_root / str(args.ci_workflow_file)).resolve()
    release_gate_file = (project_root / str(args.release_gate_file)).resolve()
    schema_wrapper_file = (project_root / str(args.schema_wrapper_file)).resolve()
    bundle_wrapper_file = (project_root / str(args.bundle_wrapper_file)).resolve()

    try:
        registry = load_registry(registry_file)
        generated_lock_payload = build_registry_lock_payload(registry)
        generated_lock_text = _render_json_file(generated_lock_payload)
        existing_lock_text = _read_text(lock_file) if lock_file.exists() else ""
        lock_changed = existing_lock_text != generated_lock_text
        original_ci = _read_text(ci_workflow_file)
        release_gate_text = _read_text(release_gate_file)
        schema_wrapper_text = _read_text(schema_wrapper_file)
        bundle_wrapper_text = _read_text(bundle_wrapper_file)
        wiring_metrics = validate_registry_wiring(
            release_gate_text=release_gate_text,
            schema_wrapper_text=schema_wrapper_text,
            bundle_wrapper_text=bundle_wrapper_text,
        )
        synchronized_ci, changed = synchronize_ci_workflow(
            original_ci,
            strict_flags=registry["strict_flags"],
            artifact_paths=registry["release_evidence_artifact_paths"],
        )
    except Exception as exc:
        print(f"[release-gate-registry-sync] ERROR: {exc}", file=sys.stderr)
        return 1

    if args.write:
        if changed:
            ci_workflow_file.write_text(synchronized_ci, encoding="utf-8")
        if lock_changed:
            lock_file.parent.mkdir(parents=True, exist_ok=True)
            lock_file.write_text(generated_lock_text, encoding="utf-8")
        print(
            json.dumps(
                {
                    "success": True,
                    "mode": "write",
                    "changed": bool(changed),
                    "lock_changed": bool(lock_changed),
                    "registry_file": str(registry_file),
                    "lock_file": str(lock_file),
                    "ci_workflow_file": str(ci_workflow_file),
                    "release_gate_file": str(release_gate_file),
                    "schema_wrapper_file": str(schema_wrapper_file),
                    "bundle_wrapper_file": str(bundle_wrapper_file),
                    "strict_flags_total": len(registry["strict_flags"]),
                    "artifact_paths_total": len(registry["release_evidence_artifact_paths"]),
                    "p0_schema_required_top_level_keys_total": len(
                        registry["p0_report_schema_contract"]["required_top_level_keys"]
                    ),
                    "p0_schema_required_decision_keys_total": len(
                        registry["p0_report_schema_contract"]["required_decision_keys"]
                    ),
                    "p0_bundle_required_files_total": len(
                        registry["p0_release_evidence_bundle"]["required_files"]
                    ),
                    "ci_artifact_paths_checked_total": registry["cross_contract_metrics"][
                        "ci_artifact_paths_checked_total"
                    ],
                    "schema_required_files_checked_total": registry["cross_contract_metrics"][
                        "schema_required_files_checked_total"
                    ],
                    "bundle_required_files_checked_total": registry["cross_contract_metrics"][
                        "bundle_required_files_checked_total"
                    ],
                    "release_gate_registry_argument_occurrences": wiring_metrics[
                        "release_gate_registry_argument_occurrences"
                    ],
                    "schema_wrapper_registry_argument_occurrences": wiring_metrics[
                        "schema_wrapper_registry_argument_occurrences"
                    ],
                    "bundle_wrapper_registry_argument_occurrences": wiring_metrics[
                        "bundle_wrapper_registry_argument_occurrences"
                    ],
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
        return 0

    if changed or lock_changed:
        drift_reasons: list[str] = []
        if changed:
            drift_reasons.append("CI workflow")
        if lock_changed:
            drift_reasons.append("registry lock file")
        print(
            (
                "[release-gate-registry-sync] ERROR: Out of sync with release-gate registry: "
                + ", ".join(drift_reasons)
                + ". Run `python scripts/release-gate-registry-sync.py --write`."
            ),
            file=sys.stderr,
        )
        return 1

    print(
        json.dumps(
            {
                "success": True,
                "mode": "check",
                "changed": False,
                "lock_changed": False,
                "registry_file": str(registry_file),
                "lock_file": str(lock_file),
                "ci_workflow_file": str(ci_workflow_file),
                "release_gate_file": str(release_gate_file),
                "schema_wrapper_file": str(schema_wrapper_file),
                "bundle_wrapper_file": str(bundle_wrapper_file),
                "strict_flags_total": len(registry["strict_flags"]),
                "artifact_paths_total": len(registry["release_evidence_artifact_paths"]),
                "p0_schema_required_top_level_keys_total": len(
                    registry["p0_report_schema_contract"]["required_top_level_keys"]
                ),
                "p0_schema_required_decision_keys_total": len(
                    registry["p0_report_schema_contract"]["required_decision_keys"]
                ),
                "p0_bundle_required_files_total": len(
                    registry["p0_release_evidence_bundle"]["required_files"]
                ),
                "ci_artifact_paths_checked_total": registry["cross_contract_metrics"][
                    "ci_artifact_paths_checked_total"
                ],
                "schema_required_files_checked_total": registry["cross_contract_metrics"][
                    "schema_required_files_checked_total"
                ],
                "bundle_required_files_checked_total": registry["cross_contract_metrics"][
                    "bundle_required_files_checked_total"
                ],
                "release_gate_registry_argument_occurrences": wiring_metrics[
                    "release_gate_registry_argument_occurrences"
                ],
                "schema_wrapper_registry_argument_occurrences": wiring_metrics[
                    "schema_wrapper_registry_argument_occurrences"
                ],
                "bundle_wrapper_registry_argument_occurrences": wiring_metrics[
                    "bundle_wrapper_registry_argument_occurrences"
                ],
            },
            ensure_ascii=True,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
