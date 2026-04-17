param(
    [string]$PythonExe = "python",
    [string]$WorkspaceDir = ".tmp\\recovery-idempotence-drills",
    [int]$RecoveryCycles = 3,
    [double]$StartupTimeoutSeconds = 15.0,
    [switch]$KeepArtifacts
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$args = @(
    ".\\scripts\\recovery-idempotence-drill.py",
    "--workspace", $WorkspaceDir,
    "--label", "manual",
    "--recovery-cycles", [string]$RecoveryCycles,
    "--startup-timeout-seconds", [string]$StartupTimeoutSeconds
)
if ($KeepArtifacts) {
    $args += "--keep-artifacts"
}

& $PythonExe @args
if ($LASTEXITCODE -ne 0) {
    throw "Recovery idempotence drill failed with exit code $LASTEXITCODE."
}

Write-Host "Recovery idempotence drill passed." -ForegroundColor Green
