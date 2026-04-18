param(
    [string]$Label = "master-reliability-digest",
    [string]$Repo = "donatomaurizio99-collab/GOC",
    [string]$Branch = "master",
    [int]$CiPerPage = 30,
    [int]$GuardPerPage = 30,
    [int]$TrendRuns = 10,
    [int]$GuardTrendRuns = 10,
    [int]$ReleaseGateWarningSeconds = 540,
    [int]$WarningSustainedRuns = 3,
    [string]$CiRunsFile = "",
    [string]$CiJobsDir = "",
    [string]$GuardRunsFile = "",
    [string]$GuardUpsertReportsDir = "",
    [string]$OutputFile = "artifacts\\master-reliability-digest.json",
    [string]$MarkdownOutputFile = "artifacts\\master-reliability-digest.md",
    [switch]$FailOnWarning
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Resolve-Path (Join-Path $scriptDir "..")
$scriptPath = Join-Path $repoRoot "scripts\\master-reliability-digest.py"

$args = @(
    $scriptPath,
    "--label", $Label,
    "--repo", $Repo,
    "--branch", $Branch,
    "--ci-per-page", [string]$CiPerPage,
    "--guard-per-page", [string]$GuardPerPage,
    "--trend-runs", [string]$TrendRuns,
    "--guard-trend-runs", [string]$GuardTrendRuns,
    "--release-gate-warning-seconds", [string]$ReleaseGateWarningSeconds,
    "--warning-sustained-runs", [string]$WarningSustainedRuns,
    "--output-file", $OutputFile,
    "--markdown-output-file", $MarkdownOutputFile
)
if ($CiRunsFile) {
    $args += @("--ci-runs-file", $CiRunsFile)
}
if ($CiJobsDir) {
    $args += @("--ci-jobs-dir", $CiJobsDir)
}
if ($GuardRunsFile) {
    $args += @("--guard-runs-file", $GuardRunsFile)
}
if ($GuardUpsertReportsDir) {
    $args += @("--guard-upsert-reports-dir", $GuardUpsertReportsDir)
}
if ($FailOnWarning) {
    $args += "--fail-on-warning"
}

& python @args
