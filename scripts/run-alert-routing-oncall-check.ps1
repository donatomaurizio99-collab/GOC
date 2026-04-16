param(
    [string]$PythonExe = "python",
    [string]$DeploymentProfile = "production",
    [string]$DatabaseUrl = ":memory:",
    [string]$BaseUrl = "",
    [string]$SloJsonFile = "",
    [string]$MockSloStatus = "",
    [int]$MockAlertCount = 1,
    [string]$RoutingPolicyFile = "docs\\oncall-alert-routing-policy.json",
    [string]$RunbookFile = "docs\\production-runbook.md",
    [int]$MaxCriticalAckMinutes = 15,
    [int]$MaxWarningAckMinutes = 120,
    [string]$OutputFile = "artifacts\\alert-routing-oncall-check-report.json",
    [switch]$AllowFailure
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$arguments = @(
    ".\\scripts\\alert-routing-oncall-check.py",
    "--label", "manual",
    "--deployment-profile", $DeploymentProfile,
    "--database-url", $DatabaseUrl,
    "--routing-policy-file", $RoutingPolicyFile,
    "--runbook-file", $RunbookFile,
    "--max-critical-ack-minutes", [string]$MaxCriticalAckMinutes,
    "--max-warning-ack-minutes", [string]$MaxWarningAckMinutes,
    "--output-file", $OutputFile
)
if (-not [string]::IsNullOrWhiteSpace($BaseUrl)) {
    $arguments += @("--base-url", $BaseUrl)
}
if (-not [string]::IsNullOrWhiteSpace($SloJsonFile)) {
    $arguments += @("--slo-json-file", $SloJsonFile)
}
if (-not [string]::IsNullOrWhiteSpace($MockSloStatus)) {
    $arguments += @("--mock-slo-status", $MockSloStatus)
    $arguments += @("--mock-alert-count", [string]$MockAlertCount)
}
if ($AllowFailure) {
    $arguments += "--allow-failure"
}

& $PythonExe @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Alert routing on-call check failed with exit code $LASTEXITCODE."
}

Write-Host "Alert routing on-call check passed." -ForegroundColor Green
