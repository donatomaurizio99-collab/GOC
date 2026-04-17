param(
    [string]$PythonExe = "python",
    [string]$WorkspaceDir = ".tmp\\backup-restore-stress-drills",
    [int]$Rounds = 3,
    [int]$GoalsPerRound = 120,
    [int]$TasksPerGoal = 2,
    [int]$WorkflowRunsPerRound = 24,
    [switch]$KeepArtifacts
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$args = @(
    ".\\scripts\\backup-restore-stress-drill.py",
    "--workspace", $WorkspaceDir,
    "--label", "manual",
    "--rounds", [string]$Rounds,
    "--goals-per-round", [string]$GoalsPerRound,
    "--tasks-per-goal", [string]$TasksPerGoal,
    "--workflow-runs-per-round", [string]$WorkflowRunsPerRound
)
if ($KeepArtifacts) {
    $args += "--keep-artifacts"
}

& $PythonExe @args
if ($LASTEXITCODE -ne 0) {
    throw "Backup/restore stress drill failed with exit code $LASTEXITCODE."
}

Write-Host "Backup/restore stress drill passed." -ForegroundColor Green
