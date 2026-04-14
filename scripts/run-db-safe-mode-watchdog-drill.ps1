param(
    [string]$PythonExe = "python",
    [int]$LockErrorInjections = 4
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$args = @(
    ".\\scripts\\db-safe-mode-watchdog-drill.py",
    "--lock-error-injections", [string]$LockErrorInjections
)

& $PythonExe @args
if ($LASTEXITCODE -ne 0) {
    throw "DB safe-mode watchdog drill failed with exit code $LASTEXITCODE."
}

Write-Host "DB safe-mode watchdog drill passed." -ForegroundColor Green
