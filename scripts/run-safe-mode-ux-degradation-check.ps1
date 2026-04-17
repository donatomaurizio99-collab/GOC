param(
    [string]$PythonExe = "python",
    [string]$OutputFile = "artifacts\safe-mode-ux-degradation-check-report.json",
    [switch]$AllowFailure
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$arguments = @(
    ".\scripts\safe-mode-ux-degradation-check.py",
    "--label", "manual",
    "--output-file", $OutputFile
)

& $PythonExe @arguments
if ($LASTEXITCODE -ne 0) {
    if ($AllowFailure) {
        Write-Warning "Safe-mode UX degradation check failed with exit code $LASTEXITCODE."
        exit 0
    }
    throw "Safe-mode UX degradation check failed with exit code $LASTEXITCODE."
}

Write-Host "Safe-mode UX degradation check passed." -ForegroundColor Green
