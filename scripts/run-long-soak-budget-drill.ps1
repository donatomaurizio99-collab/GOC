param(
    [string]$PythonExe = "python",
    [double]$DurationSeconds = 900,
    [double]$MaxP95LatencyMs = 250,
    [double]$MaxP99LatencyMs = 400,
    [double]$MaxMaxLatencyMs = 5000,
    [double]$MaxHttp429RatePercent = 1.0,
    [double]$MaxErrorRatePercent = 1.0,
    [int]$MinRequests = 300,
    [int]$DrainBatchSize = 150,
    [int]$WorkflowStartEveryCycles = 0
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$args = @(
    ".\\scripts\\long-soak-budget-drill.py",
    "--duration-seconds", [string]$DurationSeconds,
    "--max-p95-latency-ms", [string]$MaxP95LatencyMs,
    "--max-p99-latency-ms", [string]$MaxP99LatencyMs,
    "--max-max-latency-ms", [string]$MaxMaxLatencyMs,
    "--max-http-429-rate-percent", [string]$MaxHttp429RatePercent,
    "--max-error-rate-percent", [string]$MaxErrorRatePercent,
    "--min-requests", [string]$MinRequests,
    "--drain-batch-size", [string]$DrainBatchSize,
    "--workflow-start-every-cycles", [string]$WorkflowStartEveryCycles
)

& $PythonExe @args
if ($LASTEXITCODE -ne 0) {
    throw "Long soak budget drill failed with exit code $LASTEXITCODE."
}

Write-Host "Long soak budget drill passed." -ForegroundColor Green
