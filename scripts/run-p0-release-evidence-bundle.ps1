param(
    [string]$PythonExe = "python",
    [string]$ArtifactsDir = "artifacts",
    [string]$IncludeGlob = "p0-*-release-gate.json",
    [string]$RequiredFiles = "",
    [string]$OutputFile = "artifacts\\p0-release-evidence-bundle.json",
    [string]$BundleDir = "artifacts\\p0-release-evidence-files",
    [switch]$AllowEmpty
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$arguments = @(
    ".\\scripts\\p0-release-evidence-bundle.py",
    "--label", "manual",
    "--artifacts-dir", $ArtifactsDir,
    "--include-glob", $IncludeGlob,
    "--output-file", $OutputFile,
    "--bundle-dir", $BundleDir
)
if (-not [string]::IsNullOrWhiteSpace($RequiredFiles)) {
    $arguments += @("--required-files", $RequiredFiles)
}
if ($AllowEmpty) {
    $arguments += "--allow-empty"
}

& $PythonExe @arguments
if ($LASTEXITCODE -ne 0) {
    throw "P0 release evidence bundle check failed with exit code $LASTEXITCODE."
}

Write-Host "P0 release evidence bundle check passed." -ForegroundColor Green
