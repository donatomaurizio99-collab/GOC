param(
    [string]$PythonExe = "python",
    [string]$WorkspaceDir = ".tmp\\fsync-io-stall-drills",
    [int]$FaultInjections = 2,
    [double]$StallSeconds = 0.35,
    [double]$MaxStallRequestSeconds = 3.0,
    [switch]$KeepArtifacts
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$args = @(
    ".\\scripts\\fsync-io-stall-drill.py",
    "--workspace", $WorkspaceDir,
    "--label", "manual",
    "--fault-injections", [string]$FaultInjections,
    "--stall-seconds", [string]$StallSeconds,
    "--max-stall-request-seconds", [string]$MaxStallRequestSeconds
)
if ($KeepArtifacts) {
    $args += "--keep-artifacts"
}

& $PythonExe @args
if ($LASTEXITCODE -ne 0) {
    throw "fsync/io stall drill failed with exit code $LASTEXITCODE."
}

Write-Host "fsync/io stall drill passed." -ForegroundColor Green
