param(
    [string]$PythonExe = "python",
    [string]$Repo = "donatomaurizio99-collab/GOC",
    [string]$Branch = "master",
    [string]$WorkflowName = "CI",
    [string]$RequiredJobs = "Release Gate (Windows),Security CI Lane,Pytest (Python 3.11),Pytest (Python 3.12),Desktop Smoke (Windows)",
    [int]$LookbackHours = 24,
    [int]$PerPage = 100,
    [int]$MaxNonGreenRuns = 0,
    [string]$OutputFile = "artifacts\\master-required-checks-24h-report.json",
    [switch]$AllowNonGreen
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$args = @(
    ".\\scripts\\master-required-checks-24h-report.py",
    "--label", "manual",
    "--repo", $Repo,
    "--branch", $Branch,
    "--workflow-name", $WorkflowName,
    "--required-jobs", $RequiredJobs,
    "--lookback-hours", [string]$LookbackHours,
    "--per-page", [string]$PerPage,
    "--max-non-green-runs", [string]$MaxNonGreenRuns,
    "--output-file", $OutputFile
)
if ($AllowNonGreen) {
    $args += "--allow-non-green"
}

& $PythonExe @args
if ($LASTEXITCODE -ne 0) {
    throw "Master required-checks 24h report failed with exit code $LASTEXITCODE."
}

Write-Host "Master required-checks 24h report passed." -ForegroundColor Green
