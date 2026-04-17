param(
    [string]$PythonExe = "python",
    [string]$ArtifactsDir = "artifacts",
    [string]$IncludeGlob = "*-release-gate.json",
    [string]$RequiredTopLevelKeys = "label,success,generated_at_utc,duration_ms,paths,metrics,decision",
    [string]$RequiredDecisionKeys = "release_blocked",
    [string]$RequiredFiles = "",
    [string]$RequiredLabel = "",
    [string]$OutputFile = "artifacts\p0-report-schema-contract-check-report.json",
    [switch]$AllowEmpty
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$arguments = @(
    ".\scripts\p0-report-schema-contract-check.py",
    "--label", "manual",
    "--artifacts-dir", $ArtifactsDir,
    "--include-glob", $IncludeGlob,
    "--required-top-level-keys", $RequiredTopLevelKeys,
    "--required-decision-keys", $RequiredDecisionKeys,
    "--output-file", $OutputFile
)
if (-not [string]::IsNullOrWhiteSpace($RequiredFiles)) {
    $arguments += @("--required-files", $RequiredFiles)
}
if (-not [string]::IsNullOrWhiteSpace($RequiredLabel)) {
    $arguments += @("--required-label", $RequiredLabel)
}
if ($AllowEmpty) {
    $arguments += "--allow-empty"
}

& $PythonExe @arguments
if ($LASTEXITCODE -ne 0) {
    throw "P0 report schema contract check failed with exit code $LASTEXITCODE."
}

Write-Host "P0 report schema contract check passed." -ForegroundColor Green
