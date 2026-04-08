# Goal Ops Console

Supervised MVP for the Goal Ops Console.

Stack:
- Python 3.11+
- FastAPI
- Jinja2
- SQLite
- Pytest

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

Start the server:

```powershell
$env:GOAL_OPS_DATABASE_URL="goal_ops.db"
python -m uvicorn goal_ops_console.main:app --reload --app-dir "C:\Users\raffa\OneDrive\Documents\New project"
```

Open the dashboard:

[http://127.0.0.1:8000](http://127.0.0.1:8000)

## Stop And Restart

Stop the server in the terminal where `uvicorn` is running:

```powershell
Ctrl + C
```

Start it again:

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
$env:GOAL_OPS_DATABASE_URL="goal_ops.db"
python -m uvicorn goal_ops_console.main:app --reload --app-dir "C:\Users\raffa\OneDrive\Documents\New project"
```

## Reset The Local Database

Use this when you want a clean manual test run.

1. Stop the server.
2. Run:

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
Remove-Item ".\goal_ops.db", ".\goal_ops.db-journal" -ErrorAction SilentlyContinue
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

## Run The Test Suite

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
$env:PYTHONDONTWRITEBYTECODE='1'
python -m pytest -q
```

Current result during implementation:

```text
19 passed
```

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

### `ModuleNotFoundError: No module named 'goal_ops_console'`

This means `uvicorn` was started from the wrong directory or without the app dir.

Use:

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
$env:GOAL_OPS_DATABASE_URL="goal_ops.db"
python -m uvicorn goal_ops_console.main:app --reload --app-dir "C:\Users\raffa\OneDrive\Documents\New project"
```

### Old Goals Or Tasks Still Visible

`goal_ops.db` keeps manual test data between runs.

Reset it with:

```powershell
Set-Location "C:\Users\raffa\OneDrive\Documents\New project"
Remove-Item ".\goal_ops.db", ".\goal_ops.db-journal" -ErrorAction SilentlyContinue
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
- [app.js](/C:/Users/raffa/OneDrive/Documents/New%20project/goal_ops_console/static/app.js)
- [index.html](/C:/Users/raffa/OneDrive/Documents/New%20project/goal_ops_console/templates/index.html)
- [test_goal_ops.py](/C:/Users/raffa/OneDrive/Documents/New%20project/tests/test_goal_ops.py)
