param(
    [string]$DatabaseUrl = "goal_ops.db",
    [int]$Port = 0,
    [int]$Width = 1440,
    [int]$Height = 900,
    [int]$MinWidth = 1024,
    [int]$MinHeight = 720,
    [switch]$Maximized,
    [switch]$NoWindowState,
    [string]$WindowStatePath = "",
    [string]$InstanceLockPath = "",
    [switch]$AllowMultipleInstances,
    [string]$DiagnosticsDir = "",
    [string]$CrashStatePath = "",
    [switch]$AllowCrashLoop,
    [int]$CrashLoopMaxCrashes = 0,
    [int]$CrashLoopWindowSeconds = 0
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$env:GOAL_OPS_DATABASE_URL = $DatabaseUrl
if (-not [string]::IsNullOrWhiteSpace($DiagnosticsDir)) {
    $env:GOAL_OPS_DIAGNOSTICS_DIR = $DiagnosticsDir
}

Write-Host "Starting Goal Ops Console desktop shell from $ProjectRoot" -ForegroundColor Cyan
Write-Host "Database: $DatabaseUrl" -ForegroundColor Cyan
if ($Port -gt 0) {
    Write-Host "Desktop server URL: http://127.0.0.1:$Port" -ForegroundColor Cyan
} else {
    Write-Host "Desktop server URL: auto-selected free local port" -ForegroundColor Cyan
}

$args = @(
    "-m", "goal_ops_console.desktop",
    "--database-url", $DatabaseUrl,
    "--width", $Width,
    "--height", $Height,
    "--min-width", $MinWidth,
    "--min-height", $MinHeight
)
if ($Port -gt 0) {
    $args += @("--port", $Port)
}
if ($Maximized) {
    $args += "--maximized"
}
if ($NoWindowState) {
    $args += "--no-window-state"
}
if (-not [string]::IsNullOrWhiteSpace($WindowStatePath)) {
    $args += @("--window-state-path", $WindowStatePath)
}
if (-not [string]::IsNullOrWhiteSpace($InstanceLockPath)) {
    $args += @("--instance-lock-path", $InstanceLockPath)
}
if ($AllowMultipleInstances) {
    $args += "--allow-multiple-instances"
}
if (-not [string]::IsNullOrWhiteSpace($CrashStatePath)) {
    $args += @("--crash-state-path", $CrashStatePath)
}
if ($AllowCrashLoop) {
    $args += "--allow-crash-loop"
}
if ($CrashLoopMaxCrashes -gt 0) {
    $args += @("--crash-loop-max-crashes", $CrashLoopMaxCrashes)
}
if ($CrashLoopWindowSeconds -gt 0) {
    $args += @("--crash-loop-window-seconds", $CrashLoopWindowSeconds)
}

python @args
