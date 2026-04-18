# Goal Ops Console

Supervised MVP for the Goal Ops Console.

Stack:
- Python 3.11+
- FastAPI
- Jinja2
- SQLite
- Pytest
- pywebview (optional, desktop shell)
- PyInstaller (optional, desktop packaging)

## Project Path

```powershell
C:\Users\raffa\OneDrive\Documents\New project
```

## Quick Start

Open PowerShell and switch into the project directory:

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
```

Install dependencies if needed:

```powershell
python -m pip install fastapi jinja2 uvicorn pytest
```

Or install from project metadata (recommended):

```powershell
python -m pip install -e ".[test]"
```

Start the server:

```powershell
.\scripts\start-server.ps1
```

Open the dashboard:

[http://127.0.0.1:8000](http://127.0.0.1:8000)

## Desktop Shell (Preview)

Use this if you prefer a desktop window over a browser tab.

Install the optional desktop dependency:

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
python -m pip install -e ".[desktop]"
```

Start the desktop shell:

```powershell
.\scripts\start-desktop.ps1
```

Optional startup parameters:

```powershell
.\scripts\start-desktop.ps1 -DatabaseUrl "goal_ops.db" -Width 1600 -Height 1000
.\scripts\start-desktop.ps1 -Port 8010
.\scripts\start-desktop.ps1 -Maximized
.\scripts\start-desktop.ps1 -MinWidth 1200 -MinHeight 800
.\scripts\start-desktop.ps1 -NoWindowState
.\scripts\start-desktop.ps1 -WindowStatePath ".\custom-window-state.json"
.\scripts\start-desktop.ps1 -InstanceLockPath ".\goal-ops-desktop.lock"
.\scripts\start-desktop.ps1 -AllowMultipleInstances
.\scripts\start-desktop.ps1 -CrashStatePath ".\desktop-crash-state.json"
.\scripts\start-desktop.ps1 -CrashLoopMaxCrashes 3 -CrashLoopWindowSeconds 600
.\scripts\start-desktop.ps1 -AllowCrashLoop
```

Behavior:
- starts an embedded local FastAPI server (`127.0.0.1`)
- opens the same dashboard UI in a native window via `pywebview`
- remembers window size/position across launches (disable with `-NoWindowState`)
- enforces single-instance lock by default (disable with `-AllowMultipleInstances`)
- blocks startup after repeated crash loops (bypass once with `-AllowCrashLoop`)
- writes crash reports to `%USERPROFILE%\.goal_ops_console\diagnostics` (or `GOAL_OPS_DIAGNOSTICS_DIR`)
- shuts down the embedded server when the desktop window closes

## Operator Command Bar (UI)

The dashboard header now includes a command bar for faster operator workflows:

- `Global filter` searches across goals, tasks, events, audit, faults and queue rows.
- Keyboard shortcut `/` focuses the global filter input.
- `Auto-refresh` toggle pauses or resumes the 5-second refresh loop.
- `Density` toggle switches between `Comfy` and `Compact` layout.
- `Visual` toggle cycles through `Warm`, `Graphite`, and `Signal` presets.
- `Quick jump` buttons scroll directly to major panels (`Goals`, `Tasks`, `Operator`, `Events`, `Trace`, `Audit`, `Faults`, `Health`, `States`).
- Desktop mode shortcuts: `Ctrl+1/2/3` (preset), `Ctrl+Shift+F` (focus global filter), `Ctrl+Shift+R` (manual refresh).

## Build Windows Desktop EXE (Preview)

Install desktop runtime + build tooling:

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
python -m pip install -e ".[desktop,desktop-build]"
```

Build `onedir` (recommended for first packaging pass):

```powershell
.\scripts\build-desktop.ps1 -Mode onedir
```

Build `onefile`:

```powershell
.\scripts\build-desktop.ps1 -Mode onefile
```

Package a desktop distribution bundle (release-ready artifacts):

```powershell
.\scripts\package-desktop-release.ps1 -Version "0.1.0" -Channel stable -Mode both -OutputDir artifacts
```

Optional signing during packaging:

```powershell
.\scripts\package-desktop-release.ps1 `
  -Version "0.1.0" `
  -Channel stable `
  -Mode both `
  -Sign `
  -SignToolPath "C:\Program Files (x86)\Windows Kits\10\bin\x64\signtool.exe" `
  -CertThumbprint "<CERT_THUMBPRINT>" `
  -TimeStampUrl "https://timestamp.digicert.com"
```

Alternative signing via PFX file:

```powershell
.\scripts\package-desktop-release.ps1 `
  -Version "0.1.0" `
  -Channel stable `
  -Mode both `
  -Sign `
  -PfxPath ".\codesign\goal-ops.pfx" `
  -PfxPassword "<PFX_PASSWORD>" `
  -TimeStampUrl "https://timestamp.digicert.com"
```

Optional:

```powershell
.\scripts\build-desktop.ps1 -Mode onedir -InstallDependencies
.\scripts\build-desktop.ps1 -Mode onedir -Name "GoalOpsConsole"
.\scripts\build-desktop.ps1 -Mode onefile -IconPath ".\assets\goal-ops.ico"
.\scripts\build-desktop.ps1 -DryRun
```

Output paths:
- `onedir`: `dist\GoalOpsConsole\GoalOpsConsole.exe`
- `onefile`: `dist\GoalOpsConsole.exe`

Distribution bundle outputs (via `package-desktop-release.ps1`):
- `GoalOpsConsole-onedir-<version>.zip`
- `GoalOpsConsole-onefile-<version>.exe`
- `GoalOpsConsole-update-helper-<version>.ps1` (hash/signature/fallback installer helper)
- `GoalOpsConsole-install-<version>.ps1` (portable installer script)
- `desktop-update-manifest.json` (auto-update feed preparation)
- `desktop-rings.json` (ring targets + rollback pointer metadata)
- `SHA256SUMS.txt`

Optional rollout-ring target override during packaging:

```powershell
.\scripts\package-desktop-release.ps1 `
  -Version "0.2.3" `
  -Channel stable `
  -RolloutRing stable `
  -Mode both `
  -OutputDir artifacts
```

Manage ring promotion / rollback without rebuilding binaries:

```powershell
.\scripts\manage-desktop-rings.ps1 -ManifestPath ".\artifacts\desktop-rings.json" -Action show
.\scripts\manage-desktop-rings.ps1 -ManifestPath ".\artifacts\desktop-rings.json" -Action promote -Ring canary -Version "0.2.4"
.\scripts\manage-desktop-rings.ps1 -ManifestPath ".\artifacts\desktop-rings.json" -Action promote -Ring stable -Version "0.2.3"
.\scripts\manage-desktop-rings.ps1 -ManifestPath ".\artifacts\desktop-rings.json" -Action rollback -Ring stable
```

Note:
- `pywebview` on Windows requires WebView2 runtime.
- In `onefile` mode startup can be slower because the executable self-extracts before launch.

## Desktop Build In GitHub Actions

Desktop artifacts can now be built in CI via workflow:

- [desktop-build.yml](/C:/Users/raffa/OneDrive/Documents/New%20project/.github/workflows/desktop-build.yml)

Triggers:
- manual run (`workflow_dispatch`) with mode: `onedir`, `onefile`, or `both`
- push of tags matching `v*` (for release-style builds)

Published artifacts:
- `GoalOpsConsole-onedir-<version>.zip` (if `onedir` was built)
- `GoalOpsConsole-onefile-<version>.exe` (if `onefile` was built)
- `GoalOpsConsole-update-helper-<version>.ps1` (if `onefile` was built)
- `GoalOpsConsole-install-<version>.ps1` (if `onefile` was built)
- `desktop-update-manifest.json` (version/channel + artifact hashes)
- `SHA256SUMS.txt` (checksums for uploaded files)

Optional CI signing (GitHub Secrets):
- `DESKTOP_SIGN_PFX_BASE64`: base64-encoded PFX certificate payload.
- `DESKTOP_SIGN_PFX_PASSWORD`: password for the PFX file.
- `DESKTOP_SIGN_CERT_THUMBPRINT`: optional fallback for self-hosted runners with cert in store.
- `DESKTOP_SIGN_TIMESTAMP_URL`: optional timestamp URL override (default: `https://timestamp.digicert.com`).

Signing behavior in CI:
- If `DESKTOP_SIGN_PFX_BASE64` + `DESKTOP_SIGN_PFX_PASSWORD` are present, artifacts are signed via PFX.
- Else, if `DESKTOP_SIGN_CERT_THUMBPRINT` is present, artifacts are signed via certificate thumbprint.
- Else, packaging runs unsigned (same artifact layout).

GitHub release publishing on tag builds:
- On `v*` tag pushes, workflow also creates/updates a GitHub Release and uploads all `artifacts/*` files as release assets.
- Re-running the workflow for the same tag overwrites release asset files (`overwrite_files: true`).

Release trigger example:

```powershell
git tag v0.2.0
git push origin v0.2.0
```

## Stop And Restart

Stop the server in the terminal where `uvicorn` is running:

```powershell
Ctrl + C
```

Start it again:

```powershell
.\scripts\start-server.ps1
```

## Reset The Local Database

Use this when you want a clean manual test run.

1. Stop the server.
2. Run:

```powershell
.\scripts\reset-db.ps1
```

3. Start the server again.

## Automated Thin Slice Test

The repository includes a PowerShell smoke test for the thin vertical slice:

[test-thin-slice.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/test-thin-slice.ps1)

Run it from anywhere:

```powershell
powershell -ExecutionPolicy Bypass -File "C:\Users\raffa\OneDrive\Documents\New project\scripts\test-thin-slice.ps1"
```

Run it from the project directory:

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\test-thin-slice.ps1
```

Expected outcome:
- multiple `[OK]` lines
- `Thin Slice erfolgreich getestet.`

## Manual GUI Thin Slice

Use these example values:

- Goal title: `Launch website`
- Description: `GUI thin-slice test`
- Urgency: `0,9`
- Value: `0,8`
- Deadline score: `0,4`
- Task title: `Prepare landing page`

Flow:

1. Create a goal.
   Expected:
   Goal appears with state `draft`.

2. Click `Activate`.
   Expected:
   Goal changes to `active`.

3. Create a task.
   Expected:
   Task appears with state `pending`.

4. Click `Skill Fail` once.
   Expected:
   Task changes to `failed`, retry count becomes `1`, goal stays `active`.

5. Click `Skill Fail` again.
   Expected:
   Task changes to `poison`, retry count becomes `2`, goal changes to `escalation_pending`.

6. Click `HITL Approve`.
   Expected:
   Goal changes back to `active`.

7. Open the event trace.
   Expected events:
   `goal.created`, `goal.activated`, `task.created`, `task.started`, `task.failed`, `task.retried`, `task.failed`, `task.poison.detected`, `goal.escalation_pending`, `goal.hitl_approved`

## Operator Controls

The dashboard now includes an `Operator Controls` section for supervised Phase 2 operations.

- `Age Queue`
  Ages every queued or active queue entry once and updates `wait_cycles` and effective priority.
- `Pick Next Goal`
  Runs the scheduler pick logic and activates the highest-priority queued goal.
- `Drain Events`
  Manually runs one consumer batch and marks pending events as processed for the given consumer id.
- `Reclaim Stuck`
  Resets timed-out `processing` entries back to `pending` for the given consumer id.
- `Run Retention Cleanup`
  Removes old `events`, `event_processing` and `failure_log` rows based on retention policy.
- `Flow Trace` (new section)
  Loads the full goal event chain and groups retry attempts per task.
- `Dead-Letter / Fault Explorer` (new section)
  Lists failure records with filters (`failure_type`, `task_status`, `goal_id`, `error_hash`) and a dead-letter toggle.
  Includes supervised remediation actions:
  - `Retry Task` creates a new pending retry task for a dead-letter failure.
  - `Requeue Goal` transitions `blocked` / `escalation_pending` goals back to `active`.
  - `Resolve` marks a failure as operator-resolved without changing task/goal state.
  - `Resolve Filtered` applies `Resolve` to all currently filtered fault rows (bounded by a limit).
  - `Dry run` previews remediation without writing state.
  Supports filtering by `failure_status` (`recorded`, `retry_queued`, `goal_requeued`, `resolved`) in addition to existing filters.
- `Audit Log` (new section)
  Shows recent mutating API operations with status and request details.
- `Metrics Hooks` (in System Health)
  Shows live instrumentation counters for HTTP traffic, transitions, events and throttling.
- `Visual Mode` (new toggle in command bar)
  Cycles UI styling across `Warm`, `Graphite`, and `Signal` while keeping all workflows identical.
- `Quick Jump` (extended)
  Includes direct jumps for `Trace`, `Audit`, and `States` sections.

The queue table in this section shows:
- goal state
- queue status
- wait cycles
- base vs. effective priority

Backpressure behavior:

- Event writes are throttled when pending backlog reaches the configured cap.
- Goal creation is throttled when the goal queue reaches its entry limit.
- API returns `429` with `Retry-After` and `retry_after_seconds` for throttled operations.
- Scheduler operations (`/system/scheduler/age` and `/system/scheduler/pick`) are also protected by backlog checks.

Retention + backpressure endpoints:

- `GET /system/backpressure`
- `POST /system/maintenance/retention`

Observability endpoints:

- `GET /system/metrics`
- `GET /system/audit`
- `GET /system/audit/integrity`
- `GET /system/slo`
- `GET /system/safe-mode`
- `POST /system/safe-mode/enable`
- `POST /system/safe-mode/disable`
- `GET /system/invariants`
- `GET /system/database/integrity?mode=quick|full`
- `GET /system/faults`
- `GET /system/faults/summary`
- `POST /system/faults/resolve_bulk`
- `POST /system/faults/{failure_id}/retry`
- `POST /system/faults/{failure_id}/requeue_goal`
- `POST /system/faults/{failure_id}/resolve`
- `GET /events/trace/{goal_id}`

## Run The Test Suite

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-tests.ps1
```

## Run Release Gate

Reliability-focused pre-release gate (tests + desktop smoke + readiness + DB integrity + SLO alert check + security config hardening check + audit trail hardening check + security CI lane check + alert-routing/on-call runbook automation check + incident drill automation check + load profile framework check + canary guardrails check + RTO/RPO assertion suite check + release-freeze policy drill + auto-rollback hard-trigger drill + desktop-update-safety drill + recovery hard-abort drill + recovery idempotence drill + power-loss durability drill + WAL checkpoint crash drill + disk-pressure fault-injection drill + fsync/I/O stall drill + real SQLite FULL saturation drill + DB corruption quarantine drill + storage corruption hardening drill + workflow lock-resilience drill + workflow soak drill + workflow worker restart drill + DB safe-mode watchdog drill + invariant monitor watchdog drill + event-consumer recovery chaos drill + invariant burst drill + long soak budget drill + migration state + migration rehearsal on S/M/L/XL DB copies + upgrade/downgrade compatibility drill + backup/restore drill + backup/restore stress drill + snapshot/restore crash-consistency drill + multi-db atomic-switch drill + incident/rollback drill under burst load + disaster-recovery rehearsal pack + failure budget dashboard + safe-mode UX degradation check + A11y test harness check + release-gate runtime stability drill + critical drill flake gate + P0 burn-in consecutive-green monitor + P0 runbook contract check + P0 report schema contract check + P0 release evidence bundle + P0 closure go/no-go report + release-gate evidence freshness check + release-gate evidence hash manifest check + release-gate step timing schema check + release-gate performance history check + release-gate performance budget check + release-gate stability final readiness check + release-gate staging soak readiness check + release-gate RC canary rollout check + release-gate evidence lineage check + release-gate production readiness certification + release-gate SLO burn-rate v2 check + release-gate deploy rehearsal check + release-gate chaos matrix continuous check + release-gate supply-chain artifact trust check + release-gate operations handoff readiness check + release-gate evidence attestation check + release-gate release-train readiness check + release-gate production final attestation):

The gate starts with a preflight cleanup that removes stale `artifacts\*-release-gate.json` files and prior release-gate evidence directories so each run produces a deterministic evidence set.

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\release-gate.ps1 -StrictSecurityConfigHardeningCheck -StrictAuditTrailHardeningCheck -StrictSecurityCiLaneCheck -StrictAlertRoutingOnCallCheck -StrictIncidentDrillAutomationCheck -StrictLoadProfileFrameworkCheck -StrictCanaryGuardrailCheck -StrictRtoRpoAssertionCheck -StrictReleaseFreezePolicyDrill -StrictFileDatabaseProbe -StrictAutoRollbackPolicyDrill -StrictDesktopUpdateSafetyDrill -StrictRecoveryHardAbortDrill -StrictRecoveryIdempotenceDrill -StrictPowerLossDurabilityDrill -StrictWalCheckpointCrashDrill -StrictDiskPressureFaultInjectionDrill -StrictFsyncIoStallDrill -StrictSqliteRealFullDrill -StrictDbCorruptionQuarantineDrill -StrictStorageCorruptionHardeningDrill -StrictWorkflowLockResilienceDrill -StrictWorkflowSoakDrill -StrictWorkflowWorkerRestartDrill -StrictDbSafeModeWatchdogDrill -StrictInvariantMonitorWatchdogDrill -StrictEventConsumerRecoveryChaosDrill -StrictInvariantBurstDrill -StrictLongSoakBudgetDrill -StrictMigrationRehearsal -StrictUpgradeDowngradeCompatibilityDrill -StrictBackupRestoreDrill -StrictBackupRestoreStressDrill -StrictSnapshotRestoreCrashConsistencyDrill -StrictMultiDbAtomicSwitchDrill -StrictIncidentRollbackDrill -StrictDisasterRecoveryRehearsalPack -StrictFailureBudgetDashboard -StrictSafeModeUxDegradationCheck -StrictA11yTestHarnessCheck -StrictReleaseGateRuntimeStabilityDrill -StrictCriticalDrillFlakeGate -StrictP0BurnInConsecutiveGreen -StrictP0RunbookContractCheck -StrictP0ReportSchemaContractCheck -StrictP0ReleaseEvidenceBundle -StrictP0ClosureReport -StrictReleaseGateEvidenceFreshnessCheck -StrictReleaseGateEvidenceHashManifestCheck -StrictReleaseGateStepTimingSchemaCheck -StrictReleaseGatePerformanceHistoryCheck -StrictReleaseGatePerformanceBudgetCheck -StrictReleaseGateStabilityFinalReadinessCheck -StrictReleaseGateStagingSoakReadinessCheck -StrictReleaseGateRcCanaryRolloutCheck -StrictReleaseGateEvidenceLineageCheck -StrictReleaseGateProductionReadinessCertificationCheck -StrictReleaseGateSloBurnRateV2Check -StrictReleaseGateDeployRehearsalCheck -StrictReleaseGateChaosMatrixContinuousCheck -StrictReleaseGateSupplyChainArtifactTrustCheck -StrictReleaseGateOperationsHandoffReadinessCheck -StrictReleaseGateEvidenceAttestationCheck -StrictReleaseGateReleaseTrainReadinessCheck -StrictReleaseGateProductionFinalAttestationCheck
```

Standalone backup/restore drill:

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-backup-restore-drill.ps1
```

Standalone backup/restore stress drill (round-based load + restore idempotence):

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-backup-restore-stress-drill.ps1 -Rounds 3 -GoalsPerRound 120 -TasksPerGoal 2 -WorkflowRunsPerRound 24
```

Standalone snapshot/restore crash-consistency drill (fault matrix with hard-abort copy simulation):

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-snapshot-restore-crash-consistency-drill.ps1 -SeedRows 96 -PayloadBytes 128
```

Standalone multi-db atomic-switch drill (pointer crash + candidate-integrity reject + rollback switch):

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-multi-db-atomic-switch-drill.ps1 -SeedRows 96 -PayloadBytes 128
```

Standalone disaster-recovery rehearsal pack (consolidated restore/snapshot/switch/RTO-RPO evidence):

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-disaster-recovery-rehearsal-pack.ps1 -Profile scheduled -MaxTotalDurationSeconds 2400
```

Standalone failure budget dashboard (aggregated release blocker signal):

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-failure-budget-dashboard.ps1
```

Standalone safe-mode/degradation UX contract check (runtime rail + mutation lock + gate wiring):

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-safe-mode-ux-degradation-check.ps1
```

Standalone A11y test harness check (keyboard + screen-reader smoke + contrast baseline):

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-a11y-test-harness-check.ps1
```

Standalone migration rehearsal drill:

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-migration-rehearsal.ps1 -SmallRuns 500 -MediumRuns 2500 -LargeRuns 6000 -XLargeRuns 9000
```

Standalone upgrade/downgrade compatibility drill:

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-upgrade-downgrade-compatibility-drill.ps1 -NMinus1Runs 800 -PayloadBytes 512
```

Standalone auto-rollback hard-trigger check (sustained critical OR burn-rate spike OR readiness regression):

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-auto-rollback-policy.ps1 -BaseUrl "http://127.0.0.1:8000" -ManifestPath ".\artifacts\desktop-rings.json" -CriticalWindowSeconds 300 -ReadinessRegressionWindowSeconds 120 -MaxErrorBudgetBurnRatePercent 2.0 -ExpectedTriggerReason auto -PollIntervalSeconds 30 -MaxObservationSeconds 900
```

Standalone security config hardening check (production profile policy):

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-security-config-hardening-check.ps1 -OperatorAuthRequired -OperatorAuthToken "replace-with-long-secret-token" -StartupCorruptionRecoveryEnabled
```

Standalone audit trail hardening check (hash-chain integrity + tamper detection + retention policy):

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-audit-trail-hardening-check.ps1 -AuditRetentionDays 365 -MinAuditRetentionDays 90 -SeedEntries 8
```

Standalone security CI lane check (pip-audit + bandit + SBOM + fail policy):

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-security-ci-lane-check.ps1 -MaxDependencyVulnerabilities 0 -MaxSastHigh 0 -MaxSastMedium 200
```

Standalone alert-routing/on-call runbook automation check:

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-alert-routing-oncall-check.ps1 -MockSloStatus critical -MockAlertCount 2 -RoutingPolicyFile "docs\oncall-alert-routing-policy.json"
```

Standalone incident drill automation check:

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-incident-drill-automation-check.ps1 -MockReport -MockDaysSinceTabletop 7 -MockDaysSinceTechnical 3 -PolicyFile "docs\incident-drill-automation-policy.json"
```

Standalone load profile framework check:

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-load-profile-framework-check.ps1 -ProfileFile "docs\load-profile-catalog.json" -ProfileName "prod_like_ci_smoke" -ProfileVersion "1.0.0"
```

Standalone canary guardrails check (staged promotion + automatic halt/freeze on threshold breach):

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-canary-guardrails-check.ps1 -PolicyFile "docs\canary-guardrails-policy.json" -ExpectedDecision halt -MockSloStatuses "ok,ok,critical,critical" -MockErrorBudgetBurnRates "0.5,0.8,2.5,2.5"
```

Standalone RTO/RPO assertion suite:

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-rto-rpo-assertion-suite.ps1 -PolicyFile "docs\rto-rpo-assertion-policy.json" -SeedRows 48 -TailWriteRows 12 -MaxRtoSeconds 20 -MaxRpoRowsLost 96
```

Standalone release-freeze policy check (live service):

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-release-freeze-policy.ps1 -BaseUrl "http://127.0.0.1:8000" -ManifestPath ".\artifacts\desktop-rings.json" -NonOkWindowSeconds 300 -PollIntervalSeconds 30 -MaxObservationSeconds 900
```

Standalone desktop-update safety drill:

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-desktop-update-safety-drill.ps1
```

Standalone recovery hard-abort drill:

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-recovery-hard-abort-drill.ps1
```

Standalone recovery idempotence drill (restart cycles must not duplicate startup recovery):

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-recovery-idempotence-drill.ps1 -RecoveryCycles 3
```

Standalone power-loss durability drill:

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-power-loss-durability-drill.ps1 -TransactionRows 240 -PayloadBytes 256
```

Standalone WAL checkpoint crash drill (hard-abort before checkpoint completion + recovery checkpoint):

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-wal-checkpoint-crash-drill.ps1 -Rows 240 -PayloadBytes 1024 -CheckpointMode TRUNCATE
```

Standalone disk-pressure fault-injection drill (SQLITE_FULL, IOERR, readonly/permission-flip simulation):

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-disk-pressure-fault-injection-drill.ps1 -FaultInjections 2
```

Standalone fsync/I/O stall drill (bounded write stall + I/O error degradation/recovery):

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-fsync-io-stall-drill.ps1 -FaultInjections 2 -StallSeconds 0.35 -MaxStallRequestSeconds 3.0
```

Standalone real SQLite FULL drill (actual `max_page_count` saturation + recovery):

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-sqlite-real-full-drill.ps1 -PayloadBytes 8192 -MaxWriteAttempts 240 -MaxPageGrowth 24 -RecoveryPageGrowth 160
```

Standalone DB corruption quarantine drill:

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-db-corruption-quarantine-drill.ps1 -CorruptionBytes 256
```

Standalone storage corruption hardening drill (WAL/JOURNAL anomaly files + startup quarantine recovery):

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-storage-corruption-hardening-drill.ps1 -CorruptionBytes 192 -Rows 80 -PayloadBytes 128
```

Standalone workflow lock-resilience drill:

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-workflow-lock-resilience-drill.ps1
```

Standalone workflow soak drill:

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-workflow-soak-drill.ps1 -RunCount 40
```

Standalone workflow worker restart drill:

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-workflow-worker-restart-drill.ps1
```

Standalone event-consumer recovery chaos drill:

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-event-consumer-recovery-chaos-drill.ps1
```

Standalone invariant burst drill:

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-invariant-burst-drill.ps1
```

Standalone long soak budget drill (15-minute default):

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-long-soak-budget-drill.ps1 -DurationSeconds 900
```

Standalone DB safe-mode watchdog drill:

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-db-safe-mode-watchdog-drill.ps1 -LockErrorInjections 4
```

Standalone invariant monitor watchdog drill:

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-invariant-monitor-watchdog-drill.ps1 -TimeoutSeconds 8
```

Standalone SLO alert check:

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-slo-alert-check.ps1 -AllowedStatus ok
```

Standalone incident/rollback drill:

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-incident-rollback-drill.ps1 -LoadRequests 30
```

Standalone critical drill flake gate (repeat critical storage + Stage-D safe-mode/A11y checks):

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-critical-drill-flake-gate.ps1 -Repeats 2 -MaxFailedIterations 0
```

Standalone release-gate runtime stability drill (duration + variance budget across critical storage + Stage-D UX/A11y samples):

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-release-gate-runtime-stability-drill.ps1 -Samples 2 -RepeatsPerSample 1
```

Standalone release-gate evidence freshness check:

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-release-gate-evidence-freshness-check.ps1
```

Standalone release-gate evidence hash manifest check:

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-release-gate-evidence-hash-manifest-check.ps1
```

Standalone release-gate step timing schema check:

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-release-gate-step-timing-schema-check.ps1
```

Standalone release-gate performance history check:

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-release-gate-performance-history-check.ps1
```

Standalone release-gate performance budget check (step-runtime policy budget + trend summary):

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-release-gate-performance-budget-check.ps1
```

Standalone release-gate stability final readiness check:

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-release-gate-stability-final-readiness.ps1
```

Standalone master burn-in window check (3-5 master runs + flake cleanup signal):

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-release-gate-master-burnin-window-check.ps1 -MinConsecutive 3 -TargetConsecutive 5
```

Standalone release-gate performance policy calibration check:

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-release-gate-performance-policy-calibrate.ps1 -StepTimingsGlob "artifacts/release-gate-step-timings*.json" -MinSamples 3 -WriteUpdates
```

Standalone release-gate staging soak readiness check:

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-release-gate-staging-soak-readiness-check.ps1
```

Standalone release-gate RC canary rollout check:

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-release-gate-rc-canary-rollout-check.ps1 -PolicyFile "docs\\release-candidate-rollout-policy.json" -CandidateVersion "0.0.2-rc1"
```

Standalone release-gate evidence lineage check:

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-release-gate-evidence-lineage-check.ps1
```

Standalone release-gate production readiness certification:

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-release-gate-production-readiness-certification-check.ps1 -RequiredConsecutive 10
```

Standalone release-gate SLO burn-rate v2 check:

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-release-gate-slo-burn-rate-v2-check.ps1 -PolicyFile "docs\\release-gate-slo-burn-rate-v2-policy.json"
```

Standalone release-gate deploy rehearsal check:

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-release-gate-deploy-rehearsal-check.ps1 -PolicyFile "docs\\release-gate-deploy-rehearsal-policy.json"
```

Standalone release-gate chaos matrix continuous check:

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-release-gate-chaos-matrix-continuous-check.ps1 -PolicyFile "docs\\release-gate-chaos-matrix-policy.json"
```

Standalone release-gate supply-chain artifact trust check:

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-release-gate-supply-chain-artifact-trust-check.ps1 -PolicyFile "docs\\release-gate-artifact-trust-policy.json"
```

Standalone release-gate operations handoff readiness check:

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-release-gate-operations-handoff-readiness-check.ps1
```

Standalone release-gate evidence attestation check:

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-release-gate-evidence-attestation-check.ps1 -PolicyFile "docs\\release-gate-evidence-attestation-policy.json"
```

Standalone release-gate release-train readiness check:

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-release-gate-release-train-readiness-check.ps1
```

Standalone release-gate production final attestation check:

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-release-gate-production-final-attestation-check.ps1 -RequiredConsecutive 10
```

Standalone canary determinism + flake intelligence check (repeat Stage-D UX/A11y probes with quarantine-aware flake budget):

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-canary-determinism-flake-check.ps1 -ProbeRepeats 2
```

Standalone P0 burn-in consecutive-green monitor (CI history hard gate):

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-p0-burnin-consecutive-green.ps1 -RequiredConsecutive 10
```

Standalone P0 runbook contract check (release-gate/CI/runbook + canary-baseline + closure-metric token consistency):

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-p0-runbook-contract-check.ps1
```

Standalone P0 report schema contract check (baseline `label/success` schema on required release-gate evidence reports):

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-p0-report-schema-contract-check.ps1
```

Standalone P0 release evidence bundle (aggregate all `*-release-gate.json` evidence into one manifest and optionally enforce one label contract):

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-p0-release-evidence-bundle.ps1
```

Standalone P0 closure go/no-go report (final readiness signal from bundled evidence):

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\run-p0-closure-report.ps1
```

Current result during implementation:

```text
run `.\scripts\run-tests.ps1` (latest local count may change as features are added)
```

## CI And PR Guardrails

GitHub Actions now runs `pytest` automatically for:

- pushes to `master`
- pull requests targeting `master`
- manual dispatch (`workflow_dispatch`)

Workflow file:
[ci.yml](/C:/Users/raffa/OneDrive/Documents/New%20project/.github/workflows/ci.yml)

Release-gate CI strict flags + release evidence upload paths are now sourced from:
- [release-gate-registry.json](/C:/Users/raffa/OneDrive/Documents/New%20project/docs/release-gate-registry.json)
- [release-gate-registry.lock.json](/C:/Users/raffa/OneDrive/Documents/New%20project/docs/release-gate-registry.lock.json)

P0 report schema contract defaults (`required_top_level_keys`, `required_decision_keys`, `required_label`) and
P0 release evidence bundle defaults (`required_label`) are also sourced from the same registry.

Sync/verify command:
```powershell
python .\scripts\release-gate-registry-sync.py
```
CI report command (writes an auditable sync report artifact):
```powershell
.\scripts\run-release-gate-registry-sync.ps1 -OutputFile artifacts\release-gate-registry-sync-ci.json
```
Write/sync command:
```powershell
python .\scripts\release-gate-registry-sync.py --write
```
The check validates CI strict flags/artifact uploads and registry wiring in `scripts\release-gate.ps1` plus P0 schema/bundle wrappers.
It also enforces cross-contract consistency (required labels + artifact coverage across registry sections).

Release Gate workflow artifact includes `p0-release-evidence-bundle` (manifest + copied evidence reports, including safe-mode/A11y/runtime/flake stage outputs, + closure report), Stage-L/M evidence artifacts (`release-gate-evidence-freshness-release-gate.json`, `release-gate-evidence-hash-manifest-release-gate.json`, `release-gate-evidence-manifest-release-gate.json`), Stage-N/O timing-history artifacts (`release-gate-step-timing-schema-release-gate.json`, `release-gate-performance-history-release-gate.json`), Stage-K/P artifacts (`release-gate-step-timings-release-gate.json`, `release-gate-performance-budget-release-gate.json`, `release-gate-stability-final-readiness-release-gate.json`), Stage-Q/R readiness artifacts (`release-gate-staging-soak-readiness-release-gate.json`, `release-gate-rc-canary-rollout-release-gate.json`), Stage-S/T readiness artifacts (`release-gate-evidence-lineage-release-gate.json`, `release-gate-production-readiness-certification-release-gate.json`), Stage-U/AB expanded readiness artifacts (`release-gate-slo-burn-rate-v2-release-gate.json`, `release-gate-deploy-rehearsal-release-gate.json`, `release-gate-chaos-matrix-continuous-release-gate.json`, `release-gate-supply-chain-artifact-trust-release-gate.json`, `release-gate-operations-handoff-readiness-release-gate.json`, `release-gate-evidence-attestation-release-gate.json`, `release-gate-release-train-readiness-release-gate.json`, `release-gate-production-final-attestation-release-gate.json`), and Stage-AC/AJ cutover-to-sustainability artifacts (`release-gate-production-cutover-readiness-release-gate.json`, `release-gate-hypercare-activation-release-gate.json`, `release-gate-rollback-trigger-integrity-release-gate.json`, `release-gate-post-cutover-finalization-release-gate.json`, `release-gate-post-release-watch-release-gate.json`, `release-gate-steady-state-certification-release-gate.json`, `release-gate-post-release-continuity-release-gate.json`, `release-gate-production-sustainability-certification-release-gate.json`).

Desktop workflow file:
[desktop-build.yml](/C:/Users/raffa/OneDrive/Documents/New%20project/.github/workflows/desktop-build.yml)

Nightly stability canary workflow:
[stability-canary.yml](/C:/Users/raffa/OneDrive/Documents/New%20project/.github/workflows/stability-canary.yml)

Nightly burn-in monitor workflow:
[p0-burnin-monitor.yml](/C:/Users/raffa/OneDrive/Documents/New%20project/.github/workflows/p0-burnin-monitor.yml)

Weekly master release-gate burn-in workflow:
[master-release-gate-burnin.yml](/C:/Users/raffa/OneDrive/Documents/New%20project/.github/workflows/master-release-gate-burnin.yml)

Nightly disaster-recovery rehearsal workflow:
[disaster-recovery-rehearsal.yml](/C:/Users/raffa/OneDrive/Documents/New%20project/.github/workflows/disaster-recovery-rehearsal.yml)

Recommended branch protection on `master`:

1. Open repository settings on GitHub.
2. Go to `Branches` -> `Branch protection rules`.
3. Add/update rule for `master`:
4. Enable `Require a pull request before merging`.
5. Enable `Require status checks to pass before merging`.
6. Select required check: `Pytest (Python 3.11)` and `Pytest (Python 3.12)`.
7. Select required check: `Desktop Smoke (Windows)`.
8. Select required check: `Release Gate (Windows)`.

Local desktop smoke command:

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
python .\scripts\desktop-smoke.py
```

## Nightly Stability Canary

Run the full canary profile locally (includes release-freeze policy, power-loss durability, DB corruption quarantine, upgrade/downgrade compatibility, watchdog drills, recovery chaos, invariant burst, Stage-D safe-mode UX/A11y checks, canary determinism + flake-intelligence checks with quarantine policy, P0 burn-in consecutive-green fixture checks, P0 report-schema contract validation on required canary evidence reports, P0 runbook-contract consistency checks, P0 release-evidence bundle checks, P0 closure go/no-go checks, and long soak budgets). Missing baseline drill entries are treated as regressions.

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
python .\scripts\stability-canary.py --baseline-file .\docs\stability-canary-baseline.json --long-soak-duration-seconds 120 --output-file .\.tmp\stability-canary-report.json
```

## Production Runbook

Reliability-first release and incident handling guide:

- [production-runbook.md](/C:/Users/raffa/OneDrive/Documents/New%20project/docs/production-runbook.md)

## Troubleshooting

### Path With Spaces

If PowerShell rejects the project path, wrap it in quotes:

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
```

Do not type the prompt itself. Use:

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
```

Not:

```powershell
PS C:\Users\raffa> Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
```

### Server Not Reachable

If the smoke test says the server is not reachable:

- make sure `uvicorn` is already running
- keep the server terminal open
- check that the URL is `http://127.0.0.1:8000`

### `pywebview is required for desktop mode`

If desktop mode fails with this message, install the optional extra:

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
python -m pip install -e ".[desktop]"
```

### `No module named PyInstaller`

If desktop packaging fails with this error, install the optional build extra:

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
python -m pip install -e ".[desktop,desktop-build]"
```

### `ModuleNotFoundError: No module named 'goal_ops_console'`

This means `uvicorn` was started from the wrong directory or without the app dir.

Use:

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\start-server.ps1
```

### Old Goals Or Tasks Still Visible

`goal_ops.db` keeps manual test data between runs.

Reset it with:

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\reset-db.ps1
```

Then restart the server and hard-reload the browser.

### GUI Looks Wrong Or Buttons Are Missing

If the browser still shows an old layout or old JavaScript behavior:

- press `Ctrl+F5`
- confirm the server has reloaded after the latest code change
- if needed, close and reopen the browser tab

### Script Not Found

If `.\scripts\test-thin-slice.ps1` is not found, you are probably not in the project directory.

Either switch directories first:

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
.\scripts\test-thin-slice.ps1
```

Or use the absolute path:

```powershell
powershell -ExecutionPolicy Bypass -File "C:\Users\raffa\OneDrive\Documents\New project\scripts\test-thin-slice.ps1"
```

### Numeric Inputs In The GUI

Depending on Windows locale, `0,9` and `0.9` may behave differently in browser number fields.

If a value is rejected:
- try `0.9` instead of `0,9`
- click out of the field once before submitting

## Important Notes

- `goal_ops.db` keeps your manual test data between restarts.
- If you see old goals or tasks, reset the local database.
- In this sandboxed environment, file-backed SQLite can behave differently than in a normal local shell. For normal local use, keep `GOAL_OPS_DATABASE_URL="goal_ops.db"` set before starting the server.
- Optional: set `GOAL_OPS_DB_MIGRATION_BACKUP_DIR` to control where pre-migration SQLite backups are written.
- Optional: set `GOAL_OPS_DB_QUARANTINE_DIR` to control where corrupted DB files are quarantined on startup recovery.
- Optional: set `GOAL_OPS_DB_STARTUP_CORRUPTION_RECOVERY_ENABLED=false` to disable automatic startup quarantine recovery.
- If you start `uvicorn` from outside the project directory, keep the `--app-dir` flag.

## Key Files

- [main.py](/C:/Users/raffa/OneDrive/Documents/New%20project/goal_ops_console/main.py)
- [desktop.py](/C:/Users/raffa/OneDrive/Documents/New%20project/goal_ops_console/desktop.py)
- [app.js](/C:/Users/raffa/OneDrive/Documents/New%20project/goal_ops_console/static/app.js)
- [index.html](/C:/Users/raffa/OneDrive/Documents/New%20project/goal_ops_console/templates/index.html)
- [start-server.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/start-server.ps1)
- [start-desktop.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/start-desktop.ps1)
- [build-desktop.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/build-desktop.ps1)
- [package-desktop-release.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/package-desktop-release.ps1)
- [install-desktop-update.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/install-desktop-update.ps1)
- [manage-desktop-rings.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/manage-desktop-rings.ps1)
- [reset-db.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/reset-db.ps1)
- [run-tests.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-tests.ps1)
- [release-gate.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/release-gate.ps1)
- [release-gate-probe.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/release-gate-probe.py)
- [slo-alert-check.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/slo-alert-check.py)
- [run-slo-alert-check.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-slo-alert-check.ps1)
- [auto-rollback-policy.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/auto-rollback-policy.py)
- [run-auto-rollback-policy.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-auto-rollback-policy.ps1)
- [release-freeze-policy.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/release-freeze-policy.py)
- [run-release-freeze-policy.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-release-freeze-policy.ps1)
- [desktop-update-safety-drill.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/desktop-update-safety-drill.py)
- [run-desktop-update-safety-drill.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-desktop-update-safety-drill.ps1)
- [recovery-hard-abort-drill.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/recovery-hard-abort-drill.py)
- [recovery-hard-abort-target.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/recovery-hard-abort-target.py)
- [run-recovery-hard-abort-drill.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-recovery-hard-abort-drill.ps1)
- [recovery-idempotence-drill.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/recovery-idempotence-drill.py)
- [run-recovery-idempotence-drill.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-recovery-idempotence-drill.ps1)
- [power-loss-durability-drill.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/power-loss-durability-drill.py)
- [power-loss-durability-target.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/power-loss-durability-target.py)
- [run-power-loss-durability-drill.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-power-loss-durability-drill.ps1)
- [wal-checkpoint-crash-drill.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/wal-checkpoint-crash-drill.py)
- [wal-checkpoint-crash-target.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/wal-checkpoint-crash-target.py)
- [run-wal-checkpoint-crash-drill.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-wal-checkpoint-crash-drill.ps1)
- [disk-pressure-fault-injection-drill.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/disk-pressure-fault-injection-drill.py)
- [run-disk-pressure-fault-injection-drill.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-disk-pressure-fault-injection-drill.ps1)
- [fsync-io-stall-drill.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/fsync-io-stall-drill.py)
- [run-fsync-io-stall-drill.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-fsync-io-stall-drill.ps1)
- [sqlite-real-full-drill.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/sqlite-real-full-drill.py)
- [run-sqlite-real-full-drill.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-sqlite-real-full-drill.ps1)
- [db-corruption-quarantine-drill.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/db-corruption-quarantine-drill.py)
- [run-db-corruption-quarantine-drill.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-db-corruption-quarantine-drill.ps1)
- [storage-corruption-hardening-drill.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/storage-corruption-hardening-drill.py)
- [run-storage-corruption-hardening-drill.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-storage-corruption-hardening-drill.ps1)
- [workflow-lock-resilience-drill.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/workflow-lock-resilience-drill.py)
- [run-workflow-lock-resilience-drill.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-workflow-lock-resilience-drill.ps1)
- [workflow-soak-drill.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/workflow-soak-drill.py)
- [run-workflow-soak-drill.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-workflow-soak-drill.ps1)
- [workflow-worker-restart-drill.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/workflow-worker-restart-drill.py)
- [run-workflow-worker-restart-drill.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-workflow-worker-restart-drill.ps1)
- [event-consumer-recovery-chaos-drill.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/event-consumer-recovery-chaos-drill.py)
- [run-event-consumer-recovery-chaos-drill.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-event-consumer-recovery-chaos-drill.ps1)
- [invariant-burst-drill.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/invariant-burst-drill.py)
- [run-invariant-burst-drill.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-invariant-burst-drill.ps1)
- [long-soak-budget-drill.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/long-soak-budget-drill.py)
- [run-long-soak-budget-drill.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-long-soak-budget-drill.ps1)
- [db-safe-mode-watchdog-drill.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/db-safe-mode-watchdog-drill.py)
- [run-db-safe-mode-watchdog-drill.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-db-safe-mode-watchdog-drill.ps1)
- [invariant-monitor-watchdog-drill.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/invariant-monitor-watchdog-drill.py)
- [run-invariant-monitor-watchdog-drill.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-invariant-monitor-watchdog-drill.ps1)
- [stability-canary.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/stability-canary.py)
- [stability-canary-baseline.json](/C:/Users/raffa/OneDrive/Documents/New%20project/docs/stability-canary-baseline.json)
- [migration-rehearsal.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/migration-rehearsal.py)
- [run-migration-rehearsal.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-migration-rehearsal.ps1)
- [upgrade-downgrade-compatibility-drill.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/upgrade-downgrade-compatibility-drill.py)
- [run-upgrade-downgrade-compatibility-drill.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-upgrade-downgrade-compatibility-drill.ps1)
- [backup-restore-drill.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/backup-restore-drill.py)
- [run-backup-restore-drill.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-backup-restore-drill.ps1)
- [backup-restore-stress-drill.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/backup-restore-stress-drill.py)
- [run-backup-restore-stress-drill.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-backup-restore-stress-drill.ps1)
- [snapshot-restore-crash-consistency-drill.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/snapshot-restore-crash-consistency-drill.py)
- [snapshot-restore-crash-target.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/snapshot-restore-crash-target.py)
- [run-snapshot-restore-crash-consistency-drill.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-snapshot-restore-crash-consistency-drill.ps1)
- [multi-db-atomic-switch-drill.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/multi-db-atomic-switch-drill.py)
- [multi-db-atomic-switch-target.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/multi-db-atomic-switch-target.py)
- [run-multi-db-atomic-switch-drill.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-multi-db-atomic-switch-drill.ps1)
- [disaster-recovery-rehearsal-pack.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/disaster-recovery-rehearsal-pack.py)
- [run-disaster-recovery-rehearsal-pack.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-disaster-recovery-rehearsal-pack.ps1)
- [failure-budget-dashboard.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/failure-budget-dashboard.py)
- [run-failure-budget-dashboard.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-failure-budget-dashboard.ps1)
- [safe-mode-ux-degradation-check.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/safe-mode-ux-degradation-check.py)
- [run-safe-mode-ux-degradation-check.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-safe-mode-ux-degradation-check.ps1)
- [a11y-test-harness-check.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/a11y-test-harness-check.py)
- [run-a11y-test-harness-check.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-a11y-test-harness-check.ps1)
- [incident-rollback-drill.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/incident-rollback-drill.py)
- [run-incident-rollback-drill.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-incident-rollback-drill.ps1)
- [critical-drill-flake-gate.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/critical-drill-flake-gate.py)
- [run-critical-drill-flake-gate.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-critical-drill-flake-gate.ps1)
- [release-gate-runtime-stability-drill.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/release-gate-runtime-stability-drill.py)
- [run-release-gate-runtime-stability-drill.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-release-gate-runtime-stability-drill.ps1)
- [release-gate-evidence-freshness-check.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/release-gate-evidence-freshness-check.py)
- [run-release-gate-evidence-freshness-check.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-release-gate-evidence-freshness-check.ps1)
- [release-gate-evidence-hash-manifest-check.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/release-gate-evidence-hash-manifest-check.py)
- [run-release-gate-evidence-hash-manifest-check.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-release-gate-evidence-hash-manifest-check.ps1)
- [release-gate-step-timing-schema-check.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/release-gate-step-timing-schema-check.py)
- [run-release-gate-step-timing-schema-check.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-release-gate-step-timing-schema-check.ps1)
- [release-gate-performance-history-check.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/release-gate-performance-history-check.py)
- [run-release-gate-performance-history-check.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-release-gate-performance-history-check.ps1)
- [release-gate-performance-budget-check.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/release-gate-performance-budget-check.py)
- [run-release-gate-performance-budget-check.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-release-gate-performance-budget-check.ps1)
- [release-gate-stability-final-readiness.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/release-gate-stability-final-readiness.py)
- [run-release-gate-stability-final-readiness.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-release-gate-stability-final-readiness.ps1)
- [release-gate-master-burnin-window-check.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/release-gate-master-burnin-window-check.py)
- [run-release-gate-master-burnin-window-check.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-release-gate-master-burnin-window-check.ps1)
- [release-gate-performance-policy-calibrate.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/release-gate-performance-policy-calibrate.py)
- [run-release-gate-performance-policy-calibrate.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-release-gate-performance-policy-calibrate.ps1)
- [release-gate-staging-soak-readiness-check.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/release-gate-staging-soak-readiness-check.py)
- [run-release-gate-staging-soak-readiness-check.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-release-gate-staging-soak-readiness-check.ps1)
- [release-gate-rc-canary-rollout-check.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/release-gate-rc-canary-rollout-check.py)
- [run-release-gate-rc-canary-rollout-check.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-release-gate-rc-canary-rollout-check.ps1)
- [release-gate-evidence-lineage-check.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/release-gate-evidence-lineage-check.py)
- [run-release-gate-evidence-lineage-check.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-release-gate-evidence-lineage-check.ps1)
- [release-gate-production-readiness-certification.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/release-gate-production-readiness-certification.py)
- [run-release-gate-production-readiness-certification-check.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-release-gate-production-readiness-certification-check.ps1)
- [release-gate-slo-burn-rate-v2-check.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/release-gate-slo-burn-rate-v2-check.py)
- [run-release-gate-slo-burn-rate-v2-check.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-release-gate-slo-burn-rate-v2-check.ps1)
- [release-gate-slo-burn-rate-v2-policy.json](/C:/Users/raffa/OneDrive/Documents/New%20project/docs/release-gate-slo-burn-rate-v2-policy.json)
- [release-gate-deploy-rehearsal-check.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/release-gate-deploy-rehearsal-check.py)
- [run-release-gate-deploy-rehearsal-check.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-release-gate-deploy-rehearsal-check.ps1)
- [release-gate-deploy-rehearsal-policy.json](/C:/Users/raffa/OneDrive/Documents/New%20project/docs/release-gate-deploy-rehearsal-policy.json)
- [release-gate-chaos-matrix-continuous-check.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/release-gate-chaos-matrix-continuous-check.py)
- [run-release-gate-chaos-matrix-continuous-check.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-release-gate-chaos-matrix-continuous-check.ps1)
- [release-gate-chaos-matrix-policy.json](/C:/Users/raffa/OneDrive/Documents/New%20project/docs/release-gate-chaos-matrix-policy.json)
- [release-gate-supply-chain-artifact-trust-check.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/release-gate-supply-chain-artifact-trust-check.py)
- [run-release-gate-supply-chain-artifact-trust-check.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-release-gate-supply-chain-artifact-trust-check.ps1)
- [release-gate-artifact-trust-policy.json](/C:/Users/raffa/OneDrive/Documents/New%20project/docs/release-gate-artifact-trust-policy.json)
- [release-gate-operations-handoff-readiness-check.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/release-gate-operations-handoff-readiness-check.py)
- [run-release-gate-operations-handoff-readiness-check.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-release-gate-operations-handoff-readiness-check.ps1)
- [release-gate-evidence-attestation-check.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/release-gate-evidence-attestation-check.py)
- [run-release-gate-evidence-attestation-check.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-release-gate-evidence-attestation-check.ps1)
- [release-gate-evidence-attestation-policy.json](/C:/Users/raffa/OneDrive/Documents/New%20project/docs/release-gate-evidence-attestation-policy.json)
- [release-gate-release-train-readiness-check.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/release-gate-release-train-readiness-check.py)
- [run-release-gate-release-train-readiness-check.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-release-gate-release-train-readiness-check.ps1)
- [release-gate-production-final-attestation.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/release-gate-production-final-attestation.py)
- [run-release-gate-production-final-attestation-check.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-release-gate-production-final-attestation-check.ps1)
- [p0-burnin-consecutive-green.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/p0-burnin-consecutive-green.py)
- [run-p0-burnin-consecutive-green.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-p0-burnin-consecutive-green.ps1)
- [p0-runbook-contract-check.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/p0-runbook-contract-check.py)
- [run-p0-runbook-contract-check.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-p0-runbook-contract-check.ps1)
- [p0-report-schema-contract-check.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/p0-report-schema-contract-check.py)
- [run-p0-report-schema-contract-check.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-p0-report-schema-contract-check.ps1)
- [p0-release-evidence-bundle.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/p0-release-evidence-bundle.py)
- [run-p0-release-evidence-bundle.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-p0-release-evidence-bundle.ps1)
- [p0-closure-report.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/p0-closure-report.py)
- [run-p0-closure-report.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-p0-closure-report.ps1)
- [security-config-hardening-check.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/security-config-hardening-check.py)
- [run-security-config-hardening-check.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-security-config-hardening-check.ps1)
- [audit-trail-hardening-check.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/audit-trail-hardening-check.py)
- [run-audit-trail-hardening-check.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-audit-trail-hardening-check.ps1)
- [security-ci-lane-check.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/security-ci-lane-check.py)
- [run-security-ci-lane-check.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-security-ci-lane-check.ps1)
- [alert-routing-oncall-check.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/alert-routing-oncall-check.py)
- [run-alert-routing-oncall-check.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-alert-routing-oncall-check.ps1)
- [oncall-alert-routing-policy.json](/C:/Users/raffa/OneDrive/Documents/New%20project/docs/oncall-alert-routing-policy.json)
- [incident-drill-automation-check.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/incident-drill-automation-check.py)
- [run-incident-drill-automation-check.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-incident-drill-automation-check.ps1)
- [incident-drill-automation-policy.json](/C:/Users/raffa/OneDrive/Documents/New%20project/docs/incident-drill-automation-policy.json)
- [load-profile-framework-check.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/load-profile-framework-check.py)
- [run-load-profile-framework-check.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-load-profile-framework-check.ps1)
- [load-profile-catalog.json](/C:/Users/raffa/OneDrive/Documents/New%20project/docs/load-profile-catalog.json)
- [canary-guardrails-check.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/canary-guardrails-check.py)
- [run-canary-guardrails-check.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-canary-guardrails-check.ps1)
- [canary-guardrails-policy.json](/C:/Users/raffa/OneDrive/Documents/New%20project/docs/canary-guardrails-policy.json)
- [release-candidate-rollout-policy.json](/C:/Users/raffa/OneDrive/Documents/New%20project/docs/release-candidate-rollout-policy.json)
- [rto-rpo-assertion-suite.py](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/rto-rpo-assertion-suite.py)
- [run-rto-rpo-assertion-suite.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-rto-rpo-assertion-suite.ps1)
- [rto-rpo-assertion-policy.json](/C:/Users/raffa/OneDrive/Documents/New%20project/docs/rto-rpo-assertion-policy.json)
- [test_goal_ops.py](/C:/Users/raffa/OneDrive/Documents/New%20project/tests/test_goal_ops.py)
- [test_desktop_launcher.py](/C:/Users/raffa/OneDrive/Documents/New%20project/tests/test_desktop_launcher.py)

