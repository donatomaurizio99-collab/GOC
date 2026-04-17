param(
    [string]$PythonExe = "python",
    [string]$OutputFile = "artifacts\\p0-runbook-contract-check-report.json",
    [string]$RequiredRunbookScripts = "run-security-config-hardening-check.ps1,run-audit-trail-hardening-check.ps1,run-security-ci-lane-check.ps1,run-alert-routing-oncall-check.ps1,run-incident-drill-automation-check.ps1,run-load-profile-framework-check.ps1,run-canary-guardrails-check.ps1,run-rto-rpo-assertion-suite.ps1,run-disaster-recovery-rehearsal-pack.ps1,run-failure-budget-dashboard.ps1,run-safe-mode-ux-degradation-check.ps1,run-a11y-test-harness-check.ps1,run-power-loss-durability-drill.ps1,run-disk-pressure-fault-injection-drill.ps1,run-upgrade-downgrade-compatibility-drill.ps1,run-backup-restore-stress-drill.ps1,run-release-gate-runtime-stability-drill.ps1,run-p0-burnin-consecutive-green.ps1,run-p0-release-evidence-bundle.ps1,run-p0-closure-report.ps1",
    [string]$RequiredStrictFlags = "",
    [string]$StabilityCanaryBaselineFile = "docs\\stability-canary-baseline.json",
    [string]$RequiredCanaryDrills = "release_freeze_policy,db_corruption_quarantine,power_loss_durability,upgrade_downgrade_compatibility,db_safe_mode_watchdog,invariant_monitor_watchdog,event_consumer_recovery_chaos,invariant_burst,safe_mode_ux_degradation,a11y_test_harness,long_soak_budget"
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
