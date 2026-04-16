# Goal Ops Console Production Runbook

This runbook is optimized for reliability-first releases of the desktop app and API.

## 1. Release Checklist

### 1.1 Pre-release gate

Run in repo root:

```powershell
.\scripts\release-gate.ps1 -StrictReleaseFreezePolicyDrill -StrictFileDatabaseProbe -StrictAutoRollbackPolicyDrill -StrictDesktopUpdateSafetyDrill -StrictRecoveryHardAbortDrill -StrictRecoveryIdempotenceDrill -StrictPowerLossDurabilityDrill -StrictWalCheckpointCrashDrill -StrictDiskPressureFaultInjectionDrill -StrictFsyncIoStallDrill -StrictSqliteRealFullDrill -StrictDbCorruptionQuarantineDrill -StrictStorageCorruptionHardeningDrill -StrictWorkflowLockResilienceDrill -StrictWorkflowSoakDrill -StrictWorkflowWorkerRestartDrill -StrictDbSafeModeWatchdogDrill -StrictInvariantMonitorWatchdogDrill -StrictEventConsumerRecoveryChaosDrill -StrictInvariantBurstDrill -StrictLongSoakBudgetDrill -StrictMigrationRehearsal -StrictUpgradeDowngradeCompatibilityDrill -StrictBackupRestoreDrill -StrictBackupRestoreStressDrill -StrictSnapshotRestoreCrashConsistencyDrill -StrictMultiDbAtomicSwitchDrill -StrictIncidentRollbackDrill -StrictReleaseGateRuntimeStabilityDrill -StrictCriticalDrillFlakeGate -StrictP0BurnInConsecutiveGreen -StrictP0RunbookContractCheck -StrictP0ReleaseEvidenceBundle
```

This gate covers:
- full `pytest` suite
- desktop smoke boot path
- `GET /system/readiness`
- `GET /system/slo` (`status` must be `ok`)
- release-freeze policy drill (sustained non-ok window or burn-rate spike freezes ring promotion path)
- auto-rollback-policy drill (`critical` sustained window triggers stable ring rollback path)
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
- release-gate runtime stability drill (critical-drill duration/variance budget sampling)
- P0 burn-in consecutive-green monitor (latest CI history must satisfy N consecutive fully green runs)
- P0 runbook contract check (release-gate/CI/runbook strict-flag and script-reference consistency)
- P0 release evidence bundle (single artifact with required P0 report files and status summary)

Manual migration rehearsal invocation (same thresholds as gate defaults):

```powershell
.\scripts\run-migration-rehearsal.ps1 -SmallRuns 500 -MediumRuns 2500 -LargeRuns 6000 -XLargeRuns 9000
```

Manual upgrade/downgrade compatibility drill invocation:

```powershell
.\scripts\run-upgrade-downgrade-compatibility-drill.ps1 -NMinus1Runs 800 -PayloadBytes 512
```

Manual auto-rollback policy invocation (live endpoint, stable ring):

```powershell
.\scripts\run-auto-rollback-policy.ps1 -BaseUrl "http://127.0.0.1:8000" -ManifestPath ".\artifacts\desktop-rings.json" -CriticalWindowSeconds 300 -PollIntervalSeconds 30 -MaxObservationSeconds 900
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

Manual critical drill flake gate invocation:

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

Manual release-gate runtime stability drill invocation:

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

Verify before release:
- CI checks green:
  - `Release Gate (Windows)`
  - `Pytest (Python 3.11)`
  - `Pytest (Python 3.12)`
  - `Desktop Smoke (Windows)`
- burn-in monitor report confirms threshold met (`metrics.consecutive_green >= metrics.required_consecutive`)
- runbook contract report confirms zero missing flags/scripts (`success=true`)
- release evidence bundle report confirms all required P0 reports present and successful (`success=true`)
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

The scheduled workflow [stability-canary.yml](/C:/Users/raffa/OneDrive/Documents/New%20project/.github/workflows/stability-canary.yml) runs a nightly trend check against [stability-canary-baseline.json](/C:/Users/raffa/OneDrive/Documents/New%20project/docs/stability-canary-baseline.json), including power-loss durability, DB corruption quarantine startup recovery, and upgrade/downgrade compatibility.

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
4. If status remains `critical`, enforce sustained-window rollback policy check:
   ```powershell
   .\scripts\run-auto-rollback-policy.ps1 -BaseUrl "http://127.0.0.1:8000" -ManifestPath ".\artifacts\desktop-rings.json" -CriticalWindowSeconds 300 -PollIntervalSeconds 30 -MaxObservationSeconds 900
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

## 4. Operational Defaults

- Keep single-instance protection enabled in production.
- Keep readiness endpoint as launch gate.
- Keep diagnostics export enabled for operators.
- Keep crash-loop protection enabled; only bypass for supervised triage.
- Prefer rollback over hotfix-in-place during active outage.
