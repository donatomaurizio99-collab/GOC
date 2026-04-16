param(
    [string]$PythonExe = "python",
    [string]$DeploymentProfile = "production",
    [switch]$OperatorAuthRequired,
    [string]$OperatorAuthToken = "",
    [int]$MinOperatorTokenLength = 16,
    [string]$DatabaseUrl = "goal_ops.db",
    [switch]$StartupCorruptionRecoveryEnabled,
    [switch]$AllowMemoryDatabase,
    [string]$OutputFile = "artifacts\\security-config-hardening-check-report.json",
    [switch]$AllowFailure
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$arguments = @(
    ".\\scripts\\security-config-hardening-check.py",
    "--label", "manual",
    "--deployment-profile", $DeploymentProfile,
    "--operator-auth-token", $OperatorAuthToken,
    "--min-operator-token-length", [string]$MinOperatorTokenLength,
    "--database-url", $DatabaseUrl,
    "--output-file", $OutputFile
)
if ($OperatorAuthRequired) {
    $arguments += "--operator-auth-required"
}
if ($StartupCorruptionRecoveryEnabled) {
    $arguments += "--startup-corruption-recovery-enabled"
}
if ($AllowMemoryDatabase) {
    $arguments += "--allow-memory-database"
}
if ($AllowFailure) {
    $arguments += "--allow-failure"
}

& $PythonExe @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Security config hardening check failed with exit code $LASTEXITCODE."
}

Write-Host "Security config hardening check passed." -ForegroundColor Green
