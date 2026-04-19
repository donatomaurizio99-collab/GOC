param(
    [string]$Label = "master-watchdog-rehearsal-slo-guard",
    [string]$Repo = "donatomaurizio99-collab/GOC",
    [string]$Branch = "master",
    [string]$WorkflowName = "Master Watchdog Rehearsal Drill",
    [double]$MaxAgeHours = 192,
    [double]$MttrTargetSeconds = 300,
    [int]$PerPage = 20,
    [string]$RunsFile = "",
    [string]$DrillReportFile = "",
    [string]$DrillArtifactName = "master-guard-workflow-health-rehearsal-drill",
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
    "--mttr-target-seconds", [string]$MttrTargetSeconds,
    "--per-page", [string]$PerPage,
    "--drill-artifact-name", $DrillArtifactName,
    "--output-file", $OutputFile
)
if ($RunsFile) {
    $args += @("--runs-file", $RunsFile)
}
if ($DrillReportFile) {
    $args += @("--drill-report-file", $DrillReportFile)
}
if ($AllowBreach) {
    $args += "--allow-breach"
}

& python @args
