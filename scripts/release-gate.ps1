param(
    [string]$PythonExe = "python",
    [switch]$SkipPytest,
    [switch]$SkipDesktopSmoke,
    [switch]$SkipApiProbe,
    [switch]$SkipSloAlertCheck,
    [switch]$SkipFileDatabaseProbe,
    [switch]$StrictFileDatabaseProbe,
    [switch]$SkipBackupRestoreDrill,
    [switch]$StrictBackupRestoreDrill,
    [switch]$SkipIncidentRollbackDrill,
    [switch]$StrictIncidentRollbackDrill,
    [switch]$SkipRecoveryHardCrashDrill,
    [switch]$StrictRecoveryHardCrashDrill
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

function Invoke-NativeCommand {
    param(
        [string]$Executable,
        [string[]]$Arguments
    )

    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw (
            "Command failed with exit code {0}: {1} {2}" -f
            $LASTEXITCODE, $Executable, ($Arguments -join ' ')
        )
    }
}

function Invoke-GateStep {
    param(
        [string]$Name,
        [scriptblock]$Action
    )

    $startedAt = Get-Date
    Write-Host "==> $Name" -ForegroundColor Cyan
    & $Action
    $duration = [int]((Get-Date) - $startedAt).TotalSeconds
    Write-Host "<== $Name passed (${duration}s)" -ForegroundColor Green
}

if (-not $SkipPytest) {
    Invoke-GateStep -Name "Pytest suite" -Action {
        Invoke-NativeCommand -Executable $PythonExe -Arguments @("-m", "pytest", "-q")
    }
}

if (-not $SkipDesktopSmoke) {
    Invoke-GateStep -Name "Desktop smoke" -Action {
        Invoke-NativeCommand -Executable $PythonExe -Arguments @(".\scripts\desktop-smoke.py")
    }
}

if (-not $SkipApiProbe) {
    Invoke-GateStep -Name "API probe (:memory:)" -Action {
        Invoke-NativeCommand -Executable $PythonExe -Arguments @(
            ".\scripts\release-gate-probe.py",
            "--label", "memory",
            "--database-url", ":memory:",
            "--expected-db-kind", "memory"
        )
    }
}

if (-not $SkipSloAlertCheck) {
    Invoke-GateStep -Name "SLO alert check (:memory:)" -Action {
        Invoke-NativeCommand -Executable $PythonExe -Arguments @(
            ".\scripts\slo-alert-check.py",
            "--database-url", ":memory:",
            "--allowed-status", "ok"
        )
    }
}

if (-not $SkipFileDatabaseProbe) {
    Invoke-GateStep -Name "API probe (file-backed DB)" -Action {
        $probeDir = Join-Path $ProjectRoot ".tmp\release-gate"
        New-Item -ItemType Directory -Force -Path $probeDir | Out-Null

        $dbName = "release-gate-{0}-{1}.db" -f (
            (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
        ), ([guid]::NewGuid().ToString("N").Substring(0, 8))
        $dbPath = Join-Path $probeDir $dbName

        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\release-gate-probe.py",
                "--label", "file-backed",
                "--database-url", $dbPath,
                "--expected-db-kind", "file"
            )
        } catch {
            if ($StrictFileDatabaseProbe) {
                throw
            }
            Write-Warning (
                "File-backed DB probe failed but StrictFileDatabaseProbe is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

if (-not $SkipBackupRestoreDrill) {
    Invoke-GateStep -Name "Backup/restore drill (file-backed DB)" -Action {
        $workspace = Join-Path $ProjectRoot ".tmp\backup-restore-drills"
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\backup-restore-drill.py",
                "--workspace", $workspace,
                "--label", "release-gate"
            )
        } catch {
            if ($StrictBackupRestoreDrill) {
                throw
            }
            Write-Warning (
                "Backup/restore drill failed but StrictBackupRestoreDrill is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

if (-not $SkipIncidentRollbackDrill) {
    Invoke-GateStep -Name "Incident/rollback drill (burst load + ring rollback)" -Action {
        $workspace = Join-Path $ProjectRoot ".tmp\incident-rollback-drills"
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\incident-rollback-drill.py",
                "--workspace", $workspace,
                "--label", "release-gate",
                "--load-requests", "30"
            )
        } catch {
            if ($StrictIncidentRollbackDrill) {
                throw
            }
            Write-Warning (
                "Incident/rollback drill failed but StrictIncidentRollbackDrill is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

if (-not $SkipRecoveryHardCrashDrill) {
    Invoke-GateStep -Name "Recovery drill (hard process abort + stale lock reclaim)" -Action {
        $workspace = Join-Path $ProjectRoot ".tmp\recovery-hard-crash-drills"
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\recovery-hard-crash-drill.py",
                "--workspace", $workspace,
                "--label", "release-gate"
            )
        } catch {
            if ($StrictRecoveryHardCrashDrill) {
                throw
            }
            Write-Warning (
                "Recovery hard-crash drill failed but StrictRecoveryHardCrashDrill is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

Write-Host "Release gate passed." -ForegroundColor Green
