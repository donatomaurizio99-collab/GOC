param(
    [string]$PythonExe = "python",
    [string]$RequiredReports = "artifacts\release-gate-evidence-freshness-release-gate.json,artifacts\release-gate-evidence-hash-manifest-release-gate.json,artifacts\release-gate-step-timing-schema-release-gate.json,artifacts\release-gate-performance-history-release-gate.json,artifacts\release-gate-performance-budget-release-gate.json,artifacts\p0-closure-report-release-gate.json",
    [string]$RequiredLabel = "release-gate",
    [string]$OutputFile = "artifacts\release-gate-stability-final-readiness-release-gate.json",
    [switch]$AllowMissingReports
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$arguments = @(
    ".\scripts\release-gate-stability-final-readiness.py",
    "--label", "manual",
    "--required-reports", $RequiredReports,
    "--required-label", $RequiredLabel,
    "--output-file", $OutputFile
)
if ($AllowMissingReports) {
    $arguments += "--allow-missing-reports"
}

& $PythonExe @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Release-gate stability final readiness check failed with exit code $LASTEXITCODE."
}

Write-Host "Release-gate stability final readiness check passed." -ForegroundColor Green
