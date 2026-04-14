param(
    [string]$PythonExe = "python",
    [double]$TimeoutSeconds = 8
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$args = @(
    ".\\scripts\\invariant-monitor-watchdog-drill.py",
    "--timeout-seconds", [string]$TimeoutSeconds
)

& $PythonExe @args
if ($LASTEXITCODE -ne 0) {
    throw "Invariant monitor watchdog drill failed with exit code $LASTEXITCODE."
}

Write-Host "Invariant monitor watchdog drill passed." -ForegroundColor Green
