param(
    [string]$PythonExe = "python",
    [int]$LockFailures = 5,
    [double]$TimeoutSeconds = 10
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$args = @(
    ".\\scripts\\workflow-lock-resilience-drill.py",
    "--lock-failures", [string]$LockFailures,
    "--timeout-seconds", [string]$TimeoutSeconds
)

& $PythonExe @args
if ($LASTEXITCODE -ne 0) {
    throw "Workflow lock resilience drill failed with exit code $LASTEXITCODE."
}

Write-Host "Workflow lock resilience drill passed." -ForegroundColor Green
