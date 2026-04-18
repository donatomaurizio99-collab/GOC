from __future__ import annotations

import argparse
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


def _normalize_string_list(
    payload: dict[str, Any],
    key: str,
    *,
    context: str,
    value_pattern: str | None = None,
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
    _expect(normalized, f"Registry key '{key}' must contain at least one token in {context}.")
    return normalized


def load_registry(registry_file: Path) -> dict[str, Any]:
    payload = _read_json_object(registry_file)

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

    return {
        "strict_flags": strict_flags,
        "release_evidence_artifact_paths": artifact_paths,
        "p0_contract": p0_lists,
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
            "docs/release-gate-registry.json."
        )
    )
    parser.add_argument("--project-root")
    parser.add_argument("--registry-file", default="docs/release-gate-registry.json")
    parser.add_argument("--ci-workflow-file", default=".github/workflows/ci.yml")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)

    inferred_project_root = Path(__file__).resolve().parents[1]
    project_root = (
        Path(str(args.project_root)).expanduser().resolve()
        if args.project_root
        else inferred_project_root
    )
    registry_file = (project_root / str(args.registry_file)).resolve()
    ci_workflow_file = (project_root / str(args.ci_workflow_file)).resolve()

    try:
        registry = load_registry(registry_file)
        original_ci = _read_text(ci_workflow_file)
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
        print(
            json.dumps(
                {
                    "success": True,
                    "mode": "write",
                    "changed": bool(changed),
                    "registry_file": str(registry_file),
                    "ci_workflow_file": str(ci_workflow_file),
                    "strict_flags_total": len(registry["strict_flags"]),
                    "artifact_paths_total": len(registry["release_evidence_artifact_paths"]),
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
        return 0

    if changed:
        print(
            "[release-gate-registry-sync] ERROR: CI workflow is out of sync with release-gate registry. "
            "Run `python scripts/release-gate-registry-sync.py --write`.",
            file=sys.stderr,
        )
        return 1

    print(
        json.dumps(
            {
                "success": True,
                "mode": "check",
                "changed": False,
                "registry_file": str(registry_file),
                "ci_workflow_file": str(ci_workflow_file),
                "strict_flags_total": len(registry["strict_flags"]),
                "artifact_paths_total": len(registry["release_evidence_artifact_paths"]),
            },
            ensure_ascii=True,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
