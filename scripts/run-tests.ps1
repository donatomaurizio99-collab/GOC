$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$env:PYTHONDONTWRITEBYTECODE = "1"

Write-Host "Running tests from $ProjectRoot" -ForegroundColor Cyan
python -m pytest -q
