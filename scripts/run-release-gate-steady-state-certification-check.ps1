param(
    [string]$PythonExe = "python",
    [string]$PolicyFile = "docs\\release-gate-steady-state-certification-policy.json",
    [string]$RequiredReports = "artifacts/release-gate-post-release-watch-release-gate.json,artifacts/release-gate-post-cutover-finalization-release-gate.json,artifacts/p0-burnin-consecutive-green-release-gate.json,artifacts/p0-closure-report-release-gate.json,artifacts/release-gate-operations-handoff-readiness-release-gate.json",
    [string]$PostReleaseWatchReportFile = "artifacts\\release-gate-post-release-watch-release-gate.json",
    [string]$PostCutoverFinalizationReportFile = "artifacts\\release-gate-post-cutover-finalization-release-gate.json",
    [string]$BurninReportFile = "artifacts\\p0-burnin-consecutive-green-release-gate.json",
    [string]$ClosureReportFile = "artifacts\\p0-closure-report-release-gate.json",
    [string]$RequiredLabel = "release-gate",
    [string]$OutputFile = "artifacts\\release-gate-steady-state-certification-release-gate.json",
    [switch]$AllowMissingReports,
    [switch]$AllowNotReady
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$arguments = @(
    ".\scripts\release-gate-steady-state-certification-check.py",
    "--label", "manual",
    "--policy-file", $PolicyFile,
    "--required-reports", $RequiredReports,
    "--post-release-watch-report-file", $PostReleaseWatchReportFile,
    "--post-cutover-finalization-report-file", $PostCutoverFinalizationReportFile,
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
    throw "Release-gate steady-state certification check failed with exit code $LASTEXITCODE."
}

Write-Host "Release-gate steady-state certification check passed." -ForegroundColor Green
