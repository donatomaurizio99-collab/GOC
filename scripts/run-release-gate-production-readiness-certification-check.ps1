param(
    [string]$PythonExe = "python",
    [string]$RequiredReports = "artifacts/release-gate-stability-final-readiness-release-gate.json,artifacts/release-gate-staging-soak-readiness-release-gate.json,artifacts/release-gate-rc-canary-rollout-release-gate.json,artifacts/release-gate-evidence-lineage-release-gate.json,artifacts/p0-closure-report-release-gate.json,artifacts/p0-burnin-consecutive-green-release-gate.json",
    [string]$RequiredLabel = "release-gate",
    [string]$BurninReportFile = "artifacts\p0-burnin-consecutive-green-release-gate.json",
    [int]$RequiredConsecutive = 10,
    [string]$OutputFile = "artifacts\release-gate-production-readiness-certification-release-gate.json",
    [switch]$AllowMissingReports,
    [switch]$AllowNotReady
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$arguments = @(
    ".\scripts\release-gate-production-readiness-certification.py",
    "--label", "manual",
    "--required-reports", $RequiredReports,
    "--required-label", $RequiredLabel,
    "--burnin-report-file", $BurninReportFile,
    "--required-consecutive", [string]$RequiredConsecutive,
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
    throw "Release-gate production readiness certification failed with exit code $LASTEXITCODE."
}

Write-Host "Release-gate production readiness certification passed." -ForegroundColor Green
