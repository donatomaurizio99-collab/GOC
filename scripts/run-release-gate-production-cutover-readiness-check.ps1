param(
    [string]$PythonExe = "python",
    [string]$PolicyFile = "docs\\release-gate-production-cutover-policy.json",
    [string]$RequiredReports = "artifacts/release-gate-production-final-attestation-release-gate.json,artifacts/release-gate-release-train-readiness-release-gate.json,artifacts/release-gate-operations-handoff-readiness-release-gate.json,artifacts/p0-closure-report-release-gate.json",
    [string]$ProductionFinalReportFile = "artifacts\\release-gate-production-final-attestation-release-gate.json",
    [string]$ReleaseTrainReportFile = "artifacts\\release-gate-release-train-readiness-release-gate.json",
    [string]$RequiredLabel = "release-gate",
    [string]$OutputFile = "artifacts\\release-gate-production-cutover-readiness-release-gate.json",
    [switch]$AllowMissingReports,
    [switch]$AllowNotReady
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$arguments = @(
    ".\scripts\release-gate-production-cutover-readiness-check.py",
    "--label", "manual",
    "--policy-file", $PolicyFile,
    "--required-reports", $RequiredReports,
    "--production-final-report-file", $ProductionFinalReportFile,
    "--release-train-report-file", $ReleaseTrainReportFile,
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
    throw "Release-gate production cutover readiness check failed with exit code $LASTEXITCODE."
}

Write-Host "Release-gate production cutover readiness check passed." -ForegroundColor Green
