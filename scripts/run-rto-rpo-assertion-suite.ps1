param(
    [string]$PythonExe = "python",
    [string]$DeploymentProfile = "production",
    [string]$Workspace = ".tmp\rto-rpo-assertion-suite",
    [string]$PolicyFile = "docs\rto-rpo-assertion-policy.json",
    [string]$RunbookFile = "docs\production-runbook.md",
    [int]$SeedRows = 48,
    [int]$TailWriteRows = 12,
    [double]$MaxRtoSeconds = 20.0,
    [int]$MaxRpoRowsLost = 96,
    [string]$OutputFile = "artifacts\rto-rpo-assertion-suite-report.json",
    [switch]$KeepArtifacts,
    [switch]$AllowFailure
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$arguments = @(
    ".\scripts\rto-rpo-assertion-suite.py",
    "--label", "manual",
    "--deployment-profile", $DeploymentProfile,
    "--workspace", $Workspace,
    "--policy-file", $PolicyFile,
    "--runbook-file", $RunbookFile,
    "--seed-rows", [string]$SeedRows,
    "--tail-write-rows", [string]$TailWriteRows,
    "--max-rto-seconds", [string]$MaxRtoSeconds,
    "--max-rpo-rows-lost", [string]$MaxRpoRowsLost,
    "--output-file", $OutputFile
)
if ($KeepArtifacts) {
    $arguments += "--keep-artifacts"
}
if ($AllowFailure) {
    $arguments += "--allow-failure"
}

& $PythonExe @arguments
if ($LASTEXITCODE -ne 0) {
    throw "RTO/RPO assertion suite failed with exit code $LASTEXITCODE."
}

Write-Host "RTO/RPO assertion suite passed." -ForegroundColor Green
