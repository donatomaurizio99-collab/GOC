param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("master-branch-protection-drift", "master-guard-workflow-health", "release-gate-runtime-early-warning", "release-gate-runtime-slo-guard", "release-gate-runtime-alert-age-slo", "master-watchdog-rehearsal-drill-slo", "master-reliability-digest-warning", "master-reliability-digest-guard")]
    [string]$SignalId,
    [Parameter(Mandatory = $true)]
    [string]$ReportFile,
    [string]$PythonExe = "python",
    [string]$Repo = "donatomaurizio99-collab/GOC",
    [string]$RunUrl = "",
    [string]$IssuesFile = "",
    [string]$IssueOplogFile = "",
    [int]$RecoveryThreshold = 2,
    [double]$AlertAgeHours = 72,
    [int]$ActiveCommentCooldown = 3,
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
    "--recovery-threshold", [string]$RecoveryThreshold,
    "--alert-age-hours", [string]$AlertAgeHours,
    "--active-comment-cooldown", [string]$ActiveCommentCooldown,
    "--output-file", $OutputFile
)
if ($RunUrl) {
    $args += @("--run-url", $RunUrl)
}
if ($IssuesFile) {
    $args += @("--issues-file", $IssuesFile)
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
