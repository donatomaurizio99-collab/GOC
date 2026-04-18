param(
    [string]$Label = "master-watchdog-rehearsal-slo-guard",
    [string]$Repo = "donatomaurizio99-collab/GOC",
    [string]$Branch = "master",
    [string]$WorkflowName = "Master Watchdog Rehearsal Drill",
    [double]$MaxAgeHours = 192,
    [int]$PerPage = 20,
    [string]$RunsFile = "",
    [string]$OutputFile = "artifacts\\master-watchdog-rehearsal-slo-guard.json",
    [switch]$AllowBreach
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Resolve-Path (Join-Path $scriptDir "..")
$scriptPath = Join-Path $repoRoot "scripts\\master-watchdog-rehearsal-slo-guard.py"

$args = @(
    $scriptPath,
    "--label", $Label,
    "--repo", $Repo,
    "--branch", $Branch,
    "--workflow-name", $WorkflowName,
    "--max-age-hours", [string]$MaxAgeHours,
    "--per-page", [string]$PerPage,
    "--output-file", $OutputFile
)
if ($RunsFile) {
    $args += @("--runs-file", $RunsFile)
}
if ($AllowBreach) {
    $args += "--allow-breach"
}

& python @args
