param(
    [string]$PythonExe = "python",
    [string]$PolicyFile = "docs\\release-gate-rollback-trigger-integrity-policy.json",
    [string]$RequiredReports = "artifacts/release-gate-hypercare-activation-release-gate.json,artifacts/auto-rollback-policy-release-gate.json,artifacts/incident-rollback-release-gate.json,artifacts/release-gate-slo-burn-rate-v2-release-gate.json",
    [string]$AutoRollbackReportFile = "artifacts\\auto-rollback-policy-release-gate.json",
    [string]$IncidentRollbackReportFile = "artifacts\\incident-rollback-release-gate.json",
    [string]$HypercareReportFile = "artifacts\\release-gate-hypercare-activation-release-gate.json",
    [string]$RequiredLabel = "release-gate",
    [string]$OutputFile = "artifacts\\release-gate-rollback-trigger-integrity-release-gate.json",
    [switch]$AllowMissingReports,
    [switch]$AllowNotReady
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$arguments = @(
    ".\scripts\release-gate-rollback-trigger-integrity-check.py",
    "--label", "manual",
    "--policy-file", $PolicyFile,
    "--required-reports", $RequiredReports,
    "--auto-rollback-report-file", $AutoRollbackReportFile,
    "--incident-rollback-report-file", $IncidentRollbackReportFile,
    "--hypercare-report-file", $HypercareReportFile,
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
    throw "Release-gate rollback trigger integrity check failed with exit code $LASTEXITCODE."
}

Write-Host "Release-gate rollback trigger integrity check passed." -ForegroundColor Green
