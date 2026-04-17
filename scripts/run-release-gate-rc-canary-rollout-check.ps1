param(
    [string]$PythonExe = "python",
    [string]$PolicyFile = "docs\release-candidate-rollout-policy.json",
    [string]$RequiredReports = "artifacts/release-gate-staging-soak-readiness-release-gate.json,artifacts/release-gate-stability-final-readiness-release-gate.json,artifacts/p0-closure-report-release-gate.json,artifacts/canary-guardrails-release-gate.json",
    [string]$RequiredLabel = "release-gate",
    [string]$CandidateVersion = "0.0.2-rc1",
    [string]$OutputFile = "artifacts\release-gate-rc-canary-rollout-release-gate.json",
    [switch]$AllowNotReady
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$arguments = @(
    ".\scripts\release-gate-rc-canary-rollout-check.py",
    "--label", "manual",
    "--policy-file", $PolicyFile,
    "--required-reports", $RequiredReports,
    "--required-label", $RequiredLabel,
    "--candidate-version", $CandidateVersion,
    "--output-file", $OutputFile
)
if ($AllowNotReady) {
    $arguments += "--allow-not-ready"
}

& $PythonExe @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Release-gate RC canary rollout check failed with exit code $LASTEXITCODE."
}

Write-Host "Release-gate RC canary rollout check passed." -ForegroundColor Green
