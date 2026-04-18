param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("master-branch-protection-drift", "release-gate-runtime-early-warning")]
    [string]$SignalId,
    [Parameter(Mandatory = $true)]
    [string]$ReportFile,
    [string]$PythonExe = "python",
    [string]$Repo = "donatomaurizio99-collab/GOC",
    [string]$RunUrl = "",
    [string]$OpenIssuesFile = "",
    [string]$IssueOplogFile = "",
    [string]$OutputFile = "artifacts\\ci-alert-issue-upsert.json",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$args = @(
    ".\\scripts\\ci-alert-issue-upsert.py",
    "--label", "manual",
    "--signal-id", $SignalId,
    "--repo", $Repo,
    "--report-file", $ReportFile,
    "--output-file", $OutputFile
)
if ($RunUrl) {
    $args += @("--run-url", $RunUrl)
}
if ($OpenIssuesFile) {
    $args += @("--open-issues-file", $OpenIssuesFile)
}
if ($IssueOplogFile) {
    $args += @("--issue-oplog-file", $IssueOplogFile)
}
if ($DryRun) {
    $args += "--dry-run"
}

& $PythonExe @args
if ($LASTEXITCODE -ne 0) {
    throw "CI alert issue upsert failed with exit code $LASTEXITCODE."
}

Write-Host "CI alert issue upsert completed." -ForegroundColor Green
