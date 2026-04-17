param(
    [string]$PythonExe = "python",
    [string]$StepTimingsFiles = "",
    [string]$StepTimingsGlob = "artifacts/release-gate-step-timings*.json",
    [string]$RequiredLabel = "release-gate",
    [int]$MinSamples = 3,
    [double]$BaselinePercentile = 50,
    [double]$MaxDurationPercentile = 95,
    [double]$HeadroomPercent = 25,
    [double]$MaxRegressionPercent = 40,
    [int]$TrendTopN = 8,
    [string]$OutputFile = "artifacts\release-gate-performance-policy-calibration-release-gate.json",
    [string]$PolicyOutputFile = "docs\release-gate-performance-budget-policy.json",
    [string]$HistoryBaselineOutputFile = "docs\release-gate-performance-history-baseline.json",
    [switch]$WriteUpdates,
    [switch]$AllowInsufficientSamples
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$arguments = @(
    ".\scripts\release-gate-performance-policy-calibrate.py",
    "--label", "manual",
    "--step-timings-glob", $StepTimingsGlob,
    "--required-label", $RequiredLabel,
    "--min-samples", [string]$MinSamples,
    "--baseline-percentile", [string]$BaselinePercentile,
    "--max-duration-percentile", [string]$MaxDurationPercentile,
    "--headroom-percent", [string]$HeadroomPercent,
    "--max-regression-percent", [string]$MaxRegressionPercent,
    "--trend-top-n", [string]$TrendTopN,
    "--output-file", $OutputFile,
    "--policy-output-file", $PolicyOutputFile,
    "--history-baseline-output-file", $HistoryBaselineOutputFile
)
if (-not [string]::IsNullOrWhiteSpace($StepTimingsFiles)) {
    $arguments += @("--step-timings-files", $StepTimingsFiles)
}
if ($WriteUpdates) {
    $arguments += "--write-updates"
}
if ($AllowInsufficientSamples) {
    $arguments += "--allow-insufficient-samples"
}

& $PythonExe @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Release-gate performance policy calibration failed with exit code $LASTEXITCODE."
}

Write-Host "Release-gate performance policy calibration completed." -ForegroundColor Green
