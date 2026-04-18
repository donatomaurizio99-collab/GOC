param(
    [string]$PythonExe = "python",
    [string]$PolicyFile = "docs\\release-gate-hypercare-policy.json",
    [string]$RequiredReports = "artifacts/release-gate-production-cutover-readiness-release-gate.json,artifacts/release-gate-production-final-attestation-release-gate.json,artifacts/release-gate-slo-burn-rate-v2-release-gate.json,artifacts/failure-budget-dashboard-release-gate.json",
    [string]$CutoverReportFile = "artifacts\\release-gate-production-cutover-readiness-release-gate.json",
    [string]$BurnRateReportFile = "artifacts\\release-gate-slo-burn-rate-v2-release-gate.json",
    [string]$RequiredLabel = "release-gate",
    [string]$OutputFile = "artifacts\\release-gate-hypercare-activation-release-gate.json",
    [switch]$AllowMissingReports,
    [switch]$AllowNotReady
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$arguments = @(
    ".\scripts\release-gate-hypercare-activation-check.py",
    "--label", "manual",
    "--policy-file", $PolicyFile,
    "--required-reports", $RequiredReports,
    "--cutover-report-file", $CutoverReportFile,
    "--burn-rate-report-file", $BurnRateReportFile,
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
    throw "Release-gate hypercare activation check failed with exit code $LASTEXITCODE."
}

Write-Host "Release-gate hypercare activation check passed." -ForegroundColor Green
