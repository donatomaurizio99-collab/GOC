param(
    [string]$PythonExe = "python",
    [string]$RequiredReports = "artifacts/release-gate-release-train-readiness-release-gate.json,artifacts/p0-closure-report-release-gate.json,artifacts/p0-runbook-contract-check-release-gate.json,artifacts/p0-report-schema-contract-release-gate.json,artifacts/p0-burnin-consecutive-green-release-gate.json",
    [string]$BurninReportFile = "artifacts\\p0-burnin-consecutive-green-release-gate.json",
    [int]$RequiredConsecutive = 10,
    [string]$RequiredLabel = "release-gate",
    [string]$OutputFile = "artifacts\\release-gate-production-final-attestation-release-gate.json",
    [switch]$AllowMissingReports,
    [switch]$AllowNotReady
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$arguments = @(
    ".\scripts\release-gate-production-final-attestation.py",
    "--label", "manual",
    "--required-reports", $RequiredReports,
    "--burnin-report-file", $BurninReportFile,
    "--required-consecutive", [string]$RequiredConsecutive,
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
    throw "Release-gate production final attestation failed with exit code $LASTEXITCODE."
}

Write-Host "Release-gate production final attestation check passed." -ForegroundColor Green
