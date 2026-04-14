param(
    [string]$ManifestPath = "artifacts/desktop-rings.json",
    [ValidateSet("show", "promote", "rollback", "freeze", "unfreeze")]
    [string]$Action = "show",
    [ValidateSet("stable", "canary")]
    [string]$Ring = "stable",
    [string]$Version = "",
    [string]$ReleaseManifestPath = "",
    [string]$Reason = "",
    [switch]$IgnoreReleaseFreeze
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

function Resolve-TargetPath {
    param(
        [string]$PathValue
    )

    if ([string]::IsNullOrWhiteSpace($PathValue)) {
        return Join-Path $ProjectRoot "artifacts/desktop-rings.json"
    }
    if ([System.IO.Path]::IsPathRooted($PathValue)) {
        return $PathValue
    }
    return Join-Path $ProjectRoot $PathValue
}

function New-RingsState {
    return [ordered]@{
        schema_version = 1
        app_id = "goal-ops-console"
        updated_at_utc = (Get-Date).ToUniversalTime().ToString("o")
        rings = [ordered]@{
            stable = [ordered]@{
                version = $null
                rollback_version = $null
                updated_at_utc = $null
            }
            canary = [ordered]@{
                version = $null
                rollback_version = $null
                updated_at_utc = $null
            }
        }
        releases = [ordered]@{}
        release_freeze = [ordered]@{
            active = $false
            reason = $null
            source = $null
            activated_at_utc = $null
            updated_at_utc = $null
        }
    }
}

function To-NullableString {
    param(
        [object]$Value
    )

    if ($null -eq $Value) {
        return $null
    }
    $text = [string]$Value
    if ([string]::IsNullOrWhiteSpace($text)) {
        return $null
    }
    return $text
}

function Read-RingsState {
    param(
        [string]$PathValue
    )

    if (-not (Test-Path -LiteralPath $PathValue)) {
        return (New-RingsState)
    }

    try {
        $raw = Get-Content -LiteralPath $PathValue -Raw
        $loaded = $raw | ConvertFrom-Json
    } catch {
        return (New-RingsState)
    }

    $state = New-RingsState
    if ($loaded.schema_version) {
        $state.schema_version = [int]$loaded.schema_version
    }
    if ($loaded.app_id) {
        $state.app_id = [string]$loaded.app_id
    }
    if ($loaded.updated_at_utc) {
        $state.updated_at_utc = [string]$loaded.updated_at_utc
    }

    foreach ($ringKey in @("stable", "canary")) {
        if ($loaded.rings -and $loaded.rings.$ringKey) {
            $state.rings[$ringKey].version = To-NullableString $loaded.rings.$ringKey.version
            $state.rings[$ringKey].rollback_version = To-NullableString $loaded.rings.$ringKey.rollback_version
            $state.rings[$ringKey].updated_at_utc = To-NullableString $loaded.rings.$ringKey.updated_at_utc
        }
    }

    if ($loaded.releases) {
        foreach ($entry in $loaded.releases.PSObject.Properties) {
            $state.releases[$entry.Name] = [ordered]@{
                version = To-NullableString $entry.Value.version
                channel = To-NullableString $entry.Value.channel
                rollout_ring = To-NullableString $entry.Value.rollout_ring
                published_at_utc = To-NullableString $entry.Value.published_at_utc
                manifest_file = To-NullableString $entry.Value.manifest_file
                artifacts = @($entry.Value.artifacts)
            }
        }
    }

    if ($loaded.release_freeze) {
        $state.release_freeze.active = [bool]$loaded.release_freeze.active
        $state.release_freeze.reason = To-NullableString $loaded.release_freeze.reason
        $state.release_freeze.source = To-NullableString $loaded.release_freeze.source
        $state.release_freeze.activated_at_utc = To-NullableString $loaded.release_freeze.activated_at_utc
        $state.release_freeze.updated_at_utc = To-NullableString $loaded.release_freeze.updated_at_utc
    }

    return $state
}

function Save-RingsState {
    param(
        [string]$PathValue,
        [hashtable]$State
    )

    $parent = Split-Path -Parent $PathValue
    if (-not [string]::IsNullOrWhiteSpace($parent)) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    $State.updated_at_utc = (Get-Date).ToUniversalTime().ToString("o")
    $State | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $PathValue -Encoding UTF8
}

function Register-ReleaseEntry {
    param(
        [hashtable]$State,
        [string]$VersionValue,
        [string]$ManifestFile,
        [string]$ChannelValue,
        [string]$RingValue,
        [string]$PublishedAtUtc,
        [object[]]$Artifacts
    )

    $State.releases[$VersionValue] = [ordered]@{
        version = $VersionValue
        channel = $ChannelValue
        rollout_ring = $RingValue
        published_at_utc = $PublishedAtUtc
        manifest_file = $ManifestFile
        artifacts = @($Artifacts)
    }
}

function Register-ReleaseFromManifest {
    param(
        [hashtable]$State,
        [string]$ManifestFilePath
    )

    if ([string]::IsNullOrWhiteSpace($ManifestFilePath)) {
        return
    }
    if (-not (Test-Path -LiteralPath $ManifestFilePath)) {
        throw "Release manifest not found: $ManifestFilePath"
    }

    $release = (Get-Content -LiteralPath $ManifestFilePath -Raw) | ConvertFrom-Json
    if (-not $release.version) {
        throw "Release manifest is missing 'version'."
    }

    $channelValue = [string]$release.channel
    $ringValue = [string]$release.rollout_ring
    if ([string]::IsNullOrWhiteSpace($ringValue)) {
        $ringValue = if ($channelValue -eq "stable") { "stable" } else { "canary" }
    }
    Register-ReleaseEntry `
        -State $State `
        -VersionValue ([string]$release.version) `
        -ManifestFile (Split-Path -Leaf $ManifestFilePath) `
        -ChannelValue $channelValue `
        -RingValue $ringValue `
        -PublishedAtUtc ([string]$release.published_at_utc) `
        -Artifacts @($release.artifacts)
}

function Ensure-ReleaseStub {
    param(
        [hashtable]$State,
        [string]$VersionValue,
        [string]$RingValue
    )

    if (-not $State.releases.Contains($VersionValue)) {
        Register-ReleaseEntry `
            -State $State `
            -VersionValue $VersionValue `
            -ManifestFile "" `
            -ChannelValue "" `
            -RingValue $RingValue `
            -PublishedAtUtc (Get-Date).ToUniversalTime().ToString("o") `
            -Artifacts @()
    }
}

$resolvedManifestPath = Resolve-TargetPath -PathValue $ManifestPath
$state = Read-RingsState -PathValue $resolvedManifestPath

if (-not [string]::IsNullOrWhiteSpace($ReleaseManifestPath)) {
    $resolvedReleaseManifestPath = Resolve-TargetPath -PathValue $ReleaseManifestPath
    Register-ReleaseFromManifest -State $state -ManifestFilePath $resolvedReleaseManifestPath
}

if ($Action -eq "show") {
    $state | ConvertTo-Json -Depth 12
    exit 0
}

if ($Action -eq "promote") {
    if ([string]::IsNullOrWhiteSpace($Version)) {
        throw "Version is required for Action=promote."
    }
    if ([bool]$state.release_freeze.active -and -not $IgnoreReleaseFreeze) {
        $freezeReason = [string]$state.release_freeze.reason
        if ([string]::IsNullOrWhiteSpace($freezeReason)) {
            $freezeReason = "unspecified"
        }
        throw (
            "Release freeze is active; promotion blocked. " +
            "Use -IgnoreReleaseFreeze only for supervised emergency overrides. " +
            "Reason: $freezeReason"
        )
    }

    $target = [string]$Version
    Ensure-ReleaseStub -State $state -VersionValue $target -RingValue $Ring

    $previous = [string]$state.rings[$Ring].version
    if (-not [string]::IsNullOrWhiteSpace($previous) -and $previous -ne $target) {
        $state.rings[$Ring].rollback_version = $previous
    }
    $state.rings[$Ring].version = $target
    $state.rings[$Ring].updated_at_utc = (Get-Date).ToUniversalTime().ToString("o")

    Save-RingsState -PathValue $resolvedManifestPath -State $state
    Write-Host "Ring '$Ring' promoted to version '$target'." -ForegroundColor Green
    if (-not [string]::IsNullOrWhiteSpace($state.rings[$Ring].rollback_version)) {
        Write-Host "Rollback target: $($state.rings[$Ring].rollback_version)" -ForegroundColor Yellow
    }
    Write-Host "Manifest: $resolvedManifestPath" -ForegroundColor Cyan
    exit 0
}

if ($Action -eq "rollback") {
    $current = [string]$state.rings[$Ring].version
    $rollbackTarget = [string]$state.rings[$Ring].rollback_version
    if ([string]::IsNullOrWhiteSpace($rollbackTarget)) {
        throw "No rollback target available for ring '$Ring'."
    }

    $state.rings[$Ring].version = $rollbackTarget
    $state.rings[$Ring].rollback_version = $current
    $state.rings[$Ring].updated_at_utc = (Get-Date).ToUniversalTime().ToString("o")

    Save-RingsState -PathValue $resolvedManifestPath -State $state
    Write-Host "Ring '$Ring' rolled back to version '$rollbackTarget'." -ForegroundColor Green
    if (-not [string]::IsNullOrWhiteSpace($current)) {
        Write-Host "Previous current version stored as rollback target: $current" -ForegroundColor Yellow
    }
    Write-Host "Manifest: $resolvedManifestPath" -ForegroundColor Cyan
    exit 0
}

if ($Action -eq "freeze") {
    $freezeReason = [string]$Reason
    if ([string]::IsNullOrWhiteSpace($freezeReason)) {
        $freezeReason = "Manual release freeze."
    }
    $state.release_freeze.active = $true
    $state.release_freeze.reason = $freezeReason
    $state.release_freeze.source = "operator"
    $state.release_freeze.activated_at_utc = (Get-Date).ToUniversalTime().ToString("o")
    $state.release_freeze.updated_at_utc = $state.release_freeze.activated_at_utc
    Save-RingsState -PathValue $resolvedManifestPath -State $state
    Write-Host "Release freeze activated." -ForegroundColor Yellow
    Write-Host "Reason: $freezeReason"
    Write-Host "Manifest: $resolvedManifestPath" -ForegroundColor Cyan
    exit 0
}

if ($Action -eq "unfreeze") {
    $state.release_freeze.active = $false
    $state.release_freeze.reason = To-NullableString $Reason
    $state.release_freeze.source = "operator"
    $state.release_freeze.activated_at_utc = $null
    $state.release_freeze.updated_at_utc = (Get-Date).ToUniversalTime().ToString("o")
    Save-RingsState -PathValue $resolvedManifestPath -State $state
    Write-Host "Release freeze cleared." -ForegroundColor Green
    if (-not [string]::IsNullOrWhiteSpace($Reason)) {
        Write-Host "Reason: $Reason"
    }
    Write-Host "Manifest: $resolvedManifestPath" -ForegroundColor Cyan
    exit 0
}
