param(
    [string]$PythonExe = "python",
    [string]$WorkspaceDir = ".tmp\\auto-rollback-policy",
    [string]$ManifestPath = "artifacts\\desktop-rings.json",
    [string]$Ring = "stable",
    [int]$CriticalWindowSeconds = 300,
    [int]$ReadinessRegressionWindowSeconds = 120,
    [int]$PollIntervalSeconds = 30,
    [int]$MaxObservationSeconds = 900,
    [double]$MaxErrorBudgetBurnRatePercent = 2.0,
    [string]$BaseUrl = "",
    [string]$DatabaseUrl = "",
    [string]$MockSloStatuses = "",
    [string]$MockErrorBudgetBurnRates = "",
    [string]$MockReadinessValues = "",
    [string]$SeedPreviousVersion = "",
    [string]$SeedIncidentVersion = "",
    [string]$ExpectedTriggerReason = "auto",
    [string]$OutputFile = "artifacts\\auto-rollback-policy-report.json",
    [switch]$DryRun,
    [switch]$KeepArtifacts
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$args = @(
    ".\\scripts\\auto-rollback-policy.py",
    "--workspace", $WorkspaceDir,
    "--label", "manual",
    "--manifest-path", $ManifestPath,
    "--ring", $Ring,
    "--critical-window-seconds", [string]$CriticalWindowSeconds,
    "--readiness-regression-window-seconds", [string]$ReadinessRegressionWindowSeconds,
    "--poll-interval-seconds", [string]$PollIntervalSeconds,
    "--max-observation-seconds", [string]$MaxObservationSeconds,
    "--max-error-budget-burn-rate-percent", [string]$MaxErrorBudgetBurnRatePercent,
    "--expected-trigger-reason", $ExpectedTriggerReason
)
if (-not [string]::IsNullOrWhiteSpace($OutputFile)) {
    $args += @("--output-file", $OutputFile)
}
if (-not [string]::IsNullOrWhiteSpace($BaseUrl)) {
    $args += @("--base-url", $BaseUrl)
}
if (-not [string]::IsNullOrWhiteSpace($DatabaseUrl)) {
    $args += @("--database-url", $DatabaseUrl)
}
if (-not [string]::IsNullOrWhiteSpace($MockSloStatuses)) {
    $args += @("--mock-slo-statuses", $MockSloStatuses)
}
if (-not [string]::IsNullOrWhiteSpace($MockErrorBudgetBurnRates)) {
    $args += @("--mock-error-budget-burn-rates", $MockErrorBudgetBurnRates)
}
if (-not [string]::IsNullOrWhiteSpace($MockReadinessValues)) {
    $args += @("--mock-readiness-values", $MockReadinessValues)
}
if (-not [string]::IsNullOrWhiteSpace($SeedPreviousVersion)) {
    $args += @("--seed-previous-version", $SeedPreviousVersion)
}
if (-not [string]::IsNullOrWhiteSpace($SeedIncidentVersion)) {
    $args += @("--seed-incident-version", $SeedIncidentVersion)
}
if ($DryRun) {
    $args += "--dry-run"
}
if ($KeepArtifacts) {
    $args += "--keep-artifacts"
}

& $PythonExe @args
if ($LASTEXITCODE -ne 0) {
    throw "Auto rollback policy execution failed with exit code $LASTEXITCODE."
}

Write-Host "Auto rollback policy check passed." -ForegroundColor Green
