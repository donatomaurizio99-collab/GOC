param(
    [string]$PythonExe = "python",
    [string]$Repo = "donatomaurizio99-collab/GOC",
    [string]$Branch = "master",
    [string]$RequiredChecks = "Release Gate (Windows),Security CI Lane,Pytest (Python 3.11),Pytest (Python 3.12),Desktop Smoke (Windows)",
    [string]$OutputFile = "artifacts\\master-branch-protection-drift-guard.json",
    [switch]$AllowDrift
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$args = @(
    ".\\scripts\\master-branch-protection-drift-guard.py",
    "--label", "manual",
    "--repo", $Repo,
    "--branch", $Branch,
    "--required-checks", $RequiredChecks,
    "--output-file", $OutputFile
)
if ($AllowDrift) {
    $args += "--allow-drift"
}

& $PythonExe @args
if ($LASTEXITCODE -ne 0) {
    throw "Master branch-protection drift guard failed with exit code $LASTEXITCODE."
}

Write-Host "Master branch-protection drift guard passed." -ForegroundColor Green
