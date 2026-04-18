param(
    [string]$PythonExe = "python",
    [string]$Repo = "donatomaurizio99-collab/GOC",
    [string]$Branch = "master",
    [string]$WorkflowName = "CI",
    [string]$ReleaseGateJobName = "Release Gate (Windows)",
    [int]$LookbackHours = 72,
    [int]$PerPage = 80,
    [int]$ThresholdSeconds = 540,
    [int]$SustainedRuns = 3,
    [string]$OutputFile = "artifacts\\release-gate-runtime-early-warning.json",
    [switch]$FailOnWarning
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$args = @(
    ".\\scripts\\release-gate-runtime-early-warning.py",
    "--label", "manual",
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
if ($FailOnWarning) {
    $args += "--fail-on-warning"
}

& $PythonExe @args
if ($LASTEXITCODE -ne 0) {
    throw "Release-gate runtime early warning failed with exit code $LASTEXITCODE."
}

Write-Host "Release-gate runtime early warning completed." -ForegroundColor Green
