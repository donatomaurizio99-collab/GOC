param(
    [string]$PythonExe = "python",
    [string]$WorkspaceDir = ".tmp\\desktop-update-safety-drills",
    [switch]$KeepArtifacts
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$args = @(
    ".\\scripts\\desktop-update-safety-drill.py",
    "--workspace", $WorkspaceDir,
    "--label", "manual"
)
if ($KeepArtifacts) {
    $args += "--keep-artifacts"
}

& $PythonExe @args
if ($LASTEXITCODE -ne 0) {
    throw "Desktop update safety drill failed with exit code $LASTEXITCODE."
}

Write-Host "Desktop update safety drill passed." -ForegroundColor Green
