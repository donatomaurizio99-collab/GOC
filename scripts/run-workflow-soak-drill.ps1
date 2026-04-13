param(
    [string]$PythonExe = "python",
    [int]$RunCount = 40,
    [double]$TimeoutSeconds = 20
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$args = @(
    ".\\scripts\\workflow-soak-drill.py",
    "--run-count", [string]$RunCount,
    "--timeout-seconds", [string]$TimeoutSeconds
)

& $PythonExe @args
if ($LASTEXITCODE -ne 0) {
    throw "Workflow soak drill failed with exit code $LASTEXITCODE."
}

Write-Host "Workflow soak drill passed." -ForegroundColor Green
