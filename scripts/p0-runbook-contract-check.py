from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any


DEFAULT_REQUIRED_RUNBOOK_SCRIPTS = [
    "run-power-loss-durability-drill.ps1",
    "run-disk-pressure-fault-injection-drill.ps1",
    "run-upgrade-downgrade-compatibility-drill.ps1",
    "run-backup-restore-stress-drill.ps1",
    "run-release-gate-runtime-stability-drill.ps1",
    "run-p0-burnin-consecutive-green.ps1",
    "run-p0-release-evidence-bundle.ps1",
]


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


def _extract_release_gate_strict_flags(release_gate_text: str) -> list[str]:
    flags = sorted({match.group(1) for match in re.finditer(r"\$((?:Strict)[A-Za-z0-9]+)", release_gate_text)})
    _expect(flags, "No Strict* flags found in release-gate script.")
    return flags


def _extract_runbook_script_references(runbook_text: str) -> list[str]:
    pattern = re.compile(r"(?im)^\s*\.\\scripts\\([A-Za-z0-9._-]+)")
    refs = sorted({match.group(1) for match in pattern.finditer(runbook_text)})
    _expect(refs, "No .\\scripts\\* command references found in runbook.")
    return refs


def run_contract_check(
    *,
    label: str,
    project_root: Path,
    runbook_file: Path,
    release_gate_file: Path,
    ci_workflow_file: Path,
    required_runbook_scripts: list[str],
    required_strict_flags: list[str],
) -> dict[str, Any]:
    started = time.perf_counter()
    runbook_text = _read_text(runbook_file)
    release_gate_text = _read_text(release_gate_file)
    ci_workflow_text = _read_text(ci_workflow_file)

    strict_flags_from_gate = _extract_release_gate_strict_flags(release_gate_text)
    combined_required_flags = sorted(set(strict_flags_from_gate + required_strict_flags))
    missing_in_ci = [flag for flag in combined_required_flags if f"-{flag}" not in ci_workflow_text]
    missing_in_runbook = [flag for flag in combined_required_flags if f"-{flag}" not in runbook_text]

    runbook_script_refs = _extract_runbook_script_references(runbook_text)
    missing_required_runbook_scripts = [
        script_name for script_name in required_runbook_scripts if script_name not in runbook_script_refs
    ]
    missing_script_files = [
        script_name
        for script_name in runbook_script_refs
        if not (project_root / "scripts" / script_name).exists()
    ]

    success = (
        not missing_in_ci
        and not missing_in_runbook
        and not missing_required_runbook_scripts
        and not missing_script_files
    )
    report = {
        "label": label,
        "success": bool(success),
        "paths": {
            "project_root": str(project_root),
            "runbook_file": str(runbook_file),
            "release_gate_file": str(release_gate_file),
            "ci_workflow_file": str(ci_workflow_file),
        },
        "checks": {
            "strict_flags_from_release_gate": strict_flags_from_gate,
            "required_strict_flags": combined_required_flags,
            "missing_strict_flags_in_ci_workflow": missing_in_ci,
            "missing_strict_flags_in_runbook": missing_in_runbook,
            "runbook_script_references": runbook_script_refs,
            "required_runbook_scripts": required_runbook_scripts,
            "missing_required_runbook_scripts": missing_required_runbook_scripts,
            "missing_script_files_for_runbook_references": missing_script_files,
        },
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }
    if not success:
        raise RuntimeError(f"P0 runbook contract check failed: {json.dumps(report, sort_keys=True)}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify P0 release-gate strict-flag and runbook command contract consistency "
            "across release-gate.ps1, CI workflow, and production runbook."
        )
    )
    parser.add_argument("--label", default="p0-runbook-contract-check")
    parser.add_argument("--project-root")
    parser.add_argument("--runbook-file", default="docs/production-runbook.md")
    parser.add_argument("--release-gate-file", default="scripts/release-gate.ps1")
    parser.add_argument("--ci-workflow-file", default=".github/workflows/ci.yml")
    parser.add_argument("--required-runbook-scripts", default=",".join(DEFAULT_REQUIRED_RUNBOOK_SCRIPTS))
    parser.add_argument("--required-strict-flags", default="")
    parser.add_argument("--output-file")
    args = parser.parse_args(argv)

    inferred_project_root = Path(__file__).resolve().parents[1]
    project_root = _resolve_path(inferred_project_root, args.project_root) if args.project_root else inferred_project_root
    runbook_file = _resolve_path(project_root, args.runbook_file)
    release_gate_file = _resolve_path(project_root, args.release_gate_file)
    ci_workflow_file = _resolve_path(project_root, args.ci_workflow_file)
    output_file = _resolve_path(project_root, args.output_file) if args.output_file else None

    required_runbook_scripts = _parse_csv_list(args.required_runbook_scripts)
    if not required_runbook_scripts:
        print("[p0-runbook-contract-check] ERROR: at least one required runbook script is required.", file=sys.stderr)
        return 2
    required_strict_flags = _parse_csv_list(args.required_strict_flags)

    try:
        report = run_contract_check(
            label=str(args.label),
            project_root=project_root,
            runbook_file=runbook_file,
            release_gate_file=release_gate_file,
            ci_workflow_file=ci_workflow_file,
            required_runbook_scripts=required_runbook_scripts,
            required_strict_flags=required_strict_flags,
        )
    except Exception as exc:
        print(f"[p0-runbook-contract-check] ERROR: {exc}", file=sys.stderr)
        return 1

    if output_file is not None:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2), encoding="utf-8")

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
