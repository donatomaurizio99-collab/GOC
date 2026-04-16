param(
    [string]$PythonExe = "python",
    [int]$Repeats = 2,
    [int]$MaxFailedIterations = 0,
    [string]$TargetFile = "tests\\test_goal_ops.py",
    [string]$KeywordExpression = "test_79_recovery_hard_abort_drill_reports_success or test_98_power_loss_durability_drill_reports_success or test_99_disk_pressure_fault_injection_drill_reports_success or test_100_sqlite_real_full_drill_reports_success or test_101_wal_checkpoint_crash_drill_reports_success or test_102_recovery_idempotence_drill_reports_success or test_103_fsync_io_stall_drill_reports_success or test_105_storage_corruption_hardening_drill_reports_success or test_106_backup_restore_stress_drill_reports_success or test_107_snapshot_restore_crash_consistency_drill_reports_success",
    [double]$TimeoutSeconds = 600.0
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$args = @(
    ".\\scripts\\critical-drill-flake-gate.py",
    "--repeats", [string]$Repeats,
    "--max-failed-iterations", [string]$MaxFailedIterations,
    "--target-file", $TargetFile,
    "--keyword-expression", $KeywordExpression,
    "--timeout-seconds", [string]$TimeoutSeconds
)

& $PythonExe @args
if ($LASTEXITCODE -ne 0) {
    throw "Critical drill flake gate failed with exit code $LASTEXITCODE."
}

Write-Host "Critical drill flake gate passed." -ForegroundColor Green
