param(
    [string]$PythonExe = "python",
    [string]$DeploymentProfile = "production",
    [string]$Workspace = ".tmp\canary-guardrails",
    [string]$ManifestPath = ".tmp\canary-guardrails\desktop-rings.json",
    [string]$PolicyFile = "docs\canary-guardrails-policy.json",
    [string]$RunbookFile = "docs\production-runbook.md",
    [string]$StableBaselineVersion = "0.0.1",
    [string]$CanaryCandidateVersion = "0.0.2",
    [string]$ExpectedDecision = "auto",
    [string]$MockSloStatuses = "ok,ok,critical,critical",
    [string]$MockErrorBudgetBurnRates = "0.5,0.8,2.5,2.5",
    [string]$OutputFile = "artifacts\canary-guardrails-check-report.json",
    [switch]$DryRun,
    [switch]$AllowFailure
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$arguments = @(
    ".\scripts\canary-guardrails-check.py",
    "--label", "manual",
    "--deployment-profile", $DeploymentProfile,
    "--workspace", $Workspace,
    "--manifest-path", $ManifestPath,
    "--policy-file", $PolicyFile,
    "--runbook-file", $RunbookFile,
    "--stable-baseline-version", $StableBaselineVersion,
    "--canary-candidate-version", $CanaryCandidateVersion,
    "--expected-decision", $ExpectedDecision,
    "--mock-slo-statuses", $MockSloStatuses,
    "--mock-error-budget-burn-rates", $MockErrorBudgetBurnRates,
    "--output-file", $OutputFile
)
if ($DryRun) {
    $arguments += "--dry-run"
}
if ($AllowFailure) {
    $arguments += "--allow-failure"
}

& $PythonExe @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Canary guardrails check failed with exit code $LASTEXITCODE."
}

Write-Host "Canary guardrails check passed." -ForegroundColor Green
