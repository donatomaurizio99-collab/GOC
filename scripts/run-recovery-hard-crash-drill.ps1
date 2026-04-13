param(
    [string]$PythonExe = "python",
    [string]$WorkspaceDir = ".tmp\\recovery-hard-crash-drills",
    [switch]$KeepArtifacts
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$args = @(
    ".\\scripts\\recovery-hard-crash-drill.py",
    "--workspace", $WorkspaceDir,
    "--label", "manual"
)
if ($KeepArtifacts) {
    $args += "--keep-artifacts"
}

& $PythonExe @args
if ($LASTEXITCODE -ne 0) {
    throw "Recovery hard-crash drill failed with exit code $LASTEXITCODE."
}

Write-Host "Recovery hard-crash drill passed." -ForegroundColor Green
