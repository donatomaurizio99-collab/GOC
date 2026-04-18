param(
    [string]$Label = "master-guard-burnin-check",
    [string]$Repo = "donatomaurizio99-collab/GOC",
    [string]$Branch = "master",
    [int]$PerPage = 20,
    [int]$RequiredSuccessfulRuns = 3,
    [int]$DigestRequiredSuccessfulRuns = 1,
    [int]$DrillRequiredSuccessfulRuns = 1,
    [string]$FixturesFile = "",
    [string]$WorkflowSpecsFile = "",
    [string]$OutputFile = "artifacts\\master-guard-burnin-check.json",
    [switch]$AllowDegraded
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Resolve-Path (Join-Path $scriptDir "..")
$scriptPath = Join-Path $repoRoot "scripts\\master-guard-burnin-check.py"

$args = @(
    $scriptPath,
    "--label", $Label,
    "--repo", $Repo,
    "--branch", $Branch,
    "--per-page", [string]$PerPage,
    "--required-successful-runs", [string]$RequiredSuccessfulRuns,
    "--digest-required-successful-runs", [string]$DigestRequiredSuccessfulRuns,
    "--drill-required-successful-runs", [string]$DrillRequiredSuccessfulRuns,
    "--output-file", $OutputFile
)
if ($FixturesFile) {
    $args += @("--fixtures-file", $FixturesFile)
}
if ($WorkflowSpecsFile) {
    $args += @("--workflow-specs-file", $WorkflowSpecsFile)
}
if ($AllowDegraded) {
    $args += "--allow-degraded"
}

& python @args
