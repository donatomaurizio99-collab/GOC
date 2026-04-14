param(
    [string]$PythonExe = "python",
    [string]$WorkspaceDir = ".tmp\\wal-checkpoint-crash-drills",
    [int]$Rows = 240,
    [int]$PayloadBytes = 1024,
    [double]$StartupTimeoutSeconds = 15.0,
    [double]$SleepBeforeCheckpointSeconds = 30.0,
    [ValidateSet("PASSIVE", "FULL", "TRUNCATE")]
    [string]$CheckpointMode = "TRUNCATE",
    [switch]$KeepArtifacts
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$args = @(
    ".\\scripts\\wal-checkpoint-crash-drill.py",
    "--workspace", $WorkspaceDir,
    "--label", "manual",
    "--rows", [string]$Rows,
    "--payload-bytes", [string]$PayloadBytes,
    "--startup-timeout-seconds", [string]$StartupTimeoutSeconds,
    "--sleep-before-checkpoint-seconds", [string]$SleepBeforeCheckpointSeconds,
    "--checkpoint-mode", [string]$CheckpointMode
)
if ($KeepArtifacts) {
    $args += "--keep-artifacts"
}

& $PythonExe @args
if ($LASTEXITCODE -ne 0) {
    throw "WAL checkpoint crash drill failed with exit code $LASTEXITCODE."
}

Write-Host "WAL checkpoint crash drill passed." -ForegroundColor Green
