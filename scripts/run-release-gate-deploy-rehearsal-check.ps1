param(
    [string]$PythonExe = "python",
    [string]$PolicyFile = "docs\\release-gate-deploy-rehearsal-policy.json",
    [string]$RequiredReports = "artifacts/release-gate-production-readiness-certification-release-gate.json,artifacts/release-gate-rc-canary-rollout-release-gate.json,artifacts/auto-rollback-policy-release-gate.json,artifacts/p0-disaster-recovery-rehearsal-pack-release-gate.json",
    [string]$RollbackReportFile = "artifacts\\auto-rollback-policy-release-gate.json",
    [string]$DisasterRecoveryReportFile = "artifacts\\p0-disaster-recovery-rehearsal-pack-release-gate.json",
    [string]$RequiredLabel = "release-gate",
    [string]$OutputFile = "artifacts\\release-gate-deploy-rehearsal-release-gate.json",
    [switch]$AllowMissingReports,
    [switch]$AllowNotReady
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$arguments = @(
    ".\scripts\release-gate-deploy-rehearsal-check.py",
    "--label", "manual",
    "--policy-file", $PolicyFile,
    "--required-reports", $RequiredReports,
    "--rollback-report-file", $RollbackReportFile,
    "--disaster-recovery-report-file", $DisasterRecoveryReportFile,
    "--required-label", $RequiredLabel,
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
    throw "Release-gate deploy rehearsal check failed with exit code $LASTEXITCODE."
}

Write-Host "Release-gate deploy rehearsal check passed." -ForegroundColor Green
