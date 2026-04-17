param(
    [string]$PythonExe = "python",
    [string]$PolicyFile = "docs\\release-gate-artifact-trust-policy.json",
    [string]$RequiredReports = "artifacts/security-ci-lane-release-gate.json,artifacts/release-gate-evidence-hash-manifest-release-gate.json",
    [string]$ManifestFile = "artifacts\\release-gate-evidence-manifest-release-gate.json",
    [string]$RequiredLabel = "release-gate",
    [string]$OutputFile = "artifacts\\release-gate-supply-chain-artifact-trust-release-gate.json",
    [switch]$AllowMissingReports,
    [switch]$AllowNotReady
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$arguments = @(
    ".\scripts\release-gate-supply-chain-artifact-trust-check.py",
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
    throw "Release-gate supply-chain artifact trust check failed with exit code $LASTEXITCODE."
}

Write-Host "Release-gate supply-chain artifact trust check passed." -ForegroundColor Green
