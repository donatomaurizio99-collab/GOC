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
```

Behavior:
- starts an embedded local FastAPI server (`127.0.0.1`)
- opens the same dashboard UI in a native window via `pywebview`
- remembers window size/position across launches (disable with `-NoWindowState`)
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
- `GoalOpsConsole-onedir.zip` (if `onedir` was built)
- `GoalOpsConsole-onefile.exe` (if `onefile` was built)
- `SHA256SUMS.txt` (checksums for uploaded files)

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

Desktop workflow file:
[desktop-build.yml](/C:/Users/raffa/OneDrive/Documents/New%20project/.github/workflows/desktop-build.yml)

Recommended branch protection on `master`:

1. Open repository settings on GitHub.
2. Go to `Branches` -> `Branch protection rules`.
3. Add/update rule for `master`:
4. Enable `Require a pull request before merging`.
5. Enable `Require status checks to pass before merging`.
6. Select required check: `Pytest (Python 3.11)` and `Pytest (Python 3.12)`.

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
- If you start `uvicorn` from outside the project directory, keep the `--app-dir` flag.

## Key Files

- [main.py](/C:/Users/raffa/OneDrive/Documents/New%20project/goal_ops_console/main.py)
- [desktop.py](/C:/Users/raffa/OneDrive/Documents/New%20project/goal_ops_console/desktop.py)
- [app.js](/C:/Users/raffa/OneDrive/Documents/New%20project/goal_ops_console/static/app.js)
- [index.html](/C:/Users/raffa/OneDrive/Documents/New%20project/goal_ops_console/templates/index.html)
- [start-server.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/start-server.ps1)
- [start-desktop.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/start-desktop.ps1)
- [build-desktop.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/build-desktop.ps1)
- [reset-db.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/reset-db.ps1)
- [run-tests.ps1](/C:/Users/raffa/OneDrive/Documents/New%20project/scripts/run-tests.ps1)
- [test_goal_ops.py](/C:/Users/raffa/OneDrive/Documents/New%20project/tests/test_goal_ops.py)
- [test_desktop_launcher.py](/C:/Users/raffa/OneDrive/Documents/New%20project/tests/test_desktop_launcher.py)
