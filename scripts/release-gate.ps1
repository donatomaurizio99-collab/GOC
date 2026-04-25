param(
    [string]$PythonExe = "python",
    [switch]$SkipPytest,
    [switch]$SkipDesktopSmoke,
    [switch]$SkipApiProbe,
    [switch]$SkipSloAlertCheck,
    [switch]$SkipSecurityConfigHardeningCheck,
    [switch]$StrictSecurityConfigHardeningCheck,
    [switch]$SkipAuditTrailHardeningCheck,
    [switch]$StrictAuditTrailHardeningCheck,
    [switch]$SkipSecurityCiLaneCheck,
    [switch]$StrictSecurityCiLaneCheck,
    [switch]$SkipAlertRoutingOnCallCheck,
    [switch]$StrictAlertRoutingOnCallCheck,
    [switch]$SkipIncidentDrillAutomationCheck,
    [switch]$StrictIncidentDrillAutomationCheck,
    [switch]$SkipLoadProfileFrameworkCheck,
    [switch]$StrictLoadProfileFrameworkCheck,
    [switch]$SkipCanaryGuardrailCheck,
    [switch]$StrictCanaryGuardrailCheck,
    [switch]$SkipRtoRpoAssertionCheck,
    [switch]$StrictRtoRpoAssertionCheck,
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
    [switch]$SkipDisasterRecoveryRehearsalPack,
    [switch]$StrictDisasterRecoveryRehearsalPack,
    [switch]$SkipFailureBudgetDashboard,
    [switch]$StrictFailureBudgetDashboard,
    [switch]$SkipSafeModeUxDegradationCheck,
    [switch]$StrictSafeModeUxDegradationCheck,
    [switch]$SkipA11yTestHarnessCheck,
    [switch]$StrictA11yTestHarnessCheck,
    [switch]$SkipReleaseGateRuntimeStabilityDrill,
    [switch]$StrictReleaseGateRuntimeStabilityDrill,
    [switch]$SkipCriticalDrillFlakeGate,
    [switch]$StrictCriticalDrillFlakeGate,
    [switch]$SkipReleaseGateEvidenceFreshnessCheck,
    [switch]$StrictReleaseGateEvidenceFreshnessCheck,
    [switch]$SkipReleaseGateEvidenceHashManifestCheck,
    [switch]$StrictReleaseGateEvidenceHashManifestCheck,
    [switch]$SkipReleaseGateStepTimingSchemaCheck,
    [switch]$StrictReleaseGateStepTimingSchemaCheck,
    [switch]$SkipReleaseGatePerformanceHistoryCheck,
    [switch]$StrictReleaseGatePerformanceHistoryCheck,
    [switch]$SkipReleaseGatePerformanceBudgetCheck,
    [switch]$StrictReleaseGatePerformanceBudgetCheck,
    [switch]$SkipReleaseGateStabilityFinalReadinessCheck,
    [switch]$StrictReleaseGateStabilityFinalReadinessCheck,
    [switch]$SkipReleaseGateStagingSoakReadinessCheck,
    [switch]$StrictReleaseGateStagingSoakReadinessCheck,
    [switch]$SkipReleaseGateRcCanaryRolloutCheck,
    [switch]$StrictReleaseGateRcCanaryRolloutCheck,
    [switch]$SkipReleaseGateEvidenceLineageCheck,
    [switch]$StrictReleaseGateEvidenceLineageCheck,
    [switch]$SkipReleaseGateProductionReadinessCertificationCheck,
    [switch]$StrictReleaseGateProductionReadinessCertificationCheck,
    [switch]$SkipReleaseGateSloBurnRateV2Check,
    [switch]$StrictReleaseGateSloBurnRateV2Check,
    [switch]$SkipReleaseGateDeployRehearsalCheck,
    [switch]$StrictReleaseGateDeployRehearsalCheck,
    [switch]$SkipReleaseGateChaosMatrixContinuousCheck,
    [switch]$StrictReleaseGateChaosMatrixContinuousCheck,
    [switch]$SkipReleaseGateSupplyChainArtifactTrustCheck,
    [switch]$StrictReleaseGateSupplyChainArtifactTrustCheck,
    [switch]$SkipReleaseGateOperationsHandoffReadinessCheck,
    [switch]$StrictReleaseGateOperationsHandoffReadinessCheck,
    [switch]$SkipReleaseGateEvidenceAttestationCheck,
    [switch]$StrictReleaseGateEvidenceAttestationCheck,
    [switch]$SkipReleaseGateReleaseTrainReadinessCheck,
    [switch]$StrictReleaseGateReleaseTrainReadinessCheck,
    [switch]$SkipReleaseGateProductionFinalAttestationCheck,
    [switch]$StrictReleaseGateProductionFinalAttestationCheck,
    [switch]$SkipReleaseGateProductionCutoverReadinessCheck,
    [switch]$StrictReleaseGateProductionCutoverReadinessCheck,
    [switch]$SkipReleaseGateHypercareActivationCheck,
    [switch]$StrictReleaseGateHypercareActivationCheck,
    [switch]$SkipReleaseGateRollbackTriggerIntegrityCheck,
    [switch]$StrictReleaseGateRollbackTriggerIntegrityCheck,
    [switch]$SkipReleaseGatePostCutoverFinalizationCheck,
    [switch]$StrictReleaseGatePostCutoverFinalizationCheck,
    [switch]$SkipReleaseGatePostReleaseWatchCheck,
    [switch]$StrictReleaseGatePostReleaseWatchCheck,
    [switch]$SkipReleaseGateSteadyStateCertificationCheck,
    [switch]$StrictReleaseGateSteadyStateCertificationCheck,
    [switch]$SkipReleaseGatePostReleaseContinuityCheck,
    [switch]$StrictReleaseGatePostReleaseContinuityCheck,
    [switch]$SkipReleaseGateProductionSustainabilityCertificationCheck,
    [switch]$StrictReleaseGateProductionSustainabilityCertificationCheck,
    [switch]$SkipP0BurnInConsecutiveGreen,
    [switch]$StrictP0BurnInConsecutiveGreen,
    [switch]$SkipP0RunbookContractCheck,
    [switch]$StrictP0RunbookContractCheck,
    [switch]$SkipP0ReportSchemaContractCheck,
    [switch]$StrictP0ReportSchemaContractCheck,
    [switch]$SkipP0ReleaseEvidenceBundle,
    [switch]$StrictP0ReleaseEvidenceBundle,
    [switch]$SkipP0ClosureReport,
    [switch]$StrictP0ClosureReport
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot
$script:P0EvidenceReportPaths = @()
$script:GateStepTimingRecords = @()

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
    $stepCompletedAtUtc = $null
    $stepSucceeded = $false
    try {
        & $Action
        $stepSucceeded = $true
    } finally {
        $stepCompletedAtUtc = (Get-Date).ToUniversalTime()
        $duration = [int][Math]::Round(($stepCompletedAtUtc - $startedAt.ToUniversalTime()).TotalSeconds, 0)
        if ($duration -lt 0) {
            $duration = 0
        }
        $script:GateStepTimingRecords += [ordered]@{
            name = $Name
            duration_seconds = $duration
            success = [bool]$stepSucceeded
            completed_at_utc = $stepCompletedAtUtc.ToString("yyyy-MM-ddTHH:mm:ssZ")
        }
        if ($stepSucceeded) {
            Write-Host "<== $Name passed (${duration}s)" -ForegroundColor Green
        }
    }
}

function Resolve-PathInsideProjectRoot {
    param(
        [string]$PathToResolve,
        [string]$ProjectRootPath
    )

    $resolvedPath = [System.IO.Path]::GetFullPath($PathToResolve)
    $resolvedProjectRoot = [System.IO.Path]::GetFullPath($ProjectRootPath)
    $rootWithSeparator = if (
        $resolvedProjectRoot.EndsWith([System.IO.Path]::DirectorySeparatorChar) -or
        $resolvedProjectRoot.EndsWith([System.IO.Path]::AltDirectorySeparatorChar)
    ) {
        $resolvedProjectRoot
    } else {
        $resolvedProjectRoot + [System.IO.Path]::DirectorySeparatorChar
    }
    if (
        ($resolvedPath -ne $resolvedProjectRoot) -and
        -not $resolvedPath.StartsWith($rootWithSeparator, [System.StringComparison]::OrdinalIgnoreCase)
    ) {
        throw "Resolved path escapes project root. Project root: $resolvedProjectRoot ; Path: $resolvedPath"
    }
    return $resolvedPath
}

function Clear-ReleaseGateArtifacts {
    param(
        [string]$ProjectRootPath
    )

    $resolvedArtifactsDir = Resolve-PathInsideProjectRoot -PathToResolve (Join-Path $ProjectRootPath "artifacts") -ProjectRootPath $ProjectRootPath
    New-Item -ItemType Directory -Force -Path $resolvedArtifactsDir | Out-Null

    $staleReportFiles = Get-ChildItem -Path $resolvedArtifactsDir -File -Filter "*-release-gate.json" -ErrorAction SilentlyContinue
    foreach ($staleReport in $staleReportFiles) {
        Remove-Item -LiteralPath $staleReport.FullName -Force
    }

    $staleEvidenceDirs = @(
        (Join-Path $resolvedArtifactsDir "p0-release-evidence-files-release-gate"),
        (Join-Path $resolvedArtifactsDir "p0-disaster-recovery-rehearsal-pack-evidence-release-gate")
    )
    foreach ($candidateDir in $staleEvidenceDirs) {
        $resolvedDir = Resolve-PathInsideProjectRoot -PathToResolve $candidateDir -ProjectRootPath $ProjectRootPath
        if (Test-Path -LiteralPath $resolvedDir) {
            Remove-Item -LiteralPath $resolvedDir -Recurse -Force
        }
    }
}

function Write-ReleaseGateStepTimingsReport {
    param(
        [string]$OutputFile,
        [string]$Label = "release-gate"
    )

    $outputPath = Resolve-PathInsideProjectRoot -PathToResolve $OutputFile -ProjectRootPath $ProjectRoot
    $outputDir = Split-Path -Parent $outputPath
    if ($outputDir) {
        New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
    }

    $totalDurationSeconds = 0
    $successfulSteps = 0
    foreach ($entry in $script:GateStepTimingRecords) {
        if ($entry -and $entry.duration_seconds -is [ValueType]) {
            $totalDurationSeconds += [int]$entry.duration_seconds
        }
        if ($entry -and $entry.success) {
            $successfulSteps += 1
        }
    }

    $payload = [ordered]@{
        label = $Label
        success = $true
        metrics = [ordered]@{
            steps_recorded = [int]$script:GateStepTimingRecords.Count
            successful_steps = [int]$successfulSteps
            total_duration_seconds = [int]$totalDurationSeconds
        }
        steps = @($script:GateStepTimingRecords)
        generated_at_utc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    }

    $json = $payload | ConvertTo-Json -Depth 8
    Set-Content -Path $outputPath -Value $json -Encoding UTF8
}

Invoke-GateStep -Name "Release-gate artifact preflight (clean stale release-gate evidence)" -Action {
    Clear-ReleaseGateArtifacts -ProjectRootPath $ProjectRoot
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

if (-not $SkipSecurityConfigHardeningCheck) {
    Invoke-GateStep -Name "Security config hardening check (production profile)" -Action {
        $reportPath = Join-Path $ProjectRoot "artifacts\security-config-hardening-release-gate.json"
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\security-config-hardening-check.py",
                "--label", "release-gate",
                "--deployment-profile", "production",
                "--operator-auth-required",
                "--operator-auth-token", "release-gate-operator-token-0001",
                "--min-operator-token-length", "16",
                "--database-url", "goal_ops.db",
                "--startup-corruption-recovery-enabled",
                "--output-file", $reportPath
            )
        } catch {
            if ($StrictSecurityConfigHardeningCheck) {
                throw
            }
            Write-Warning (
                "Security config hardening check failed but StrictSecurityConfigHardeningCheck is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

if (-not $SkipAuditTrailHardeningCheck) {
    Invoke-GateStep -Name "Audit trail hardening check (immutable hash-chain + tamper detection + retention policy)" -Action {
        $reportPath = Join-Path $ProjectRoot "artifacts\audit-trail-hardening-release-gate.json"
        $workspace = Join-Path $ProjectRoot ".tmp\audit-trail-hardening-release-gate"
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\audit-trail-hardening-check.py",
                "--label", "release-gate",
                "--deployment-profile", "production",
                "--audit-retention-days", "365",
                "--min-audit-retention-days", "90",
                "--seed-entries", "8",
                "--workspace", $workspace,
                "--output-file", $reportPath
            )
        } catch {
            if ($StrictAuditTrailHardeningCheck) {
                throw
            }
            Write-Warning (
                "Audit trail hardening check failed but StrictAuditTrailHardeningCheck is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

if (-not $SkipSecurityCiLaneCheck) {
    Invoke-GateStep -Name "Security CI lane check (dependency audit + SAST + SBOM + fail policy)" -Action {
        $reportPath = Join-Path $ProjectRoot "artifacts\security-ci-lane-release-gate.json"
        $sbomPath = Join-Path $ProjectRoot "artifacts\security-sbom-release-gate.json"
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\security-ci-lane-check.py",
                "--label", "release-gate",
                "--python-exe", $PythonExe,
                "--deployment-profile", "production",
                "--scan-path", "goal_ops_console",
                "--max-dependency-vulnerabilities", "0",
                "--ignore-dependency-vulnerability", "CVE-2026-3219",
                "--max-sast-high", "0",
                "--max-sast-medium", "200",
                "--timeout-seconds", "300",
                "--sbom-output-file", $sbomPath,
                "--output-file", $reportPath
            )
        } catch {
            if ($StrictSecurityCiLaneCheck) {
                throw
            }
            Write-Warning (
                "Security CI lane check failed but StrictSecurityCiLaneCheck is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

if (-not $SkipAlertRoutingOnCallCheck) {
    Invoke-GateStep -Name "Alert routing + on-call runbook automation check (severity routing + escalation plan)" -Action {
        $reportPath = Join-Path $ProjectRoot "artifacts\alert-routing-oncall-release-gate.json"
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\alert-routing-oncall-check.py",
                "--label", "release-gate",
                "--deployment-profile", "production",
                "--mock-slo-status", "critical",
                "--mock-alert-count", "2",
                "--routing-policy-file", "docs/oncall-alert-routing-policy.json",
                "--runbook-file", "docs/production-runbook.md",
                "--max-critical-ack-minutes", "15",
                "--max-warning-ack-minutes", "120",
                "--output-file", $reportPath
            )
        } catch {
            if ($StrictAlertRoutingOnCallCheck) {
                throw
            }
            Write-Warning (
                "Alert routing on-call check failed but StrictAlertRoutingOnCallCheck is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

if (-not $SkipIncidentDrillAutomationCheck) {
    Invoke-GateStep -Name "Incident drill automation check (tabletop cadence + technical rollback evidence)" -Action {
        $reportPath = Join-Path $ProjectRoot "artifacts\incident-drill-automation-release-gate.json"
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\incident-drill-automation-check.py",
                "--label", "release-gate",
                "--deployment-profile", "production",
                "--mock-report",
                "--mock-days-since-tabletop", "7",
                "--mock-days-since-technical", "3",
                "--mock-tabletop-status", "completed",
                "--mock-technical-status", "completed",
                "--mock-open-followups", "0",
                "--policy-file", "docs/incident-drill-automation-policy.json",
                "--runbook-file", "docs/production-runbook.md",
                "--max-tabletop-age-days", "30",
                "--max-technical-age-days", "14",
                "--min-technical-load-requests", "20",
                "--max-open-followups", "3",
                "--output-file", $reportPath
            )
        } catch {
            if ($StrictIncidentDrillAutomationCheck) {
                throw
            }
            Write-Warning (
                "Incident drill automation check failed but StrictIncidentDrillAutomationCheck is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

if (-not $SkipLoadProfileFrameworkCheck) {
    Invoke-GateStep -Name "Load profile framework check (versioned prod-like load profile budgets)" -Action {
        $reportPath = Join-Path $ProjectRoot "artifacts\load-profile-framework-release-gate.json"
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\load-profile-framework-check.py",
                "--label", "release-gate",
                "--deployment-profile", "production",
                "--profile-file", "docs/load-profile-catalog.json",
                "--profile-name", "prod_like_ci_smoke",
                "--profile-version", "1.0.0",
                "--output-file", $reportPath
            )
        } catch {
            if ($StrictLoadProfileFrameworkCheck) {
                throw
            }
            Write-Warning (
                "Load profile framework check failed but StrictLoadProfileFrameworkCheck is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

if (-not $SkipCanaryGuardrailCheck) {
    Invoke-GateStep -Name "Canary guardrails check (staged promotion with automatic halt/freeze)" -Action {
        $workspace = Join-Path $ProjectRoot ".tmp\canary-guardrails"
        $manifestPath = Join-Path $workspace "desktop-rings.json"
        $reportPath = Join-Path $ProjectRoot "artifacts\canary-guardrails-release-gate.json"
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\canary-guardrails-check.py",
                "--label", "release-gate",
                "--deployment-profile", "production",
                "--workspace", $workspace,
                "--manifest-path", $manifestPath,
                "--policy-file", "docs/canary-guardrails-policy.json",
                "--runbook-file", "docs/production-runbook.md",
                "--stable-baseline-version", "0.0.1",
                "--canary-candidate-version", "0.0.2",
                "--expected-decision", "halt",
                "--mock-slo-statuses", "ok,ok,critical,critical",
                "--mock-error-budget-burn-rates", "0.5,0.8,2.5,2.5",
                "--output-file", $reportPath
            )
        } catch {
            if ($StrictCanaryGuardrailCheck) {
                throw
            }
            Write-Warning (
                "Canary guardrails check failed but StrictCanaryGuardrailCheck is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
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
    Invoke-GateStep -Name "Auto rollback hard-trigger drill (readiness regression + burn-rate + sustained critical => ring rollback)" -Action {
        $workspace = Join-Path $ProjectRoot ".tmp\auto-rollback-policy-drills"
        $manifestPath = Join-Path $workspace "desktop-rings.json"
        $reportPath = Join-Path $ProjectRoot "artifacts\auto-rollback-policy-release-gate.json"
        New-Item -ItemType Directory -Force -Path $workspace | Out-Null
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\auto-rollback-policy.py",
                "--workspace", $workspace,
                "--label", "release-gate",
                "--manifest-path", $manifestPath,
                "--ring", "stable",
                "--mock-slo-statuses", "ok,degraded,degraded,degraded",
                "--mock-error-budget-burn-rates", "0.5,0.8,0.9,0.9",
                "--mock-readiness-values", "true,false,false,false",
                "--critical-window-seconds", "4",
                "--readiness-regression-window-seconds", "1",
                "--poll-interval-seconds", "1",
                "--max-observation-seconds", "8",
                "--max-error-budget-burn-rate-percent", "2.0",
                "--seed-previous-version", "0.0.1",
                "--seed-incident-version", "0.0.2",
                "--expected-trigger-reason", "readiness_regression",
                "--output-file", $reportPath
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

if (-not $SkipRtoRpoAssertionCheck) {
    Invoke-GateStep -Name "RTO/RPO assertion suite (restore-time and bounded data-loss budgets)" -Action {
        $workspace = Join-Path $ProjectRoot ".tmp\rto-rpo-assertion-suite"
        $reportPath = Join-Path $ProjectRoot "artifacts\rto-rpo-assertion-release-gate.json"
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\rto-rpo-assertion-suite.py",
                "--workspace", $workspace,
                "--label", "release-gate",
                "--deployment-profile", "production",
                "--policy-file", "docs/rto-rpo-assertion-policy.json",
                "--runbook-file", "docs/production-runbook.md",
                "--seed-rows", "48",
                "--tail-write-rows", "12",
                "--max-rto-seconds", "20",
                "--max-rpo-rows-lost", "96",
                "--output-file", $reportPath
            )
        } catch {
            if ($StrictRtoRpoAssertionCheck) {
                throw
            }
            Write-Warning (
                "RTO/RPO assertion suite failed but StrictRtoRpoAssertionCheck is off. " +
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
        $reportPath = Join-Path $ProjectRoot "artifacts\incident-rollback-release-gate.json"
        $script:P0EvidenceReportPaths += $reportPath
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\incident-rollback-drill.py",
                "--workspace", $workspace,
                "--label", "release-gate",
                "--load-requests", "30",
                "--output-file", $reportPath
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

if (-not $SkipDisasterRecoveryRehearsalPack) {
    Invoke-GateStep -Name "Disaster-recovery rehearsal pack (consolidated restore/switch/RTO-RPO evidence)" -Action {
        $workspace = Join-Path $ProjectRoot ".tmp\disaster-recovery-rehearsal-pack"
        $reportPath = Join-Path $ProjectRoot "artifacts\p0-disaster-recovery-rehearsal-pack-release-gate.json"
        $evidenceDir = Join-Path $ProjectRoot "artifacts\p0-disaster-recovery-rehearsal-pack-evidence-release-gate"
        $script:P0EvidenceReportPaths += $reportPath
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\disaster-recovery-rehearsal-pack.py",
                "--workspace", $workspace,
                "--label", "release-gate",
                "--profile", "release-gate",
                "--runbook-file", "docs/production-runbook.md",
                "--rto-rpo-policy-file", "docs/rto-rpo-assertion-policy.json",
                "--max-failed-drills", "0",
                "--max-total-duration-seconds", "2400",
                "--output-file", $reportPath,
                "--evidence-dir", $evidenceDir
            )
        } catch {
            if ($StrictDisasterRecoveryRehearsalPack) {
                throw
            }
            Write-Warning (
                "Disaster-recovery rehearsal pack failed but StrictDisasterRecoveryRehearsalPack is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

if (-not $SkipFailureBudgetDashboard) {
    Invoke-GateStep -Name "Failure budget dashboard (aggregated budget checks + release blocker hook)" -Action {
        $reportPath = Join-Path $ProjectRoot "artifacts\failure-budget-dashboard-release-gate.json"
        $budgetReportFiles = @(
            "artifacts\load-profile-framework-release-gate.json",
            "artifacts\rto-rpo-assertion-release-gate.json",
            "artifacts\canary-guardrails-release-gate.json",
            "artifacts\auto-rollback-policy-release-gate.json",
            "artifacts\p0-disaster-recovery-rehearsal-pack-release-gate.json"
        ) -join ","
        $script:P0EvidenceReportPaths += $reportPath
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\failure-budget-dashboard.py",
                "--label", "release-gate",
                "--runbook-file", "docs/production-runbook.md",
                "--budget-report-files", $budgetReportFiles,
                "--output-file", $reportPath
            )
        } catch {
            if ($StrictFailureBudgetDashboard) {
                throw
            }
            Write-Warning (
                "Failure budget dashboard failed but StrictFailureBudgetDashboard is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

if (-not $SkipSafeModeUxDegradationCheck) {
    Invoke-GateStep -Name "Safe-mode UX degradation check (runtime rail + mutation lock + release/runbook contract)" -Action {
        $reportPath = Join-Path $ProjectRoot "artifacts\safe-mode-ux-degradation-release-gate.json"
        $script:P0EvidenceReportPaths += $reportPath
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\safe-mode-ux-degradation-check.py",
                "--label", "release-gate",
                "--output-file", $reportPath
            )
        } catch {
            if ($StrictSafeModeUxDegradationCheck) {
                throw
            }
            Write-Warning (
                "Safe-mode UX degradation check failed but StrictSafeModeUxDegradationCheck is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

if (-not $SkipA11yTestHarnessCheck) {
    Invoke-GateStep -Name "A11y test harness check (keyboard + screen-reader smoke + contrast baseline)" -Action {
        $reportPath = Join-Path $ProjectRoot "artifacts\a11y-test-harness-release-gate.json"
        $script:P0EvidenceReportPaths += $reportPath
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\a11y-test-harness-check.py",
                "--label", "release-gate",
                "--output-file", $reportPath
            )
        } catch {
            if ($StrictA11yTestHarnessCheck) {
                throw
            }
            Write-Warning (
                "A11y test harness check failed but StrictA11yTestHarnessCheck is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

if (-not $SkipReleaseGateRuntimeStabilityDrill) {
    Invoke-GateStep -Name "Release-gate runtime stability drill (duration/variance budget on critical drills)" -Action {
        $reportPath = Join-Path $ProjectRoot "artifacts\release-gate-runtime-stability-release-gate.json"
        $script:P0EvidenceReportPaths += $reportPath
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\release-gate-runtime-stability-drill.py",
                "--label", "release-gate",
                "--samples", "2",
                "--repeats-per-sample", "1",
                "--target-file", ".\tests\test_goal_ops.py",
                "--keyword-expression", "test_105_storage_corruption_hardening_drill_reports_success or test_106_backup_restore_stress_drill_reports_success or test_107_snapshot_restore_crash_consistency_drill_reports_success or test_108_multi_db_atomic_switch_drill_reports_success or test_144_dashboard_template_contains_runtime_rail_contract or test_145_safe_mode_ux_degradation_check_reports_success or test_147_a11y_test_harness_check_reports_success or test_149_dashboard_template_exposes_keyboard_and_screen_reader_baseline",
                "--timeout-seconds", "900",
                "--max-mean-duration-ms", "120000",
                "--max-stddev-ms", "60000",
                "--max-iteration-duration-ms", "180000",
                "--output-file", $reportPath
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
        $reportPath = Join-Path $ProjectRoot "artifacts\critical-drill-flake-gate-release-gate.json"
        $script:P0EvidenceReportPaths += $reportPath
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\critical-drill-flake-gate.py",
                "--label", "release-gate",
                "--repeats", "2",
                "--max-failed-iterations", "0",
                "--target-file", ".\tests\test_goal_ops.py",
                "--output-file", $reportPath
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
        $script:P0EvidenceReportPaths += $reportPath
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
                "--required-jobs", "Pytest (Python 3.11),Pytest (Python 3.12),Desktop Smoke (Windows)",
                "--ignore-run-conclusion",
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

if (-not $SkipP0RunbookContractCheck) {
    Invoke-GateStep -Name "P0 runbook contract check (release-gate/CI/runbook consistency)" -Action {
        $reportPath = Join-Path $ProjectRoot "artifacts\p0-runbook-contract-check-release-gate.json"
        $script:P0EvidenceReportPaths += $reportPath
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\p0-runbook-contract-check.py",
                "--label", "release-gate",
                "--output-file", $reportPath
            )
        } catch {
            if ($StrictP0RunbookContractCheck) {
                throw
            }
            Write-Warning (
                "P0 runbook contract check failed but StrictP0RunbookContractCheck is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

if (-not $SkipP0ReportSchemaContractCheck) {
    Invoke-GateStep -Name "P0 report schema contract check (release-gate evidence baseline schema)" -Action {
        $reportPath = Join-Path $ProjectRoot "artifacts\p0-report-schema-contract-release-gate.json"
        $requiredReportPaths = @($script:P0EvidenceReportPaths)
        $script:P0EvidenceReportPaths += $reportPath
        $arguments = @(
            ".\scripts\p0-report-schema-contract-check.py",
            "--label", "release-gate",
            "--artifacts-dir", "artifacts",
            "--include-glob", "*-release-gate.json",
            "--registry-file", "docs/release-gate-registry.json",
            "--output-file", $reportPath
        )
        if ($requiredReportPaths.Count -gt 0) {
            $arguments += @("--required-files", ($requiredReportPaths -join ","))
        } else {
            $arguments += "--allow-empty"
        }
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments $arguments
        } catch {
            if ($StrictP0ReportSchemaContractCheck) {
                throw
            }
            Write-Warning (
                "P0 report schema contract check failed but StrictP0ReportSchemaContractCheck is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

if (-not $SkipP0ReleaseEvidenceBundle) {
    Invoke-GateStep -Name "P0 release evidence bundle (aggregate release-gate reports)" -Action {
        $bundleOutputPath = Join-Path $ProjectRoot "artifacts\p0-release-evidence-bundle-release-gate.json"
        $bundleDir = Join-Path $ProjectRoot "artifacts\p0-release-evidence-files-release-gate"
        $arguments = @(
            ".\scripts\p0-release-evidence-bundle.py",
            "--label", "release-gate",
            "--artifacts-dir", "artifacts",
            "--include-glob", "*-release-gate.json",
            "--registry-file", "docs/release-gate-registry.json",
            "--output-file", $bundleOutputPath,
            "--bundle-dir", $bundleDir
        )
        if ($script:P0EvidenceReportPaths.Count -gt 0) {
            $arguments += @("--required-files", ($script:P0EvidenceReportPaths -join ","))
        } else {
            $arguments += "--allow-empty"
        }
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments $arguments
        } catch {
            if ($StrictP0ReleaseEvidenceBundle) {
                throw
            }
            Write-Warning (
                "P0 release evidence bundle failed but StrictP0ReleaseEvidenceBundle is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

if (-not $SkipP0ClosureReport) {
    Invoke-GateStep -Name "P0 closure go/no-go report (consolidated readiness signal)" -Action {
        $outputPath = Join-Path $ProjectRoot "artifacts\p0-closure-report-release-gate.json"
        $arguments = @(
            ".\scripts\p0-closure-report.py",
            "--label", "release-gate",
            "--required-consecutive", "10",
            "--evidence-bundle-file", "artifacts\p0-release-evidence-bundle-release-gate.json",
            "--burnin-file", "artifacts\p0-burnin-consecutive-green-release-gate.json",
            "--runbook-contract-file", "artifacts\p0-runbook-contract-check-release-gate.json",
            "--output-file", $outputPath
        )
        if ($script:P0EvidenceReportPaths.Count -gt 0) {
            $arguments += @("--required-evidence-reports", ($script:P0EvidenceReportPaths -join ","))
        }
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments $arguments
        } catch {
            if ($StrictP0ClosureReport) {
                throw
            }
            Write-Warning (
                "P0 closure report failed but StrictP0ClosureReport is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

if (-not $SkipReleaseGateEvidenceFreshnessCheck) {
    Invoke-GateStep -Name "Release-gate evidence freshness check (required reports are recent + green)" -Action {
        $reportPath = Join-Path $ProjectRoot "artifacts\release-gate-evidence-freshness-release-gate.json"
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\release-gate-evidence-freshness-check.py",
                "--label", "release-gate",
                "--policy-file", ".\docs\release-gate-evidence-freshness-policy.json",
                "--required-label", "release-gate",
                "--output-file", $reportPath
            )
        } catch {
            if ($StrictReleaseGateEvidenceFreshnessCheck) {
                throw
            }
            Write-Warning (
                "Release-gate evidence freshness check failed but StrictReleaseGateEvidenceFreshnessCheck is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

if (-not $SkipReleaseGateEvidenceHashManifestCheck) {
    Invoke-GateStep -Name "Release-gate evidence hash manifest check (deterministic evidence digest contract)" -Action {
        $reportPath = Join-Path $ProjectRoot "artifacts\release-gate-evidence-hash-manifest-release-gate.json"
        $manifestPath = Join-Path $ProjectRoot "artifacts\release-gate-evidence-manifest-release-gate.json"
        $requiredFiles = @(
            (Join-Path $ProjectRoot "artifacts\safe-mode-ux-degradation-release-gate.json"),
            (Join-Path $ProjectRoot "artifacts\a11y-test-harness-release-gate.json"),
            (Join-Path $ProjectRoot "artifacts\release-gate-runtime-stability-release-gate.json"),
            (Join-Path $ProjectRoot "artifacts\critical-drill-flake-gate-release-gate.json"),
            (Join-Path $ProjectRoot "artifacts\p0-report-schema-contract-release-gate.json"),
            (Join-Path $ProjectRoot "artifacts\p0-release-evidence-bundle-release-gate.json"),
            (Join-Path $ProjectRoot "artifacts\p0-closure-report-release-gate.json")
        )
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\release-gate-evidence-hash-manifest-check.py",
                "--label", "release-gate",
                "--required-files", ($requiredFiles -join ","),
                "--required-label", "release-gate",
                "--output-file", $reportPath,
                "--manifest-file", $manifestPath
            )
        } catch {
            if ($StrictReleaseGateEvidenceHashManifestCheck) {
                throw
            }
            Write-Warning (
                "Release-gate evidence hash manifest check failed but StrictReleaseGateEvidenceHashManifestCheck is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

if (-not $SkipReleaseGateStepTimingSchemaCheck) {
    Invoke-GateStep -Name "Release-gate step timing schema check (step ledger schema + success contract)" -Action {
        $stepTimingsPath = Join-Path $ProjectRoot "artifacts\release-gate-step-timings-release-gate.json"
        $reportPath = Join-Path $ProjectRoot "artifacts\release-gate-step-timing-schema-release-gate.json"
        Write-ReleaseGateStepTimingsReport -OutputFile $stepTimingsPath -Label "release-gate"
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\release-gate-step-timing-schema-check.py",
                "--label", "release-gate",
                "--step-timings-file", $stepTimingsPath,
                "--required-label", "release-gate",
                "--required-keys", "name,duration_seconds,success,completed_at_utc",
                "--output-file", $reportPath
            )
        } catch {
            if ($StrictReleaseGateStepTimingSchemaCheck) {
                throw
            }
            Write-Warning (
                "Release-gate step timing schema check failed but StrictReleaseGateStepTimingSchemaCheck is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

if (-not $SkipReleaseGatePerformanceHistoryCheck) {
    Invoke-GateStep -Name "Release-gate performance history check (baseline regression budget trend)" -Action {
        $stepTimingsPath = Join-Path $ProjectRoot "artifacts\release-gate-step-timings-release-gate.json"
        $reportPath = Join-Path $ProjectRoot "artifacts\release-gate-performance-history-release-gate.json"
        Write-ReleaseGateStepTimingsReport -OutputFile $stepTimingsPath -Label "release-gate"
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\release-gate-performance-history-check.py",
                "--label", "release-gate",
                "--history-baseline-file", ".\docs\release-gate-performance-history-baseline.json",
                "--step-timings-file", $stepTimingsPath,
                "--required-label", "release-gate",
                "--output-file", $reportPath
            )
        } catch {
            if ($StrictReleaseGatePerformanceHistoryCheck) {
                throw
            }
            Write-Warning (
                "Release-gate performance history check failed but StrictReleaseGatePerformanceHistoryCheck is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

if (-not $SkipReleaseGatePerformanceBudgetCheck) {
    Invoke-GateStep -Name "Release-gate performance budget check (step runtime budgets + trend report)" -Action {
        $stepTimingsPath = Join-Path $ProjectRoot "artifacts\release-gate-step-timings-release-gate.json"
        $reportPath = Join-Path $ProjectRoot "artifacts\release-gate-performance-budget-release-gate.json"
        Write-ReleaseGateStepTimingsReport -OutputFile $stepTimingsPath -Label "release-gate"
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\release-gate-performance-budget-check.py",
                "--label", "release-gate",
                "--policy-file", ".\docs\release-gate-performance-budget-policy.json",
                "--step-timings-file", $stepTimingsPath,
                "--required-label", "release-gate",
                "--output-file", $reportPath
            )
        } catch {
            if ($StrictReleaseGatePerformanceBudgetCheck) {
                throw
            }
            Write-Warning (
                "Release-gate performance budget check failed but StrictReleaseGatePerformanceBudgetCheck is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

if (-not $SkipReleaseGateStabilityFinalReadinessCheck) {
    Invoke-GateStep -Name "Release-gate stability final readiness check (Stage L-P consolidated go/no-go)" -Action {
        $reportPath = Join-Path $ProjectRoot "artifacts\release-gate-stability-final-readiness-release-gate.json"
        $requiredReports = @(
            (Join-Path $ProjectRoot "artifacts\release-gate-evidence-freshness-release-gate.json"),
            (Join-Path $ProjectRoot "artifacts\release-gate-evidence-hash-manifest-release-gate.json"),
            (Join-Path $ProjectRoot "artifacts\release-gate-step-timing-schema-release-gate.json"),
            (Join-Path $ProjectRoot "artifacts\release-gate-performance-history-release-gate.json"),
            (Join-Path $ProjectRoot "artifacts\release-gate-performance-budget-release-gate.json"),
            (Join-Path $ProjectRoot "artifacts\p0-closure-report-release-gate.json")
        )
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\release-gate-stability-final-readiness.py",
                "--label", "release-gate",
                "--required-reports", ($requiredReports -join ","),
                "--required-label", "release-gate",
                "--output-file", $reportPath
            )
        } catch {
            if ($StrictReleaseGateStabilityFinalReadinessCheck) {
                throw
            }
            Write-Warning (
                "Release-gate stability final readiness check failed but StrictReleaseGateStabilityFinalReadinessCheck is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

if (-not $SkipReleaseGateStagingSoakReadinessCheck) {
    Invoke-GateStep -Name "Release-gate staging soak readiness check (Stage Q incident/restore gate)" -Action {
        $reportPath = Join-Path $ProjectRoot "artifacts\release-gate-staging-soak-readiness-release-gate.json"
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\release-gate-staging-soak-readiness-check.py",
                "--label", "release-gate",
                "--required-reports", "artifacts/canary-guardrails-release-gate.json,artifacts/auto-rollback-policy-release-gate.json,artifacts/p0-disaster-recovery-rehearsal-pack-release-gate.json,artifacts/failure-budget-dashboard-release-gate.json",
                "--canary-report-file", "artifacts/canary-guardrails-release-gate.json",
                "--rollback-report-file", "artifacts/auto-rollback-policy-release-gate.json",
                "--disaster-recovery-report-file", "artifacts/p0-disaster-recovery-rehearsal-pack-release-gate.json",
                "--failure-budget-report-file", "artifacts/failure-budget-dashboard-release-gate.json",
                "--required-label", "release-gate",
                "--required-canary-stage-count", "4",
                "--output-file", $reportPath
            )
        } catch {
            if ($StrictReleaseGateStagingSoakReadinessCheck) {
                throw
            }
            Write-Warning (
                "Release-gate staging soak readiness check failed but StrictReleaseGateStagingSoakReadinessCheck is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

if (-not $SkipReleaseGateRcCanaryRolloutCheck) {
    Invoke-GateStep -Name "Release-gate RC canary rollout check (Stage R rollout policy gate)" -Action {
        $reportPath = Join-Path $ProjectRoot "artifacts\release-gate-rc-canary-rollout-release-gate.json"
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\release-gate-rc-canary-rollout-check.py",
                "--label", "release-gate",
                "--policy-file", "docs/release-candidate-rollout-policy.json",
                "--required-reports", "artifacts/release-gate-staging-soak-readiness-release-gate.json,artifacts/release-gate-stability-final-readiness-release-gate.json,artifacts/p0-closure-report-release-gate.json,artifacts/canary-guardrails-release-gate.json",
                "--required-label", "release-gate",
                "--candidate-version", "0.0.2-rc1",
                "--output-file", $reportPath
            )
        } catch {
            if ($StrictReleaseGateRcCanaryRolloutCheck) {
                throw
            }
            Write-Warning (
                "Release-gate RC canary rollout check failed but StrictReleaseGateRcCanaryRolloutCheck is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

if (-not $SkipReleaseGateEvidenceHashManifestCheck) {
    Invoke-GateStep -Name "Release-gate evidence hash manifest refresh (Stage S lineage coherence)" -Action {
        $reportPath = Join-Path $ProjectRoot "artifacts\release-gate-evidence-hash-manifest-release-gate.json"
        $manifestPath = Join-Path $ProjectRoot "artifacts\release-gate-evidence-manifest-release-gate.json"
        $requiredFiles = @(
            (Join-Path $ProjectRoot "artifacts\release-gate-stability-final-readiness-release-gate.json"),
            (Join-Path $ProjectRoot "artifacts\release-gate-staging-soak-readiness-release-gate.json"),
            (Join-Path $ProjectRoot "artifacts\release-gate-rc-canary-rollout-release-gate.json"),
            (Join-Path $ProjectRoot "artifacts\p0-closure-report-release-gate.json")
        )
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\release-gate-evidence-hash-manifest-check.py",
                "--label", "release-gate",
                "--required-files", ($requiredFiles -join ","),
                "--required-label", "release-gate",
                "--output-file", $reportPath,
                "--manifest-file", $manifestPath
            )
        } catch {
            if ($StrictReleaseGateEvidenceHashManifestCheck) {
                throw
            }
            Write-Warning (
                "Release-gate evidence hash manifest refresh failed but StrictReleaseGateEvidenceHashManifestCheck is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

if (-not $SkipReleaseGateEvidenceLineageCheck) {
    Invoke-GateStep -Name "Release-gate evidence lineage check (Stage S timestamp + manifest coherence gate)" -Action {
        $reportPath = Join-Path $ProjectRoot "artifacts\release-gate-evidence-lineage-release-gate.json"
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\release-gate-evidence-lineage-check.py",
                "--label", "release-gate",
                "--required-reports", "artifacts/release-gate-stability-final-readiness-release-gate.json,artifacts/release-gate-staging-soak-readiness-release-gate.json,artifacts/release-gate-rc-canary-rollout-release-gate.json,artifacts/p0-closure-report-release-gate.json",
                "--manifest-file", "artifacts/release-gate-evidence-manifest-release-gate.json",
                "--required-label", "release-gate",
                "--max-report-timestamp-skew-seconds", "900",
                "--output-file", $reportPath
            )
        } catch {
            if ($StrictReleaseGateEvidenceLineageCheck) {
                throw
            }
            Write-Warning (
                "Release-gate evidence lineage check failed but StrictReleaseGateEvidenceLineageCheck is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

if (-not $SkipReleaseGateProductionReadinessCertificationCheck) {
    Invoke-GateStep -Name "Release-gate production readiness certification (Stage T final go/no-go certificate)" -Action {
        $reportPath = Join-Path $ProjectRoot "artifacts\release-gate-production-readiness-certification-release-gate.json"
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\release-gate-production-readiness-certification.py",
                "--label", "release-gate",
                "--required-reports", "artifacts/release-gate-stability-final-readiness-release-gate.json,artifacts/release-gate-staging-soak-readiness-release-gate.json,artifacts/release-gate-rc-canary-rollout-release-gate.json,artifacts/release-gate-evidence-lineage-release-gate.json,artifacts/p0-closure-report-release-gate.json,artifacts/p0-burnin-consecutive-green-release-gate.json",
                "--required-label", "release-gate",
                "--burnin-report-file", "artifacts/p0-burnin-consecutive-green-release-gate.json",
                "--required-consecutive", "10",
                "--output-file", $reportPath
            )
        } catch {
            if ($StrictReleaseGateProductionReadinessCertificationCheck) {
                throw
            }
            Write-Warning (
                "Release-gate production readiness certification failed but StrictReleaseGateProductionReadinessCertificationCheck is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

if (-not $SkipReleaseGateSloBurnRateV2Check) {
    Invoke-GateStep -Name "Release-gate SLO burn-rate v2 check (Stage U multi-window burn-rate gate)" -Action {
        $reportPath = Join-Path $ProjectRoot "artifacts\release-gate-slo-burn-rate-v2-release-gate.json"
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\release-gate-slo-burn-rate-v2-check.py",
                "--label", "release-gate",
                "--policy-file", "docs/release-gate-slo-burn-rate-v2-policy.json",
                "--required-reports", "artifacts/failure-budget-dashboard-release-gate.json,artifacts/release-gate-staging-soak-readiness-release-gate.json",
                "--required-label", "release-gate",
                "--output-file", $reportPath
            )
        } catch {
            if ($StrictReleaseGateSloBurnRateV2Check) {
                throw
            }
            Write-Warning (
                "Release-gate SLO burn-rate v2 check failed but StrictReleaseGateSloBurnRateV2Check is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

if (-not $SkipReleaseGateDeployRehearsalCheck) {
    Invoke-GateStep -Name "Release-gate deploy rehearsal check (Stage V deploy/rollback rehearsal gate)" -Action {
        $reportPath = Join-Path $ProjectRoot "artifacts\release-gate-deploy-rehearsal-release-gate.json"
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\release-gate-deploy-rehearsal-check.py",
                "--label", "release-gate",
                "--policy-file", "docs/release-gate-deploy-rehearsal-policy.json",
                "--required-reports", "artifacts/release-gate-production-readiness-certification-release-gate.json,artifacts/release-gate-rc-canary-rollout-release-gate.json,artifacts/auto-rollback-policy-release-gate.json,artifacts/p0-disaster-recovery-rehearsal-pack-release-gate.json",
                "--rollback-report-file", "artifacts/auto-rollback-policy-release-gate.json",
                "--disaster-recovery-report-file", "artifacts/p0-disaster-recovery-rehearsal-pack-release-gate.json",
                "--required-label", "release-gate",
                "--output-file", $reportPath
            )
        } catch {
            if ($StrictReleaseGateDeployRehearsalCheck) {
                throw
            }
            Write-Warning (
                "Release-gate deploy rehearsal check failed but StrictReleaseGateDeployRehearsalCheck is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

if (-not $SkipReleaseGateChaosMatrixContinuousCheck) {
    Invoke-GateStep -Name "Release-gate chaos matrix continuous check (Stage W chaos continuity gate)" -Action {
        $reportPath = Join-Path $ProjectRoot "artifacts\release-gate-chaos-matrix-continuous-release-gate.json"
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\release-gate-chaos-matrix-continuous-check.py",
                "--label", "release-gate",
                "--policy-file", "docs/release-gate-chaos-matrix-policy.json",
                "--required-reports", "artifacts/critical-drill-flake-gate-release-gate.json,artifacts/release-gate-runtime-stability-release-gate.json,artifacts/p0-disaster-recovery-rehearsal-pack-release-gate.json",
                "--critical-drill-report-file", "artifacts/critical-drill-flake-gate-release-gate.json",
                "--required-label", "release-gate",
                "--output-file", $reportPath
            )
        } catch {
            if ($StrictReleaseGateChaosMatrixContinuousCheck) {
                throw
            }
            Write-Warning (
                "Release-gate chaos matrix continuous check failed but StrictReleaseGateChaosMatrixContinuousCheck is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

if (-not $SkipReleaseGateSupplyChainArtifactTrustCheck) {
    Invoke-GateStep -Name "Release-gate supply-chain artifact trust check (Stage X artifact trust gate)" -Action {
        $reportPath = Join-Path $ProjectRoot "artifacts\release-gate-supply-chain-artifact-trust-release-gate.json"
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\release-gate-supply-chain-artifact-trust-check.py",
                "--label", "release-gate",
                "--policy-file", "docs/release-gate-artifact-trust-policy.json",
                "--required-reports", "artifacts/security-ci-lane-release-gate.json,artifacts/release-gate-evidence-hash-manifest-release-gate.json",
                "--manifest-file", "artifacts/release-gate-evidence-manifest-release-gate.json",
                "--required-label", "release-gate",
                "--output-file", $reportPath
            )
        } catch {
            if ($StrictReleaseGateSupplyChainArtifactTrustCheck) {
                throw
            }
            Write-Warning (
                "Release-gate supply-chain artifact trust check failed but StrictReleaseGateSupplyChainArtifactTrustCheck is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

if (-not $SkipReleaseGateOperationsHandoffReadinessCheck) {
    Invoke-GateStep -Name "Release-gate operations handoff readiness check (Stage Y cross-gate handoff readiness)" -Action {
        $reportPath = Join-Path $ProjectRoot "artifacts\release-gate-operations-handoff-readiness-release-gate.json"
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\release-gate-operations-handoff-readiness-check.py",
                "--label", "release-gate",
                "--required-reports", "artifacts/release-gate-production-readiness-certification-release-gate.json,artifacts/release-gate-slo-burn-rate-v2-release-gate.json,artifacts/release-gate-deploy-rehearsal-release-gate.json,artifacts/release-gate-chaos-matrix-continuous-release-gate.json,artifacts/release-gate-supply-chain-artifact-trust-release-gate.json",
                "--required-label", "release-gate",
                "--output-file", $reportPath
            )
        } catch {
            if ($StrictReleaseGateOperationsHandoffReadinessCheck) {
                throw
            }
            Write-Warning (
                "Release-gate operations handoff readiness check failed but StrictReleaseGateOperationsHandoffReadinessCheck is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

if (-not $SkipReleaseGateEvidenceAttestationCheck) {
    Invoke-GateStep -Name "Release-gate evidence attestation check (Stage Z manifest attestation gate)" -Action {
        $reportPath = Join-Path $ProjectRoot "artifacts\release-gate-evidence-attestation-release-gate.json"
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\release-gate-evidence-attestation-check.py",
                "--label", "release-gate",
                "--policy-file", "docs/release-gate-evidence-attestation-policy.json",
                "--required-reports", "artifacts/release-gate-supply-chain-artifact-trust-release-gate.json,artifacts/release-gate-operations-handoff-readiness-release-gate.json,artifacts/release-gate-evidence-hash-manifest-release-gate.json",
                "--manifest-file", "artifacts/release-gate-evidence-manifest-release-gate.json",
                "--required-label", "release-gate",
                "--output-file", $reportPath
            )
        } catch {
            if ($StrictReleaseGateEvidenceAttestationCheck) {
                throw
            }
            Write-Warning (
                "Release-gate evidence attestation check failed but StrictReleaseGateEvidenceAttestationCheck is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

if (-not $SkipReleaseGateReleaseTrainReadinessCheck) {
    Invoke-GateStep -Name "Release-gate release-train readiness check (Stage AA expanded readiness gate)" -Action {
        $reportPath = Join-Path $ProjectRoot "artifacts\release-gate-release-train-readiness-release-gate.json"
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\release-gate-release-train-readiness-check.py",
                "--label", "release-gate",
                "--required-reports", "artifacts/release-gate-production-readiness-certification-release-gate.json,artifacts/release-gate-slo-burn-rate-v2-release-gate.json,artifacts/release-gate-deploy-rehearsal-release-gate.json,artifacts/release-gate-chaos-matrix-continuous-release-gate.json,artifacts/release-gate-supply-chain-artifact-trust-release-gate.json,artifacts/release-gate-operations-handoff-readiness-release-gate.json,artifacts/release-gate-evidence-attestation-release-gate.json,artifacts/p0-closure-report-release-gate.json",
                "--required-label", "release-gate",
                "--output-file", $reportPath
            )
        } catch {
            if ($StrictReleaseGateReleaseTrainReadinessCheck) {
                throw
            }
            Write-Warning (
                "Release-gate release-train readiness check failed but StrictReleaseGateReleaseTrainReadinessCheck is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

if (-not $SkipReleaseGateProductionFinalAttestationCheck) {
    Invoke-GateStep -Name "Release-gate production final attestation (Stage AB final go/no-go attestation)" -Action {
        $reportPath = Join-Path $ProjectRoot "artifacts\release-gate-production-final-attestation-release-gate.json"
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\release-gate-production-final-attestation.py",
                "--label", "release-gate",
                "--required-reports", "artifacts/release-gate-release-train-readiness-release-gate.json,artifacts/p0-closure-report-release-gate.json,artifacts/p0-runbook-contract-check-release-gate.json,artifacts/p0-report-schema-contract-release-gate.json,artifacts/p0-burnin-consecutive-green-release-gate.json",
                "--burnin-report-file", "artifacts/p0-burnin-consecutive-green-release-gate.json",
                "--required-consecutive", "10",
                "--required-label", "release-gate",
                "--output-file", $reportPath
            )
        } catch {
            if ($StrictReleaseGateProductionFinalAttestationCheck) {
                throw
            }
            Write-Warning (
                "Release-gate production final attestation failed but StrictReleaseGateProductionFinalAttestationCheck is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

if (-not $SkipReleaseGateProductionCutoverReadinessCheck) {
    Invoke-GateStep -Name "Release-gate production cutover readiness check (Stage AC cutover readiness gate)" -Action {
        $reportPath = Join-Path $ProjectRoot "artifacts\release-gate-production-cutover-readiness-release-gate.json"
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\release-gate-production-cutover-readiness-check.py",
                "--label", "release-gate",
                "--policy-file", "docs/release-gate-production-cutover-policy.json",
                "--required-reports", "artifacts/release-gate-production-final-attestation-release-gate.json,artifacts/release-gate-release-train-readiness-release-gate.json,artifacts/release-gate-operations-handoff-readiness-release-gate.json,artifacts/p0-closure-report-release-gate.json",
                "--production-final-report-file", "artifacts/release-gate-production-final-attestation-release-gate.json",
                "--release-train-report-file", "artifacts/release-gate-release-train-readiness-release-gate.json",
                "--required-label", "release-gate",
                "--output-file", $reportPath
            )
        } catch {
            if ($StrictReleaseGateProductionCutoverReadinessCheck) {
                throw
            }
            Write-Warning (
                "Release-gate production cutover readiness check failed but StrictReleaseGateProductionCutoverReadinessCheck is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

if (-not $SkipReleaseGateHypercareActivationCheck) {
    Invoke-GateStep -Name "Release-gate hypercare activation check (Stage AD hypercare activation gate)" -Action {
        $reportPath = Join-Path $ProjectRoot "artifacts\release-gate-hypercare-activation-release-gate.json"
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\release-gate-hypercare-activation-check.py",
                "--label", "release-gate",
                "--policy-file", "docs/release-gate-hypercare-policy.json",
                "--required-reports", "artifacts/release-gate-production-cutover-readiness-release-gate.json,artifacts/release-gate-production-final-attestation-release-gate.json,artifacts/release-gate-slo-burn-rate-v2-release-gate.json,artifacts/failure-budget-dashboard-release-gate.json",
                "--cutover-report-file", "artifacts/release-gate-production-cutover-readiness-release-gate.json",
                "--burn-rate-report-file", "artifacts/release-gate-slo-burn-rate-v2-release-gate.json",
                "--required-label", "release-gate",
                "--output-file", $reportPath
            )
        } catch {
            if ($StrictReleaseGateHypercareActivationCheck) {
                throw
            }
            Write-Warning (
                "Release-gate hypercare activation check failed but StrictReleaseGateHypercareActivationCheck is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

if (-not $SkipReleaseGateRollbackTriggerIntegrityCheck) {
    Invoke-GateStep -Name "Release-gate rollback trigger integrity check (Stage AE rollback integrity gate)" -Action {
        $reportPath = Join-Path $ProjectRoot "artifacts\release-gate-rollback-trigger-integrity-release-gate.json"
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\release-gate-rollback-trigger-integrity-check.py",
                "--label", "release-gate",
                "--policy-file", "docs/release-gate-rollback-trigger-integrity-policy.json",
                "--required-reports", "artifacts/release-gate-hypercare-activation-release-gate.json,artifacts/auto-rollback-policy-release-gate.json,artifacts/incident-rollback-release-gate.json,artifacts/release-gate-slo-burn-rate-v2-release-gate.json",
                "--auto-rollback-report-file", "artifacts/auto-rollback-policy-release-gate.json",
                "--incident-rollback-report-file", "artifacts/incident-rollback-release-gate.json",
                "--hypercare-report-file", "artifacts/release-gate-hypercare-activation-release-gate.json",
                "--required-label", "release-gate",
                "--output-file", $reportPath
            )
        } catch {
            if ($StrictReleaseGateRollbackTriggerIntegrityCheck) {
                throw
            }
            Write-Warning (
                "Release-gate rollback trigger integrity check failed but StrictReleaseGateRollbackTriggerIntegrityCheck is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

if (-not $SkipReleaseGatePostCutoverFinalizationCheck) {
    Invoke-GateStep -Name "Release-gate post-cutover finalization check (Stage AF production finalization gate)" -Action {
        $reportPath = Join-Path $ProjectRoot "artifacts\release-gate-post-cutover-finalization-release-gate.json"
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\release-gate-post-cutover-finalization-check.py",
                "--label", "release-gate",
                "--policy-file", "docs/release-gate-post-cutover-finalization-policy.json",
                "--required-reports", "artifacts/release-gate-production-cutover-readiness-release-gate.json,artifacts/release-gate-hypercare-activation-release-gate.json,artifacts/release-gate-rollback-trigger-integrity-release-gate.json,artifacts/release-gate-production-final-attestation-release-gate.json",
                "--rollback-integrity-report-file", "artifacts/release-gate-rollback-trigger-integrity-release-gate.json",
                "--production-final-report-file", "artifacts/release-gate-production-final-attestation-release-gate.json",
                "--required-label", "release-gate",
                "--output-file", $reportPath
            )
        } catch {
            if ($StrictReleaseGatePostCutoverFinalizationCheck) {
                throw
            }
            Write-Warning (
                "Release-gate post-cutover finalization check failed but StrictReleaseGatePostCutoverFinalizationCheck is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

if (-not $SkipReleaseGatePostReleaseWatchCheck) {
    Invoke-GateStep -Name "Release-gate post-release watch check (Stage AG post-release watch gate)" -Action {
        $reportPath = Join-Path $ProjectRoot "artifacts\release-gate-post-release-watch-release-gate.json"
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\release-gate-post-release-watch-check.py",
                "--label", "release-gate",
                "--policy-file", "docs/release-gate-post-release-watch-policy.json",
                "--required-reports", "artifacts/release-gate-post-cutover-finalization-release-gate.json,artifacts/release-gate-slo-burn-rate-v2-release-gate.json,artifacts/release-gate-chaos-matrix-continuous-release-gate.json,artifacts/release-gate-operations-handoff-readiness-release-gate.json",
                "--finalization-report-file", "artifacts/release-gate-post-cutover-finalization-release-gate.json",
                "--burn-rate-report-file", "artifacts/release-gate-slo-burn-rate-v2-release-gate.json",
                "--chaos-report-file", "artifacts/release-gate-chaos-matrix-continuous-release-gate.json",
                "--required-label", "release-gate",
                "--output-file", $reportPath
            )
        } catch {
            if ($StrictReleaseGatePostReleaseWatchCheck) {
                throw
            }
            Write-Warning (
                "Release-gate post-release watch check failed but StrictReleaseGatePostReleaseWatchCheck is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

if (-not $SkipReleaseGateSteadyStateCertificationCheck) {
    Invoke-GateStep -Name "Release-gate steady-state certification check (Stage AH steady-state production certificate)" -Action {
        $reportPath = Join-Path $ProjectRoot "artifacts\release-gate-steady-state-certification-release-gate.json"
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\release-gate-steady-state-certification-check.py",
                "--label", "release-gate",
                "--policy-file", "docs/release-gate-steady-state-certification-policy.json",
                "--required-reports", "artifacts/release-gate-post-release-watch-release-gate.json,artifacts/release-gate-post-cutover-finalization-release-gate.json,artifacts/p0-burnin-consecutive-green-release-gate.json,artifacts/p0-closure-report-release-gate.json,artifacts/release-gate-operations-handoff-readiness-release-gate.json",
                "--post-release-watch-report-file", "artifacts/release-gate-post-release-watch-release-gate.json",
                "--post-cutover-finalization-report-file", "artifacts/release-gate-post-cutover-finalization-release-gate.json",
                "--burnin-report-file", "artifacts/p0-burnin-consecutive-green-release-gate.json",
                "--closure-report-file", "artifacts/p0-closure-report-release-gate.json",
                "--required-label", "release-gate",
                "--output-file", $reportPath
            )
        } catch {
            if ($StrictReleaseGateSteadyStateCertificationCheck) {
                throw
            }
            Write-Warning (
                "Release-gate steady-state certification check failed but StrictReleaseGateSteadyStateCertificationCheck is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

if (-not $SkipReleaseGatePostReleaseContinuityCheck) {
    Invoke-GateStep -Name "Release-gate post-release continuity check (Stage AI continuity gate)" -Action {
        $reportPath = Join-Path $ProjectRoot "artifacts\release-gate-post-release-continuity-release-gate.json"
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\release-gate-post-release-continuity-check.py",
                "--label", "release-gate",
                "--policy-file", "docs/release-gate-post-release-continuity-policy.json",
                "--required-reports", "artifacts/release-gate-post-release-watch-release-gate.json,artifacts/release-gate-steady-state-certification-release-gate.json,artifacts/release-gate-evidence-freshness-release-gate.json,artifacts/release-gate-evidence-attestation-release-gate.json,artifacts/release-gate-operations-handoff-readiness-release-gate.json",
                "--post-release-watch-report-file", "artifacts/release-gate-post-release-watch-release-gate.json",
                "--steady-state-report-file", "artifacts/release-gate-steady-state-certification-release-gate.json",
                "--freshness-report-file", "artifacts/release-gate-evidence-freshness-release-gate.json",
                "--attestation-report-file", "artifacts/release-gate-evidence-attestation-release-gate.json",
                "--required-label", "release-gate",
                "--output-file", $reportPath
            )
        } catch {
            if ($StrictReleaseGatePostReleaseContinuityCheck) {
                throw
            }
            Write-Warning (
                "Release-gate post-release continuity check failed but StrictReleaseGatePostReleaseContinuityCheck is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

if (-not $SkipReleaseGateProductionSustainabilityCertificationCheck) {
    Invoke-GateStep -Name "Release-gate production sustainability certification check (Stage AJ sustained production certificate)" -Action {
        $reportPath = Join-Path $ProjectRoot "artifacts\release-gate-production-sustainability-certification-release-gate.json"
        try {
            Invoke-NativeCommand -Executable $PythonExe -Arguments @(
                ".\scripts\release-gate-production-sustainability-certification-check.py",
                "--label", "release-gate",
                "--policy-file", "docs/release-gate-production-sustainability-certification-policy.json",
                "--required-reports", "artifacts/release-gate-post-release-continuity-release-gate.json,artifacts/release-gate-steady-state-certification-release-gate.json,artifacts/release-gate-post-release-watch-release-gate.json,artifacts/p0-burnin-consecutive-green-release-gate.json,artifacts/p0-closure-report-release-gate.json,artifacts/release-gate-production-final-attestation-release-gate.json",
                "--post-release-continuity-report-file", "artifacts/release-gate-post-release-continuity-release-gate.json",
                "--steady-state-report-file", "artifacts/release-gate-steady-state-certification-release-gate.json",
                "--production-final-report-file", "artifacts/release-gate-production-final-attestation-release-gate.json",
                "--burnin-report-file", "artifacts/p0-burnin-consecutive-green-release-gate.json",
                "--closure-report-file", "artifacts/p0-closure-report-release-gate.json",
                "--required-label", "release-gate",
                "--output-file", $reportPath
            )
        } catch {
            if ($StrictReleaseGateProductionSustainabilityCertificationCheck) {
                throw
            }
            Write-Warning (
                "Release-gate production sustainability certification check failed but StrictReleaseGateProductionSustainabilityCertificationCheck is off. " +
                "Continuing. Error: $($_.Exception.Message)"
            )
        }
    }
}

Write-Host "Release gate passed." -ForegroundColor Green
