# Goal Ops Console Production Runbook

This runbook is optimized for reliability-first releases of the desktop app and API.

## 1. Release Checklist

### 1.1 Pre-release gate

Run in repo root:

```powershell
python -m pytest -q
python .\scripts\desktop-smoke.py
```

Verify before release:
- CI checks green:
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
- `artifacts\SHA256SUMS.txt` exists.
- `GoalOpsConsole-onefile-<version>.exe` starts and reaches dashboard.

### 1.3 Publish

Tag and push:

```powershell
git tag v<VERSION>
git push origin v<VERSION>
```

GitHub Actions `Desktop Build` publishes release assets on tag pushes.

### 1.4 Post-release checks

After release is live:
- Launch desktop binary on a clean Windows machine.
- Confirm `GET /system/readiness` returns `{"ready": true, ...}`.
- Trigger `Export Diagnostics` once from Operator Controls and confirm snapshot file exists.
- Verify one workflow run can be queued and completed from UI.

## 2. Rollback

Use rollback when crash rate or readiness failures rise immediately after rollout.

1. Disable rollout in updater channel (set previous stable manifest/version).
2. Repoint download/install instructions to previous stable artifact.
3. Keep diagnostics snapshots from failed version for triage.
4. Open incident ticket with:
   - failing version
   - first failure timestamp (UTC)
   - attached diagnostics JSON and crash report JSON

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
3. Export diagnostics:
   - UI: `Export Diagnostics`
   - API: `POST /system/diagnostics`
4. Restart app and retry once. If still failing, rollback.

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

### 3.4 Desktop startup conflicts (single-instance lock)

Symptoms:
- error: another desktop instance is already running.

Actions:
1. Confirm no active app process.
2. Retry launch once (stale lock auto-recovery is enabled).
3. If still blocked, collect crash/diagnostics and escalate.

### 3.5 Crash reports

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

## 4. Operational Defaults

- Keep single-instance protection enabled in production.
- Keep readiness endpoint as launch gate.
- Keep diagnostics export enabled for operators.
- Prefer rollback over hotfix-in-place during active outage.
