param(
    [string]$PythonExe = "python",
    [string]$RequiredReports = "artifacts/canary-guardrails-release-gate.json,artifacts/auto-rollback-policy-release-gate.json,artifacts/p0-disaster-recovery-rehearsal-pack-release-gate.json,artifacts/failure-budget-dashboard-release-gate.json",
    [string]$CanaryReportFile = "artifacts\canary-guardrails-release-gate.json",
    [string]$RollbackReportFile = "artifacts\auto-rollback-policy-release-gate.json",
    [string]$DisasterRecoveryReportFile = "artifacts\p0-disaster-recovery-rehearsal-pack-release-gate.json",
    [string]$FailureBudgetReportFile = "artifacts\failure-budget-dashboard-release-gate.json",
    [string]$RequiredLabel = "release-gate",
    [int]$RequiredCanaryStageCount = 4,
    [string]$OutputFile = "artifacts\release-gate-staging-soak-readiness-release-gate.json",
    [switch]$AllowMissingReports,
    [switch]$AllowNotReady
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$arguments = @(
    ".\scripts\release-gate-staging-soak-readiness-check.py",
    "--label", "manual",
    "--required-reports", $RequiredReports,
    "--canary-report-file", $CanaryReportFile,
    "--rollback-report-file", $RollbackReportFile,
    "--disaster-recovery-report-file", $DisasterRecoveryReportFile,
    "--failure-budget-report-file", $FailureBudgetReportFile,
    "--required-label", $RequiredLabel,
    "--required-canary-stage-count", [string]$RequiredCanaryStageCount,
    "--output-file", $OutputFile
)
if ($AllowMissingReports) {
    $arguments += "--allow-missing-reports"
}
if ($AllowNotReady) {
    $arguments += "--allow-not-ready"
}

& $PythonExe @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Release-gate staging soak readiness check failed with exit code $LASTEXITCODE."
}

Write-Host "Release-gate staging soak readiness check passed." -ForegroundColor Green
