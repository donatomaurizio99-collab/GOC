param(
    [string]$PythonExe = "python",
    [string]$PolicyFile = "docs\\release-gate-post-release-watch-policy.json",
    [string]$RequiredReports = "artifacts/release-gate-post-cutover-finalization-release-gate.json,artifacts/release-gate-slo-burn-rate-v2-release-gate.json,artifacts/release-gate-chaos-matrix-continuous-release-gate.json,artifacts/release-gate-operations-handoff-readiness-release-gate.json",
    [string]$FinalizationReportFile = "artifacts\\release-gate-post-cutover-finalization-release-gate.json",
    [string]$BurnRateReportFile = "artifacts\\release-gate-slo-burn-rate-v2-release-gate.json",
    [string]$ChaosReportFile = "artifacts\\release-gate-chaos-matrix-continuous-release-gate.json",
    [string]$RequiredLabel = "release-gate",
    [string]$OutputFile = "artifacts\\release-gate-post-release-watch-release-gate.json",
    [switch]$AllowMissingReports,
    [switch]$AllowNotReady
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$arguments = @(
    ".\scripts\release-gate-post-release-watch-check.py",
    "--label", "manual",
    "--policy-file", $PolicyFile,
    "--required-reports", $RequiredReports,
    "--finalization-report-file", $FinalizationReportFile,
    "--burn-rate-report-file", $BurnRateReportFile,
    "--chaos-report-file", $ChaosReportFile,
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
    throw "Release-gate post-release watch check failed with exit code $LASTEXITCODE."
}

Write-Host "Release-gate post-release watch check passed." -ForegroundColor Green
