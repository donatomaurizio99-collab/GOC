param(
    [string]$PythonExe = "python",
    [string]$RegistrySyncReportFile = "artifacts\\release-gate-registry-sync-ci.json",
    [string]$RegistryFile = "docs\\release-gate-registry.json",
    [string]$LockFile = "docs\\release-gate-registry.lock.json",
    [string]$CiWorkflowFile = ".github\\workflows\\ci.yml",
    [string]$RequiredMode = "check",
    [string]$ExpectedRegistrySyncReportPath = "artifacts/release-gate-registry-sync-ci.json",
    [string]$OutputFile = ""
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$arguments = @(
    ".\\scripts\\release-gate-registry-attestation-gate.py",
    "--registry-sync-report-file", $RegistrySyncReportFile,
    "--registry-file", $RegistryFile,
    "--lock-file", $LockFile,
    "--ci-workflow-file", $CiWorkflowFile,
    "--required-mode", $RequiredMode,
    "--expected-registry-sync-report-path", $ExpectedRegistrySyncReportPath
)
if ($OutputFile) {
    $arguments += @("--output-file", $OutputFile)
}

& $PythonExe @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Release-gate registry attestation gate failed with exit code $LASTEXITCODE."
}

Write-Host "Release-gate registry attestation gate passed." -ForegroundColor Green
