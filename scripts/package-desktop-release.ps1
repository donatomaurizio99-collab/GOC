param(
    [string]$Name = "GoalOpsConsole",
    [string]$Version = "0.1.0-dev",
    [ValidateSet("snapshot", "stable", "beta")]
    [string]$Channel = "snapshot",
    [ValidateSet("onedir", "onefile", "both")]
    [string]$Mode = "both",
    [string]$OutputDir = "artifacts",
    [string]$BaseDownloadUrl = "",
    [switch]$Sign,
    [string]$SignToolPath = "signtool.exe",
    [string]$CertThumbprint = "",
    [string]$PfxPath = "",
    [string]$PfxPassword = "",
    [string]$TimeStampUrl = "https://timestamp.digicert.com"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$safeVersion = ($Version -replace "[^0-9A-Za-z\.\-_]", "-")
$resolvedOutputDir = Join-Path $ProjectRoot $OutputDir
$oneDirRoot = Join-Path $ProjectRoot "dist/$Name"
$oneDirExe = Join-Path $oneDirRoot "$Name.exe"
$oneFileExe = Join-Path $ProjectRoot "dist/$Name.exe"
$includeOneDir = $Mode -in @("onedir", "both")
$includeOneFile = $Mode -in @("onefile", "both")
$resolvedPfxPath = ""
if (-not [string]::IsNullOrWhiteSpace($PfxPath)) {
    $resolvedPfxPath = (Resolve-Path -LiteralPath $PfxPath).Path
}

function Invoke-SignBinary {
    param(
        [string]$PathToSign
    )

    if (-not $Sign) {
        return
    }

    $arguments = @(
        "sign",
        "/fd", "SHA256",
        "/tr", $TimeStampUrl,
        "/td", "SHA256",
        "/d", $Name,
        "/v"
    )

    if (-not [string]::IsNullOrWhiteSpace($resolvedPfxPath)) {
        $arguments += @("/f", $resolvedPfxPath)
        if (-not [string]::IsNullOrWhiteSpace($PfxPassword)) {
            $arguments += @("/p", $PfxPassword)
        }
    } elseif (-not [string]::IsNullOrWhiteSpace($CertThumbprint)) {
        $arguments += @("/sha1", $CertThumbprint)
    } else {
        throw "Signing requested but no certificate source provided. Use -CertThumbprint or -PfxPath."
    }

    $arguments += $PathToSign

    Write-Host "Signing $PathToSign" -ForegroundColor Cyan
    & $SignToolPath @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Signing failed for $PathToSign (exit code $LASTEXITCODE)."
    }

    $signature = Get-AuthenticodeSignature -LiteralPath $PathToSign
    if ($signature.Status -ne "Valid") {
        throw "Signature verification failed for $PathToSign. Status=$($signature.Status). $($signature.StatusMessage)"
    }
    Write-Host "Signature valid for $PathToSign" -ForegroundColor Green
}

if ($includeOneDir -and -not (Test-Path -LiteralPath $oneDirExe)) {
    throw "Onedir desktop executable not found at $oneDirExe"
}
if ($includeOneFile -and -not (Test-Path -LiteralPath $oneFileExe)) {
    throw "Onefile desktop executable not found at $oneFileExe"
}

New-Item -ItemType Directory -Force -Path $resolvedOutputDir | Out-Null

if ($includeOneDir) {
    Invoke-SignBinary -PathToSign $oneDirExe
}
if ($includeOneFile) {
    Invoke-SignBinary -PathToSign $oneFileExe
}

$artifacts = @()

function Add-ArtifactMetadata {
    param(
        [string]$Kind,
        [string]$ArtifactPath
    )

    $item = Get-Item -LiteralPath $ArtifactPath
    $hash = (Get-FileHash -LiteralPath $item.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    $url = if ([string]::IsNullOrWhiteSpace($BaseDownloadUrl)) {
        $item.Name
    } else {
        "$($BaseDownloadUrl.TrimEnd('/'))/$($item.Name)"
    }
    $script:artifacts += [pscustomobject]@{
        kind = $Kind
        file = $item.Name
        sha256 = $hash
        size_bytes = [int64]$item.Length
        url = $url
    }
}

if ($includeOneDir) {
    $oneDirArchiveName = "$Name-onedir-$safeVersion.zip"
    $oneDirArchivePath = Join-Path $resolvedOutputDir $oneDirArchiveName
    if (Test-Path -LiteralPath $oneDirArchivePath) {
        Remove-Item -LiteralPath $oneDirArchivePath -Force
    }
    Compress-Archive -Path (Join-Path $oneDirRoot "*") -DestinationPath $oneDirArchivePath -Force
    Add-ArtifactMetadata -Kind "onedir" -ArtifactPath $oneDirArchivePath
}

$oneFileArtifactName = ""
if ($includeOneFile) {
    $oneFileArtifactName = "$Name-onefile-$safeVersion.exe"
    $oneFileArtifactPath = Join-Path $resolvedOutputDir $oneFileArtifactName
    Copy-Item -LiteralPath $oneFileExe -Destination $oneFileArtifactPath -Force
    Add-ArtifactMetadata -Kind "onefile" -ArtifactPath $oneFileArtifactPath
}

if ($includeOneFile) {
    $installerScriptName = "$Name-install-$safeVersion.ps1"
    $installerScriptPath = Join-Path $resolvedOutputDir $installerScriptName
    $installerContent = @"
param(
    [string]`$InstallDir = "`$env:LOCALAPPDATA\$Name",
    [switch]`$DesktopShortcut
)

`$ErrorActionPreference = "Stop"

`$SourceExe = Join-Path `$PSScriptRoot "$oneFileArtifactName"
if (-not (Test-Path -LiteralPath `$SourceExe)) {
    throw "Desktop executable not found next to installer script: `$SourceExe"
}

New-Item -ItemType Directory -Force -Path `$InstallDir | Out-Null
`$TargetExe = Join-Path `$InstallDir "$Name.exe"
Copy-Item -LiteralPath `$SourceExe -Destination `$TargetExe -Force

`$ProgramsPath = [Environment]::GetFolderPath("Programs")
`$ShortcutDir = Join-Path `$ProgramsPath "Goal Ops Console"
New-Item -ItemType Directory -Force -Path `$ShortcutDir | Out-Null
`$ShortcutPath = Join-Path `$ShortcutDir "Goal Ops Console.lnk"

`$shell = New-Object -ComObject WScript.Shell
`$shortcut = `$shell.CreateShortcut(`$ShortcutPath)
`$shortcut.TargetPath = `$TargetExe
`$shortcut.WorkingDirectory = `$InstallDir
`$shortcut.IconLocation = "`$TargetExe,0"
`$shortcut.Save()

if (`$DesktopShortcut) {
    `$desktopPath = [Environment]::GetFolderPath("Desktop")
    `$desktopLinkPath = Join-Path `$desktopPath "Goal Ops Console.lnk"
    `$desktopLink = `$shell.CreateShortcut(`$desktopLinkPath)
    `$desktopLink.TargetPath = `$TargetExe
    `$desktopLink.WorkingDirectory = `$InstallDir
    `$desktopLink.IconLocation = "`$TargetExe,0"
    `$desktopLink.Save()
}

Write-Host "Installed Goal Ops Console to: `$TargetExe" -ForegroundColor Green
Write-Host "Start Menu shortcut: `$ShortcutPath" -ForegroundColor Green
"@
    Set-Content -LiteralPath $installerScriptPath -Value $installerContent -Encoding UTF8
    Add-ArtifactMetadata -Kind "installer_script" -ArtifactPath $installerScriptPath
}

if (-not $artifacts.Count) {
    throw "No release artifacts were created."
}

$checksumsPath = Join-Path $resolvedOutputDir "SHA256SUMS.txt"
$checksumLines = @()
foreach ($artifact in $artifacts) {
    $checksumLines += "$($artifact.sha256)  $($artifact.file)"
}
$checksumLines | Set-Content -LiteralPath $checksumsPath -Encoding UTF8

$manifestPath = Join-Path $resolvedOutputDir "desktop-update-manifest.json"
$manifest = [pscustomobject]@{
    app_id = "goal-ops-console"
    name = $Name
    version = $Version
    channel = $Channel
    published_at_utc = (Get-Date).ToUniversalTime().ToString("o")
    artifacts = $artifacts
}
$manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $manifestPath -Encoding UTF8

Write-Host "Desktop release package ready." -ForegroundColor Green
Write-Host "Output directory: $resolvedOutputDir" -ForegroundColor Green
Write-Host "Manifest: $manifestPath" -ForegroundColor Green
Write-Host "Checksums: $checksumsPath" -ForegroundColor Green
