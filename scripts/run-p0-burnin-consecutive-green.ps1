param(
    [string]$PythonExe = "python",
    [string]$Repo = "donatomaurizio99-collab/GOC",
    [string]$Branch = "master",
    [string]$WorkflowName = "CI",
    [string]$RequiredJobs = "Release Gate (Windows),Pytest (Python 3.11),Pytest (Python 3.12),Desktop Smoke (Windows)",
    [int]$RequiredConsecutive = 10,
    [int]$PerPage = 50,
    [string]$OutputFile = "artifacts\\p0-burnin-consecutive-green-report.json",
    [switch]$AllowNotMet
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$args = @(
    ".\\scripts\\p0-burnin-consecutive-green.py",
    "--label", "manual",
    "--repo", $Repo,
    "--branch", $Branch,
    "--workflow-name", $WorkflowName,
    "--required-jobs", $RequiredJobs,
    "--required-consecutive", [string]$RequiredConsecutive,
    "--per-page", [string]$PerPage,
    "--output-file", $OutputFile
)
if ($AllowNotMet) {
    $args += "--allow-not-met"
}

& $PythonExe @args
if ($LASTEXITCODE -ne 0) {
    throw "P0 burn-in consecutive-green check failed with exit code $LASTEXITCODE."
}

Write-Host "P0 burn-in consecutive-green check passed." -ForegroundColor Green
