param(
    [string]$PythonExe = "python",
    [string]$WorkspaceDir = ".tmp\\sqlite-real-full-drills",
    [int]$PayloadBytes = 8192,
    [int]$MaxWriteAttempts = 240,
    [int]$MaxPageGrowth = 24,
    [int]$RecoveryPageGrowth = 160,
    [switch]$KeepArtifacts
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$args = @(
    ".\\scripts\\sqlite-real-full-drill.py",
    "--workspace", $WorkspaceDir,
    "--label", "manual",
    "--payload-bytes", [string]$PayloadBytes,
    "--max-write-attempts", [string]$MaxWriteAttempts,
    "--max-page-growth", [string]$MaxPageGrowth,
    "--recovery-page-growth", [string]$RecoveryPageGrowth
)
if ($KeepArtifacts) {
    $args += "--keep-artifacts"
}

& $PythonExe @args
if ($LASTEXITCODE -ne 0) {
    throw "SQLite real full drill failed with exit code $LASTEXITCODE."
}

Write-Host "SQLite real full drill passed." -ForegroundColor Green
