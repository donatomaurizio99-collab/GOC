param(
    [ValidateSet("onedir", "onefile")]
    [string]$Mode = "onedir",
    [string]$Name = "GoalOpsConsole",
    [switch]$InstallDependencies,
    [switch]$Clean = $true,
    [switch]$DryRun,
    [string]$IconPath = ""
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

function Invoke-PythonCommand {
    param(
        [string[]]$PythonArgs,
        [string]$Description
    )

    Write-Host "Running: python $($PythonArgs -join ' ')" -ForegroundColor DarkGray
    & python @PythonArgs
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        throw "$Description failed (exit code $exitCode)."
    }
}

$pyInstallerArgs = @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--name", $Name,
    "--collect-data", "goal_ops_console",
    "--collect-submodules", "webview",
    "--hidden-import", "uvicorn.logging",
    "--hidden-import", "uvicorn.loops.auto",
    "--hidden-import", "uvicorn.protocols.http.auto",
    "--hidden-import", "uvicorn.protocols.websockets.auto",
    "--hidden-import", "uvicorn.lifespan.on",
    "--windowed",
    "--distpath", "dist",
    "--workpath", "build/pyinstaller/work",
    "--specpath", "build/pyinstaller/spec",
    "goal_ops_console/desktop.py"
)

if ($Clean) {
    $pyInstallerArgs += "--clean"
}

if ($Mode -eq "onefile") {
    $pyInstallerArgs += "--onefile"
} else {
    $pyInstallerArgs += "--onedir"
}

if ($IconPath) {
    $resolvedIconPath = Resolve-Path -LiteralPath $IconPath
    $pyInstallerArgs += @("--icon", $resolvedIconPath.Path)
}

Write-Host "Building desktop app ($Mode)..." -ForegroundColor Cyan
Write-Host "Project root: $ProjectRoot" -ForegroundColor Cyan
Write-Host "Command: python $($pyInstallerArgs -join ' ')" -ForegroundColor DarkGray

if ($DryRun) {
    Write-Host "Dry run only. No build executed." -ForegroundColor Yellow
    exit 0
}

if ($InstallDependencies) {
    $LocalPipTemp = Join-Path $ProjectRoot ".tmp/pip"
    New-Item -ItemType Directory -Force -Path $LocalPipTemp | Out-Null
    $env:TMP = $LocalPipTemp
    $env:TEMP = $LocalPipTemp

    Write-Host "Installing desktop build dependencies..." -ForegroundColor Cyan
    Write-Host "Using temp dir: $LocalPipTemp" -ForegroundColor DarkGray
    Invoke-PythonCommand -PythonArgs @("-m", "pip", "install", "-e", ".[desktop,desktop-build]") -Description "Dependency installation"
}

Invoke-PythonCommand -PythonArgs @("-m", "PyInstaller", "--version") -Description "PyInstaller availability check"
Invoke-PythonCommand -PythonArgs $pyInstallerArgs -Description "Desktop packaging"

$outputPath = if ($Mode -eq "onefile") {
    Join-Path $ProjectRoot "dist/$Name.exe"
} else {
    Join-Path $ProjectRoot "dist/$Name/$Name.exe"
}

if (-not (Test-Path -LiteralPath $outputPath)) {
    throw "Desktop packaging completed but executable was not found at: $outputPath"
}

Write-Host "Build complete." -ForegroundColor Green
Write-Host "Desktop executable: $outputPath" -ForegroundColor Green
