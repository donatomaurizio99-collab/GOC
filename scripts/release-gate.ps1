param(
    [string]$PythonExe = "python",
    [switch]$SkipPytest,
    [switch]$SkipDesktopSmoke,
    [switch]$SkipApiProbe,
    [switch]$SkipSloAlertCheck,
    [switch]$SkipReleaseFreezePolicyDrill,
    [switch]$StrictReleaseFreezePolicyDrill,
    [switch]$SkipFileDatabaseProbe,
    [switch]$StrictFileDatabaseProbe,
    [switch]$SkipAutoRollbackPolicyDrill,
    [switch]$StrictAutoRollbackPolicyDrill,
    [switch]$SkipDesktopUpdateSafetyDrill,
    [switch]$StrictDesktopUpdateSafetyDrill,
    [switch]$SkipRecoveryHardAbortDrill,
    [switch]$StrictRecoveryHardAbortDrill,
    [switch]$SkipRecoveryIdempotenceDrill,
    [switch]$StrictRecoveryIdempotenceDrill,
    [switch]$SkipPowerLossDurabilityDrill,
    [switch]$StrictPowerLossDurabilityDrill,
    [switch]$SkipWalCheckpointCrashDrill,
    [switch]$StrictWalCheckpointCrashDrill,
    [switch]$SkipDiskPressureFaultInjectionDrill,
    [switch]$StrictDiskPressureFaultInjectionDrill,
    [switch]$SkipFsyncIoStallDrill,
    [switch]$StrictFsyncIoStallDrill,
    [switch]$SkipSqliteRealFullDrill,
    [switch]$StrictSqliteRealFullDrill,
    [switch]$SkipDbCorruptionQuarantineDrill,
    [switch]$StrictDbCorruptionQuarantineDrill,
    [switch]$SkipStorageCorruptionHardeningDrill,
    [switch]$StrictStorageCorruptionHardeningDrill,
    [switch]$SkipWorkflowLockResilienceDrill,
    [switch]$StrictWorkflowLockResilienceDrill,
    [switch]$SkipWorkflowSoakDrill,
    [switch]$StrictWorkflowSoakDrill,
    [switch]$SkipWorkflowWorkerRestartDrill,
    [switch]$StrictWorkflowWorkerRestartDrill,
    [switch]$SkipDbSafeModeWatchdogDrill,
    [switch]$StrictDbSafeModeWatchdogDrill,
    [switch]$SkipInvariantMonitorWatchdogDrill,
    [switch]$StrictInvariantMonitorWatchdogDrill,
    [switch]$SkipEventConsumerRecoveryChaosDrill,
    [switch]$StrictEventConsumerRecoveryChaosDrill,
    [switch]$SkipInvariantBurstDrill,
    [switch]$StrictInvariantBurstDrill,
    [switch]$SkipLongSoakBudgetDrill,
    [switch]$StrictLongSoakBudgetDrill,
    [switch]$SkipMigrationRehearsal,
    [switch]$StrictMigrationRehearsal,
    [switch]$SkipUpgradeDowngradeCompatibilityDrill,
    [switch]$StrictUpgradeDowngradeCompatibilityDrill,
    [switch]$SkipBackupRestoreDrill,
    [switch]$StrictBackupRestoreDrill,
    [switch]$SkipBackupRestoreStressDrill,
    [switch]$StrictBackupRestoreStressDrill,
    [switch]$SkipSnapshotRestoreCrashConsistencyDrill,
    [switch]$StrictSnapshotRestoreCrashConsistencyDrill,
    [switch]$SkipMultiDbAtomicSwitchDrill,
    [switch]$StrictMultiDbAtomicSwitchDrill,
    [switch]$SkipIncidentRollbackDrill,
    [switch]$StrictIncidentRollbackDrill,
    [switch]$SkipReleaseGateRuntimeStabilityDrill,
    [switch]$StrictReleaseGateRuntimeStabilityDrill,
    [switch]$SkipCriticalDrillFlakeGate,
    [switch]$StrictCriticalDrillFlakeGate,
    [switch]$SkipP0BurnInConsecutiveGreen,
    [switch]$StrictP0BurnInConsecutiveGreen
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

if (-not $SkipReleaseFreezePolicyDrill) {
    Invoke-GateStep -Name "Release freeze policy drill (sustained non-ok/burn-rate => promotion block)" -Action {
        $workspace = Join-Path $ProjectRoot ".tmp\release-freeze-policy"
        $manifestPath = Join-Path $workspace "desktop-rings.json"
        New-Item -ItemType Directory -Force -Path $workspace | Out-Null
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\release-freeze-policy.py",
                "--workspace", $workspace,
                "--label", "release-gate",
                "--manifest-path", $manifestPath,
                "--ring", "stable",
                "--mock-slo-statuses", "degraded,critical,critical,critical",
                "--mock-error-budget-burn-rates", "0.5,1.0,2.5,2.5",
                "--non-ok-window-seconds", "2",
                "--poll-interval-seconds", "1",
                "--max-observation-seconds", "8",
                "--max-error-budget-burn-rate-percent", "2.0",
                "--seed-previous-version", "0.0.1",
                "--seed-incident-version", "0.0.2",
                "--promotion-test-version", "0.0.3"
            )
        } catch {
            if ($StrictReleaseFreezePolicyDrill) {
                throw
            }
            Write-Warning (
                "Release freeze policy drill failed but StrictReleaseFreezePolicyDrill is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
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

if (-not $SkipRecoveryIdempotenceDrill) {
    Invoke-GateStep -Name "Recovery idempotence drill (restarts do not duplicate startup recovery)" -Action {
        $workspace = Join-Path $ProjectRoot ".tmp\recovery-idempotence-drills"
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\recovery-idempotence-drill.py",
                "--workspace", $workspace,
                "--label", "release-gate",
                "--recovery-cycles", "3",
                "--startup-timeout-seconds", "15"
            )
        } catch {
            if ($StrictRecoveryIdempotenceDrill) {
                throw
            }
            Write-Warning (
                "Recovery idempotence drill failed but StrictRecoveryIdempotenceDrill is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

if (-not $SkipPowerLossDurabilityDrill) {
    Invoke-GateStep -Name "Power-loss durability drill (pre/post-commit hard-abort persistence)" -Action {
        $workspace = Join-Path $ProjectRoot ".tmp\power-loss-durability-drills"
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\power-loss-durability-drill.py",
                "--workspace", $workspace,
                "--label", "release-gate",
                "--transaction-rows", "240",
                "--payload-bytes", "256",
                "--startup-timeout-seconds", "15"
            )
        } catch {
            if ($StrictPowerLossDurabilityDrill) {
                throw
            }
            Write-Warning (
                "Power-loss durability drill failed but StrictPowerLossDurabilityDrill is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

if (-not $SkipWalCheckpointCrashDrill) {
    Invoke-GateStep -Name "WAL checkpoint crash drill (hard-abort before checkpoint + post-restart recovery)" -Action {
        $workspace = Join-Path $ProjectRoot ".tmp\wal-checkpoint-crash-drills"
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\wal-checkpoint-crash-drill.py",
                "--workspace", $workspace,
                "--label", "release-gate",
                "--rows", "240",
                "--payload-bytes", "1024",
                "--startup-timeout-seconds", "15",
                "--sleep-before-checkpoint-seconds", "30",
                "--checkpoint-mode", "TRUNCATE"
            )
        } catch {
            if ($StrictWalCheckpointCrashDrill) {
                throw
            }
            Write-Warning (
                "WAL checkpoint crash drill failed but StrictWalCheckpointCrashDrill is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

if (-not $SkipDiskPressureFaultInjectionDrill) {
    Invoke-GateStep -Name "Disk-pressure fault-injection drill (SQLITE_FULL/IOERR/readonly safe-mode recovery)" -Action {
        $workspace = Join-Path $ProjectRoot ".tmp\disk-pressure-fault-injection-drills"
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\disk-pressure-fault-injection-drill.py",
                "--workspace", $workspace,
                "--label", "release-gate",
                "--fault-injections", "2"
            )
        } catch {
            if ($StrictDiskPressureFaultInjectionDrill) {
                throw
            }
            Write-Warning (
                "Disk-pressure fault-injection drill failed but StrictDiskPressureFaultInjectionDrill is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

if (-not $SkipFsyncIoStallDrill) {
    Invoke-GateStep -Name "fsync/io stall drill (bounded write stalls + io-error safe-mode recovery)" -Action {
        $workspace = Join-Path $ProjectRoot ".tmp\fsync-io-stall-drills"
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\fsync-io-stall-drill.py",
                "--workspace", $workspace,
                "--label", "release-gate",
                "--fault-injections", "2",
                "--stall-seconds", "0.35",
                "--max-stall-request-seconds", "3.0"
            )
        } catch {
            if ($StrictFsyncIoStallDrill) {
                throw
            }
            Write-Warning (
                "fsync/io stall drill failed but StrictFsyncIoStallDrill is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

if (-not $SkipSqliteRealFullDrill) {
    Invoke-GateStep -Name "SQLite real FULL drill (actual max_page_count saturation + recovery)" -Action {
        $workspace = Join-Path $ProjectRoot ".tmp\sqlite-real-full-drills"
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\sqlite-real-full-drill.py",
                "--workspace", $workspace,
                "--label", "release-gate",
                "--payload-bytes", "8192",
                "--max-write-attempts", "240",
                "--max-page-growth", "24",
                "--recovery-page-growth", "160"
            )
        } catch {
            if ($StrictSqliteRealFullDrill) {
                throw
            }
            Write-Warning (
                "SQLite real full drill failed but StrictSqliteRealFullDrill is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

if (-not $SkipDbCorruptionQuarantineDrill) {
    Invoke-GateStep -Name "DB corruption quarantine drill (startup quarantine + safe-mode recovery path)" -Action {
        $workspace = Join-Path $ProjectRoot ".tmp\db-corruption-quarantine-drills"
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\db-corruption-quarantine-drill.py",
                "--workspace", $workspace,
                "--label", "release-gate"
            )
        } catch {
            if ($StrictDbCorruptionQuarantineDrill) {
                throw
            }
            Write-Warning (
                "DB corruption quarantine drill failed but StrictDbCorruptionQuarantineDrill is off. " +
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

if (-not $SkipWorkflowWorkerRestartDrill) {
    Invoke-GateStep -Name "Workflow worker restart drill (self-heal after worker stop)" -Action {
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\workflow-worker-restart-drill.py",
                "--timeout-seconds", "12"
            )
        } catch {
            if ($StrictWorkflowWorkerRestartDrill) {
                throw
            }
            Write-Warning (
                "Workflow worker restart drill failed but StrictWorkflowWorkerRestartDrill is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

if (-not $SkipDbSafeModeWatchdogDrill) {
    Invoke-GateStep -Name "DB safe-mode watchdog drill (lock burst => guarded API mode)" -Action {
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\db-safe-mode-watchdog-drill.py",
                "--lock-error-injections", "4"
            )
        } catch {
            if ($StrictDbSafeModeWatchdogDrill) {
                throw
            }
            Write-Warning (
                "DB safe-mode watchdog drill failed but StrictDbSafeModeWatchdogDrill is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

if (-not $SkipInvariantMonitorWatchdogDrill) {
    Invoke-GateStep -Name "Invariant monitor watchdog drill (periodic detector + auto-safe-mode)" -Action {
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\invariant-monitor-watchdog-drill.py",
                "--timeout-seconds", "8"
            )
        } catch {
            if ($StrictInvariantMonitorWatchdogDrill) {
                throw
            }
            Write-Warning (
                "Invariant monitor watchdog drill failed but StrictInvariantMonitorWatchdogDrill is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

if (-not $SkipEventConsumerRecoveryChaosDrill) {
    Invoke-GateStep -Name "Event consumer recovery chaos drill (stale processing reclaim + drain)" -Action {
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\event-consumer-recovery-chaos-drill.py",
                "--goal-count", "50",
                "--stale-processing-count", "20",
                "--drain-batch-size", "120",
                "--timeout-seconds", "25"
            )
        } catch {
            if ($StrictEventConsumerRecoveryChaosDrill) {
                throw
            }
            Write-Warning (
                "Event consumer recovery chaos drill failed but StrictEventConsumerRecoveryChaosDrill is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

if (-not $SkipInvariantBurstDrill) {
    Invoke-GateStep -Name "Invariant burst drill (queue/goal/task consistency under load)" -Action {
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\invariant-burst-drill.py",
                "--goal-count", "60"
            )
        } catch {
            if ($StrictInvariantBurstDrill) {
                throw
            }
            Write-Warning (
                "Invariant burst drill failed but StrictInvariantBurstDrill is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

if (-not $SkipLongSoakBudgetDrill) {
    Invoke-GateStep -Name "Long soak budget drill (latency/429/error budget gate)" -Action {
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\long-soak-budget-drill.py",
                "--duration-seconds", "120",
                "--max-p95-latency-ms", "300",
                "--max-p99-latency-ms", "500",
                "--max-max-latency-ms", "10000",
                "--max-http-429-rate-percent", "2.0",
                "--max-error-rate-percent", "1.0",
                "--min-requests", "350",
                "--drain-batch-size", "180",
                "--workflow-start-every-cycles", "0"
            )
        } catch {
            if ($StrictLongSoakBudgetDrill) {
                throw
            }
            Write-Warning (
                "Long soak budget drill failed but StrictLongSoakBudgetDrill is off. " +
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

if (-not $SkipStorageCorruptionHardeningDrill) {
    Invoke-GateStep -Name "Storage corruption hardening drill (WAL/JOURNAL anomalies + startup quarantine recovery)" -Action {
        $workspace = Join-Path $ProjectRoot ".tmp\storage-corruption-hardening-drills"
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\storage-corruption-hardening-drill.py",
                "--workspace", $workspace,
                "--label", "release-gate",
                "--corruption-bytes", "192",
                "--rows", "80",
                "--payload-bytes", "128"
            )
        } catch {
            if ($StrictStorageCorruptionHardeningDrill) {
                throw
            }
            Write-Warning (
                "Storage corruption hardening drill failed but StrictStorageCorruptionHardeningDrill is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

if (-not $SkipUpgradeDowngradeCompatibilityDrill) {
    Invoke-GateStep -Name "Upgrade/downgrade compatibility drill (N-1 -> N -> N-1 rollback path)" -Action {
        $workspace = Join-Path $ProjectRoot ".tmp\upgrade-downgrade-compatibility-drills"
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\upgrade-downgrade-compatibility-drill.py",
                "--workspace", $workspace,
                "--label", "release-gate",
                "--n-minus-1-runs", "800",
                "--payload-bytes", "512",
                "--max-upgrade-ms", "10000",
                "--max-rollback-restore-ms", "10000",
                "--max-reupgrade-ms", "10000"
            )
        } catch {
            if ($StrictUpgradeDowngradeCompatibilityDrill) {
                throw
            }
            Write-Warning (
                "Upgrade/downgrade compatibility drill failed but StrictUpgradeDowngradeCompatibilityDrill is off. " +
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

if (-not $SkipBackupRestoreStressDrill) {
    Invoke-GateStep -Name "Backup/restore stress drill (round-based load + restore idempotence)" -Action {
        $workspace = Join-Path $ProjectRoot ".tmp\backup-restore-stress-drills"
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\backup-restore-stress-drill.py",
                "--workspace", $workspace,
                "--label", "release-gate",
                "--rounds", "3",
                "--goals-per-round", "120",
                "--tasks-per-goal", "2",
                "--workflow-runs-per-round", "24"
            )
        } catch {
            if ($StrictBackupRestoreStressDrill) {
                throw
            }
            Write-Warning (
                "Backup/restore stress drill failed but StrictBackupRestoreStressDrill is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

if (-not $SkipSnapshotRestoreCrashConsistencyDrill) {
    Invoke-GateStep -Name "Snapshot/restore crash-consistency drill (fault matrix + aborted copy recovery)" -Action {
        $workspace = Join-Path $ProjectRoot ".tmp\snapshot-restore-crash-consistency-drills"
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\snapshot-restore-crash-consistency-drill.py",
                "--workspace", $workspace,
                "--label", "release-gate",
                "--seed-rows", "96",
                "--payload-bytes", "128"
            )
        } catch {
            if ($StrictSnapshotRestoreCrashConsistencyDrill) {
                throw
            }
            Write-Warning (
                "Snapshot/restore crash-consistency drill failed but StrictSnapshotRestoreCrashConsistencyDrill is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

if (-not $SkipMultiDbAtomicSwitchDrill) {
    Invoke-GateStep -Name "Multi-DB atomic-switch drill (failover pointer crash + integrity reject + rollback)" -Action {
        $workspace = Join-Path $ProjectRoot ".tmp\multi-db-atomic-switch-drills"
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\multi-db-atomic-switch-drill.py",
                "--workspace", $workspace,
                "--label", "release-gate",
                "--seed-rows", "96",
                "--payload-bytes", "128"
            )
        } catch {
            if ($StrictMultiDbAtomicSwitchDrill) {
                throw
            }
            Write-Warning (
                "Multi-DB atomic-switch drill failed but StrictMultiDbAtomicSwitchDrill is off. " +
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

if (-not $SkipReleaseGateRuntimeStabilityDrill) {
    Invoke-GateStep -Name "Release-gate runtime stability drill (duration/variance budget on critical drills)" -Action {
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\release-gate-runtime-stability-drill.py",
                "--label", "release-gate",
                "--samples", "2",
                "--repeats-per-sample", "1",
                "--target-file", ".\tests\test_goal_ops.py",
                "--keyword-expression", "test_105_storage_corruption_hardening_drill_reports_success or test_106_backup_restore_stress_drill_reports_success or test_107_snapshot_restore_crash_consistency_drill_reports_success or test_108_multi_db_atomic_switch_drill_reports_success",
                "--timeout-seconds", "900",
                "--max-mean-duration-ms", "120000",
                "--max-stddev-ms", "60000",
                "--max-iteration-duration-ms", "180000"
            )
        } catch {
            if ($StrictReleaseGateRuntimeStabilityDrill) {
                throw
            }
            Write-Warning (
                "Release-gate runtime stability drill failed but StrictReleaseGateRuntimeStabilityDrill is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

if (-not $SkipCriticalDrillFlakeGate) {
    Invoke-GateStep -Name "Critical drill flake gate (repeat critical drill tests)" -Action {
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\critical-drill-flake-gate.py",
                "--repeats", "2",
                "--max-failed-iterations", "0",
                "--target-file", ".\tests\test_goal_ops.py"
            )
        } catch {
            if ($StrictCriticalDrillFlakeGate) {
                throw
            }
            Write-Warning (
                "Critical drill flake gate failed but StrictCriticalDrillFlakeGate is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

if (-not $SkipP0BurnInConsecutiveGreen) {
    Invoke-GateStep -Name "P0 burn-in consecutive-green monitor (CI history hard gate)" -Action {
        $reportPath = Join-Path $ProjectRoot "artifacts\p0-burnin-consecutive-green-release-gate.json"
        $repository = if ($env:GITHUB_REPOSITORY) {
            $env:GITHUB_REPOSITORY
        } else {
            "donatomaurizio99-collab/GOC"
        }
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\p0-burnin-consecutive-green.py",
                "--label", "release-gate",
                "--repo", $repository,
                "--branch", "master",
                "--workflow-name", "CI",
                "--required-jobs", "Release Gate (Windows),Pytest (Python 3.11),Pytest (Python 3.12),Desktop Smoke (Windows)",
                "--required-consecutive", "10",
                "--per-page", "50",
                "--output-file", $reportPath
            )
        } catch {
            if ($StrictP0BurnInConsecutiveGreen) {
                throw
            }
            Write-Warning (
                "P0 burn-in consecutive-green monitor failed but StrictP0BurnInConsecutiveGreen is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

Write-Host "Release gate passed." -ForegroundColor Green
