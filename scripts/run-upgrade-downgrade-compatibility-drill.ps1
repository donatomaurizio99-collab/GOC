param(
    [string]$PythonExe = "python",
    [string]$WorkspaceDir = ".tmp\\upgrade-downgrade-compatibility-drills",
    [int]$NMinus1Runs = 800,
    [int]$PayloadBytes = 512,
    [int]$MaxUpgradeMs = 10000,
    [int]$MaxRollbackRestoreMs = 10000,
    [int]$MaxReupgradeMs = 10000,
    [switch]$KeepArtifacts
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$args = @(
    ".\\scripts\\upgrade-downgrade-compatibility-drill.py",
    "--workspace", $WorkspaceDir,
    "--label", "manual",
    "--n-minus-1-runs", [string]$NMinus1Runs,
    "--payload-bytes", [string]$PayloadBytes,
    "--max-upgrade-ms", [string]$MaxUpgradeMs,
    "--max-rollback-restore-ms", [string]$MaxRollbackRestoreMs,
    "--max-reupgrade-ms", [string]$MaxReupgradeMs
)
if ($KeepArtifacts) {
    $args += "--keep-artifacts"
}

& $PythonExe @args
if ($LASTEXITCODE -ne 0) {
    throw "Upgrade/downgrade compatibility drill failed with exit code $LASTEXITCODE."
}

Write-Host "Upgrade/downgrade compatibility drill passed." -ForegroundColor Green
