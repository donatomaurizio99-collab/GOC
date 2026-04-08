param(
    [string]$DatabaseFile = "goal_ops.db"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$databasePath = Join-Path $ProjectRoot $DatabaseFile
$journalPath = "$databasePath-journal"

$removed = @()

foreach ($path in @($databasePath, $journalPath)) {
    if (Test-Path $path) {
        Remove-Item $path -Force
        $removed += $path
    }
}

if ($removed.Count -eq 0) {
    Write-Host "No database files found to remove." -ForegroundColor Yellow
} else {
    Write-Host "Removed database files:" -ForegroundColor Green
    $removed | ForEach-Object { Write-Host $_ }
}
