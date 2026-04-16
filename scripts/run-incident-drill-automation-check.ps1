param(
    [string]$PythonExe = "python",
    [string]$DeploymentProfile = "production",
    [string]$PolicyFile = "docs\incident-drill-automation-policy.json",
    [string]$RunbookFile = "docs\production-runbook.md",
    [string]$DrillReportFile = "",
    [switch]$MockReport,
    [int]$MockDaysSinceTabletop = 7,
    [int]$MockDaysSinceTechnical = 3,
    [string]$MockTabletopStatus = "completed",
    [string]$MockTechnicalStatus = "completed",
    [int]$MockOpenFollowups = 0,
    [int]$MaxTabletopAgeDays = 30,
    [int]$MaxTechnicalAgeDays = 14,
    [int]$MinTechnicalLoadRequests = 20,
    [int]$MaxOpenFollowups = 3,
    [string]$OutputFile = "artifacts\incident-drill-automation-check-report.json",
    [switch]$AllowFailure
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$arguments = @(
    ".\scripts\incident-drill-automation-check.py",
    "--label", "manual",
    "--deployment-profile", $DeploymentProfile,
    "--policy-file", $PolicyFile,
    "--runbook-file", $RunbookFile,
    "--mock-days-since-tabletop", [string]$MockDaysSinceTabletop,
    "--mock-days-since-technical", [string]$MockDaysSinceTechnical,
    "--mock-tabletop-status", $MockTabletopStatus,
    "--mock-technical-status", $MockTechnicalStatus,
    "--mock-open-followups", [string]$MockOpenFollowups,
    "--max-tabletop-age-days", [string]$MaxTabletopAgeDays,
    "--max-technical-age-days", [string]$MaxTechnicalAgeDays,
    "--min-technical-load-requests", [string]$MinTechnicalLoadRequests,
    "--max-open-followups", [string]$MaxOpenFollowups,
    "--output-file", $OutputFile
)

if (-not [string]::IsNullOrWhiteSpace($DrillReportFile)) {
    $arguments += @("--drill-report-file", $DrillReportFile)
}

if ($MockReport -or [string]::IsNullOrWhiteSpace($DrillReportFile)) {
    $arguments += "--mock-report"
}

if ($AllowFailure) {
    $arguments += "--allow-failure"
}

& $PythonExe @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Incident drill automation check failed with exit code $LASTEXITCODE."
}

Write-Host "Incident drill automation check passed." -ForegroundColor Green
