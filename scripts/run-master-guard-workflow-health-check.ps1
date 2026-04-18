param(
    [string]$PythonExe = "python",
    [string]$Repo = "donatomaurizio99-collab/GOC",
    [string]$Branch = "master",
    [int]$LookbackHours = 30,
    [int]$PerPage = 50,
    [string]$FixturesFile = "",
    [string]$OutputFile = "artifacts\\master-guard-workflow-health-check.json",
    [switch]$AllowDegraded
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$args = @(
    ".\\scripts\\master-guard-workflow-health-check.py",
    "--label", "manual",
    "--repo", $Repo,
    "--branch", $Branch,
    "--lookback-hours", [string]$LookbackHours,
    "--per-page", [string]$PerPage,
    "--output-file", $OutputFile
)
if ($FixturesFile) {
    $args += @("--fixtures-file", $FixturesFile)
}
if ($AllowDegraded) {
    $args += "--allow-degraded"
}

& $PythonExe @args
if ($LASTEXITCODE -ne 0) {
    throw "Master guard-workflow health check failed with exit code $LASTEXITCODE."
}

Write-Host "Master guard-workflow health check passed." -ForegroundColor Green
