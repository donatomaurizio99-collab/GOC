# Goal Ops Console Production Runbook

This runbook is optimized for reliability-first releases of the desktop app and API.

## 1. Release Checklist

### 1.1 Pre-release gate

Run in repo root:

```powershell
.\scripts\release-gate.ps1 -StrictFileDatabaseProbe -StrictAutoRollbackPolicyDrill -StrictDesktopUpdateSafetyDrill -StrictMigrationRehearsal -StrictBackupRestoreDrill -StrictIncidentRollbackDrill
```

This gate covers:
- full `pytest` suite
- desktop smoke boot path
- `GET /system/readiness`
- `GET /system/slo` (`status` must be `ok`)
- auto-rollback-policy drill (`critical` sustained window triggers stable ring rollback path)
- desktop-update-safety drill (hash validation + rollback-to-stable fallback path)
- `GET /system/database/integrity?mode=quick|full`
- schema migration pending-version check (`pending_versions` must be empty)
- migration rehearsal across small/medium/large DB copies with explicit backup/restore/migration runtime thresholds
- backup/restore drill with row-count and integrity verification on restored DB
- incident/rollback drill with controlled burst load, SLO incident detection, and stable-ring rollback validation

Manual migration rehearsal invocation (same thresholds as gate defaults):

```powershell
.\scripts\run-migration-rehearsal.ps1 -SmallRuns 500 -MediumRuns 2500 -LargeRuns 6000
```

Manual auto-rollback policy invocation (live endpoint, stable ring):

```powershell
.\scripts\run-auto-rollback-policy.ps1 -BaseUrl "http://127.0.0.1:8000" -ManifestPath ".\artifacts\desktop-rings.json" -CriticalWindowSeconds 300 -PollIntervalSeconds 30 -MaxObservationSeconds 900
```

Manual desktop-update safety drill invocation:

```powershell
.\scripts\run-desktop-update-safety-drill.ps1
```

Verify before release:
- CI checks green:
  - `Release Gate (Windows)`
  - `Pytest (Python 3.11)`
  - `Pytest (Python 3.12)`
  - `Desktop Smoke (Windows)`
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
   .\scripts\run-auto-rollback-policy.ps1 -BaseUrl "http://127.0.0.1:8000" -ManifestPath ".\artifacts\desktop-rings.json" -CriticalWindowSeconds 300 -PollIntervalSeconds 30 -MaxObservationSeconds 900
   ```
4. Export diagnostics snapshot and attach to incident ticket.

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

## 4. Operational Defaults

- Keep single-instance protection enabled in production.
- Keep readiness endpoint as launch gate.
- Keep diagnostics export enabled for operators.
- Keep crash-loop protection enabled; only bypass for supervised triage.
- Prefer rollback over hotfix-in-place during active outage.
