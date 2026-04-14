param(
    [string]$PythonExe = "python",
    [string]$WorkspaceDir = ".tmp\\disk-pressure-fault-injection-drills",
    [int]$FaultInjections = 2,
    [switch]$KeepArtifacts
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$args = @(
    ".\\scripts\\disk-pressure-fault-injection-drill.py",
    "--workspace", $WorkspaceDir,
    "--label", "manual",
    "--fault-injections", [string]$FaultInjections
)
if ($KeepArtifacts) {
    $args += "--keep-artifacts"
}

& $PythonExe @args
if ($LASTEXITCODE -ne 0) {
    throw "Disk-pressure fault injection drill failed with exit code $LASTEXITCODE."
}

Write-Host "Disk-pressure fault injection drill passed." -ForegroundColor Green
