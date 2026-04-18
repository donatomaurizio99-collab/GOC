param(
    [string]$PythonExe = "python",
    [string]$ArtifactsDir = "artifacts",
    [string]$IncludeGlob = "*-release-gate.json",
    [string]$RegistryFile = "docs\\release-gate-registry.json",
    [string]$RequiredFiles = "",
    [string]$RequiredLabel = "",
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
    "--registry-file", $RegistryFile,
    "--output-file", $OutputFile,
    "--bundle-dir", $BundleDir
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
    throw "P0 release evidence bundle check failed with exit code $LASTEXITCODE."
}

Write-Host "P0 release evidence bundle check passed." -ForegroundColor Green
