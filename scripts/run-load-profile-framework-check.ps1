param(
    [string]$PythonExe = "python",
    [string]$DeploymentProfile = "production",
    [string]$ProfileFile = "docs\load-profile-catalog.json",
    [string]$ProfileName = "prod_like_ci_smoke",
    [string]$ProfileVersion = "1.0.0",
    [string]$OutputFile = "artifacts\load-profile-framework-check-report.json",
    [switch]$AllowFailure
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$arguments = @(
    ".\scripts\load-profile-framework-check.py",
    "--label", "manual",
    "--deployment-profile", $DeploymentProfile,
    "--profile-file", $ProfileFile,
    "--profile-name", $ProfileName,
    "--profile-version", $ProfileVersion,
    "--output-file", $OutputFile
)
if ($AllowFailure) {
    $arguments += "--allow-failure"
}

& $PythonExe @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Load profile framework check failed with exit code $LASTEXITCODE."
}

Write-Host "Load profile framework check passed." -ForegroundColor Green
