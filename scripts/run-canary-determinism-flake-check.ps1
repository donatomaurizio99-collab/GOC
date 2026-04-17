param(
    [string]$PythonExe = "python",
    [string]$PolicyFile = "docs\canary-determinism-policy.json",
    [string]$QuarantineFile = "docs\canary-determinism-quarantine.json",
    [string]$RunbookFile = "docs\production-runbook.md",
    [string]$Workspace = ".tmp\canary-determinism-flake-check",
    [string]$RequiredLabel = "stability-canary",
    [int]$ProbeRepeats = 2,
    [string]$OutputFile = "artifacts\canary-determinism-flake-report.json",
    [switch]$AllowFailure
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$arguments = @(
    ".\scripts\canary-determinism-flake-check.py",
    "--label", "manual",
    "--policy-file", $PolicyFile,
    "--quarantine-file", $QuarantineFile,
    "--runbook-file", $RunbookFile,
    "--workspace", $Workspace,
    "--required-label", $RequiredLabel,
    "--probe-repeats", [string]$ProbeRepeats,
    "--output-file", $OutputFile
)
if ($AllowFailure) {
    $arguments += "--allow-failure"
}

& $PythonExe @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Canary determinism flake check failed with exit code $LASTEXITCODE."
}

Write-Host "Canary determinism flake check passed." -ForegroundColor Green
