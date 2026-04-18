param(
    [string]$PythonExe = "python",
    [string]$PolicyFile = "docs\\release-gate-slo-burn-rate-v2-policy.json",
    [string]$RequiredReports = "artifacts/failure-budget-dashboard-release-gate.json,artifacts/release-gate-staging-soak-readiness-release-gate.json",
    [string]$RequiredLabel = "release-gate",
    [string]$OutputFile = "artifacts\\release-gate-slo-burn-rate-v2-release-gate.json",
    [switch]$AllowMissingReports,
    [switch]$AllowNotReady
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$arguments = @(
    ".\scripts\release-gate-slo-burn-rate-v2-check.py",
    "--label", "manual",
    "--policy-file", $PolicyFile,
    "--required-reports", $RequiredReports,
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
    throw "Release-gate SLO burn-rate v2 check failed with exit code $LASTEXITCODE."
}

Write-Host "Release-gate SLO burn-rate v2 check passed." -ForegroundColor Green
