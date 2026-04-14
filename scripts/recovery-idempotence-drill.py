from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from goal_ops_console.config import Settings
from goal_ops_console.main import create_app


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    _expect(isinstance(payload, dict), f"Expected JSON object in {path}")
    return payload


def _consume_process_output(process: subprocess.Popen[str]) -> tuple[str, str]:
    stdout_text = process.stdout.read() if process.stdout is not None else ""
    stderr_text = process.stderr.read() if process.stderr is not None else ""
    return stdout_text, stderr_text


def _stop_process(process: subprocess.Popen[str], *, timeout_seconds: float = 10.0) -> tuple[int, str, str]:
    if process.poll() is None:
        process.kill()
    try:
        process.wait(timeout=max(1.0, float(timeout_seconds)))
    except subprocess.TimeoutExpired:
        process.terminate()
        process.wait(timeout=5.0)
    stdout_text, stderr_text = _consume_process_output(process)
    return int(process.returncode if process.returncode is not None else -1), stdout_text, stderr_text


def _wait_for_target_state(
    *,
    process: subprocess.Popen[str],
    state_file: Path,
    timeout_seconds: float,
) -> dict[str, Any]:
    deadline = time.time() + max(1.0, float(timeout_seconds))
    while time.time() < deadline:
        if state_file.exists():
            try:
                payload = _read_json(state_file)
            except Exception:
                payload = {}
            if payload.get("status") == "running" and payload.get("run_id"):
                return payload
        if process.poll() is not None:
            stdout_text, stderr_text = _consume_process_output(process)
            raise RuntimeError(
                "Recovery idempotence target process exited before reporting running state. "
                f"rc={process.returncode} stdout={stdout_text!r} stderr={stderr_text!r}"
            )
        time.sleep(0.05)
    raise RuntimeError(
        f"Recovery idempotence target did not report running state within {timeout_seconds}s."
    )


def _read_run_status(database_path: Path, run_id: str) -> str | None:
    connection = sqlite3.connect(str(database_path))
    try:
        row = connection.execute(
            "SELECT status FROM workflow_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        return str(row[0]) if row is not None else None
    finally:
        connection.close()


def _running_run_ids(client: TestClient) -> list[str]:
    response = client.get("/workflows/runs?limit=200")
    _expect(response.status_code == 200, f"Failed to list workflow runs: {response.status_code}")
    runs = response.json().get("runs") or []
    running: list[str] = []
    for item in runs:
        if not isinstance(item, dict):
            continue
        if str(item.get("status")) == "running" and item.get("run_id"):
            running.append(str(item["run_id"]))
    return running


def _recovered_event_count(client: TestClient, run_id: str) -> int:
    response = client.get(f"/events?entity_id={run_id}&limit=500")
    _expect(response.status_code == 200, f"Failed to list events for {run_id}: {response.status_code}")
    events = response.json() or []
    count = 0
    for event in events:
        if not isinstance(event, dict):
            continue
        if str(event.get("event_type")) == "workflow.run.recovered_after_abort":
            count += 1
    return int(count)


def _goal_payload(cycle: int) -> dict[str, Any]:
    return {
        "title": f"Recovery Idempotence Drill Goal {cycle}",
        "description": "Mutation probe after startup recovery cycle.",
        "urgency": 0.6,
        "value": 0.7,
        "deadline_score": 0.3,
    }


@dataclass(slots=True)
class DrillPaths:
    run_dir: Path
    database_path: Path
    target_state_file: Path


def _create_paths(workspace_root: Path) -> DrillPaths:
    run_id = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    run_dir = workspace_root / f"recovery-idempotence-{run_id}"
    return DrillPaths(
        run_dir=run_dir,
        database_path=run_dir / "recovery-idempotence.db",
        target_state_file=run_dir / "target-state.json",
    )


def run_drill(
    *,
    workspace_root: Path,
    label: str,
    recovery_cycles: int,
    startup_timeout_seconds: float,
    keep_artifacts: bool,
) -> dict[str, Any]:
    started = time.perf_counter()
    paths = _create_paths(workspace_root)
    paths.run_dir.mkdir(parents=True, exist_ok=False)

    target_script = PROJECT_ROOT / "scripts" / "recovery-hard-abort-target.py"
    _expect(target_script.exists(), f"Target helper script missing: {target_script}")

    target_process: subprocess.Popen[str] | None = None
    target_return_code = -1
    target_stdout = ""
    target_stderr = ""
    run_id = ""
    status_before_abort = ""

    try:
        target_process = subprocess.Popen(
            [
                sys.executable,
                str(target_script),
                "--database-url",
                str(paths.database_path),
                "--state-file",
                str(paths.target_state_file),
                "--startup-timeout-seconds",
                str(float(startup_timeout_seconds)),
            ],
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        target_state = _wait_for_target_state(
            process=target_process,
            state_file=paths.target_state_file,
            timeout_seconds=max(5.0, float(startup_timeout_seconds)),
        )
        run_id = str(target_state.get("run_id") or "")
        _expect(bool(run_id), f"Target did not provide run_id: {target_state}")

        status_before_abort = str(_read_run_status(paths.database_path, run_id) or "")
        _expect(
            status_before_abort == "running",
            f"Expected run status 'running' before abort, got {status_before_abort!r}.",
        )

        target_return_code, target_stdout, target_stderr = _stop_process(target_process)
        target_process = None

        cycle_reports: list[dict[str, Any]] = []
        expected_recovery_event_count = 1
        for cycle in range(1, max(1, int(recovery_cycles)) + 1):
            app = create_app(
                Settings(
                    database_url=str(paths.database_path),
                    workflow_run_timeout_seconds=1800,
                    workflow_worker_poll_interval_seconds=0.05,
                    workflow_startup_recovery_max_age_seconds=0,
                )
            )
            with TestClient(app, raise_server_exceptions=False) as client:
                readiness_response = client.get("/system/readiness")
                slo_response = client.get("/system/slo")
                run_response = client.get(f"/workflows/runs/{run_id}")
                recovery_event_count = _recovered_event_count(client, run_id)
                running_run_ids = _running_run_ids(client)
                created_goal = client.post("/goals", json=_goal_payload(cycle))

            _expect(readiness_response.status_code == 200, "Readiness probe failed in recovery cycle.")
            _expect(slo_response.status_code == 200, "SLO probe failed in recovery cycle.")
            _expect(run_response.status_code == 200, f"Failed to read run {run_id} in recovery cycle.")
            _expect(
                created_goal.status_code == 201,
                (
                    "Mutating write probe failed during recovery idempotence drill. "
                    f"status={created_goal.status_code} body={created_goal.text!r}"
                ),
            )

            readiness_payload = readiness_response.json()
            slo_payload = slo_response.json()
            run_payload = run_response.json().get("run") or {}
            startup_recovery = (
                readiness_payload.get("checks", {})
                .get("workflow_worker", {})
                .get("startup_recovery", {})
            )
            recovered_count = int(startup_recovery.get("recovered_count") or 0)
            recovered_run_ids = [
                str(item)
                for item in (startup_recovery.get("run_ids") or [])
                if str(item)
            ]
            run_status = str(run_payload.get("status") or "")
            result_payload = run_payload.get("result_payload") or {}
            error_type = str(result_payload.get("error_type") or "")

            _expect(bool(readiness_payload.get("ready")), f"Readiness not ready in cycle {cycle}: {readiness_payload}")
            _expect(str(slo_payload.get("status")) == "ok", f"SLO not ok in cycle {cycle}: {slo_payload}")
            _expect(run_status == "failed", f"Recovered run did not stay failed in cycle {cycle}: {run_payload}")
            _expect(
                error_type == "ProcessAbortRecovery",
                f"Recovered run error_type mismatch in cycle {cycle}: {run_payload}",
            )
            _expect(
                run_id not in running_run_ids,
                f"Recovered run is still running in cycle {cycle}: running={running_run_ids}",
            )

            if cycle == 1:
                _expect(
                    recovered_count >= 1 and run_id in recovered_run_ids,
                    (
                        "First startup recovery cycle did not recover interrupted run. "
                        f"startup_recovery={json.dumps(startup_recovery, sort_keys=True)}"
                    ),
                )
                _expect(
                    int(recovery_event_count) == int(expected_recovery_event_count),
                    (
                        "Unexpected recovered-after-abort event count in first cycle. "
                        f"observed={recovery_event_count} expected={expected_recovery_event_count}"
                    ),
                )
            else:
                _expect(
                    recovered_count == 0 and run_id not in recovered_run_ids,
                    (
                        "Later startup cycle repeated recovery instead of staying idempotent. "
                        f"cycle={cycle} startup_recovery={json.dumps(startup_recovery, sort_keys=True)}"
                    ),
                )
                _expect(
                    int(recovery_event_count) == int(expected_recovery_event_count),
                    (
                        "Recovered-after-abort event was duplicated across restart cycles. "
                        f"cycle={cycle} observed={recovery_event_count} expected={expected_recovery_event_count}"
                    ),
                )

            cycle_reports.append(
                {
                    "cycle": int(cycle),
                    "startup_recovery": startup_recovery,
                    "run_status": run_status,
                    "run_error_type": error_type,
                    "recovered_event_count": int(recovery_event_count),
                    "running_run_ids": running_run_ids,
                    "readiness_ready": bool(readiness_payload.get("ready")),
                    "slo_status": str(slo_payload.get("status")),
                    "goal_create_status_code": int(created_goal.status_code),
                    "goal_id": str((created_goal.json() or {}).get("goal_id")),
                }
            )

        return {
            "label": label,
            "success": True,
            "run_id": run_id,
            "status_before_abort": status_before_abort,
            "recovery_cycles": int(recovery_cycles),
            "cycles": cycle_reports,
            "target_process": {
                "return_code": int(target_return_code),
                "stdout": target_stdout.strip(),
                "stderr": target_stderr.strip(),
            },
            "paths": {
                "run_dir": str(paths.run_dir),
                "database_path": str(paths.database_path),
                "target_state_file": str(paths.target_state_file),
                "target_script": str(target_script),
            },
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }
    finally:
        if target_process is not None and target_process.poll() is None:
            try:
                target_process.kill()
                target_process.wait(timeout=5.0)
            except Exception:
                pass
        if not keep_artifacts:
            shutil.rmtree(paths.run_dir, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Recovery idempotence drill: hard-abort a running workflow process, then restart "
            "the app multiple times and verify startup recovery executes exactly once per "
            "interrupted run (no duplicate recovery events or state churn)."
        )
    )
    parser.add_argument("--workspace", default=str(PROJECT_ROOT / ".tmp" / "recovery-idempotence-drills"))
    parser.add_argument("--label", default="recovery-idempotence-drill")
    parser.add_argument("--recovery-cycles", type=int, default=3)
    parser.add_argument("--startup-timeout-seconds", type=float, default=15.0)
    parser.add_argument("--keep-artifacts", action="store_true")
    args = parser.parse_args(argv)

    if int(args.recovery_cycles) <= 0:
        print("[recovery-idempotence-drill] ERROR: --recovery-cycles must be > 0.", file=sys.stderr)
        return 2
    if float(args.startup_timeout_seconds) <= 0:
        print("[recovery-idempotence-drill] ERROR: --startup-timeout-seconds must be > 0.", file=sys.stderr)
        return 2

    workspace_root = Path(str(args.workspace)).expanduser()
    workspace_root.mkdir(parents=True, exist_ok=True)

    try:
        report = run_drill(
            workspace_root=workspace_root,
            label=str(args.label),
            recovery_cycles=int(args.recovery_cycles),
            startup_timeout_seconds=float(args.startup_timeout_seconds),
            keep_artifacts=bool(args.keep_artifacts),
        )
    except Exception as exc:
        print(f"[recovery-idempotence-drill] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
