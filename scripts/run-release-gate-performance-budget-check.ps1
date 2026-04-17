param(
    [string]$PythonExe = "python",
    [string]$PolicyFile = "docs\release-gate-performance-budget-policy.json",
    [string]$StepTimingsFile = "artifacts\release-gate-step-timings-release-gate.json",
    [string]$RequiredLabel = "release-gate",
    [string]$OutputFile = "artifacts\release-gate-performance-budget-release-gate.json",
    [switch]$AllowMissingSteps
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$arguments = @(
    ".\scripts\release-gate-performance-budget-check.py",
    "--label", "manual",
    "--policy-file", $PolicyFile,
    "--step-timings-file", $StepTimingsFile,
    "--required-label", $RequiredLabel,
    "--output-file", $OutputFile
)
if ($AllowMissingSteps) {
    $arguments += "--allow-missing-steps"
}

& $PythonExe @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Release-gate performance budget check failed with exit code $LASTEXITCODE."
}

Write-Host "Release-gate performance budget check passed." -ForegroundColor Green
