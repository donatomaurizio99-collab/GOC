param(
    [string]$Label = "watchdog-rehearsal-mttr-calibration",
    [string]$ProjectRoot = "",
    [string]$ReportFiles = "",
    [string]$ReportsGlob = "artifacts\\master-guard-workflow-health-rehearsal-drill*.json",
    [int]$MinSamples = 10,
    [int]$MaxSamples = 14,
    [double]$PercentileTarget = 95,
    [double]$HeadroomPercent = 10,
    [string]$OutputFile = "artifacts\\watchdog-rehearsal-mttr-calibration.json",
    [string]$PolicyOutputFile = "docs\\watchdog-rehearsal-mttr-policy.json",
    [switch]$WriteUpdates,
    [switch]$AllowInsufficientSamples
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Resolve-Path (Join-Path $scriptDir "..")
$scriptPath = Join-Path $repoRoot "scripts\\watchdog-rehearsal-mttr-calibrate.py"

$args = @(
    $scriptPath,
    "--label", $Label,
    "--reports-glob", $ReportsGlob,
    "--min-samples", [string]$MinSamples,
    "--max-samples", [string]$MaxSamples,
    "--percentile-target", [string]$PercentileTarget,
    "--headroom-percent", [string]$HeadroomPercent,
    "--output-file", $OutputFile,
    "--policy-output-file", $PolicyOutputFile
)
if ($ProjectRoot) {
    $args += @("--project-root", $ProjectRoot)
}
if ($ReportFiles) {
    $args += @("--report-files", $ReportFiles)
}
if ($WriteUpdates) {
    $args += "--write-updates"
}
if ($AllowInsufficientSamples) {
    $args += "--allow-insufficient-samples"
}

& python @args
