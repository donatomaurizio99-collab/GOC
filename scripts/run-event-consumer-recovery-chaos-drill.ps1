param(
    [string]$PythonExe = "python",
    [int]$GoalCount = 40,
    [int]$StaleProcessingCount = 15,
    [string]$ConsumerId = "chaos-recovery",
    [int]$DrainBatchSize = 100,
    [double]$TimeoutSeconds = 20
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$args = @(
    ".\\scripts\\event-consumer-recovery-chaos-drill.py",
    "--goal-count", [string]$GoalCount,
    "--stale-processing-count", [string]$StaleProcessingCount,
    "--consumer-id", [string]$ConsumerId,
    "--drain-batch-size", [string]$DrainBatchSize,
    "--timeout-seconds", [string]$TimeoutSeconds
)

& $PythonExe @args
if ($LASTEXITCODE -ne 0) {
    throw "Event consumer recovery chaos drill failed with exit code $LASTEXITCODE."
}

Write-Host "Event consumer recovery chaos drill passed." -ForegroundColor Green
