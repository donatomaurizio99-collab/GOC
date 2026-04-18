param(
    [string]$PythonExe = "python",
    [string]$PolicyFile = "docs\\release-gate-post-cutover-finalization-policy.json",
    [string]$RequiredReports = "artifacts/release-gate-production-cutover-readiness-release-gate.json,artifacts/release-gate-hypercare-activation-release-gate.json,artifacts/release-gate-rollback-trigger-integrity-release-gate.json,artifacts/release-gate-production-final-attestation-release-gate.json",
    [string]$RollbackIntegrityReportFile = "artifacts\\release-gate-rollback-trigger-integrity-release-gate.json",
    [string]$ProductionFinalReportFile = "artifacts\\release-gate-production-final-attestation-release-gate.json",
    [string]$RequiredLabel = "release-gate",
    [string]$OutputFile = "artifacts\\release-gate-post-cutover-finalization-release-gate.json",
    [switch]$AllowMissingReports,
    [switch]$AllowNotReady
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$arguments = @(
    ".\scripts\release-gate-post-cutover-finalization-check.py",
    "--label", "manual",
    "--policy-file", $PolicyFile,
    "--required-reports", $RequiredReports,
    "--rollback-integrity-report-file", $RollbackIntegrityReportFile,
    "--production-final-report-file", $ProductionFinalReportFile,
    "--required-label", $RequiredLabel,
    "--output-file", $OutputFile
)
if ($AllowMissingReports) {
    $arguments += "--allow-missing-reports"
}
if ($AllowNotReady) {
    $arguments += "--allow-not-ready"
}

& $PythonExe @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Release-gate post-cutover finalization check failed with exit code $LASTEXITCODE."
}

Write-Host "Release-gate post-cutover finalization check passed." -ForegroundColor Green
