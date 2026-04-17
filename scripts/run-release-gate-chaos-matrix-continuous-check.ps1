param(
    [string]$PythonExe = "python",
    [string]$PolicyFile = "docs\\release-gate-chaos-matrix-policy.json",
    [string]$RequiredReports = "artifacts/critical-drill-flake-gate-release-gate.json,artifacts/release-gate-runtime-stability-release-gate.json,artifacts/p0-disaster-recovery-rehearsal-pack-release-gate.json",
    [string]$CriticalDrillReportFile = "artifacts\\critical-drill-flake-gate-release-gate.json",
    [string]$RequiredLabel = "release-gate",
    [string]$OutputFile = "artifacts\\release-gate-chaos-matrix-continuous-release-gate.json",
    [switch]$AllowMissingReports,
    [switch]$AllowNotReady
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$arguments = @(
    ".\scripts\release-gate-chaos-matrix-continuous-check.py",
    "--label", "manual",
    "--policy-file", $PolicyFile,
    "--required-reports", $RequiredReports,
    "--critical-drill-report-file", $CriticalDrillReportFile,
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
    throw "Release-gate chaos matrix continuous check failed with exit code $LASTEXITCODE."
}

Write-Host "Release-gate chaos matrix continuous check passed." -ForegroundColor Green
