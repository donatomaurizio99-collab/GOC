param(
    [string]$PythonExe = "python",
    [string]$OutputFile = "artifacts\a11y-test-harness-check-report.json",
    [switch]$AllowFailure
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$arguments = @(
    ".\scripts\a11y-test-harness-check.py",
    "--label", "manual",
    "--output-file", $OutputFile
)

& $PythonExe @arguments
if ($LASTEXITCODE -ne 0) {
    if ($AllowFailure) {
        Write-Warning "A11y test harness check failed with exit code $LASTEXITCODE."
        exit 0
    }
    throw "A11y test harness check failed with exit code $LASTEXITCODE."
}

Write-Host "A11y test harness check passed." -ForegroundColor Green
