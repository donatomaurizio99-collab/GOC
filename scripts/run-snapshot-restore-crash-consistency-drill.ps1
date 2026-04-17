param(
    [string]$PythonExe = "python",
    [string]$WorkspaceDir = ".tmp\\snapshot-restore-crash-consistency-drills",
    [int]$SeedRows = 96,
    [int]$PayloadBytes = 128,
    [switch]$KeepArtifacts
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$args = @(
    ".\\scripts\\snapshot-restore-crash-consistency-drill.py",
    "--workspace", $WorkspaceDir,
    "--label", "manual",
    "--seed-rows", [string]$SeedRows,
    "--payload-bytes", [string]$PayloadBytes
)
if ($KeepArtifacts) {
    $args += "--keep-artifacts"
}

& $PythonExe @args
if ($LASTEXITCODE -ne 0) {
    throw "Snapshot/restore crash-consistency drill failed with exit code $LASTEXITCODE."
}

Write-Host "Snapshot/restore crash-consistency drill passed." -ForegroundColor Green
