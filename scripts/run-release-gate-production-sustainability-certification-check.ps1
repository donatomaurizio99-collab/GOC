param(
    [string]$PythonExe = "python",
    [string]$PolicyFile = "docs\\release-gate-production-sustainability-certification-policy.json",
    [string]$RequiredReports = "artifacts/release-gate-post-release-continuity-release-gate.json,artifacts/release-gate-steady-state-certification-release-gate.json,artifacts/release-gate-post-release-watch-release-gate.json,artifacts/p0-burnin-consecutive-green-release-gate.json,artifacts/p0-closure-report-release-gate.json,artifacts/release-gate-production-final-attestation-release-gate.json",
    [string]$PostReleaseContinuityReportFile = "artifacts\\release-gate-post-release-continuity-release-gate.json",
    [string]$SteadyStateReportFile = "artifacts\\release-gate-steady-state-certification-release-gate.json",
    [string]$ProductionFinalReportFile = "artifacts\\release-gate-production-final-attestation-release-gate.json",
    [string]$BurninReportFile = "artifacts\\p0-burnin-consecutive-green-release-gate.json",
    [string]$ClosureReportFile = "artifacts\\p0-closure-report-release-gate.json",
    [string]$RequiredLabel = "release-gate",
    [string]$OutputFile = "artifacts\\release-gate-production-sustainability-certification-release-gate.json",
    [switch]$AllowMissingReports,
    [switch]$AllowNotReady
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$arguments = @(
    ".\scripts\release-gate-production-sustainability-certification-check.py",
    "--label", "manual",
    "--policy-file", $PolicyFile,
    "--required-reports", $RequiredReports,
    "--post-release-continuity-report-file", $PostReleaseContinuityReportFile,
    "--steady-state-report-file", $SteadyStateReportFile,
    "--production-final-report-file", $ProductionFinalReportFile,
    "--burnin-report-file", $BurninReportFile,
    "--closure-report-file", $ClosureReportFile,
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
    throw "Release-gate production sustainability certification check failed with exit code $LASTEXITCODE."
}

Write-Host "Release-gate production sustainability certification check passed." -ForegroundColor Green
