param(
    [string]$PythonExe = "python",
    [switch]$SkipPytest,
    [switch]$SkipDesktopSmoke,
    [switch]$SkipApiProbe,
    [switch]$SkipSloAlertCheck,
    [switch]$SkipFileDatabaseProbe,
    [switch]$StrictFileDatabaseProbe,
    [switch]$SkipAutoRollbackPolicyDrill,
    [switch]$StrictAutoRollbackPolicyDrill,
    [switch]$SkipDesktopUpdateSafetyDrill,
    [switch]$StrictDesktopUpdateSafetyDrill,
    [switch]$SkipRecoveryHardAbortDrill,
    [switch]$StrictRecoveryHardAbortDrill,
    [switch]$SkipWorkflowLockResilienceDrill,
    [switch]$StrictWorkflowLockResilienceDrill,
    [switch]$SkipWorkflowSoakDrill,
    [switch]$StrictWorkflowSoakDrill,
    [switch]$SkipMigrationRehearsal,
    [switch]$StrictMigrationRehearsal,
    [switch]$SkipBackupRestoreDrill,
    [switch]$StrictBackupRestoreDrill,
    [switch]$SkipIncidentRollbackDrill,
    [switch]$StrictIncidentRollbackDrill
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

if (-not $SkipAutoRollbackPolicyDrill) {
    Invoke-GateStep -Name "Auto rollback policy drill (sustained critical => ring rollback)" -Action {
        $workspace = Join-Path $ProjectRoot ".tmp\auto-rollback-policy-drills"
        $manifestPath = Join-Path $workspace "desktop-rings.json"
        New-Item -ItemType Directory -Force -Path $workspace | Out-Null
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\auto-rollback-policy.py",
                "--workspace", $workspace,
                "--label", "release-gate",
                "--manifest-path", $manifestPath,
                "--ring", "stable",
                "--mock-slo-statuses", "critical,critical,critical,critical",
                "--critical-window-seconds", "2",
                "--poll-interval-seconds", "1",
                "--max-observation-seconds", "8",
                "--seed-previous-version", "0.0.1",
                "--seed-incident-version", "0.0.2"
            )
        } catch {
            if ($StrictAutoRollbackPolicyDrill) {
                throw
            }
            Write-Warning (
                "Auto rollback policy drill failed but StrictAutoRollbackPolicyDrill is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

if (-not $SkipDesktopUpdateSafetyDrill) {
    Invoke-GateStep -Name "Desktop update safety drill (hash/signature validation + fallback)" -Action {
        $workspace = Join-Path $ProjectRoot ".tmp\desktop-update-safety-drills"
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\desktop-update-safety-drill.py",
                "--workspace", $workspace,
                "--label", "release-gate"
            )
        } catch {
            if ($StrictDesktopUpdateSafetyDrill) {
                throw
            }
            Write-Warning (
                "Desktop update safety drill failed but StrictDesktopUpdateSafetyDrill is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

if (-not $SkipRecoveryHardAbortDrill) {
    Invoke-GateStep -Name "Recovery hard-abort drill (kill process + startup recovery)" -Action {
        $workspace = Join-Path $ProjectRoot ".tmp\recovery-hard-abort-drills"
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\recovery-hard-abort-drill.py",
                "--workspace", $workspace,
                "--label", "release-gate"
            )
        } catch {
            if ($StrictRecoveryHardAbortDrill) {
                throw
            }
            Write-Warning (
                "Recovery hard-abort drill failed but StrictRecoveryHardAbortDrill is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

if (-not $SkipWorkflowLockResilienceDrill) {
    Invoke-GateStep -Name "Workflow lock resilience drill (transient SQLite lock conflicts)" -Action {
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\workflow-lock-resilience-drill.py",
                "--lock-failures", "8",
                "--timeout-seconds", "12"
            )
        } catch {
            if ($StrictWorkflowLockResilienceDrill) {
                throw
            }
            Write-Warning (
                "Workflow lock resilience drill failed but StrictWorkflowLockResilienceDrill is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

if (-not $SkipWorkflowSoakDrill) {
    Invoke-GateStep -Name "Workflow soak drill (no hanging runs after burst enqueue)" -Action {
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\workflow-soak-drill.py",
                "--run-count", "40",
                "--timeout-seconds", "25"
            )
        } catch {
            if ($StrictWorkflowSoakDrill) {
                throw
            }
            Write-Warning (
                "Workflow soak drill failed but StrictWorkflowSoakDrill is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
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

if (-not $SkipMigrationRehearsal) {
    Invoke-GateStep -Name "Migration rehearsal (S/M/L/XL DB copies)" -Action {
        $workspace = Join-Path $ProjectRoot ".tmp\migration-rehearsals"
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\migration-rehearsal.py",
                "--workspace", $workspace,
                "--label", "release-gate",
                "--xlarge-runs", "9000"
            )
        } catch {
            if ($StrictMigrationRehearsal) {
                throw
            }
            Write-Warning (
                "Migration rehearsal failed but StrictMigrationRehearsal is off. " +
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

Write-Host "Release gate passed." -ForegroundColor Green
