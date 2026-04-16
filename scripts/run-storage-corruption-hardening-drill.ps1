param(
    [string]$PythonExe = "python",
    [string]$WorkspaceDir = ".tmp\\storage-corruption-hardening-drills",
    [int]$CorruptionBytes = 192,
    [int]$Rows = 80,
    [int]$PayloadBytes = 128,
    [switch]$KeepArtifacts
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$args = @(
    ".\\scripts\\storage-corruption-hardening-drill.py",
    "--workspace", $WorkspaceDir,
    "--label", "manual",
    "--corruption-bytes", [string]$CorruptionBytes,
    "--rows", [string]$Rows,
    "--payload-bytes", [string]$PayloadBytes
)
if ($KeepArtifacts) {
    $args += "--keep-artifacts"
}

& $PythonExe @args
if ($LASTEXITCODE -ne 0) {
    throw "Storage corruption hardening drill failed with exit code $LASTEXITCODE."
}

Write-Host "Storage corruption hardening drill passed." -ForegroundColor Green
