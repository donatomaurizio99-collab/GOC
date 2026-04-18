param(
    [string]$Label = "master-watchdog-rehearsal-drill",
    [string]$Repo = "donatomaurizio99-collab/GOC",
    [string]$Branch = "master",
    [string]$RunUrl = "",
    [string]$CheckReportFile = "artifacts\\master-guard-workflow-health-rehearsal-check.json",
    [string]$IssueUpsertReportFile = "artifacts\\master-guard-workflow-health-rehearsal-issue-upsert.json",
    [string]$OutputFile = "artifacts\\master-guard-workflow-health-rehearsal-drill.json"
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Resolve-Path (Join-Path $scriptDir "..")
$scriptPath = Join-Path $repoRoot "scripts\\master-watchdog-rehearsal-drill.py"

$args = @(
    $scriptPath,
    "--label", $Label,
    "--repo", $Repo,
    "--branch", $Branch,
    "--check-report-file", $CheckReportFile,
    "--issue-upsert-report-file", $IssueUpsertReportFile,
    "--output-file", $OutputFile
)
if ($RunUrl) {
    $args += @("--run-url", $RunUrl)
}

& python @args
