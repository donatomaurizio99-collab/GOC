param(
    [string]$PythonExe = "python",
    [string]$WorkspaceDir = ".tmp\\multi-db-atomic-switch-drills",
    [int]$SeedRows = 96,
    [int]$PayloadBytes = 128,
    [switch]$KeepArtifacts
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$args = @(
    ".\\scripts\\multi-db-atomic-switch-drill.py",
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
    throw "Multi-DB atomic-switch drill failed with exit code $LASTEXITCODE."
}

Write-Host "Multi-DB atomic-switch drill passed." -ForegroundColor Green
