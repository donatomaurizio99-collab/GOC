param(
    [string]$PythonExe = "python",
    [string]$WorkspaceDir = ".tmp\\db-corruption-quarantine-drills",
    [int]$CorruptionBytes = 256,
    [switch]$KeepArtifacts
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$args = @(
    ".\\scripts\\db-corruption-quarantine-drill.py",
    "--workspace", $WorkspaceDir,
    "--label", "manual",
    "--corruption-bytes", [string]$CorruptionBytes
)
if ($KeepArtifacts) {
    $args += "--keep-artifacts"
}

& $PythonExe @args
if ($LASTEXITCODE -ne 0) {
    throw "DB corruption quarantine drill failed with exit code $LASTEXITCODE."
}

Write-Host "DB corruption quarantine drill passed." -ForegroundColor Green
