param(
    [string]$PythonExe = "python",
    [string]$DeploymentProfile = "production",
    [int]$AuditRetentionDays = 365,
    [int]$MinAuditRetentionDays = 90,
    [int]$SeedEntries = 8,
    [string]$Workspace = ".tmp\\audit-trail-hardening-check-manual",
    [string]$OutputFile = "artifacts\\audit-trail-hardening-check-report.json",
    [switch]$AllowFailure
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$arguments = @(
    ".\\scripts\\audit-trail-hardening-check.py",
    "--label", "manual",
    "--deployment-profile", $DeploymentProfile,
    "--audit-retention-days", [string]$AuditRetentionDays,
    "--min-audit-retention-days", [string]$MinAuditRetentionDays,
    "--seed-entries", [string]$SeedEntries,
    "--workspace", $Workspace,
    "--output-file", $OutputFile
)
if ($AllowFailure) {
    $arguments += "--allow-failure"
}

& $PythonExe @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Audit trail hardening check failed with exit code $LASTEXITCODE."
}

Write-Host "Audit trail hardening check passed." -ForegroundColor Green
