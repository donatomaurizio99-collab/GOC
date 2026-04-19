param(
    [string]$Label = "master-guard-burnin-check",
    [string]$Repo = "donatomaurizio99-collab/GOC",
    [string]$Branch = "master",
    [int]$PerPage = 20,
    [int]$RequiredSuccessfulRuns = 3,
    [int]$DigestRequiredSuccessfulRuns = 1,
    [int]$DrillRequiredSuccessfulRuns = 1,
    [int]$BurninWindowDays = 14,
    [double]$MttrTargetSeconds = 0,
    [string]$MttrPolicyFile = "docs\\watchdog-rehearsal-mttr-policy.json",
    [string]$WatchdogSloWorkflowName = "Master Watchdog Rehearsal SLO Guard",
    [string]$WatchdogSloArtifactName = "master-watchdog-rehearsal-slo-guard",
    [string]$WatchdogSloReportFilename = "master-watchdog-rehearsal-slo-guard.json",
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
    "--burnin-window-days", [string]$BurninWindowDays,
    "--mttr-policy-file", $MttrPolicyFile,
    "--watchdog-slo-workflow-name", $WatchdogSloWorkflowName,
    "--watchdog-slo-artifact-name", $WatchdogSloArtifactName,
    "--watchdog-slo-report-filename", $WatchdogSloReportFilename,
    "--output-file", $OutputFile
)
if ($MttrTargetSeconds -gt 0) {
    $args += @("--mttr-target-seconds", [string]$MttrTargetSeconds)
}
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
