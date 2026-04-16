param(
    [string]$PythonExe = "python",
    [string]$DeploymentProfile = "production",
    [string]$ScanPath = "goal_ops_console",
    [int]$MaxDependencyVulnerabilities = 0,
    [int]$MaxSastHigh = 0,
    [int]$MaxSastMedium = 200,
    [int]$TimeoutSeconds = 180,
    [switch]$SkipDependencyAudit,
    [switch]$SkipSast,
    [switch]$SkipSbom,
    [switch]$AllowMissingTools,
    [string]$DependencyAuditJsonFile = "",
    [string]$SastJsonFile = "",
    [string]$SbomOutputFile = "artifacts\\security-sbom-check.json",
    [string]$OutputFile = "artifacts\\security-ci-lane-check-report.json",
    [switch]$AllowFailure
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$arguments = @(
    ".\\scripts\\security-ci-lane-check.py",
    "--label", "manual",
    "--python-exe", $PythonExe,
    "--deployment-profile", $DeploymentProfile,
    "--scan-path", $ScanPath,
    "--max-dependency-vulnerabilities", [string]$MaxDependencyVulnerabilities,
    "--max-sast-high", [string]$MaxSastHigh,
    "--max-sast-medium", [string]$MaxSastMedium,
    "--timeout-seconds", [string]$TimeoutSeconds,
    "--sbom-output-file", $SbomOutputFile,
    "--output-file", $OutputFile
)
if ($SkipDependencyAudit) {
    $arguments += "--skip-dependency-audit"
}
if ($SkipSast) {
    $arguments += "--skip-sast"
}
if ($SkipSbom) {
    $arguments += "--skip-sbom"
}
if ($AllowMissingTools) {
    $arguments += "--allow-missing-tools"
}
if (-not [string]::IsNullOrWhiteSpace($DependencyAuditJsonFile)) {
    $arguments += @("--dependency-audit-json-file", $DependencyAuditJsonFile)
}
if (-not [string]::IsNullOrWhiteSpace($SastJsonFile)) {
    $arguments += @("--sast-json-file", $SastJsonFile)
}
if ($AllowFailure) {
    $arguments += "--allow-failure"
}

& $PythonExe @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Security CI lane check failed with exit code $LASTEXITCODE."
}

Write-Host "Security CI lane check passed." -ForegroundColor Green
