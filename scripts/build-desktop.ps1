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

if ($InstallDependencies) {
    Write-Host "Installing desktop build dependencies..." -ForegroundColor Cyan
    python -m pip install -e ".[desktop,desktop-build]"
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

python @pyInstallerArgs

$outputPath = if ($Mode -eq "onefile") {
    Join-Path $ProjectRoot "dist/$Name.exe"
} else {
    Join-Path $ProjectRoot "dist/$Name/$Name.exe"
}

Write-Host "Build complete." -ForegroundColor Green
Write-Host "Desktop executable: $outputPath" -ForegroundColor Green
