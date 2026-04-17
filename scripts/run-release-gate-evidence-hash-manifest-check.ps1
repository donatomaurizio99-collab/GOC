param(
    [string]$PythonExe = "python",
    [string]$RequiredFiles = "artifacts\safe-mode-ux-degradation-release-gate.json,artifacts\a11y-test-harness-release-gate.json,artifacts\release-gate-runtime-stability-release-gate.json,artifacts\critical-drill-flake-gate-release-gate.json,artifacts\p0-report-schema-contract-release-gate.json,artifacts\p0-release-evidence-bundle-release-gate.json,artifacts\p0-closure-report-release-gate.json,artifacts\release-gate-performance-budget-release-gate.json",
    [string]$RequiredLabel = "release-gate",
    [string]$OutputFile = "artifacts\release-gate-evidence-hash-manifest-release-gate.json",
    [string]$ManifestFile = "artifacts\release-gate-evidence-manifest-release-gate.json",
    [switch]$AllowMissingReports
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$arguments = @(
    ".\scripts\release-gate-evidence-hash-manifest-check.py",
    "--label", "manual",
    "--required-files", $RequiredFiles,
    "--required-label", $RequiredLabel,
    "--output-file", $OutputFile,
    "--manifest-file", $ManifestFile
)
if ($AllowMissingReports) {
    $arguments += "--allow-missing-reports"
}

& $PythonExe @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Release-gate evidence hash manifest check failed with exit code $LASTEXITCODE."
}

Write-Host "Release-gate evidence hash manifest check passed." -ForegroundColor Green
