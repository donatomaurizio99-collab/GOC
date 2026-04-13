param(
    [string]$PythonExe = "python",
    [string]$WorkspaceDir = ".tmp\\auto-rollback-policy",
    [string]$ManifestPath = "artifacts\\desktop-rings.json",
    [string]$Ring = "stable",
    [int]$CriticalWindowSeconds = 300,
    [int]$PollIntervalSeconds = 30,
    [int]$MaxObservationSeconds = 900,
    [string]$BaseUrl = "",
    [string]$DatabaseUrl = "",
    [string]$MockSloStatuses = "",
    [string]$SeedPreviousVersion = "",
    [string]$SeedIncidentVersion = "",
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
    "--poll-interval-seconds", [string]$PollIntervalSeconds,
    "--max-observation-seconds", [string]$MaxObservationSeconds
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
