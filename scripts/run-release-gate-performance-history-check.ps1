param(
    [string]$PythonExe = "python",
    [string]$HistoryBaselineFile = "docs\release-gate-performance-history-baseline.json",
    [string]$StepTimingsFile = "artifacts\release-gate-step-timings-release-gate.json",
    [string]$RequiredLabel = "release-gate",
    [string]$OutputFile = "artifacts\release-gate-performance-history-release-gate.json",
    [switch]$AllowMissingBaseline
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$arguments = @(
    ".\scripts\release-gate-performance-history-check.py",
    "--label", "manual",
    "--history-baseline-file", $HistoryBaselineFile,
    "--step-timings-file", $StepTimingsFile,
    "--required-label", $RequiredLabel,
    "--output-file", $OutputFile
)
if ($AllowMissingBaseline) {
    $arguments += "--allow-missing-baseline"
}

& $PythonExe @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Release-gate performance history check failed with exit code $LASTEXITCODE."
}

Write-Host "Release-gate performance history check passed." -ForegroundColor Green
