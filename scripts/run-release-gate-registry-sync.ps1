param(
    [string]$PythonExe = "python",
    [string]$RegistryFile = "docs\\release-gate-registry.json",
    [string]$LockFile = "docs\\release-gate-registry.lock.json",
    [string]$CiWorkflowFile = ".github\\workflows\\ci.yml",
    [string]$OutputFile = "",
    [switch]$Write
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$arguments = @(
    ".\\scripts\\release-gate-registry-sync.py",
    "--registry-file", $RegistryFile,
    "--lock-file", $LockFile,
    "--ci-workflow-file", $CiWorkflowFile
)
if ($OutputFile) {
    $arguments += @("--output-file", $OutputFile)
}
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
