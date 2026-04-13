param(
    [string]$PythonExe = "python",
    [double]$TimeoutSeconds = 12
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$args = @(
    ".\\scripts\\workflow-worker-restart-drill.py",
    "--timeout-seconds", [string]$TimeoutSeconds
)

& $PythonExe @args
if ($LASTEXITCODE -ne 0) {
    throw "Workflow worker restart drill failed with exit code $LASTEXITCODE."
}

Write-Host "Workflow worker restart drill passed." -ForegroundColor Green
