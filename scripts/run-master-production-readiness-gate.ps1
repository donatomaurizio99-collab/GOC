param(
    [string]$Label = "master-production-readiness-gate",
    [string]$RequiredChecksReportFile = "artifacts\\master-required-checks-24h-report-readiness.json",
    [string]$BranchProtectionReportFile = "artifacts\\master-branch-protection-drift-guard-readiness.json",
    [string]$GuardHealthReportFile = "artifacts\\master-guard-workflow-health-check-readiness.json",
    [string]$GuardBurninReportFile = "artifacts\\master-guard-burnin-check-readiness.json",
    [string]$OutputFile = "artifacts\\master-production-readiness-gate.json",
    [switch]$AllowNotReady
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Resolve-Path (Join-Path $scriptDir "..")
$scriptPath = Join-Path $repoRoot "scripts\\master-production-readiness-gate.py"

$args = @(
    $scriptPath,
    "--label", $Label,
    "--required-checks-report-file", $RequiredChecksReportFile,
    "--branch-protection-report-file", $BranchProtectionReportFile,
    "--guard-health-report-file", $GuardHealthReportFile,
    "--guard-burnin-report-file", $GuardBurninReportFile,
    "--output-file", $OutputFile
)
if ($AllowNotReady) {
    $args += "--allow-not-ready"
}

& python @args
