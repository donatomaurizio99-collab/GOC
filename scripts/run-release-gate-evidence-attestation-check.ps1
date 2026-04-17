param(
    [string]$PythonExe = "python",
    [string]$PolicyFile = "docs\\release-gate-evidence-attestation-policy.json",
    [string]$RequiredReports = "artifacts/release-gate-supply-chain-artifact-trust-release-gate.json,artifacts/release-gate-operations-handoff-readiness-release-gate.json,artifacts/release-gate-evidence-hash-manifest-release-gate.json",
    [string]$ManifestFile = "artifacts\\release-gate-evidence-manifest-release-gate.json",
    [string]$RequiredLabel = "release-gate",
    [string]$OutputFile = "artifacts\\release-gate-evidence-attestation-release-gate.json",
    [switch]$AllowMissingReports,
    [switch]$AllowNotReady
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$arguments = @(
    ".\scripts\release-gate-evidence-attestation-check.py",
    "--label", "manual",
    "--policy-file", $PolicyFile,
    "--required-reports", $RequiredReports,
    "--manifest-file", $ManifestFile,
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
    throw "Release-gate evidence attestation check failed with exit code $LASTEXITCODE."
}

Write-Host "Release-gate evidence attestation check passed." -ForegroundColor Green
