param(
    [string]$PythonExe = "python",
    [string]$OutputFile = "artifacts\\p0-runbook-contract-check-report.json",
    [string]$RequiredRunbookScripts = "run-security-config-hardening-check.ps1,run-audit-trail-hardening-check.ps1,run-security-ci-lane-check.ps1,run-alert-routing-oncall-check.ps1,run-incident-drill-automation-check.ps1,run-load-profile-framework-check.ps1,run-canary-guardrails-check.ps1,run-rto-rpo-assertion-suite.ps1,run-disaster-recovery-rehearsal-pack.ps1,run-failure-budget-dashboard.ps1,run-safe-mode-ux-degradation-check.ps1,run-a11y-test-harness-check.ps1,run-canary-determinism-flake-check.ps1,run-power-loss-durability-drill.ps1,run-disk-pressure-fault-injection-drill.ps1,run-upgrade-downgrade-compatibility-drill.ps1,run-backup-restore-stress-drill.ps1,run-release-gate-runtime-stability-drill.ps1,run-release-gate-evidence-freshness-check.ps1,run-release-gate-evidence-hash-manifest-check.ps1,run-release-gate-step-timing-schema-check.ps1,run-release-gate-performance-history-check.ps1,run-release-gate-performance-budget-check.ps1,run-release-gate-stability-final-readiness.ps1,run-release-gate-master-burnin-window-check.ps1,run-release-gate-performance-policy-calibrate.ps1,run-release-gate-staging-soak-readiness-check.ps1,run-release-gate-rc-canary-rollout-check.ps1,run-release-gate-evidence-lineage-check.ps1,run-release-gate-production-readiness-certification-check.ps1,run-release-gate-slo-burn-rate-v2-check.ps1,run-release-gate-deploy-rehearsal-check.ps1,run-release-gate-chaos-matrix-continuous-check.ps1,run-release-gate-supply-chain-artifact-trust-check.ps1,run-release-gate-operations-handoff-readiness-check.ps1,run-release-gate-evidence-attestation-check.ps1,run-release-gate-release-train-readiness-check.ps1,run-release-gate-production-final-attestation-check.ps1,run-p0-burnin-consecutive-green.ps1,run-p0-release-evidence-bundle.ps1,run-p0-report-schema-contract-check.ps1,run-p0-closure-report.ps1",
    [string]$RequiredStrictFlags = "",
    [string]$StabilityCanaryBaselineFile = "docs\\stability-canary-baseline.json",
    [string]$RequiredCanaryDrills = "release_freeze_policy,db_corruption_quarantine,power_loss_durability,upgrade_downgrade_compatibility,db_safe_mode_watchdog,invariant_monitor_watchdog,event_consumer_recovery_chaos,invariant_burst,safe_mode_ux_degradation,a11y_test_harness,canary_determinism_flake_intelligence,p0_report_schema_contract,p0_runbook_contract,p0_release_evidence_bundle,p0_burnin_consecutive_green,p0_closure_report,long_soak_budget",
    [string]$RequiredCiArtifactPaths = "artifacts/p0-report-schema-contract-release-gate.json,artifacts/p0-release-evidence-bundle-release-gate.json,artifacts/p0-closure-report-release-gate.json,artifacts/release-gate-step-timings-release-gate.json,artifacts/release-gate-evidence-freshness-release-gate.json,artifacts/release-gate-evidence-hash-manifest-release-gate.json,artifacts/release-gate-evidence-manifest-release-gate.json,artifacts/release-gate-step-timing-schema-release-gate.json,artifacts/release-gate-performance-history-release-gate.json,artifacts/release-gate-performance-budget-release-gate.json,artifacts/release-gate-stability-final-readiness-release-gate.json,artifacts/release-gate-staging-soak-readiness-release-gate.json,artifacts/release-gate-rc-canary-rollout-release-gate.json,artifacts/release-gate-evidence-lineage-release-gate.json,artifacts/release-gate-production-readiness-certification-release-gate.json,artifacts/release-gate-slo-burn-rate-v2-release-gate.json,artifacts/release-gate-deploy-rehearsal-release-gate.json,artifacts/release-gate-chaos-matrix-continuous-release-gate.json,artifacts/release-gate-supply-chain-artifact-trust-release-gate.json,artifacts/release-gate-operations-handoff-readiness-release-gate.json,artifacts/release-gate-evidence-attestation-release-gate.json,artifacts/release-gate-release-train-readiness-release-gate.json,artifacts/release-gate-production-final-attestation-release-gate.json",
    [string]$RequiredRunbookTokens = "metrics.label_mismatch_reports=0,metrics.required_evidence_reports_missing=0,metrics.required_evidence_reports_non_green=0,metrics.stale_reports=0,metrics.schema_failed_steps=0,metrics.history_regression_violations=0,metrics.steps_over_budget=0,metrics.regression_budget_exceeded=0,metrics.required_reports_non_green=0,metrics.staging_reports_non_green=0,metrics.incident_rollback_proof_failed=0,metrics.restore_proof_failed=0,metrics.rollout_required_reports_non_green=0,metrics.rollout_policy_invalid=0,metrics.lineage_reports_non_green=0,metrics.invalid_timestamp_reports=0,metrics.manifest_missing_entries=0,metrics.reports_with_release_block_signal=0,metrics.burnin_threshold_failed=0,metrics.slo_burn_rate_non_green=0,metrics.burn_rate_violations=0,metrics.non_ok_window_violations=0,metrics.deploy_rehearsal_non_green=0,metrics.deploy_rehearsal_policy_invalid=0,metrics.deploy_rehearsal_rollback_failed=0,metrics.deploy_rehearsal_restore_failed=0,metrics.chaos_required_reports_non_green=0,metrics.chaos_failed_scenarios=0,metrics.chaos_regression_violations=0,metrics.artifact_trust_reports_non_green=0,metrics.artifact_trust_missing_entries=0,metrics.artifact_trust_unverified_entries=0,metrics.ops_handoff_reports_non_green=0,metrics.ops_handoff_release_block_signals=0,metrics.evidence_attestation_reports_non_green=0,metrics.evidence_attestation_missing_entries=0,metrics.evidence_attestation_unverified_entries=0,metrics.release_train_reports_non_green=0,metrics.release_train_block_signals=0,metrics.final_attestation_reports_non_green=0,metrics.final_attestation_block_signals=0"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$arguments = @(
    ".\\scripts\\p0-runbook-contract-check.py",
    "--label", "manual",
    "--required-runbook-scripts", $RequiredRunbookScripts,
    "--stability-canary-baseline-file", $StabilityCanaryBaselineFile,
    "--required-canary-drills", $RequiredCanaryDrills,
    "--required-ci-artifact-paths", $RequiredCiArtifactPaths,
    "--required-runbook-tokens", $RequiredRunbookTokens,
    "--output-file", $OutputFile
)
if (-not [string]::IsNullOrWhiteSpace($RequiredStrictFlags)) {
    $arguments += @("--required-strict-flags", $RequiredStrictFlags)
}

& $PythonExe @arguments
if ($LASTEXITCODE -ne 0) {
    throw "P0 runbook contract check failed with exit code $LASTEXITCODE."
}

Write-Host "P0 runbook contract check passed." -ForegroundColor Green
