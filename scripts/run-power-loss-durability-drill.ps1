param(
    [string]$PythonExe = "python",
    [string]$WorkspaceDir = ".tmp\\power-loss-durability-drills",
    [int]$TransactionRows = 240,
    [int]$PayloadBytes = 256,
    [double]$StartupTimeoutSeconds = 15,
    [switch]$KeepArtifacts
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$args = @(
    ".\\scripts\\power-loss-durability-drill.py",
    "--workspace", $WorkspaceDir,
    "--label", "manual",
    "--transaction-rows", [string]$TransactionRows,
    "--payload-bytes", [string]$PayloadBytes,
    "--startup-timeout-seconds", [string]$StartupTimeoutSeconds
)
if ($KeepArtifacts) {
    $args += "--keep-artifacts"
}

& $PythonExe @args
if ($LASTEXITCODE -ne 0) {
    throw "Power-loss durability drill failed with exit code $LASTEXITCODE."
}

Write-Host "Power-loss durability drill passed." -ForegroundColor Green
