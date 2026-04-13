param(
    [string]$PythonExe = "python",
    [string]$WorkspaceDir = ".tmp\\recovery-hard-abort-drills",
    [double]$StartupTimeoutSeconds = 15,
    [switch]$KeepArtifacts
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$args = @(
    ".\\scripts\\recovery-hard-abort-drill.py",
    "--workspace", $WorkspaceDir,
    "--label", "manual",
    "--startup-timeout-seconds", [string]$StartupTimeoutSeconds
)
if ($KeepArtifacts) {
    $args += "--keep-artifacts"
}

& $PythonExe @args
if ($LASTEXITCODE -ne 0) {
    throw "Recovery hard-abort drill failed with exit code $LASTEXITCODE."
}

Write-Host "Recovery hard-abort drill passed." -ForegroundColor Green
