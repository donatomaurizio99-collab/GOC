param(
    [string]$PythonExe = "python",
    [int]$GoalCount = 45
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$args = @(
    ".\\scripts\\invariant-burst-drill.py",
    "--goal-count", [string]$GoalCount
)

& $PythonExe @args
if ($LASTEXITCODE -ne 0) {
    throw "Invariant burst drill failed with exit code $LASTEXITCODE."
}

Write-Host "Invariant burst drill passed." -ForegroundColor Green
