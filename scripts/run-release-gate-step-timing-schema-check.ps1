param(
    [string]$PythonExe = "python",
    [string]$StepTimingsFile = "artifacts\release-gate-step-timings-release-gate.json",
    [string]$RequiredLabel = "release-gate",
    [string]$RequiredKeys = "name,duration_seconds,success,completed_at_utc",
    [string]$OutputFile = "artifacts\release-gate-step-timing-schema-release-gate.json",
    [switch]$AllowFailedSteps
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$arguments = @(
    ".\scripts\release-gate-step-timing-schema-check.py",
    "--label", "manual",
    "--step-timings-file", $StepTimingsFile,
    "--required-label", $RequiredLabel,
    "--required-keys", $RequiredKeys,
    "--output-file", $OutputFile
)
if ($AllowFailedSteps) {
    $arguments += "--allow-failed-steps"
}

& $PythonExe @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Release-gate step timing schema check failed with exit code $LASTEXITCODE."
}

Write-Host "Release-gate step timing schema check passed." -ForegroundColor Green
