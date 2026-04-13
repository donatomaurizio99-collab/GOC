param(
    [string]$PythonExe = "python",
    [string]$WorkspaceDir = ".tmp\\backup-restore-drills",
    [switch]$KeepArtifacts
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$args = @(
    ".\\scripts\\backup-restore-drill.py",
    "--workspace", $WorkspaceDir,
    "--label", "manual"
)
if ($KeepArtifacts) {
    $args += "--keep-artifacts"
}

& $PythonExe @args
if ($LASTEXITCODE -ne 0) {
    throw "Backup/restore drill failed with exit code $LASTEXITCODE."
}

Write-Host "Backup/restore drill passed." -ForegroundColor Green
