param(
    [string]$PythonExe = "python",
    [ValidateSet("release-gate", "scheduled")]
    [string]$Profile = "scheduled",
    [string]$Workspace = ".tmp\disaster-recovery-rehearsal-pack",
    [string]$RunbookFile = "docs\production-runbook.md",
    [string]$RtoRpoPolicyFile = "docs\rto-rpo-assertion-policy.json",
    [string]$DrillFilter = "",
    [string]$MockDrillResultsFile = "",
    [int]$MaxFailedDrills = 0,
    [int]$MaxTotalDurationSeconds = 2400,
    [string]$OutputFile = "artifacts\disaster-recovery-rehearsal-pack-report.json",
    [string]$EvidenceDir = "artifacts\disaster-recovery-rehearsal-pack-evidence",
    [switch]$KeepArtifacts,
    [switch]$AllowFailure
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$arguments = @(
    ".\scripts\disaster-recovery-rehearsal-pack.py",
    "--label", "manual",
    "--profile", $Profile,
    "--workspace", $Workspace,
    "--runbook-file", $RunbookFile,
    "--rto-rpo-policy-file", $RtoRpoPolicyFile,
    "--max-failed-drills", [string]$MaxFailedDrills,
    "--max-total-duration-seconds", [string]$MaxTotalDurationSeconds,
    "--output-file", $OutputFile,
    "--evidence-dir", $EvidenceDir
)
if (-not [string]::IsNullOrWhiteSpace($DrillFilter)) {
    $arguments += @("--drill-filter", $DrillFilter)
}
if (-not [string]::IsNullOrWhiteSpace($MockDrillResultsFile)) {
    $arguments += @("--mock-drill-results-file", $MockDrillResultsFile)
}
if ($KeepArtifacts) {
    $arguments += "--keep-artifacts"
}
if ($AllowFailure) {
    $arguments += "--allow-failure"
}

& $PythonExe @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Disaster-recovery rehearsal pack failed with exit code $LASTEXITCODE."
}

Write-Host "Disaster-recovery rehearsal pack passed." -ForegroundColor Green
