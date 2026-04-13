param(
    [string]$PythonExe = "python",
    [string]$DatabaseUrl = ":memory:",
    [ValidateSet("ok", "degraded", "critical")]
    [string]$AllowedStatus = "ok",
    [string]$BaseUrl = ""
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$args = @(
    ".\\scripts\\slo-alert-check.py",
    "--allowed-status", $AllowedStatus
)
if (-not [string]::IsNullOrWhiteSpace($BaseUrl)) {
    $args += @("--base-url", $BaseUrl)
} else {
    $args += @("--database-url", $DatabaseUrl)
}

& $PythonExe @args
if ($LASTEXITCODE -ne 0) {
    throw "SLO alert check failed with exit code $LASTEXITCODE."
}

Write-Host "SLO alert check passed." -ForegroundColor Green
