param(
    [string]$Name = "GoalOpsConsole",
    [string]$Version = "0.1.0-dev",
    [ValidateSet("snapshot", "stable", "beta")]
    [string]$Channel = "snapshot",
    [ValidateSet("auto", "stable", "canary")]
    [string]$RolloutRing = "auto",
    [ValidateSet("onedir", "onefile", "both")]
    [string]$Mode = "both",
    [string]$OutputDir = "artifacts",
    [string]$RingsManifestPath = "",
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
        [string]$ArtifactPath,
        [bool]$SignatureRequired = $false
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
        signature_required = [bool]$SignatureRequired
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
$updateHelperArtifactName = ""
if ($includeOneFile) {
    $oneFileArtifactName = "$Name-onefile-$safeVersion.exe"
    $oneFileArtifactPath = Join-Path $resolvedOutputDir $oneFileArtifactName
    Copy-Item -LiteralPath $oneFileExe -Destination $oneFileArtifactPath -Force
    Add-ArtifactMetadata -Kind "onefile" -ArtifactPath $oneFileArtifactPath -SignatureRequired:$Sign

    $updateHelperSource = Join-Path $ProjectRoot "scripts/install-desktop-update.ps1"
    if (-not (Test-Path -LiteralPath $updateHelperSource)) {
        throw "Update helper script not found at $updateHelperSource"
    }
    $updateHelperArtifactName = "$Name-update-helper-$safeVersion.ps1"
    $updateHelperArtifactPath = Join-Path $resolvedOutputDir $updateHelperArtifactName
    Copy-Item -LiteralPath $updateHelperSource -Destination $updateHelperArtifactPath -Force
    Add-ArtifactMetadata -Kind "update_helper_script" -ArtifactPath $updateHelperArtifactPath
}

if ($includeOneFile) {
    $oneFileArtifact = $artifacts | Where-Object { $_.kind -eq "onefile" } | Select-Object -First 1
    if ($null -eq $oneFileArtifact) {
        throw "Missing onefile artifact metadata for installer generation."
    }
    $oneFileExpectedSha = [string]$oneFileArtifact.sha256
    $requireValidSignatureLiteral = if ($Sign) { "`$true" } else { "`$false" }

    $installerScriptName = "$Name-install-$safeVersion.ps1"
    $installerScriptPath = Join-Path $resolvedOutputDir $installerScriptName
    $installerContent = @"
param(
    [string]`$InstallDir = "`$env:LOCALAPPDATA\$Name",
    [switch]`$DesktopShortcut,
    [bool]`$RequireValidSignature = $requireValidSignatureLiteral,
    [string]`$ExpectedSha256 = "$oneFileExpectedSha",
    [switch]`$FailAfterCopy
)

`$ErrorActionPreference = "Stop"

`$SourceExe = Join-Path `$PSScriptRoot "$oneFileArtifactName"
`$HelperScript = Join-Path `$PSScriptRoot "$updateHelperArtifactName"
if (-not (Test-Path -LiteralPath `$SourceExe)) {
    throw "Desktop executable not found next to installer script: `$SourceExe"
}
if (-not (Test-Path -LiteralPath `$HelperScript)) {
    throw "Update helper script not found next to installer script: `$HelperScript"
}

`$invokeArgs = @(
    "-SourceExePath", `$SourceExe,
    "-InstallDir", `$InstallDir,
    "-AppName", "$Name",
    "-ExpectedSha256", `$ExpectedSha256
)
if (`$DesktopShortcut) {
    `$invokeArgs += "-DesktopShortcut"
}
if (`$RequireValidSignature) {
    `$invokeArgs += "-RequireValidSignature"
}
if (`$FailAfterCopy) {
    `$invokeArgs += "-FailAfterCopy"
}

& `$HelperScript @invokeArgs
if (`$LASTEXITCODE -ne 0) {
    throw "Desktop update helper failed with exit code `$LASTEXITCODE."
}

Write-Host "Installer completed with hash/signature validation and fallback protection." -ForegroundColor Green
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
$resolvedRolloutRing = if ($RolloutRing -eq "auto") {
    if ($Channel -eq "stable") { "stable" } else { "canary" }
} else {
    $RolloutRing
}

$manifest = [pscustomobject]@{
    app_id = "goal-ops-console"
    name = $Name
    version = $Version
    channel = $Channel
    rollout_ring = $resolvedRolloutRing
    published_at_utc = (Get-Date).ToUniversalTime().ToString("o")
    artifacts = $artifacts
}
$manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $manifestPath -Encoding UTF8

$resolvedRingsManifestPath = if ([string]::IsNullOrWhiteSpace($RingsManifestPath)) {
    Join-Path $resolvedOutputDir "desktop-rings.json"
} elseif ([System.IO.Path]::IsPathRooted($RingsManifestPath)) {
    $RingsManifestPath
} else {
    Join-Path $ProjectRoot $RingsManifestPath
}
$ringsScriptPath = Join-Path $ProjectRoot "scripts/manage-desktop-rings.ps1"
if (Test-Path -LiteralPath $ringsScriptPath) {
    & $ringsScriptPath `
        -ManifestPath $resolvedRingsManifestPath `
        -Action promote `
        -Ring $resolvedRolloutRing `
        -Version $Version `
        -ReleaseManifestPath $manifestPath | Out-Null
}

Write-Host "Desktop release package ready." -ForegroundColor Green
Write-Host "Output directory: $resolvedOutputDir" -ForegroundColor Green
Write-Host "Manifest: $manifestPath" -ForegroundColor Green
Write-Host "Rings: $resolvedRingsManifestPath" -ForegroundColor Green
Write-Host "Checksums: $checksumsPath" -ForegroundColor Green
