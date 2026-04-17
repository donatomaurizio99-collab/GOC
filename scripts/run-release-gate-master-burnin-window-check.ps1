param(
    [string]$PythonExe = "python",
    [string]$BurninReportFile = "artifacts\p0-burnin-consecutive-green-release-gate.json",
    [int]$MinConsecutive = 3,
    [int]$TargetConsecutive = 5,
    [int]$MaxFailingJobs = 0,
    [string]$OutputFile = "artifacts\release-gate-master-burnin-window-release-gate.json",
    [switch]$AllowTargetNotMet,
    [switch]$AllowFlakyJobs
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$arguments = @(
    ".\scripts\release-gate-master-burnin-window-check.py",
    "--label", "manual",
    "--burnin-report-file", $BurninReportFile,
    "--min-consecutive", [string]$MinConsecutive,
    "--target-consecutive", [string]$TargetConsecutive,
    "--max-failing-jobs", [string]$MaxFailingJobs,
    "--output-file", $OutputFile
)
if ($AllowTargetNotMet) {
    $arguments += "--allow-target-not-met"
}
if ($AllowFlakyJobs) {
    $arguments += "--allow-flaky-jobs"
}

& $PythonExe @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Release-gate master burn-in window check failed with exit code $LASTEXITCODE."
}

Write-Host "Release-gate master burn-in window check passed." -ForegroundColor Green
