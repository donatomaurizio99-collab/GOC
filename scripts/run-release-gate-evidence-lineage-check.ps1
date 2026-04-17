param(
    [string]$PythonExe = "python",
    [string]$RequiredReports = "artifacts/release-gate-stability-final-readiness-release-gate.json,artifacts/release-gate-staging-soak-readiness-release-gate.json,artifacts/release-gate-rc-canary-rollout-release-gate.json,artifacts/p0-closure-report-release-gate.json",
    [string]$ManifestFile = "artifacts\release-gate-evidence-manifest-release-gate.json",
    [string]$RequiredLabel = "release-gate",
    [int]$MaxReportTimestampSkewSeconds = 900,
    [string]$OutputFile = "artifacts\release-gate-evidence-lineage-release-gate.json",
    [switch]$AllowMissingReports,
    [switch]$AllowNotReady
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$arguments = @(
    ".\scripts\release-gate-evidence-lineage-check.py",
    "--label", "manual",
    "--required-reports", $RequiredReports,
    "--manifest-file", $ManifestFile,
    "--required-label", $RequiredLabel,
    "--max-report-timestamp-skew-seconds", [string]$MaxReportTimestampSkewSeconds,
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
    throw "Release-gate evidence lineage check failed with exit code $LASTEXITCODE."
}

Write-Host "Release-gate evidence lineage check passed." -ForegroundColor Green
