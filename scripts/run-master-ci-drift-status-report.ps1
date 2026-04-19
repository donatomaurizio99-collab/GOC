param(
    [string]$Label = "master-ci-drift-status-report",
    [string]$Repo = "donatomaurizio99-collab/GOC",
    [double]$BlockedAgeHours = 24,
    [int]$PerPage = 100,
    [string]$IssuesFile = "",
    [string]$OutputFile = "artifacts\\master-ci-drift-status-report.json",
    [string]$MarkdownOutputFile = "artifacts\\master-ci-drift-status-report.md",
    [string]$PythonExe = "python"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$args = @(
    ".\\scripts\\master-ci-drift-status-report.py",
    "--label", $Label,
    "--repo", $Repo,
    "--blocked-age-hours", [string]$BlockedAgeHours,
    "--per-page", [string]$PerPage,
    "--output-file", $OutputFile,
    "--markdown-output-file", $MarkdownOutputFile
)
if ($IssuesFile) {
    $args += @("--issues-file", $IssuesFile)
}

& $PythonExe @args
if ($LASTEXITCODE -ne 0) {
    throw "Master CI-drift status report failed with exit code $LASTEXITCODE."
}

Write-Host "Master CI-drift status report completed." -ForegroundColor Green
