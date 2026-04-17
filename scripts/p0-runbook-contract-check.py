from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any


DEFAULT_REQUIRED_RUNBOOK_SCRIPTS = [
    "run-security-config-hardening-check.ps1",
    "run-audit-trail-hardening-check.ps1",
    "run-security-ci-lane-check.ps1",
    "run-alert-routing-oncall-check.ps1",
    "run-incident-drill-automation-check.ps1",
    "run-load-profile-framework-check.ps1",
    "run-canary-guardrails-check.ps1",
    "run-rto-rpo-assertion-suite.ps1",
    "run-disaster-recovery-rehearsal-pack.ps1",
    "run-failure-budget-dashboard.ps1",
    "run-safe-mode-ux-degradation-check.ps1",
    "run-a11y-test-harness-check.ps1",
    "run-power-loss-durability-drill.ps1",
    "run-disk-pressure-fault-injection-drill.ps1",
    "run-upgrade-downgrade-compatibility-drill.ps1",
    "run-backup-restore-stress-drill.ps1",
    "run-release-gate-runtime-stability-drill.ps1",
    "run-p0-burnin-consecutive-green.ps1",
    "run-p0-release-evidence-bundle.ps1",
    "run-p0-report-schema-contract-check.ps1",
    "run-p0-closure-report.ps1",
]

DEFAULT_REQUIRED_CANARY_DRILLS = [
    "release_freeze_policy",
    "db_corruption_quarantine",
    "power_loss_durability",
    "upgrade_downgrade_compatibility",
    "db_safe_mode_watchdog",
    "invariant_monitor_watchdog",
    "event_consumer_recovery_chaos",
    "invariant_burst",
    "safe_mode_ux_degradation",
    "a11y_test_harness",
    "p0_report_schema_contract",
    "p0_runbook_contract",
    "long_soak_budget",
]

DEFAULT_REQUIRED_RELEASE_GATE_TOKENS = [
    "Release-gate artifact preflight (clean stale release-gate evidence)",
    '--required-label", "release-gate"',
    "--required-evidence-reports",
]

DEFAULT_REQUIRED_CI_ARTIFACT_PATHS = [
    "artifacts/p0-report-schema-contract-release-gate.json",
    "artifacts/p0-release-evidence-bundle-release-gate.json",
    "artifacts/p0-closure-report-release-gate.json",
]

DEFAULT_REQUIRED_RUNBOOK_TOKENS = [
    "metrics.label_mismatch_reports=0",
    "metrics.required_evidence_reports_missing=0",
    "metrics.required_evidence_reports_non_green=0",
]


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _read_text(path: Path) -> str:
    _expect(path.exists(), f"Required file not found: {path}")
    return path.read_text(encoding="utf-8")


def _parse_csv_list(text: str) -> list[str]:
    return [item.strip() for item in str(text).split(",") if item.strip()]


def _read_json_object(path: Path) -> dict[str, Any]:
    raw = _read_text(path)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Failed to parse JSON file {path}: {exc}") from exc
    _expect(isinstance(payload, dict), f"JSON file must contain an object: {path}")
    return payload


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
    stability_canary_baseline_file: Path,
    required_runbook_scripts: list[str],
    required_strict_flags: list[str],
    required_canary_drills: list[str],
    required_release_gate_tokens: list[str],
    required_ci_artifact_paths: list[str],
    required_runbook_tokens: list[str],
) -> dict[str, Any]:
    started = time.perf_counter()
    runbook_text = _read_text(runbook_file)
    release_gate_text = _read_text(release_gate_file)
    ci_workflow_text = _read_text(ci_workflow_file)
    canary_baseline = _read_json_object(stability_canary_baseline_file)

    strict_flags_from_gate = _extract_release_gate_strict_flags(release_gate_text)
    combined_required_flags = sorted(set(strict_flags_from_gate + required_strict_flags))
    missing_in_ci = [flag for flag in combined_required_flags if f"-{flag}" not in ci_workflow_text]
    missing_in_runbook = [flag for flag in combined_required_flags if f"-{flag}" not in runbook_text]
    missing_required_release_gate_tokens = [
        token for token in required_release_gate_tokens if token not in release_gate_text
    ]
    missing_required_ci_artifact_paths = [
        path_token for path_token in required_ci_artifact_paths if path_token not in ci_workflow_text
    ]
    missing_required_runbook_tokens = [
        token for token in required_runbook_tokens if token not in runbook_text
    ]

    runbook_script_refs = _extract_runbook_script_references(runbook_text)
    missing_required_runbook_scripts = [
        script_name for script_name in required_runbook_scripts if script_name not in runbook_script_refs
    ]
    missing_script_files = [
        script_name
        for script_name in runbook_script_refs
        if not (project_root / "scripts" / script_name).exists()
    ]
    canary_drills = canary_baseline.get("drills")
    _expect(
        isinstance(canary_drills, dict),
        f"stability canary baseline must contain an object in 'drills': {stability_canary_baseline_file}",
    )

    missing_required_canary_drills: list[str] = []
    invalid_canary_baseline_durations: list[dict[str, Any]] = []
    for drill_name in required_canary_drills:
        drill_entry = canary_drills.get(drill_name)
        if not isinstance(drill_entry, dict):
            missing_required_canary_drills.append(drill_name)
            continue
        duration_raw = drill_entry.get("baseline_duration_seconds", 0.0)
        try:
            baseline_duration = float(duration_raw)
        except (TypeError, ValueError):
            baseline_duration = 0.0
        if baseline_duration <= 0:
            invalid_canary_baseline_durations.append(
                {
                    "drill": drill_name,
                    "baseline_duration_seconds": duration_raw,
                }
            )

    success = (
        not missing_in_ci
        and not missing_in_runbook
        and not missing_required_runbook_scripts
        and not missing_script_files
        and not missing_required_canary_drills
        and not invalid_canary_baseline_durations
        and not missing_required_release_gate_tokens
        and not missing_required_ci_artifact_paths
        and not missing_required_runbook_tokens
    )
    report = {
        "label": label,
        "success": bool(success),
        "paths": {
            "project_root": str(project_root),
            "runbook_file": str(runbook_file),
            "release_gate_file": str(release_gate_file),
            "ci_workflow_file": str(ci_workflow_file),
            "stability_canary_baseline_file": str(stability_canary_baseline_file),
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
            "required_canary_drills": required_canary_drills,
            "canary_baseline_drill_names": sorted(canary_drills.keys()),
            "missing_required_canary_drills": missing_required_canary_drills,
            "invalid_canary_baseline_durations": invalid_canary_baseline_durations,
            "required_release_gate_tokens": required_release_gate_tokens,
            "missing_required_release_gate_tokens": missing_required_release_gate_tokens,
            "required_ci_artifact_paths": required_ci_artifact_paths,
            "missing_required_ci_artifact_paths": missing_required_ci_artifact_paths,
            "required_runbook_tokens": required_runbook_tokens,
            "missing_required_runbook_tokens": missing_required_runbook_tokens,
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
            "across release-gate.ps1, CI workflow, production runbook, and stability canary baseline."
        )
    )
    parser.add_argument("--label", default="p0-runbook-contract-check")
    parser.add_argument("--project-root")
    parser.add_argument("--runbook-file", default="docs/production-runbook.md")
    parser.add_argument("--release-gate-file", default="scripts/release-gate.ps1")
    parser.add_argument("--ci-workflow-file", default=".github/workflows/ci.yml")
    parser.add_argument("--stability-canary-baseline-file", default="docs/stability-canary-baseline.json")
    parser.add_argument("--required-runbook-scripts", default=",".join(DEFAULT_REQUIRED_RUNBOOK_SCRIPTS))
    parser.add_argument("--required-strict-flags", default="")
    parser.add_argument("--required-canary-drills", default=",".join(DEFAULT_REQUIRED_CANARY_DRILLS))
    parser.add_argument("--required-release-gate-tokens", default=",".join(DEFAULT_REQUIRED_RELEASE_GATE_TOKENS))
    parser.add_argument("--required-ci-artifact-paths", default=",".join(DEFAULT_REQUIRED_CI_ARTIFACT_PATHS))
    parser.add_argument("--required-runbook-tokens", default=",".join(DEFAULT_REQUIRED_RUNBOOK_TOKENS))
    parser.add_argument("--output-file")
    args = parser.parse_args(argv)

    inferred_project_root = Path(__file__).resolve().parents[1]
    project_root = _resolve_path(inferred_project_root, args.project_root) if args.project_root else inferred_project_root
    runbook_file = _resolve_path(project_root, args.runbook_file)
    release_gate_file = _resolve_path(project_root, args.release_gate_file)
    ci_workflow_file = _resolve_path(project_root, args.ci_workflow_file)
    stability_canary_baseline_file = _resolve_path(project_root, args.stability_canary_baseline_file)
    output_file = _resolve_path(project_root, args.output_file) if args.output_file else None

    required_runbook_scripts = _parse_csv_list(args.required_runbook_scripts)
    if not required_runbook_scripts:
        print("[p0-runbook-contract-check] ERROR: at least one required runbook script is required.", file=sys.stderr)
        return 2
    required_strict_flags = _parse_csv_list(args.required_strict_flags)
    required_canary_drills = _parse_csv_list(args.required_canary_drills)
    required_release_gate_tokens = _parse_csv_list(args.required_release_gate_tokens)
    required_ci_artifact_paths = _parse_csv_list(args.required_ci_artifact_paths)
    required_runbook_tokens = _parse_csv_list(args.required_runbook_tokens)
    if not required_canary_drills:
        print("[p0-runbook-contract-check] ERROR: at least one required canary drill is required.", file=sys.stderr)
        return 2
    if not required_release_gate_tokens:
        print("[p0-runbook-contract-check] ERROR: at least one required release-gate token is required.", file=sys.stderr)
        return 2
    if not required_ci_artifact_paths:
        print("[p0-runbook-contract-check] ERROR: at least one required CI artifact path is required.", file=sys.stderr)
        return 2
    if not required_runbook_tokens:
        print("[p0-runbook-contract-check] ERROR: at least one required runbook token is required.", file=sys.stderr)
        return 2

    try:
        report = run_contract_check(
            label=str(args.label),
            project_root=project_root,
            runbook_file=runbook_file,
            release_gate_file=release_gate_file,
            ci_workflow_file=ci_workflow_file,
            stability_canary_baseline_file=stability_canary_baseline_file,
            required_runbook_scripts=required_runbook_scripts,
            required_strict_flags=required_strict_flags,
            required_canary_drills=required_canary_drills,
            required_release_gate_tokens=required_release_gate_tokens,
            required_ci_artifact_paths=required_ci_artifact_paths,
            required_runbook_tokens=required_runbook_tokens,
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
