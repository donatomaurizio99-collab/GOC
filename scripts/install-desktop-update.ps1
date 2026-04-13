param(
    [Parameter(Mandatory = $true)]
    [string]$SourceExePath,
    [string]$InstallDir = "$env:LOCALAPPDATA\GoalOpsConsole",
    [string]$AppName = "GoalOpsConsole",
    [Parameter(Mandatory = $true)]
    [string]$ExpectedSha256,
    [switch]$RequireValidSignature,
    [switch]$DesktopShortcut,
    [string]$StartMenuFolderName = "Goal Ops Console",
    [string]$ShortcutName = "Goal Ops Console",
    [string]$StableBackupName = "",
    [switch]$SkipShortcuts,
    [switch]$FailAfterCopy,
    [string]$ReportPath = ""
)

$ErrorActionPreference = "Stop"

function Normalize-Sha256 {
    param(
        [string]$Value
    )

    $normalized = ([string]$Value).Trim().ToLowerInvariant()
    if ($normalized -notmatch "^[a-f0-9]{64}$") {
        throw "ExpectedSha256 must be a 64-character hexadecimal SHA256 hash."
    }
    return $normalized
}

function Get-Sha256 {
    param(
        [string]$FilePath
    )

    return (Get-FileHash -LiteralPath $FilePath -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Assert-ValidSignature {
    param(
        [string]$FilePath
    )

    $signature = Get-AuthenticodeSignature -LiteralPath $FilePath
    if ($signature.Status -ne "Valid") {
        throw "Signature verification failed for '$FilePath'. Status=$($signature.Status). $($signature.StatusMessage)"
    }
    return [string]$signature.Status
}

function Write-InstallReport {
    param(
        [string]$PathValue,
        [hashtable]$Report
    )

    if ([string]::IsNullOrWhiteSpace($PathValue)) {
        return
    }
    $parent = Split-Path -Parent $PathValue
    if (-not [string]::IsNullOrWhiteSpace($parent)) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    $Report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $PathValue -Encoding UTF8
}

function New-AppShortcut {
    param(
        [object]$Shell,
        [string]$PathValue,
        [string]$TargetPath,
        [string]$WorkingDirectory
    )

    $shortcut = $Shell.CreateShortcut($PathValue)
    $shortcut.TargetPath = $TargetPath
    $shortcut.WorkingDirectory = $WorkingDirectory
    $shortcut.IconLocation = "$TargetPath,0"
    $shortcut.Save()
}

$fallbackPrepared = $false
$resolvedSourcePath = ""
$stableBackupPath = ""
$targetExe = ""

$report = [ordered]@{
    success = $false
    source_exe = $SourceExePath
    install_dir = $InstallDir
    app_name = $AppName
    expected_sha256 = $null
    source_sha256 = $null
    target_sha256 = $null
    signature_required = [bool]$RequireValidSignature
    source_signature_status = $null
    target_signature_status = $null
    previous_target_found = $false
    stable_backup_path = $null
    fallback = [ordered]@{
        attempted = $false
        restored = $false
        error = $null
    }
    fail_after_copy = [bool]$FailAfterCopy
    skip_shortcuts = [bool]$SkipShortcuts
    target_exe = $null
    shortcut_startmenu = $null
    shortcut_desktop = $null
    decision = "pending"
    error = $null
}

try {
    $resolvedSourcePath = (Resolve-Path -LiteralPath $SourceExePath).Path
    $report.source_exe = $resolvedSourcePath

    $normalizedExpectedSha = Normalize-Sha256 -Value $ExpectedSha256
    $report.expected_sha256 = $normalizedExpectedSha

    $sourceSha = Get-Sha256 -FilePath $resolvedSourcePath
    $report.source_sha256 = $sourceSha
    if ($sourceSha -ne $normalizedExpectedSha) {
        throw "Source executable hash mismatch. expected=$normalizedExpectedSha actual=$sourceSha"
    }

    if ($RequireValidSignature) {
        $report.source_signature_status = Assert-ValidSignature -FilePath $resolvedSourcePath
    }

    if ([string]::IsNullOrWhiteSpace($StableBackupName)) {
        $StableBackupName = "$AppName-stable.exe"
    }

    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    $targetExe = Join-Path $InstallDir "$AppName.exe"
    $stableBackupPath = Join-Path $InstallDir $StableBackupName
    $report.target_exe = $targetExe
    $report.stable_backup_path = $stableBackupPath

    if (Test-Path -LiteralPath $targetExe) {
        $report.previous_target_found = $true
        Copy-Item -LiteralPath $targetExe -Destination $stableBackupPath -Force
        $fallbackPrepared = Test-Path -LiteralPath $stableBackupPath
    }

    Copy-Item -LiteralPath $resolvedSourcePath -Destination $targetExe -Force

    if ($FailAfterCopy) {
        throw "Injected failure after copy (FailAfterCopy)."
    }

    $targetSha = Get-Sha256 -FilePath $targetExe
    $report.target_sha256 = $targetSha
    if ($targetSha -ne $normalizedExpectedSha) {
        throw "Installed executable hash mismatch. expected=$normalizedExpectedSha actual=$targetSha"
    }

    if ($RequireValidSignature) {
        $report.target_signature_status = Assert-ValidSignature -FilePath $targetExe
    }

    if (-not $SkipShortcuts) {
        $programsPath = [Environment]::GetFolderPath("Programs")
        $shortcutDir = Join-Path $programsPath $StartMenuFolderName
        New-Item -ItemType Directory -Force -Path $shortcutDir | Out-Null
        $startMenuShortcutPath = Join-Path $shortcutDir "$ShortcutName.lnk"

        $shell = New-Object -ComObject WScript.Shell
        New-AppShortcut -Shell $shell -PathValue $startMenuShortcutPath -TargetPath $targetExe -WorkingDirectory $InstallDir
        $report.shortcut_startmenu = $startMenuShortcutPath

        if ($DesktopShortcut) {
            $desktopPath = [Environment]::GetFolderPath("Desktop")
            $desktopShortcutPath = Join-Path $desktopPath "$ShortcutName.lnk"
            New-AppShortcut -Shell $shell -PathValue $desktopShortcutPath -TargetPath $targetExe -WorkingDirectory $InstallDir
            $report.shortcut_desktop = $desktopShortcutPath
        }
    }

    $report.success = $true
    $report.decision = "update_installed"
    Write-InstallReport -PathValue $ReportPath -Report $report

    Write-Host "Installed $AppName to: $targetExe" -ForegroundColor Green
    if ($report.previous_target_found) {
        Write-Host "Stable fallback backup: $stableBackupPath" -ForegroundColor Yellow
    }
    if (-not $SkipShortcuts) {
        Write-Host "Start Menu shortcut: $startMenuShortcutPath" -ForegroundColor Green
    } else {
        Write-Host "Shortcut creation skipped (-SkipShortcuts)." -ForegroundColor Yellow
    }
}
catch {
    $report.error = $_.Exception.Message
    $report.decision = "update_failed"

    if ($fallbackPrepared -and (Test-Path -LiteralPath $stableBackupPath)) {
        $report.fallback.attempted = $true
        try {
            Copy-Item -LiteralPath $stableBackupPath -Destination $targetExe -Force
            $report.target_sha256 = Get-Sha256 -FilePath $targetExe
            $report.fallback.restored = $true
            $report.decision = "rollback_to_stable_restored"
        }
        catch {
            $report.fallback.error = $_.Exception.Message
            $report.fallback.restored = $false
        }
    }

    Write-InstallReport -PathValue $ReportPath -Report $report

    if ($report.fallback.restored) {
        throw "Desktop update failed, previous stable version restored. Root cause: $($report.error)"
    }
    throw
}
