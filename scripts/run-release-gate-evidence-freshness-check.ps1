param(
    [string]$PythonExe = "python",
    [string]$PolicyFile = "docs\release-gate-evidence-freshness-policy.json",
    [string]$RequiredLabel = "release-gate",
    [string]$OutputFile = "artifacts\release-gate-evidence-freshness-release-gate.json",
    [switch]$AllowMissingReports
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$arguments = @(
    ".\scripts\release-gate-evidence-freshness-check.py",
    "--label", "manual",
    "--policy-file", $PolicyFile,
    "--required-label", $RequiredLabel,
    "--output-file", $OutputFile
)
if ($AllowMissingReports) {
    $arguments += "--allow-missing-reports"
}

& $PythonExe @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Release-gate evidence freshness check failed with exit code $LASTEXITCODE."
}

Write-Host "Release-gate evidence freshness check passed." -ForegroundColor Green
