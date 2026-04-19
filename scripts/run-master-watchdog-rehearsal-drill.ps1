param(
    [string]$Label = "master-watchdog-rehearsal-drill",
    [string]$Repo = "donatomaurizio99-collab/GOC",
    [string]$Branch = "master",
    [string]$RunUrl = "",
    [double]$MttrTargetSeconds = 300,
    [string]$CheckReportFile = "artifacts\\master-guard-workflow-health-rehearsal-check.json",
    [string]$IssueUpsertReportFile = "artifacts\\master-guard-workflow-health-rehearsal-issue-upsert.json",
    [string]$OutputFile = "artifacts\\master-guard-workflow-health-rehearsal-drill.json",
    [string]$MarkdownOutputFile = "artifacts\\master-guard-workflow-health-rehearsal-drill.md"
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
    "--mttr-target-seconds", [string]$MttrTargetSeconds,
    "--check-report-file", $CheckReportFile,
    "--issue-upsert-report-file", $IssueUpsertReportFile,
    "--output-file", $OutputFile,
    "--markdown-output-file", $MarkdownOutputFile
)
if ($RunUrl) {
    $args += @("--run-url", $RunUrl)
}

& python @args
