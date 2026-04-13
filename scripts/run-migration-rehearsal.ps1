param(
    [string]$PythonExe = "python",
    [string]$WorkspaceDir = ".tmp\\migration-rehearsals",
    [int]$SmallRuns = 500,
    [int]$MediumRuns = 2500,
    [int]$LargeRuns = 6000,
    [int]$PayloadBytes = 1024,
    [int]$MaxBackupMs = 15000,
    [int]$MaxRestoreMs = 15000,
    [int]$MaxMigrationMs = 20000,
    [switch]$KeepArtifacts
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$args = @(
    ".\\scripts\\migration-rehearsal.py",
    "--workspace", $WorkspaceDir,
    "--label", "manual",
    "--small-runs", [string]$SmallRuns,
    "--medium-runs", [string]$MediumRuns,
    "--large-runs", [string]$LargeRuns,
    "--payload-bytes", [string]$PayloadBytes,
    "--max-backup-ms", [string]$MaxBackupMs,
    "--max-restore-ms", [string]$MaxRestoreMs,
    "--max-migration-ms", [string]$MaxMigrationMs
)
if ($KeepArtifacts) {
    $args += "--keep-artifacts"
}

& $PythonExe @args
if ($LASTEXITCODE -ne 0) {
    throw "Migration rehearsal failed with exit code $LASTEXITCODE."
}

Write-Host "Migration rehearsal passed." -ForegroundColor Green
