param(
    [string]$PythonExe = "python",
    [int]$Samples = 2,
    [int]$RepeatsPerSample = 1,
    [string]$TargetFile = "tests\\test_goal_ops.py",
    [string]$KeywordExpression = "test_105_storage_corruption_hardening_drill_reports_success or test_106_backup_restore_stress_drill_reports_success or test_107_snapshot_restore_crash_consistency_drill_reports_success or test_108_multi_db_atomic_switch_drill_reports_success",
    [double]$TimeoutSeconds = 900.0,
    [int]$MaxMeanDurationMs = 120000,
    [int]$MaxStddevMs = 60000,
    [int]$MaxIterationDurationMs = 180000
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$args = @(
    ".\\scripts\\release-gate-runtime-stability-drill.py",
    "--label", "manual",
    "--samples", [string]$Samples,
    "--repeats-per-sample", [string]$RepeatsPerSample,
    "--target-file", $TargetFile,
    "--keyword-expression", $KeywordExpression,
    "--timeout-seconds", [string]$TimeoutSeconds,
    "--max-mean-duration-ms", [string]$MaxMeanDurationMs,
    "--max-stddev-ms", [string]$MaxStddevMs,
    "--max-iteration-duration-ms", [string]$MaxIterationDurationMs
)

& $PythonExe @args
if ($LASTEXITCODE -ne 0) {
    throw "Release-gate runtime stability drill failed with exit code $LASTEXITCODE."
}

Write-Host "Release-gate runtime stability drill passed." -ForegroundColor Green
