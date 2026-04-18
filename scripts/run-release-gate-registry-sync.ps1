param(
    [string]$PythonExe = "python",
    [string]$RegistryFile = "docs\\release-gate-registry.json",
    [string]$CiWorkflowFile = ".github\\workflows\\ci.yml",
    [switch]$Write
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$arguments = @(
    ".\\scripts\\release-gate-registry-sync.py",
    "--registry-file", $RegistryFile,
    "--ci-workflow-file", $CiWorkflowFile
)
if ($Write) {
    $arguments += "--write"
}

& $PythonExe @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Release-gate registry sync failed with exit code $LASTEXITCODE."
}

if ($Write) {
    Write-Host "Release-gate registry sync write completed." -ForegroundColor Green
} else {
    Write-Host "Release-gate registry sync check passed." -ForegroundColor Green
}
