param(
    [string]$DatabaseUrl = "goal_ops.db",
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$env:GOAL_OPS_DATABASE_URL = $DatabaseUrl

Write-Host "Starting Goal Ops Console from $ProjectRoot" -ForegroundColor Cyan
Write-Host "Database: $DatabaseUrl" -ForegroundColor Cyan
Write-Host "URL: http://127.0.0.1:$Port" -ForegroundColor Cyan

python -m uvicorn goal_ops_console.main:app --reload --host 127.0.0.1 --port $Port --app-dir $ProjectRoot
