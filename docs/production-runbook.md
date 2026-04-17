# Goal Ops Console Production Runbook

This runbook is optimized for reliability-first releases of the desktop app and API.

## 1. Release Checklist

### 1.1 Pre-release gate

Run in repo root:

```powershell
.\scripts\release-gate.ps1 -StrictSecurityConfigHardeningCheck -StrictAuditTrailHardeningCheck -StrictSecurityCiLaneCheck -StrictAlertRoutingOnCallCheck -StrictIncidentDrillAutomationCheck -StrictLoadProfileFrameworkCheck -StrictCanaryGuardrailCheck -StrictRtoRpoAssertionCheck -StrictReleaseFreezePolicyDrill -StrictFileDatabaseProbe -StrictAutoRollbackPolicyDrill -StrictDesktopUpdateSafetyDrill -StrictRecoveryHardAbortDrill -StrictRecoveryIdempotenceDrill -StrictPowerLossDurabilityDrill -StrictWalCheckpointCrashDrill -StrictDiskPressureFaultInjectionDrill -StrictFsyncIoStallDrill -StrictSqliteRealFullDrill -StrictDbCorruptionQuarantineDrill -StrictStorageCorruptionHardeningDrill -StrictWorkflowLockResilienceDrill -StrictWorkflowSoakDrill -StrictWorkflowWorkerRestartDrill -StrictDbSafeModeWatchdogDrill -StrictInvariantMonitorWatchdogDrill -StrictEventConsumerRecoveryChaosDrill -StrictInvariantBurstDrill -StrictLongSoakBudgetDrill -StrictMigrationRehearsal -StrictUpgradeDowngradeCompatibilityDrill -StrictBackupRestoreDrill -StrictBackupRestoreStressDrill -StrictSnapshotRestoreCrashConsistencyDrill -StrictMultiDbAtomicSwitchDrill -StrictIncidentRollbackDrill -StrictDisasterRecoveryRehearsalPack -StrictFailureBudgetDashboard -StrictSafeModeUxDegradationCheck -StrictA11yTestHarnessCheck -StrictReleaseGateRuntimeStabilityDrill -StrictCriticalDrillFlakeGate -StrictP0BurnInConsecutiveGreen -StrictP0RunbookContractCheck -StrictP0ReleaseEvidenceBundle -StrictP0ClosureReport
```

This gate covers:
- full `pytest` suite
- desktop smoke boot path
- `GET /system/readiness`
- `GET /system/slo` (`status` must be `ok`)
- security config hardening check (production profile must require operator auth, strong token, non-memory DB, and startup corruption recovery guard)
- audit trail hardening check (audit hash-chain integrity verification, tamper detection, and retention policy floor)
- security CI lane check (`pip-audit` + `bandit` + SBOM export with explicit fail policy thresholds)
- alert-routing/on-call check (severity route policy, escalation SLA budgets, and runbook-section references)
- incident drill automation check (tabletop + technical drill cadence, evidence completeness, and follow-up budget)
- load profile framework check (versioned production-like traffic profile with deterministic latency/error budgets)
- canary guardrails check (staged ring exposure with deterministic halt/freeze policy and promotion-block validation)
- RTO/RPO assertion suite (restore duration budgets plus bounded data-loss budget assertions)
- release-freeze policy drill (sustained non-ok window or burn-rate spike freezes ring promotion path)
- auto-rollback hard-trigger drill (sustained `critical`, burn-rate spike, or readiness regression triggers stable ring rollback path)
- desktop-update-safety drill (hash validation + rollback-to-stable fallback path)
- recovery hard-abort drill (kill running worker process, restart, and verify no hanging `running` runs)
- recovery idempotence drill (repeated restarts keep startup recovery one-shot/idempotent for interrupted runs)
- power-loss durability drill (abort transaction process before commit and after commit, validate rollback + durable persistence)
- WAL checkpoint crash drill (hard-abort after commit but before checkpoint completion, verify durability + checkpoint recovery)
- disk-pressure fault-injection drill (SQLITE_FULL/IOERR/readonly signatures trigger deterministic safe-mode degradation and recovery)
- fsync/I/O stall drill (bounded write stall + I/O error path with deterministic safe-mode degradation/recovery)
- real SQLite FULL drill (actual `max_page_count` saturation to force natural write failure + controlled recovery)
- DB corruption quarantine drill (startup quarantines corrupted SQLite file and enters guarded safe mode)
- storage corruption hardening drill (WAL/JOURNAL anomaly file path + startup quarantine recovery with deterministic safe-mode behavior)
- workflow lock-resilience drill (transient SQLite lock conflicts while worker remains healthy)
- workflow soak drill (burst enqueue with zero lingering `running` or `queued` runs)
- workflow worker restart drill (stop worker, enqueue run, and verify self-healing restart path)
- DB safe-mode watchdog drill (database lock bursts trigger guarded mutating API mode)
- invariant monitor watchdog drill (periodic invariant scanner detects drift and can force safe mode)
- event-consumer recovery chaos drill (stale `processing` rows reclaimed and drained to clean `processed` state)
- invariant burst drill (mixed goal/task state transitions under burst load with zero invariant violations)
- long soak budget drill (sustained mixed load with latency/429/error budgets enforced)
- `GET /system/database/integrity?mode=quick|full`
- schema migration pending-version check (`pending_versions` must be empty)
- migration rehearsal across small/medium/large/xlarge DB copies with explicit backup/restore/migration runtime thresholds
- upgrade/downgrade compatibility drill (N-1 -> N upgrade + rollback restore to N-1 + legacy schema probe)
- backup/restore drill with row-count and integrity verification on restored DB
- backup/restore stress drill (multi-round load, restore parity validation, and restore idempotence checks)
- snapshot/restore crash-consistency drill (hard-aborted copy fault matrix, manifest preflight checks, and deterministic recovery restore)
- multi-db atomic-switch drill (pointer update crash simulation, corrupted-candidate reject, and deterministic switch rollback path)
- incident/rollback drill with controlled burst load, SLO incident detection, and stable-ring rollback validation
- disaster-recovery rehearsal pack (aggregated backup/snapshot/switch/RTO-RPO drills with deterministic release-block decision + evidence files)
- failure budget dashboard (aggregated machine-readable release-block signal across critical budget reports)
- safe-mode UX degradation check (runtime rail + mutation-lock UX contract + release/CI/runbook wiring)
- A11y test harness check (keyboard navigation baseline, screen-reader semantics smoke, and contrast ratios across visual presets)
- release-gate runtime stability drill (critical-drill duration/variance budget sampling across storage + Stage-D UX/A11y contracts)
- P0 burn-in consecutive-green monitor (latest CI history must satisfy N consecutive fully green runs)
- P0 runbook contract check (release-gate/CI/runbook strict-flag + script-reference consistency and canary baseline drill completeness)
- P0 release evidence bundle (single artifact with required P0 report files and status summary)
- P0 closure report (single go/no-go signal from burn-in + contract + evidence checks)

Manual migration rehearsal invocation (same thresholds as gate defaults):

```powershell
.\scripts\run-migration-rehearsal.ps1 -SmallRuns 500 -MediumRuns 2500 -LargeRuns 6000 -XLargeRuns 9000
```

Manual upgrade/downgrade compatibility drill invocation:

```powershell
.\scripts\run-upgrade-downgrade-compatibility-drill.ps1 -NMinus1Runs 800 -PayloadBytes 512
```

Manual auto-rollback hard-trigger invocation (live endpoint, stable ring):

```powershell
.\scripts\run-auto-rollback-policy.ps1 -BaseUrl "http://127.0.0.1:8000" -ManifestPath ".\artifacts\desktop-rings.json" -CriticalWindowSeconds 300 -ReadinessRegressionWindowSeconds 120 -MaxErrorBudgetBurnRatePercent 2.0 -ExpectedTriggerReason auto -PollIntervalSeconds 30 -MaxObservationSeconds 900
```

Manual security config hardening check invocation:

```powershell
.\scripts\run-security-config-hardening-check.ps1 -OperatorAuthRequired -OperatorAuthToken "replace-with-long-secret-token" -StartupCorruptionRecoveryEnabled
```

Manual audit trail hardening check invocation:

```powershell
.\scripts\run-audit-trail-hardening-check.ps1 -AuditRetentionDays 365 -MinAuditRetentionDays 90 -SeedEntries 8
```

Manual security CI lane check invocation:

```powershell
.\scripts\run-security-ci-lane-check.ps1 -MaxDependencyVulnerabilities 0 -MaxSastHigh 0 -MaxSastMedium 200
```

Manual alert-routing/on-call runbook automation invocation:

```powershell
.\scripts\run-alert-routing-oncall-check.ps1 -MockSloStatus critical -MockAlertCount 2 -RoutingPolicyFile "docs\oncall-alert-routing-policy.json"
```

Manual incident drill automation invocation:

```powershell
.\scripts\run-incident-drill-automation-check.ps1 -MockReport -MockDaysSinceTabletop 7 -MockDaysSinceTechnical 3 -PolicyFile "docs\incident-drill-automation-policy.json"
```

Manual load profile framework invocation:

```powershell
.\scripts\run-load-profile-framework-check.ps1 -ProfileFile "docs\load-profile-catalog.json" -ProfileName "prod_like_ci_smoke" -ProfileVersion "1.0.0"
```

Manual canary guardrails invocation:

```powershell
.\scripts\run-canary-guardrails-check.ps1 -PolicyFile "docs\canary-guardrails-policy.json" -ExpectedDecision halt -MockSloStatuses "ok,ok,critical,critical" -MockErrorBudgetBurnRates "0.5,0.8,2.5,2.5"
```

Manual RTO/RPO assertion suite invocation:

```powershell
.\scripts\run-rto-rpo-assertion-suite.ps1 -PolicyFile "docs\rto-rpo-assertion-policy.json" -SeedRows 48 -TailWriteRows 12 -MaxRtoSeconds 20 -MaxRpoRowsLost 96
```

Manual release-freeze policy invocation (live endpoint, stable ring):

```powershell
.\scripts\run-release-freeze-policy.ps1 -BaseUrl "http://127.0.0.1:8000" -ManifestPath ".\artifacts\desktop-rings.json" -NonOkWindowSeconds 300 -PollIntervalSeconds 30 -MaxObservationSeconds 900
```

Manual desktop-update safety drill invocation:

```powershell
.\scripts\run-desktop-update-safety-drill.ps1
```

Manual recovery hard-abort drill invocation:

```powershell
.\scripts\run-recovery-hard-abort-drill.ps1
```

Manual recovery idempotence drill invocation:

```powershell
.\scripts\run-recovery-idempotence-drill.ps1 -RecoveryCycles 3
```

Manual power-loss durability drill invocation:

```powershell
.\scripts\run-power-loss-durability-drill.ps1 -TransactionRows 240 -PayloadBytes 256
```

Manual WAL checkpoint crash drill invocation:

```powershell
.\scripts\run-wal-checkpoint-crash-drill.ps1 -Rows 240 -PayloadBytes 1024 -CheckpointMode TRUNCATE
```

Manual disk-pressure fault-injection drill invocation:

```powershell
.\scripts\run-disk-pressure-fault-injection-drill.ps1 -FaultInjections 2
```

Manual fsync/I/O stall drill invocation:

```powershell
.\scripts\run-fsync-io-stall-drill.ps1 -FaultInjections 2 -StallSeconds 0.35 -MaxStallRequestSeconds 3.0
```

Manual real SQLite FULL drill invocation:

```powershell
.\scripts\run-sqlite-real-full-drill.ps1 -PayloadBytes 8192 -MaxWriteAttempts 240 -MaxPageGrowth 24 -RecoveryPageGrowth 160
```

Manual DB corruption quarantine drill invocation:

```powershell
.\scripts\run-db-corruption-quarantine-drill.ps1 -CorruptionBytes 256
```

Manual storage corruption hardening drill invocation:

```powershell
.\scripts\run-storage-corruption-hardening-drill.ps1 -CorruptionBytes 192 -Rows 80 -PayloadBytes 128
```

Manual workflow lock-resilience drill invocation:

```powershell
.\scripts\run-workflow-lock-resilience-drill.ps1
```

Manual workflow soak drill invocation:

```powershell
.\scripts\run-workflow-soak-drill.ps1 -RunCount 40
```

Manual workflow worker restart drill invocation:

```powershell
.\scripts\run-workflow-worker-restart-drill.ps1
```

Manual event-consumer recovery chaos drill invocation:

```powershell
.\scripts\run-event-consumer-recovery-chaos-drill.ps1
```

Manual invariant burst drill invocation:

```powershell
.\scripts\run-invariant-burst-drill.ps1
```

Manual long soak budget drill invocation (15-minute pre-release profile):

```powershell
.\scripts\run-long-soak-budget-drill.ps1 -DurationSeconds 900
```

Manual DB safe-mode watchdog drill invocation:

```powershell
.\scripts\run-db-safe-mode-watchdog-drill.ps1 -LockErrorInjections 4
```

Manual critical drill flake gate invocation (critical storage + Stage-D safe-mode/A11y checks):

```powershell
.\scripts\run-critical-drill-flake-gate.ps1 -Repeats 2 -MaxFailedIterations 0
```

Manual invariant monitor watchdog drill invocation:

```powershell
.\scripts\run-invariant-monitor-watchdog-drill.ps1 -TimeoutSeconds 8
```

Manual backup/restore stress drill invocation:

```powershell
.\scripts\run-backup-restore-stress-drill.ps1 -Rounds 3 -GoalsPerRound 120 -TasksPerGoal 2 -WorkflowRunsPerRound 24
```

Manual snapshot/restore crash-consistency drill invocation:

```powershell
.\scripts\run-snapshot-restore-crash-consistency-drill.ps1 -SeedRows 96 -PayloadBytes 128
```

Manual multi-db atomic-switch drill invocation:

```powershell
.\scripts\run-multi-db-atomic-switch-drill.ps1 -SeedRows 96 -PayloadBytes 128
```

Manual disaster-recovery rehearsal pack invocation:

```powershell
.\scripts\run-disaster-recovery-rehearsal-pack.ps1 -Profile scheduled -MaxTotalDurationSeconds 2400
```

Manual failure budget dashboard invocation:

```powershell
.\scripts\run-failure-budget-dashboard.ps1
```

Manual safe-mode/degradation UX contract check invocation:

```powershell
.\scripts\run-safe-mode-ux-degradation-check.ps1
```

Manual A11y test harness check invocation:

```powershell
.\scripts\run-a11y-test-harness-check.ps1
```

Manual release-gate runtime stability drill invocation (critical storage + Stage-D UX/A11y checks):

```powershell
.\scripts\run-release-gate-runtime-stability-drill.ps1 -Samples 2 -RepeatsPerSample 1
```

Manual P0 burn-in consecutive-green monitor invocation:

```powershell
.\scripts\run-p0-burnin-consecutive-green.ps1 -RequiredConsecutive 10
```

Manual P0 runbook contract check invocation:

```powershell
.\scripts\run-p0-runbook-contract-check.ps1
```

Manual P0 release evidence bundle invocation:

```powershell
.\scripts\run-p0-release-evidence-bundle.ps1
```

Manual P0 closure report invocation:

```powershell
.\scripts\run-p0-closure-report.ps1
```

Verify before release:
- CI checks green:
  - `Release Gate (Windows)`
  - `Pytest (Python 3.11)`
  - `Pytest (Python 3.12)`
  - `Desktop Smoke (Windows)`
- burn-in monitor report confirms threshold met (`metrics.consecutive_green >= metrics.required_consecutive`)
- runbook contract report confirms zero missing flags/scripts and canary baseline drills (`success=true`)
- release evidence bundle report confirms all required P0 reports present and successful (`success=true`)
- disaster-recovery rehearsal release-gate report is present and green (`artifacts\p0-disaster-recovery-rehearsal-pack-release-gate.json`, `success=true`)
- failure budget dashboard report is present and green (`artifacts\failure-budget-dashboard-release-gate.json`, `success=true`)
- closure report confirms all readiness criteria are green (`success=true`, `metrics.criteria_failed=0`)
- security hardening report confirms production policy criteria are green (`success=true`)
- `master` branch only receives PR merges (no direct pushes).
- No unresolved high-severity bug tickets for release scope.

### 1.2 Build and package

```powershell
.\scripts\package-desktop-release.ps1 -Version "<VERSION>" -Channel stable -Mode both -OutputDir artifacts
```

Validate artifacts:
- `artifacts\desktop-update-manifest.json` exists and version matches release tag.
- `artifacts\desktop-rings.json` exists and points `stable` to intended version.
- `artifacts\SHA256SUMS.txt` exists.
- `GoalOpsConsole-onefile-<version>.exe` starts and reaches dashboard.
- `GoalOpsConsole-update-helper-<version>.ps1` exists next to installer script.

### 1.3 Ring promotion

Use one control manifest for production channel pointers:

```powershell
.\scripts\manage-desktop-rings.ps1 `
  -ManifestPath ".\artifacts\desktop-rings.json" `
  -Action promote `
  -Ring stable `
  -Version "<VERSION>" `
  -ReleaseManifestPath ".\artifacts\desktop-update-manifest.json"
```

### 1.4 Publish

Tag and push:

```powershell
git tag v<VERSION>
git push origin v<VERSION>
```

GitHub Actions `Desktop Build` publishes release assets on tag pushes.

### 1.5 Post-release checks

After release is live:
- Launch desktop binary on a clean Windows machine.
- Confirm `GET /system/readiness` returns `{"ready": true, ...}`.
- Confirm `GET /system/slo` returns `"status": "ok"`.
- Confirm `GET /system/database/integrity?mode=quick` returns `"ok": true`.
- Trigger `Export Diagnostics` once from Operator Controls and confirm snapshot file exists.
- Verify one workflow run can be queued and completed from UI.

### 1.6 Nightly stability canary

The scheduled workflow [stability-canary.yml](/C:/Users/raffa/OneDrive/Documents/New%20project/.github/workflows/stability-canary.yml) runs a nightly trend check against [stability-canary-baseline.json](/C:/Users/raffa/OneDrive/Documents/New%20project/docs/stability-canary-baseline.json), including power-loss durability, DB corruption quarantine startup recovery, upgrade/downgrade compatibility, and Stage-D safe-mode UX + A11y baseline checks.
Missing baseline entries are treated as regressions and fail the canary.

Manual canary invocation:

```powershell
python .\scripts\stability-canary.py --baseline-file .\docs\stability-canary-baseline.json --long-soak-duration-seconds 120 --output-file .\.tmp\stability-canary-report.json
```

## 2. Rollback

Use rollback when crash rate or readiness failures rise immediately after rollout.

1. Roll back stable ring pointer (fast path):
   ```powershell
   .\scripts\manage-desktop-rings.ps1 `
     -ManifestPath ".\artifacts\desktop-rings.json" `
     -Action rollback `
     -Ring stable
   ```
2. Repoint download/install instructions to previous stable artifact.
3. Keep diagnostics snapshots from failed version for triage.
4. Open incident ticket with:
   - failing version
   - first failure timestamp (UTC)
   - attached diagnostics JSON and crash report JSON
5. After rollback, run a short verification drill:
   ```powershell
   .\scripts\run-incident-rollback-drill.ps1 -LoadRequests 30
   ```

## 3. Failure Playbook

### 3.1 Readiness is false

Symptoms:
- Desktop launcher fails with `Desktop server did not become ready`.
- `GET /system/readiness` shows `ready=false`.

Actions:
1. Call readiness endpoint directly:
   ```powershell
   Invoke-RestMethod http://127.0.0.1:8000/system/readiness
   ```
2. Check failing check:
   - `checks.database.ok = false`: inspect DB path/permissions/locking.
   - `checks.database.startup_recovery.triggered = true`: validate quarantined DB file and keep safe mode active until recovery validation is complete.
   - `checks.workflow_worker.ok = false`: worker thread failed to start.
3. If the database check fails, run integrity probe:
   ```powershell
   Invoke-RestMethod "http://127.0.0.1:8000/system/database/integrity?mode=quick"
   ```
4. Export diagnostics:
   - UI: `Export Diagnostics`
   - API: `POST /system/diagnostics`
5. Restart app and retry once. If still failing, rollback.

### 3.2 Workflow worker stalled

Symptoms:
- workflow runs stay `queued`.
- readiness worker check flips to `ok=false` or `is_running=false`.

Actions:
1. Verify with:
   ```powershell
   Invoke-RestMethod http://127.0.0.1:8000/system/readiness
   ```
2. Restart the process.
3. Reap stale runs:
   ```powershell
   Invoke-RestMethod -Method Post http://127.0.0.1:8000/workflows/runs/reap
   ```
4. If recurring, open incident and attach diagnostics snapshots from before/after restart.

### 3.3 Backpressure / degraded throughput

Symptoms:
- API returns `429` with retry hints.
- queue and pending events keep growing.

Actions:
1. Check:
   - `GET /system/backpressure`
   - `GET /system/queue`
2. Run controlled drain and reclaim from Operator Controls.
3. Temporarily reduce operator-initiated workflow starts.
4. When stable, run retention cleanup once.

### 3.4 SLO alert status degraded or critical

Symptoms:
- `GET /system/slo` returns `status=degraded` or `status=critical`.
- alert list contains active SLO violations.

Actions:
1. Check current SLO payload:
   ```powershell
   Invoke-RestMethod http://127.0.0.1:8000/system/slo
   ```
2. Run operator gate check against live service:
   ```powershell
   .\scripts\run-slo-alert-check.ps1 -BaseUrl "http://127.0.0.1:8000" -AllowedStatus degraded
   ```
3. If status is `critical`, enforce sustained-window policy check:
   ```powershell
   .\scripts\run-release-freeze-policy.ps1 -BaseUrl "http://127.0.0.1:8000" -ManifestPath ".\artifacts\desktop-rings.json" -NonOkWindowSeconds 300 -PollIntervalSeconds 30 -MaxObservationSeconds 900
   ```
4. If status remains `critical` or readiness regresses under elevated burn-rate, enforce hard-trigger rollback policy check:
   ```powershell
   .\scripts\run-auto-rollback-policy.ps1 -BaseUrl "http://127.0.0.1:8000" -ManifestPath ".\artifacts\desktop-rings.json" -CriticalWindowSeconds 300 -ReadinessRegressionWindowSeconds 120 -MaxErrorBudgetBurnRatePercent 2.0 -ExpectedTriggerReason auto -PollIntervalSeconds 30 -MaxObservationSeconds 900
   ```
5. Export diagnostics snapshot and attach to incident ticket.

### 3.5 Desktop startup conflicts (single-instance lock)

Symptoms:
- error: another desktop instance is already running.

Actions:
1. Confirm no active app process.
2. Retry launch once (stale lock auto-recovery is enabled).
3. If still blocked, collect crash/diagnostics and escalate.

### 3.6 Crash-loop protection triggered

Symptoms:
- launcher exits with crash-loop protection message.
- repeated crash on startup despite restart.

Actions:
1. Review latest crash report and diagnostics snapshot.
2. Validate fix/hypothesis before bypassing protection.
3. Use one-time bypass only for supervised verification:
   ```powershell
   .\scripts\start-desktop.ps1 -AllowCrashLoop
   ```
4. If crash reproduces, stop and roll back ring immediately.

### 3.7 Crash reports

Location:
- default: `%USERPROFILE%\.goal_ops_console\diagnostics`
- or `GOAL_OPS_DIAGNOSTICS_DIR` when configured.

Crash file pattern:
- `desktop-crash-<timestamp>.json`

Required triage fields:
- `error_type`
- `error`
- `traceback`
- `context`

### 3.8 Desktop update validation or install failure

Symptoms:
- installer exits with hash/signature verification failure
- update copy fails and app version appears unchanged after install attempt

Actions:
1. Confirm checksum line for onefile artifact:
   ```powershell
   Get-Content .\artifacts\SHA256SUMS.txt
   ```
2. Re-run installer (it enforces hash validation and restores previous stable executable on failure):
   ```powershell
   .\artifacts\GoalOpsConsole-install-<version>.ps1
   ```
3. If signature enforcement is required, verify signature explicitly:
   ```powershell
   Get-AuthenticodeSignature .\artifacts\GoalOpsConsole-onefile-<version>.exe
   ```
4. Run desktop-update safety drill before retrying rollout:
   ```powershell
   .\scripts\run-desktop-update-safety-drill.ps1
   ```
5. If fallback restore happened, keep stable version active and open an incident with checksum/signature evidence.

### 3.9 Hard process abort / unexpected termination

Symptoms:
- host process is killed while a workflow run is still `running`
- after restart, historical runs appear stuck in `running`

Actions:
1. Verify worker recovery on restart:
   ```powershell
   Invoke-RestMethod http://127.0.0.1:8000/system/readiness
   ```
2. Confirm no hanging runs remain:
   ```powershell
   Invoke-RestMethod http://127.0.0.1:8000/workflows/runs
   ```
3. Run deterministic hard-abort recovery drill:
   ```powershell
   .\scripts\run-recovery-hard-abort-drill.ps1
   ```
4. If drill fails, block rollout and escalate with drill JSON output + diagnostics snapshot.

### 3.10 Recovery idempotence uncertainty after repeated restarts

Symptoms:
- interrupted runs are recovered once, but restart behavior is unclear across multiple successive process restarts
- concern about duplicate `workflow.run.recovered_after_abort` events or repeated state churn

Actions:
1. Run recovery idempotence drill:
   ```powershell
   .\scripts\run-recovery-idempotence-drill.ps1 -RecoveryCycles 3
   ```
2. Confirm drill outputs:
   - first cycle shows `startup_recovery.recovered_count >= 1`
   - subsequent cycles show `startup_recovery.recovered_count = 0`
   - `recovered_event_count` for the interrupted run remains constant (no duplicates)
3. If idempotence fails, hold release and escalate with drill JSON + diagnostics snapshot.

### 3.11 Workflow lock contention spikes

Symptoms:
- intermittent SQLite lock-conflict errors under concurrent workflow activity
- worker appears healthy but throughput jitters

Actions:
1. Run lock-resilience drill:
   ```powershell
   .\scripts\run-workflow-lock-resilience-drill.ps1
   ```
2. Confirm worker remains healthy in readiness payload:
   ```powershell
   Invoke-RestMethod http://127.0.0.1:8000/system/readiness
   ```
3. If drill fails, block rollout and investigate DB contention before retrying release.

### 3.12 Post-burst hanging run check

Symptoms:
- large queue bursts are processed, but some runs remain `queued`/`running`

Actions:
1. Run soak drill:
   ```powershell
   .\scripts\run-workflow-soak-drill.ps1 -RunCount 40
   ```
2. Verify workflow run list contains only terminal states:
   ```powershell
   Invoke-RestMethod http://127.0.0.1:8000/workflows/runs
   ```
3. If hanging runs remain, trigger incident and hold release promotion.

### 3.13 Workflow worker does not recover after stop/crash

Symptoms:
- readiness check shows `workflow_worker.ok = false`
- workflow starts remain queued because worker thread is not running

Actions:
1. Run worker restart drill:
   ```powershell
   .\scripts\run-workflow-worker-restart-drill.ps1
   ```
2. Verify readiness recovers to `ready=true`:
   ```powershell
   Invoke-RestMethod http://127.0.0.1:8000/system/readiness
   ```
3. If drill fails or `startup_recovery_error` is set, block release and escalate with diagnostics snapshot.

### 3.14 Event consumer backlog stuck in `processing`

Symptoms:
- consumer stats show persistent `processing` rows
- pending backlog does not shrink despite repeated drain attempts

Actions:
1. Run event-consumer recovery chaos drill:
   ```powershell
   .\scripts\run-event-consumer-recovery-chaos-drill.ps1
   ```
2. Verify consumer status no longer includes stale `processing` rows:
   ```powershell
   Invoke-RestMethod http://127.0.0.1:8000/system/health
   ```
3. If reclaim/drain cannot clear backlog, block rollout and escalate with diagnostics + backlog snapshot.

### 3.15 Queue/goal/task consistency drift after burst activity

Symptoms:
- dashboard/system health reports invariant violations
- archived/cancelled goals appear unexpectedly in queue-related views

Actions:
1. Run invariant burst drill:
   ```powershell
   .\scripts\run-invariant-burst-drill.ps1
   ```
2. Re-check invariants:
   ```powershell
   Invoke-RestMethod http://127.0.0.1:8000/system/health
   ```
3. If violations persist, stop promotion and investigate state transition paths before release.

### 3.16 Sustained-load budget regression

Symptoms:
- throughput appears fine in short tests but degrades in longer sustained runs
- release candidate shows rising latency, throttling, or server errors under prolonged activity

Actions:
1. Run long soak budget drill with release profile:
   ```powershell
   .\scripts\run-long-soak-budget-drill.ps1 -DurationSeconds 900
   ```
2. Confirm budget thresholds stay within limits (`p95 latency`, `HTTP 429 rate`, `error rate`).
3. If budgets fail, hold release, attach soak JSON output + diagnostics to incident ticket, and require remediation before retry.

### 3.17 Release freeze prevents ring promotion

Symptoms:
- stable ring promotion command fails with active freeze message
- `/system/slo` stays non-ok beyond allowed window

Actions:
1. Inspect freeze state:
   ```powershell
   Get-Content .\artifacts\desktop-rings.json
   ```
2. Validate current runtime state:
   ```powershell
   Invoke-RestMethod http://127.0.0.1:8000/system/slo
   ```
3. If freeze should remain active, do not override and keep rollout paused.
4. If issue is mitigated and override is approved, clear freeze with documented reason:
   ```powershell
   .\scripts\manage-desktop-rings.ps1 -ManifestPath ".\artifacts\desktop-rings.json" -Action unfreeze -Reason "Mitigated incident INC-<ID>"
   ```

### 3.18 Runtime safe mode active (mutations blocked)

Symptoms:
- mutating API calls return `503` with safe-mode details
- readiness reports `checks.safe_mode.ok = false`

Actions:
1. Inspect guard state:
   ```powershell
   Invoke-RestMethod http://127.0.0.1:8000/system/safe-mode
   ```
2. Run DB safe-mode watchdog drill to verify lock/io handling:
   ```powershell
   .\scripts\run-db-safe-mode-watchdog-drill.ps1 -LockErrorInjections 4
   ```
3. If cause is mitigated, disable safe mode with operator reason:
   ```powershell
   Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/system/safe-mode/disable -ContentType "application/json" -Body '{"reason":"Mitigated lock burst"}'
   ```

### 3.19 Invariant monitor reports violations

Symptoms:
- readiness reports `checks.invariant_monitor.ok = false`
- `/system/invariants` returns non-empty violation list

Actions:
1. Fetch monitor and violation details:
   ```powershell
   Invoke-RestMethod http://127.0.0.1:8000/system/invariants
   ```
2. Run invariant monitor watchdog drill:
   ```powershell
   .\scripts\run-invariant-monitor-watchdog-drill.ps1 -TimeoutSeconds 8
   ```
3. If violations persist, keep safe mode enabled and hold promotion until root cause is fixed.

### 3.20 Startup DB corruption quarantine was triggered

Symptoms:
- readiness shows `checks.database.startup_recovery.triggered = true`
- SLO includes `database.startup_recovery.triggered` alert
- mutating endpoints are blocked by safe mode until operator validation

Actions:
1. Inspect startup recovery payload and quarantined file path:
   ```powershell
   Invoke-RestMethod http://127.0.0.1:8000/system/database/integrity?mode=quick
   ```
2. Run DB corruption quarantine drill to confirm recovery flow remains healthy:
   ```powershell
   .\scripts\run-db-corruption-quarantine-drill.ps1 -CorruptionBytes 256
   ```
3. Validate restored runtime state and backup posture before re-enabling mutations.
4. Disable safe mode with explicit operator reason only after validation:
   ```powershell
   Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/system/safe-mode/disable -ContentType "application/json" -Body '{"reason":"DB quarantine validated and recovery complete"}'
   ```

### 3.21 Upgrade or rollback compatibility uncertainty

Symptoms:
- migration rehearsal passes, but there is no fresh proof for N-1 -> N -> N-1 compatibility on current build
- release decision depends on safe rollback confidence under real data copy

Actions:
1. Run compatibility drill:
   ```powershell
   .\scripts\run-upgrade-downgrade-compatibility-drill.ps1 -NMinus1Runs 800 -PayloadBytes 512
   ```
2. Confirm drill outputs:
   - `probes.upgrade.readiness_ready = true`
   - `probes.upgrade.slo_status = ok`
   - `probes.reupgrade.readiness_ready = true`
   - `probes.reupgrade.slo_status = ok`
3. If drill fails any threshold or data-preservation check, hold release and keep rollback path ready using migration backup.

### 3.22 Power-loss durability uncertainty

Symptoms:
- crash/reboot concerns around SQLite commit boundaries under release-candidate load
- uncertainty whether interrupted transactions are fully rolled back while committed writes remain durable

Actions:
1. Run power-loss durability drill:
   ```powershell
   .\scripts\run-power-loss-durability-drill.ps1 -TransactionRows 240 -PayloadBytes 256
   ```
2. Confirm drill outputs:
   - `scenarios.abort_before_commit.observed_rows = 0`
   - `scenarios.abort_after_commit.observed_rows = transaction_rows`
   - `app_probe.readiness_ready = true`
   - `app_probe.slo_status = ok`
3. If durability checks fail, block release, preserve artifacts (`-KeepArtifacts`), and escalate with drill JSON + diagnostics snapshot.

### 3.23 WAL checkpoint crash durability uncertainty

Symptoms:
- process crash/power-loss concern after a transaction commit but before WAL checkpoint completion
- uncertainty whether committed rows remain durable and whether recovery checkpoint returns the DB to a clean steady state

Actions:
1. Run WAL checkpoint crash drill:
   ```powershell
   .\scripts\run-wal-checkpoint-crash-drill.ps1 -Rows 240 -PayloadBytes 1024 -CheckpointMode TRUNCATE
   ```
2. Confirm drill outputs:
   - `scenario.rows_persisted_before_crash = rows`
   - `scenario.rows_observed_after_crash = rows`
   - `checkpoint_recovery.busy = 0`
   - `app_probe.readiness_ready = true`
   - `app_probe.slo_status = ok`
3. If durability, integrity, or checkpoint recovery checks fail, hold release, preserve artifacts (`-KeepArtifacts`), and escalate with drill JSON + diagnostics snapshot.

### 3.24 Disk pressure, IO errors, or permission-flip uncertainty

Symptoms:
- intermittent writes fail with SQLite errors such as `database or disk is full`, `disk i/o error`, or `attempt to write a readonly database`
- release confidence is unclear for safe degradation/recovery behavior under write-path storage faults

Actions:
1. Run disk-pressure fault-injection drill:
   ```powershell
   .\scripts\run-disk-pressure-fault-injection-drill.ps1 -FaultInjections 2
   ```
2. Confirm per-case outputs:
   - `safe_mode.active_after_faults = true`
   - `status_codes.blocked_mutation = 503`
   - `readiness.during_fault = false`
   - `slo.during_fault = critical`
   - `readiness.after_recovery = true`
   - `slo.after_recovery = ok`
3. If any case fails, hold release and escalate with drill JSON + diagnostics snapshot before retrying rollout.

### 3.25 fsync/I/O stall degradation uncertainty

Symptoms:
- write operations intermittently stall and then fail with I/O-like signatures under storage pressure
- uncertainty whether stall behavior remains bounded and whether recovery returns to deterministic readiness/SLO

Actions:
1. Run fsync/I/O stall drill:
   ```powershell
   .\scripts\run-fsync-io-stall-drill.ps1 -FaultInjections 2 -StallSeconds 0.35 -MaxStallRequestSeconds 3.0
   ```
2. Confirm drill outputs:
   - `stall_observations.request_latencies_ms` are bounded by configured max
   - `safe_mode.active_after_faults = true`
   - `readiness.after_recovery = true`
   - `slo.after_recovery = ok`
3. If stall latency budget or recovery checks fail, hold release and escalate with drill JSON + diagnostics snapshot.

### 3.26 Real SQLite FULL saturation uncertainty

Symptoms:
- uncertainty whether the app handles true file-backed `SQLITE_FULL` behavior (not only simulated exceptions)
- concern that recovery after storage-pressure mitigation might still leave readiness/SLO or write-path unstable

Actions:
1. Run real SQLite FULL drill:
   ```powershell
   .\scripts\run-sqlite-real-full-drill.ps1 -PayloadBytes 8192 -MaxWriteAttempts 240 -MaxPageGrowth 24 -RecoveryPageGrowth 160
   ```
2. Confirm drill outputs:
   - `fill.first_failure_status = 500`
   - `safe_mode.after_full_trigger.active = true`
   - `status_codes.blocked_mutation_during_safe_mode = 503`
   - `readiness.after_recovery = true`
   - `slo.after_recovery = ok`
3. If recovery or integrity checks fail, hold release and escalate with drill JSON + diagnostics snapshot before retrying rollout.

### 3.27 On-call critical alert routing

Symptoms:
- `/system/slo` returns `status=critical` or one/more critical alert objects in `alerts[]`
- customer-impacting error budget burn or safe-mode incidents require immediate human response

Actions:
1. Trigger on-call routing automation:
   ```powershell
   .\scripts\run-alert-routing-oncall-check.ps1 -MockSloStatus critical -MockAlertCount 2 -RoutingPolicyFile "docs\oncall-alert-routing-policy.json"
   ```
2. Ensure policy output includes for every critical alert:
   - `page_primary_oncall` action to `primary-oncall`
   - `page_backup_oncall` action to `secondary-oncall`
   - due windows within policy (`critical_page_within_minutes`, `critical_backup_after_minutes`)
3. Follow escalation order:
   - page primary immediately
   - page backup if ack/mitigation is not confirmed within escalation SLA
   - open incident bridge and attach diagnostics bundle
4. Keep release frozen until `status` returns to `ok` and post-incident checklist is complete.

### 3.28 On-call warning alert routing

Symptoms:
- `/system/slo` returns `status=degraded` or warning-level alerts
- no immediate outage, but error budget and stability trends indicate heightened risk

Actions:
1. Run warning-route verification:
   ```powershell
   .\scripts\run-alert-routing-oncall-check.ps1 -MockSloStatus degraded -MockAlertCount 1 -RoutingPolicyFile "docs\oncall-alert-routing-policy.json"
   ```
2. Ensure policy output includes for every warning alert:
   - `notify_warning_channel` action to `ops-slack-warning`
   - `create_warning_ticket` action with owner + due window
3. Assign mitigation owner and ETA in incident tracker.
4. Escalate to critical routing immediately if warning turns into `status=critical` or burn-rate spikes.

### 3.29 Incident tabletop drill automation

Symptoms:
- recurring incidents reveal process gaps between detection, communication, and rollback decision points
- tabletop evidence is missing/stale and release confidence drops below expected operational baseline

Actions:
1. Run tabletop drill automation check:
   ```powershell
   .\scripts\run-incident-drill-automation-check.ps1 -MockReport -MockDaysSinceTabletop 7 -MockDaysSinceTechnical 3 -PolicyFile "docs\incident-drill-automation-policy.json"
   ```
2. Confirm policy checks pass for `tabletop.release-rollback`:
   - `status_completed = true`
   - `recency_budget` within `tabletop_max_age_days`
   - `postmortem_link_present = true`
3. Ensure owner roles are mapped and active (`incident_commander`, `scribe`, `communications`).
4. Convert unresolved tabletop findings into tracked follow-ups before release promotion resumes.

### 3.30 Incident technical drill automation

Symptoms:
- rollback path has not been proven recently under controlled load
- technical drill evidence (load + rollback verification) is missing or out of budget

Actions:
1. Execute technical drill automation validation:
   ```powershell
   .\scripts\run-incident-drill-automation-check.ps1 -MockReport -MockDaysSinceTabletop 7 -MockDaysSinceTechnical 3 -PolicyFile "docs\incident-drill-automation-policy.json"
   ```
2. Verify `technical.incident-rollback` evidence:
   - `technical_min_load_requests` meets policy floor
   - `rollback_verified = true`
   - `recency_budget` within `technical_drill_max_age_days`
3. Keep open follow-ups within policy budget (`max_open_followup_actions`) before approving release.
4. If check fails, hold release and run a fresh end-to-end incident/rollback drill, then re-run automation check.

### 3.31 Prod-like load profile execution

Symptoms:
- release candidate has no recent evidence under versioned production-like load profiles
- latency/error behavior is unknown for current build in steady-plus-burst traffic shape

Actions:
1. Run load-profile framework check:
   ```powershell
   .\scripts\run-load-profile-framework-check.ps1 -ProfileFile "docs\load-profile-catalog.json" -ProfileName "prod_like_ci_smoke" -ProfileVersion "1.0.0"
   ```
2. Verify reported budgets are all green:
   - `min_total_requests`
   - `p95_latency_budget`, `p99_latency_budget`, `max_latency_budget`
   - `http_429_budget`, `error_rate_budget`
3. Confirm final health gates after profile run:
   - `final_readiness_true = true`
   - `final_slo_ok = true`
   - `workflow_queue_drained = true`
4. If a budget fails, hold release and attach JSON report to incident/change ticket for remediation.

### 3.32 Load profile catalog version management

Symptoms:
- profile definitions change over time and previous evidence cannot be compared reproducibly
- operators need deterministic mapping between release decision and exact profile revision

Actions:
1. Keep `docs/load-profile-catalog.json` under version control and bump `catalog_version` when profile semantics change.
2. Add/retain explicit `name` + `version` for each profile and run checks with pinned values (`--profile-name`, `--profile-version`).
3. For major profile updates, run both old and new versions once in rehearsal, compare deltas, then switch release gate default.
4. Record selected profile/version in release notes for auditability and rollback analysis.

### 3.33 RTO zero-loss restore assertion

Symptoms:
- restore path was not validated recently for strict recovery-time objective on full-state backup
- operators need deterministic proof that zero-loss restore remains within release budget

Actions:
1. Execute RTO/RPO suite with default policy:
   ```powershell
   .\scripts\run-rto-rpo-assertion-suite.ps1 -PolicyFile "docs\rto-rpo-assertion-policy.json" -SeedRows 48 -TailWriteRows 12 -MaxRtoSeconds 20 -MaxRpoRowsLost 96
   ```
2. Validate `restore.zero_loss` criteria:
   - `zero_loss_restore_matches_source = true`
   - `zero_loss_rows_lost_zero = true`
   - `zero_loss_rto_budget = true`
3. Confirm runtime probe remains healthy (`readiness=true`, `slo=ok`, integrity endpoint ok) on restored DB.
4. If RTO budget fails, hold release and optimize restore chain before re-running gate.

### 3.34 RPO bounded-loss assertion

Symptoms:
- backup cutoff points might allow excessive data loss after incident rollback/restore
- no current evidence that RPO remains within agreed bounded-loss budget

Actions:
1. Run the same suite and inspect `restore.bounded_loss` scenario output.
2. Validate RPO constraints:
   - `bounded_loss_restore_matches_backup_point = true`
   - `bounded_loss_rpo_budget = true` (rows lost <= policy max)
   - `bounded_loss_rto_budget = true`
3. Track `bounded_rows_lost` trend over releases; investigate increases even if still within budget.
4. If bounded-loss budget is exceeded, freeze promotion and open incident with report JSON attached.

### 3.35 Canary promotion guardrails

Symptoms:
- release candidate canary rollout has no deterministic evidence for staged promotion rules
- operators cannot prove that non-ok status or burn-rate spikes halt further exposure

Actions:
1. Run guardrail evaluation:
   ```powershell
   .\scripts\run-canary-guardrails-check.ps1 -PolicyFile "docs\canary-guardrails-policy.json" -ExpectedDecision halt -MockSloStatuses "ok,ok,critical,critical" -MockErrorBudgetBurnRates "0.5,0.8,2.5,2.5"
   ```
2. Verify stage verdicts are deterministic:
   - staged traffic percentages are strictly increasing (`10 -> 25 -> 50 -> 100`)
   - threshold evaluation identifies first halt stage (`decision.result = halt`)
   - `decision_matches_expected = true`
3. Confirm candidate remains only on canary ring (`canary_seeded_with_candidate = true`) while stable ring does not promote in halt path.
4. Attach JSON report to release evidence bundle before promotion decision.

### 3.36 Canary automatic halt and freeze

Symptoms:
- canary thresholds are breached but promotion path still appears possible
- release freeze activation state is unclear during incident triage

Actions:
1. Validate halt-path safety criteria from report:
   - `release_freeze_active = true`
   - `stable_not_promoted_when_halted = true`
   - `promotion_blocked_by_freeze = true`
2. If any halt criterion fails, stop rollout and keep ring frozen until root cause is mitigated.
3. After recovery signals return to `ok` and burn-rate normalizes, run guardrails again with a promote expectation:
   ```powershell
   .\scripts\run-canary-guardrails-check.ps1 -PolicyFile "docs\canary-guardrails-policy.json" -ExpectedDecision promote -MockSloStatuses "ok,ok,ok,ok" -MockErrorBudgetBurnRates "0.4,0.5,0.6,0.7"
   ```
4. Only clear freeze and continue promotion after a green guardrail report is attached to incident/change record.

### 3.37 Auto-rollback hard triggers

Symptoms:
- incident conditions escalate beyond release-freeze scope and require immediate stable rollback
- operators need deterministic policy evidence for rollback trigger reason

Actions:
1. Execute hard-trigger rollback drill:
   ```powershell
   .\scripts\run-auto-rollback-policy.ps1 -ManifestPath ".\artifacts\desktop-rings.json" -MockSloStatuses "ok,ok,ok,ok" -MockErrorBudgetBurnRates "0.5,0.8,2.5,2.5" -MockReadinessValues "true,true,true,true" -CriticalWindowSeconds 4 -ReadinessRegressionWindowSeconds 2 -MaxErrorBudgetBurnRatePercent 2.0 -ExpectedTriggerReason error_budget_burn_rate -PollIntervalSeconds 1 -MaxObservationSeconds 8 -SeedPreviousVersion "0.0.1" -SeedIncidentVersion "0.0.2"
   ```
2. Validate drill report fields:
   - `observation.triggered = true`
   - `observation.trigger_reason` is one of `critical_window`, `error_budget_burn_rate`, `readiness_regression`
   - `decision.expected_reason_matched = true`
3. Confirm rollback execution succeeded (`rollback.executed = true`) and stable ring swapped to previous version.
4. Attach report JSON to incident evidence before promotion resumes.

### 3.38 Readiness-regression rollback guard

Symptoms:
- readiness flips to `ready=false` while SLO remains non-critical or ambiguous
- uncertainty whether rollback policy reacts quickly enough to sustained readiness regression

Actions:
1. Run readiness-focused rollback trigger check:
   ```powershell
   .\scripts\run-auto-rollback-policy.ps1 -ManifestPath ".\artifacts\desktop-rings.json" -MockSloStatuses "ok,degraded,degraded,degraded" -MockErrorBudgetBurnRates "0.5,0.8,0.9,0.9" -MockReadinessValues "true,false,false,false" -CriticalWindowSeconds 4 -ReadinessRegressionWindowSeconds 1 -MaxErrorBudgetBurnRatePercent 2.0 -ExpectedTriggerReason readiness_regression -PollIntervalSeconds 1 -MaxObservationSeconds 8 -SeedPreviousVersion "0.0.1" -SeedIncidentVersion "0.0.2"
   ```
2. Verify hard-trigger precedence in report:
   - `observation.trigger_reason = readiness_regression`
   - `decision.recommended_action = rollback_executed` (or `manual_rollback_required` in dry-run mode)
3. If expected trigger reason is not matched, keep release blocked and escalate policy defect immediately.
4. Resume rollout only after readiness is stable and a fresh rollback-policy drill passes with expected reason.

### 3.39 Disaster-recovery rehearsal pack

Symptoms:
- restore/snapshot/switch and RTO/RPO signals are green individually, but there is no single consolidated go/no-go artifact
- release evidence lacks deterministic proof that all disaster-recovery drills passed under one execution budget

Actions:
1. Execute consolidated rehearsal pack (release-gate profile):
   ```powershell
   .\scripts\run-disaster-recovery-rehearsal-pack.ps1 -Profile release-gate -MaxFailedDrills 0 -MaxTotalDurationSeconds 2400 -OutputFile "artifacts\p0-disaster-recovery-rehearsal-pack-release-gate.json" -EvidenceDir "artifacts\p0-disaster-recovery-rehearsal-pack-evidence-release-gate"
   ```
2. Validate release-block decision:
   - `success = true`
   - `decision.release_blocked = false`
   - `metrics.drills_failed = 0`
   - `metrics.duration_budget_exceeded = false`
3. Confirm per-drill evidence exists under `paths.evidence_files` and attach artifacts to change/incident record.
4. If report fails any threshold, keep release blocked and remediate before re-running release gate.

### 3.40 Scheduled DR evidence freshness

Symptoms:
- no recent periodic disaster-recovery rehearsal evidence is available
- operators cannot confirm recovery posture drift between releases

Actions:
1. Trigger scheduled pack manually if needed:
   ```powershell
   .\scripts\run-disaster-recovery-rehearsal-pack.ps1 -Profile scheduled -MaxTotalDurationSeconds 2400 -OutputFile "artifacts\p0-disaster-recovery-rehearsal-pack-scheduled.json" -EvidenceDir "artifacts\p0-disaster-recovery-rehearsal-pack-evidence-scheduled"
   ```
2. Verify report criteria remain green (`success=true`, `metrics.drills_failed=0`, `decision.release_blocked=false`).
3. Track report timestamp (`generated_at_utc`) and keep at least one recent successful scheduled report available during release review.
4. If scheduled report fails, open incident and hold promotion until fresh green evidence is produced.

### 3.41 Failure budget dashboard and release blocker

Symptoms:
- critical budget reports are distributed across multiple artifacts and release decision is not deterministic at a glance
- operators cannot quickly prove that all budget-relevant checks stayed green in the same gate execution

Actions:
1. Run aggregated failure-budget dashboard:
   ```powershell
   .\scripts\run-failure-budget-dashboard.ps1
   ```
2. Validate release-block signal:
   - `success = true`
   - `decision.release_blocked = false`
   - `metrics.reports_failed = 0`
   - `metrics.reports_missing = 0`
3. Confirm dashboard includes critical report set (load-profile, RTO/RPO, canary, auto-rollback, DR rehearsal pack).
4. If dashboard is red, treat as hard release blocker and remediate failing/missing budget report before promotion.

### 3.42 Safe-mode/degraded-state UX contract regression

Symptoms:
- dashboard does not clearly communicate safe-mode/degraded runtime state to operators
- mutating controls remain available while readiness is false, safe mode is active, or SLO is critical

Actions:
1. Execute the UX contract check:
   ```powershell
   .\scripts\run-safe-mode-ux-degradation-check.ps1
   ```
2. Validate report criteria:
   - `success = true`
   - `checks.missing_template_tokens = []`
   - `checks.missing_app_js_tokens = []`
   - `checks.release_gate_has_strict_flag = true`
   - `checks.ci_has_strict_flag = true`
3. If check fails, hold release and restore runtime rail + mutation-lock behavior before retry.

### 3.43 Accessibility baseline regression (keyboard/contrast/screen-reader smoke)

Symptoms:
- keyboard-first navigation flow regresses (skip link, focus handling, or shortcut handling)
- live-region or semantic labels drift, reducing screen-reader operability in operator workflows
- contrast baseline drifts below release thresholds in one or more visual presets

Actions:
1. Execute A11y harness check:
   ```powershell
   .\scripts\run-a11y-test-harness-check.ps1
   ```
2. Validate report criteria:
   - `success = true`
   - `checks.sr_only_label_count >= checks.min_sr_only_labels`
   - `checks.aria_live_count >= checks.min_aria_live_regions`
   - `checks.contrast_failures = []`
   - `checks.release_gate_has_strict_flag = true`
3. If check fails, treat as release blocker and restore the failing keyboard, semantics, or contrast contract before retrying gate.

## 4. Operational Defaults

- Keep single-instance protection enabled in production.
- Keep readiness endpoint as launch gate.
- Keep diagnostics export enabled for operators.
- Keep crash-loop protection enabled; only bypass for supervised triage.
- Prefer rollback over hotfix-in-place during active outage.
