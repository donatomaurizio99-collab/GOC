param(
    [string]$Label = "master-guard-chain-selftest",
    [ValidateSet("master-guard-workflow-health", "master-watchdog-rehearsal-drill-slo", "master-reliability-digest-guard")]
    [string]$SignalId = "master-guard-workflow-health",
    [string]$GuardReportFile = "artifacts\\master-guard-workflow-health-check.json",
    [string]$IssueUpsertReportFile = "artifacts\\master-guard-workflow-health-issue-upsert.json",
    [string]$OutputFile = "artifacts\\master-guard-chain-selftest.json"
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Resolve-Path (Join-Path $scriptDir "..")
$scriptPath = Join-Path $repoRoot "scripts\\master-guard-chain-selftest.py"

$args = @(
    $scriptPath,
    "--label", $Label,
    "--signal-id", $SignalId,
    "--guard-report-file", $GuardReportFile,
    "--issue-upsert-report-file", $IssueUpsertReportFile,
    "--output-file", $OutputFile
)

& python @args
