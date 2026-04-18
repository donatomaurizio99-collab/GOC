param(
    [string]$PythonExe = "python",
    [string]$WorkspaceDir = ".tmp\\incident-rollback-drills",
    [string]$OutputFile = "",
    [int]$LoadRequests = 30,
    [switch]$KeepArtifacts
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$args = @(
    ".\\scripts\\incident-rollback-drill.py",
    "--workspace", $WorkspaceDir,
    "--label", "manual",
    "--load-requests", [string]$LoadRequests
)
if (-not [string]::IsNullOrWhiteSpace($OutputFile)) {
    $args += @("--output-file", $OutputFile)
}
if ($KeepArtifacts) {
    $args += "--keep-artifacts"
}

& $PythonExe @args
if ($LASTEXITCODE -ne 0) {
    throw "Incident/rollback drill failed with exit code $LASTEXITCODE."
}

Write-Host "Incident/rollback drill passed." -ForegroundColor Green
