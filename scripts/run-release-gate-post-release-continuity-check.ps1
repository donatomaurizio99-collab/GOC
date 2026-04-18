param(
    [string]$PythonExe = "python",
    [string]$PolicyFile = "docs\\release-gate-post-release-continuity-policy.json",
    [string]$RequiredReports = "artifacts/release-gate-post-release-watch-release-gate.json,artifacts/release-gate-steady-state-certification-release-gate.json,artifacts/release-gate-evidence-freshness-release-gate.json,artifacts/release-gate-evidence-attestation-release-gate.json,artifacts/release-gate-operations-handoff-readiness-release-gate.json",
    [string]$PostReleaseWatchReportFile = "artifacts\\release-gate-post-release-watch-release-gate.json",
    [string]$SteadyStateReportFile = "artifacts\\release-gate-steady-state-certification-release-gate.json",
    [string]$FreshnessReportFile = "artifacts\\release-gate-evidence-freshness-release-gate.json",
    [string]$AttestationReportFile = "artifacts\\release-gate-evidence-attestation-release-gate.json",
    [string]$RequiredLabel = "release-gate",
    [string]$OutputFile = "artifacts\\release-gate-post-release-continuity-release-gate.json",
    [switch]$AllowMissingReports,
    [switch]$AllowNotReady
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$arguments = @(
    ".\scripts\release-gate-post-release-continuity-check.py",
    "--label", "manual",
    "--policy-file", $PolicyFile,
    "--required-reports", $RequiredReports,
    "--post-release-watch-report-file", $PostReleaseWatchReportFile,
    "--steady-state-report-file", $SteadyStateReportFile,
    "--freshness-report-file", $FreshnessReportFile,
    "--attestation-report-file", $AttestationReportFile,
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
    throw "Release-gate post-release continuity check failed with exit code $LASTEXITCODE."
}

Write-Host "Release-gate post-release continuity check passed." -ForegroundColor Green
