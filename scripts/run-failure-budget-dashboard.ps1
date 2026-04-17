param(
    [string]$PythonExe = "python",
    [string]$RunbookFile = "docs\production-runbook.md",
    [string]$BudgetReportFiles = "artifacts\load-profile-framework-release-gate.json,artifacts\rto-rpo-assertion-release-gate.json,artifacts\canary-guardrails-release-gate.json,artifacts\auto-rollback-policy-release-gate.json,artifacts\p0-disaster-recovery-rehearsal-pack-release-gate.json",
    [string]$OutputFile = "artifacts\failure-budget-dashboard-release-gate.json",
    [switch]$AllowMissingReports
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$arguments = @(
    ".\scripts\failure-budget-dashboard.py",
    "--label", "manual",
    "--runbook-file", $RunbookFile,
    "--budget-report-files", $BudgetReportFiles,
    "--output-file", $OutputFile
)
if ($AllowMissingReports) {
    $arguments += "--allow-missing-reports"
}

& $PythonExe @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Failure budget dashboard failed with exit code $LASTEXITCODE."
}

Write-Host "Failure budget dashboard passed." -ForegroundColor Green
