param(
    [string]$PythonExe = "python",
    [string]$RequiredReports = "artifacts/release-gate-production-readiness-certification-release-gate.json,artifacts/release-gate-slo-burn-rate-v2-release-gate.json,artifacts/release-gate-deploy-rehearsal-release-gate.json,artifacts/release-gate-chaos-matrix-continuous-release-gate.json,artifacts/release-gate-supply-chain-artifact-trust-release-gate.json,artifacts/release-gate-operations-handoff-readiness-release-gate.json,artifacts/release-gate-evidence-attestation-release-gate.json,artifacts/p0-closure-report-release-gate.json",
    [string]$RequiredLabel = "release-gate",
    [string]$OutputFile = "artifacts\\release-gate-release-train-readiness-release-gate.json",
    [switch]$AllowMissingReports,
    [switch]$AllowNotReady
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$arguments = @(
    ".\scripts\release-gate-release-train-readiness-check.py",
    "--label", "manual",
    "--required-reports", $RequiredReports,
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
    throw "Release-gate release-train readiness check failed with exit code $LASTEXITCODE."
}

Write-Host "Release-gate release-train readiness check passed." -ForegroundColor Green
