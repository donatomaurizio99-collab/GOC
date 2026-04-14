param(
    [string]$PythonExe = "python",
    [string]$WorkspaceDir = ".tmp\\release-freeze-policy",
    [string]$ManifestPath = "artifacts\\desktop-rings.json",
    [string]$Ring = "stable",
    [int]$NonOkWindowSeconds = 300,
    [int]$PollIntervalSeconds = 30,
    [int]$MaxObservationSeconds = 900,
    [double]$MaxErrorBudgetBurnRatePercent = 2.0,
    [string]$BaseUrl = "",
    [string]$DatabaseUrl = "",
    [string]$MockSloStatuses = "",
    [string]$MockErrorBudgetBurnRates = "",
    [string]$SeedPreviousVersion = "",
    [string]$SeedIncidentVersion = "",
    [string]$PromotionTestVersion = "0.0.3",
    [switch]$DryRun,
    [switch]$KeepArtifacts
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$args = @(
    ".\\scripts\\release-freeze-policy.py",
    "--workspace", $WorkspaceDir,
    "--label", "manual",
    "--manifest-path", $ManifestPath,
    "--ring", $Ring,
    "--non-ok-window-seconds", [string]$NonOkWindowSeconds,
    "--poll-interval-seconds", [string]$PollIntervalSeconds,
    "--max-observation-seconds", [string]$MaxObservationSeconds,
    "--max-error-budget-burn-rate-percent", [string]$MaxErrorBudgetBurnRatePercent,
    "--promotion-test-version", [string]$PromotionTestVersion
)
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
    throw "Release freeze policy execution failed with exit code $LASTEXITCODE."
}

Write-Host "Release freeze policy check passed." -ForegroundColor Green
