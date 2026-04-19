param(
    [string]$PythonExe = "python",
    [string]$Label = "master-release-gate-runtime-slo-guard",
    [string]$Repo = "donatomaurizio99-collab/GOC",
    [string]$Branch = "master",
    [string]$WorkflowName = "CI",
    [string]$ReleaseGateJobName = "Release Gate (Windows)",
    [int]$LookbackHours = 72,
    [int]$PerPage = 80,
    [int]$ThresholdSeconds = 600,
    [int]$SustainedRuns = 3,
    [string]$OutputFile = "artifacts\\release-gate-runtime-slo-guard.json",
    [switch]$AllowBreach
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$args = @(
    ".\\scripts\\release-gate-runtime-early-warning.py",
    "--label", $Label,
    "--repo", $Repo,
    "--branch", $Branch,
    "--workflow-name", $WorkflowName,
    "--release-gate-job-name", $ReleaseGateJobName,
    "--lookback-hours", [string]$LookbackHours,
    "--per-page", [string]$PerPage,
    "--threshold-seconds", [string]$ThresholdSeconds,
    "--sustained-runs", [string]$SustainedRuns,
    "--output-file", $OutputFile
)
if (-not $AllowBreach) {
    $args += "--fail-on-warning"
}

& $PythonExe @args
if ($LASTEXITCODE -ne 0) {
    throw "Master release-gate runtime SLO guard failed with exit code $LASTEXITCODE."
}

Write-Host "Master release-gate runtime SLO guard completed." -ForegroundColor Green
