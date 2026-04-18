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
    "run-canary-determinism-flake-check.ps1",
    "run-power-loss-durability-drill.ps1",
    "run-disk-pressure-fault-injection-drill.ps1",
    "run-upgrade-downgrade-compatibility-drill.ps1",
    "run-backup-restore-stress-drill.ps1",
    "run-release-gate-runtime-stability-drill.ps1",
    "run-release-gate-performance-budget-check.ps1",
    "run-release-gate-evidence-freshness-check.ps1",
    "run-release-gate-evidence-hash-manifest-check.ps1",
    "run-release-gate-step-timing-schema-check.ps1",
    "run-release-gate-performance-history-check.ps1",
    "run-release-gate-stability-final-readiness.ps1",
    "run-release-gate-master-burnin-window-check.ps1",
    "run-release-gate-performance-policy-calibrate.ps1",
    "run-release-gate-staging-soak-readiness-check.ps1",
    "run-release-gate-rc-canary-rollout-check.ps1",
    "run-release-gate-evidence-lineage-check.ps1",
    "run-release-gate-production-readiness-certification-check.ps1",
    "run-release-gate-slo-burn-rate-v2-check.ps1",
    "run-release-gate-deploy-rehearsal-check.ps1",
    "run-release-gate-chaos-matrix-continuous-check.ps1",
    "run-release-gate-supply-chain-artifact-trust-check.ps1",
    "run-release-gate-operations-handoff-readiness-check.ps1",
    "run-release-gate-evidence-attestation-check.ps1",
    "run-release-gate-release-train-readiness-check.ps1",
    "run-release-gate-production-final-attestation-check.ps1",
    "run-release-gate-production-cutover-readiness-check.ps1",
    "run-release-gate-hypercare-activation-check.ps1",
    "run-release-gate-rollback-trigger-integrity-check.ps1",
    "run-release-gate-post-cutover-finalization-check.ps1",
    "run-release-gate-post-release-watch-check.ps1",
    "run-release-gate-steady-state-certification-check.ps1",
    "run-release-gate-post-release-continuity-check.ps1",
    "run-release-gate-production-sustainability-certification-check.ps1",
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
    "canary_determinism_flake_intelligence",
    "p0_report_schema_contract",
    "p0_runbook_contract",
    "p0_release_evidence_bundle",
    "p0_burnin_consecutive_green",
    "p0_closure_report",
    "long_soak_budget",
]

DEFAULT_REQUIRED_RELEASE_GATE_TOKENS = [
    "Release-gate artifact preflight (clean stale release-gate evidence)",
    '--required-label", "release-gate"',
    "--required-evidence-reports",
    "--step-timings-file",
    "release-gate-performance-budget-policy.json",
    "release-gate-evidence-freshness-policy.json",
    "release-gate-performance-history-baseline.json",
    "release-gate-evidence-manifest-release-gate.json",
    "Release-gate stability final readiness check (Stage L-P consolidated go/no-go)",
    "Release-gate staging soak readiness check (Stage Q incident/restore gate)",
    "Release-gate RC canary rollout check (Stage R rollout policy gate)",
    "Release-gate evidence lineage check (Stage S timestamp + manifest coherence gate)",
    "Release-gate production readiness certification (Stage T final go/no-go certificate)",
    "Release-gate SLO burn-rate v2 check (Stage U multi-window burn-rate gate)",
    "Release-gate deploy rehearsal check (Stage V deploy/rollback rehearsal gate)",
    "Release-gate chaos matrix continuous check (Stage W chaos continuity gate)",
    "Release-gate supply-chain artifact trust check (Stage X artifact trust gate)",
    "Release-gate operations handoff readiness check (Stage Y cross-gate handoff readiness)",
    "Release-gate evidence attestation check (Stage Z manifest attestation gate)",
    "Release-gate release-train readiness check (Stage AA expanded readiness gate)",
    "Release-gate production final attestation (Stage AB final go/no-go attestation)",
    "Release-gate production cutover readiness check (Stage AC cutover readiness gate)",
    "Release-gate hypercare activation check (Stage AD hypercare activation gate)",
    "Release-gate rollback trigger integrity check (Stage AE rollback integrity gate)",
    "Release-gate post-cutover finalization check (Stage AF production finalization gate)",
    "Release-gate post-release watch check (Stage AG post-release watch gate)",
    "Release-gate steady-state certification check (Stage AH steady-state production certificate)",
    "Release-gate post-release continuity check (Stage AI continuity gate)",
    "Release-gate production sustainability certification check (Stage AJ sustained production certificate)",
    "release-gate-evidence-lineage-check.py",
    "release-gate-production-readiness-certification.py",
    "release-gate-slo-burn-rate-v2-check.py",
    "release-gate-deploy-rehearsal-check.py",
    "release-gate-chaos-matrix-continuous-check.py",
    "release-gate-supply-chain-artifact-trust-check.py",
    "release-gate-operations-handoff-readiness-check.py",
    "release-gate-evidence-attestation-check.py",
    "release-gate-release-train-readiness-check.py",
    "release-gate-production-final-attestation.py",
    "release-gate-production-cutover-readiness-check.py",
    "release-gate-hypercare-activation-check.py",
    "release-gate-rollback-trigger-integrity-check.py",
    "release-gate-post-cutover-finalization-check.py",
    "release-gate-post-release-watch-check.py",
    "release-gate-steady-state-certification-check.py",
    "release-gate-post-release-continuity-check.py",
    "release-gate-production-sustainability-certification-check.py",
    "release-candidate-rollout-policy.json",
    "release-gate-slo-burn-rate-v2-policy.json",
    "release-gate-deploy-rehearsal-policy.json",
    "release-gate-chaos-matrix-policy.json",
    "release-gate-artifact-trust-policy.json",
    "release-gate-evidence-attestation-policy.json",
    "release-gate-production-cutover-policy.json",
    "release-gate-hypercare-policy.json",
    "release-gate-rollback-trigger-integrity-policy.json",
    "release-gate-post-cutover-finalization-policy.json",
    "release-gate-post-release-watch-policy.json",
    "release-gate-steady-state-certification-policy.json",
    "release-gate-post-release-continuity-policy.json",
    "release-gate-production-sustainability-certification-policy.json",
]

DEFAULT_REQUIRED_CI_ARTIFACT_PATHS = [
    "artifacts/p0-report-schema-contract-release-gate.json",
    "artifacts/p0-release-evidence-bundle-release-gate.json",
    "artifacts/p0-closure-report-release-gate.json",
    "artifacts/incident-rollback-release-gate.json",
    "artifacts/release-gate-step-timings-release-gate.json",
    "artifacts/release-gate-evidence-freshness-release-gate.json",
    "artifacts/release-gate-evidence-hash-manifest-release-gate.json",
    "artifacts/release-gate-evidence-manifest-release-gate.json",
    "artifacts/release-gate-step-timing-schema-release-gate.json",
    "artifacts/release-gate-performance-history-release-gate.json",
    "artifacts/release-gate-performance-budget-release-gate.json",
    "artifacts/release-gate-stability-final-readiness-release-gate.json",
    "artifacts/release-gate-staging-soak-readiness-release-gate.json",
    "artifacts/release-gate-rc-canary-rollout-release-gate.json",
    "artifacts/release-gate-evidence-lineage-release-gate.json",
    "artifacts/release-gate-production-readiness-certification-release-gate.json",
    "artifacts/release-gate-slo-burn-rate-v2-release-gate.json",
    "artifacts/release-gate-deploy-rehearsal-release-gate.json",
    "artifacts/release-gate-chaos-matrix-continuous-release-gate.json",
    "artifacts/release-gate-supply-chain-artifact-trust-release-gate.json",
    "artifacts/release-gate-operations-handoff-readiness-release-gate.json",
    "artifacts/release-gate-evidence-attestation-release-gate.json",
    "artifacts/release-gate-release-train-readiness-release-gate.json",
    "artifacts/release-gate-production-final-attestation-release-gate.json",
    "artifacts/release-gate-production-cutover-readiness-release-gate.json",
    "artifacts/release-gate-hypercare-activation-release-gate.json",
    "artifacts/release-gate-rollback-trigger-integrity-release-gate.json",
    "artifacts/release-gate-post-cutover-finalization-release-gate.json",
    "artifacts/release-gate-post-release-watch-release-gate.json",
    "artifacts/release-gate-steady-state-certification-release-gate.json",
    "artifacts/release-gate-post-release-continuity-release-gate.json",
    "artifacts/release-gate-production-sustainability-certification-release-gate.json",
]

DEFAULT_REQUIRED_RUNBOOK_TOKENS = [
    "metrics.label_mismatch_reports=0",
    "metrics.required_evidence_reports_missing=0",
    "metrics.required_evidence_reports_non_green=0",
    "metrics.stale_reports=0",
    "metrics.schema_failed_steps=0",
    "metrics.history_regression_violations=0",
    "metrics.steps_over_budget=0",
    "metrics.regression_budget_exceeded=0",
    "metrics.required_reports_non_green=0",
    "metrics.staging_reports_non_green=0",
    "metrics.incident_rollback_proof_failed=0",
    "metrics.restore_proof_failed=0",
    "metrics.rollout_required_reports_non_green=0",
    "metrics.rollout_policy_invalid=0",
    "metrics.lineage_reports_non_green=0",
    "metrics.invalid_timestamp_reports=0",
    "metrics.manifest_missing_entries=0",
    "metrics.reports_with_release_block_signal=0",
    "metrics.burnin_threshold_failed=0",
    "metrics.slo_burn_rate_non_green=0",
    "metrics.burn_rate_violations=0",
    "metrics.non_ok_window_violations=0",
    "metrics.deploy_rehearsal_non_green=0",
    "metrics.deploy_rehearsal_policy_invalid=0",
    "metrics.deploy_rehearsal_rollback_failed=0",
    "metrics.deploy_rehearsal_restore_failed=0",
    "metrics.chaos_required_reports_non_green=0",
    "metrics.chaos_failed_scenarios=0",
    "metrics.chaos_regression_violations=0",
    "metrics.artifact_trust_reports_non_green=0",
    "metrics.artifact_trust_missing_entries=0",
    "metrics.artifact_trust_unverified_entries=0",
    "metrics.ops_handoff_reports_non_green=0",
    "metrics.ops_handoff_release_block_signals=0",
    "metrics.evidence_attestation_reports_non_green=0",
    "metrics.evidence_attestation_missing_entries=0",
    "metrics.evidence_attestation_unverified_entries=0",
    "metrics.release_train_reports_non_green=0",
    "metrics.release_train_block_signals=0",
    "metrics.final_attestation_reports_non_green=0",
    "metrics.final_attestation_block_signals=0",
    "metrics.cutover_reports_non_green=0",
    "metrics.cutover_release_block_signals=0",
    "metrics.hypercare_reports_non_green=0",
    "metrics.hypercare_release_block_signals=0",
    "metrics.rollback_integrity_reports_non_green=0",
    "metrics.rollback_integrity_expected_reason_mismatches=0",
    "metrics.rollback_integrity_trigger_reason_violations=0",
    "metrics.post_cutover_reports_non_green=0",
    "metrics.post_cutover_release_block_signals=0",
    "metrics.post_cutover_final_signal_failed=0",
    "metrics.post_release_watch_reports_non_green=0",
    "metrics.post_release_watch_release_block_signals=0",
    "metrics.post_release_watch_non_ok_window_violations=0",
    "metrics.post_release_watch_chaos_regression_violations=0",
    "metrics.post_release_watch_finalization_signal_failed=0",
    "metrics.steady_state_reports_non_green=0",
    "metrics.steady_state_release_block_signals=0",
    "metrics.steady_state_watch_signal_failed=0",
    "metrics.steady_state_burnin_threshold_failed=0",
    "metrics.steady_state_closure_signal_failed=0",
    "metrics.post_release_continuity_reports_non_green=0",
    "metrics.post_release_continuity_release_block_signals=0",
    "metrics.post_release_continuity_watch_signal_failed=0",
    "metrics.post_release_continuity_steady_state_signal_failed=0",
    "metrics.post_release_continuity_freshness_budget_violations=0",
    "metrics.post_release_continuity_attestation_budget_violations=0",
    "metrics.production_sustainability_reports_non_green=0",
    "metrics.production_sustainability_release_block_signals=0",
    "metrics.production_sustainability_continuity_signal_failed=0",
    "metrics.production_sustainability_steady_state_signal_failed=0",
    "metrics.production_sustainability_production_final_signal_failed=0",
    "metrics.production_sustainability_burnin_threshold_failed=0",
    "metrics.production_sustainability_closure_signal_failed=0",
]

DEFAULT_REGISTRY_FILE = "docs/release-gate-registry.json"


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


def _normalize_registry_string_list(
    payload: dict[str, Any],
    key: str,
    *,
    context: str,
) -> list[str]:
    raw = payload.get(key)
    _expect(isinstance(raw, list), f"Registry key '{key}' must be a list in {context}.")

    normalized: list[str] = []
    duplicates: list[str] = []
    seen: set[str] = set()

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
    _expect(normalized, f"Registry key '{key}' must contain at least one token in {context}.")
    return normalized


def _load_registry_defaults(registry_file: Path) -> dict[str, list[str]]:
    payload = _read_json_object(registry_file)
    p0_contract = payload.get("p0_runbook_contract")
    _expect(isinstance(p0_contract, dict), "Registry key 'p0_runbook_contract' must be an object.")

    keys = [
        "required_runbook_scripts",
        "required_canary_drills",
        "required_release_gate_tokens",
        "required_ci_artifact_paths",
        "required_runbook_tokens",
    ]
    return {
        key: _normalize_registry_string_list(p0_contract, key, context="p0_runbook_contract")
        for key in keys
    }


def _resolve_required_list(
    override_csv: str,
    *,
    registry_values: list[str],
    fallback_values: list[str],
) -> list[str]:
    parsed_override = _parse_csv_list(override_csv)
    if parsed_override:
        return parsed_override
    if registry_values:
        return list(registry_values)
    return list(fallback_values)


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
            "across release-gate.ps1, CI workflow, production runbook, stability canary baseline, "
            "and release-gate registry defaults."
        )
    )
    parser.add_argument("--label", default="p0-runbook-contract-check")
    parser.add_argument("--project-root")
    parser.add_argument("--runbook-file", default="docs/production-runbook.md")
    parser.add_argument("--release-gate-file", default="scripts/release-gate.ps1")
    parser.add_argument("--ci-workflow-file", default=".github/workflows/ci.yml")
    parser.add_argument("--stability-canary-baseline-file", default="docs/stability-canary-baseline.json")
    parser.add_argument("--registry-file", default=DEFAULT_REGISTRY_FILE)
    parser.add_argument("--required-runbook-scripts", default="")
    parser.add_argument("--required-strict-flags", default="")
    parser.add_argument("--required-canary-drills", default="")
    parser.add_argument("--required-release-gate-tokens", default="")
    parser.add_argument("--required-ci-artifact-paths", default="")
    parser.add_argument("--required-runbook-tokens", default="")
    parser.add_argument("--output-file")
    args = parser.parse_args(argv)

    inferred_project_root = Path(__file__).resolve().parents[1]
    project_root = _resolve_path(inferred_project_root, args.project_root) if args.project_root else inferred_project_root
    runbook_file = _resolve_path(project_root, args.runbook_file)
    release_gate_file = _resolve_path(project_root, args.release_gate_file)
    ci_workflow_file = _resolve_path(project_root, args.ci_workflow_file)
    stability_canary_baseline_file = _resolve_path(project_root, args.stability_canary_baseline_file)
    registry_file = _resolve_path(project_root, args.registry_file)
    output_file = _resolve_path(project_root, args.output_file) if args.output_file else None

    registry_defaults: dict[str, list[str]] = {}
    if registry_file.exists():
        registry_defaults = _load_registry_defaults(registry_file)
    else:
        no_explicit_contract_overrides = not any(
            [
                _parse_csv_list(args.required_runbook_scripts),
                _parse_csv_list(args.required_canary_drills),
                _parse_csv_list(args.required_release_gate_tokens),
                _parse_csv_list(args.required_ci_artifact_paths),
                _parse_csv_list(args.required_runbook_tokens),
            ]
        )
        if no_explicit_contract_overrides:
            print(
                f"[p0-runbook-contract-check] ERROR: registry file not found: {registry_file}",
                file=sys.stderr,
            )
            return 2

    required_runbook_scripts = _resolve_required_list(
        args.required_runbook_scripts,
        registry_values=registry_defaults.get("required_runbook_scripts", []),
        fallback_values=DEFAULT_REQUIRED_RUNBOOK_SCRIPTS,
    )
    if not required_runbook_scripts:
        print("[p0-runbook-contract-check] ERROR: at least one required runbook script is required.", file=sys.stderr)
        return 2
    required_strict_flags = _parse_csv_list(args.required_strict_flags)
    required_canary_drills = _resolve_required_list(
        args.required_canary_drills,
        registry_values=registry_defaults.get("required_canary_drills", []),
        fallback_values=DEFAULT_REQUIRED_CANARY_DRILLS,
    )
    required_release_gate_tokens = _resolve_required_list(
        args.required_release_gate_tokens,
        registry_values=registry_defaults.get("required_release_gate_tokens", []),
        fallback_values=DEFAULT_REQUIRED_RELEASE_GATE_TOKENS,
    )
    required_ci_artifact_paths = _resolve_required_list(
        args.required_ci_artifact_paths,
        registry_values=registry_defaults.get("required_ci_artifact_paths", []),
        fallback_values=DEFAULT_REQUIRED_CI_ARTIFACT_PATHS,
    )
    required_runbook_tokens = _resolve_required_list(
        args.required_runbook_tokens,
        registry_values=registry_defaults.get("required_runbook_tokens", []),
        fallback_values=DEFAULT_REQUIRED_RUNBOOK_TOKENS,
    )
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
