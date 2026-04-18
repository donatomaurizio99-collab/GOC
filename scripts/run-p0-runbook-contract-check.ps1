param(
    [string]$PythonExe = "python",
    [string]$RegistryFile = "docs\\release-gate-registry.json",
    [string]$OutputFile = "artifacts\\p0-runbook-contract-check-report.json",
    [string]$RequiredRunbookScripts = "",
    [string]$RequiredStrictFlags = "",
    [string]$StabilityCanaryBaselineFile = "docs\\stability-canary-baseline.json",
    [string]$RequiredCanaryDrills = "",
    [string]$RequiredCiArtifactPaths = "",
    [string]$RequiredRunbookTokens = "",
    [string]$RequiredReleaseGateTokens = ""
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$arguments = @(
    ".\\scripts\\p0-runbook-contract-check.py",
    "--label", "manual",
    "--registry-file", $RegistryFile,
    "--stability-canary-baseline-file", $StabilityCanaryBaselineFile,
    "--output-file", $OutputFile
)
if (-not [string]::IsNullOrWhiteSpace($RequiredRunbookScripts)) {
    $arguments += @("--required-runbook-scripts", $RequiredRunbookScripts)
}
if (-not [string]::IsNullOrWhiteSpace($RequiredStrictFlags)) {
    $arguments += @("--required-strict-flags", $RequiredStrictFlags)
}
if (-not [string]::IsNullOrWhiteSpace($RequiredCanaryDrills)) {
    $arguments += @("--required-canary-drills", $RequiredCanaryDrills)
}
if (-not [string]::IsNullOrWhiteSpace($RequiredCiArtifactPaths)) {
    $arguments += @("--required-ci-artifact-paths", $RequiredCiArtifactPaths)
}
if (-not [string]::IsNullOrWhiteSpace($RequiredRunbookTokens)) {
    $arguments += @("--required-runbook-tokens", $RequiredRunbookTokens)
}
if (-not [string]::IsNullOrWhiteSpace($RequiredReleaseGateTokens)) {
    $arguments += @("--required-release-gate-tokens", $RequiredReleaseGateTokens)
}

& $PythonExe @arguments
if ($LASTEXITCODE -ne 0) {
    throw "P0 runbook contract check failed with exit code $LASTEXITCODE."
}

Write-Host "P0 runbook contract check passed." -ForegroundColor Green
