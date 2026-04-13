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
    stdout_text = ""
    stderr_text = ""
    if process.stdout is not None:
        stdout_text = process.stdout.read()
    if process.stderr is not None:
        stderr_text = process.stderr.read()
    return stdout_text, stderr_text


def _read_run_status(db_path: Path, run_id: str) -> str | None:
    last_error: Exception | None = None
    for _ in range(20):
        connection = sqlite3.connect(str(db_path))
        try:
            row = connection.execute(
                "SELECT status FROM workflow_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                return None
            return str(row[0])
        except sqlite3.OperationalError as exc:
            last_error = exc
            if "locked" in str(exc).lower():
                time.sleep(0.05)
                continue
            raise
        finally:
            connection.close()
    if last_error is not None:
        raise last_error
    return None


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
                "Hard-abort target process exited before reporting running state. "
                f"rc={process.returncode} stdout={stdout_text!r} stderr={stderr_text!r}"
            )
        time.sleep(0.05)
    raise RuntimeError(
        f"Hard-abort target did not report running state within {timeout_seconds}s."
    )


@dataclass(slots=True)
class DrillPaths:
    run_dir: Path
    database_path: Path
    target_state_file: Path


def _create_paths(workspace_root: Path) -> DrillPaths:
    run_id = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    run_dir = workspace_root / f"recovery-hard-abort-{run_id}"
    return DrillPaths(
        run_dir=run_dir,
        database_path=run_dir / "hard-abort-drill.db",
        target_state_file=run_dir / "target-state.json",
    )


def run_drill(
    *,
    workspace_root: Path,
    label: str,
    keep_artifacts: bool,
    startup_timeout_seconds: float,
) -> dict[str, Any]:
    started = time.perf_counter()
    paths = _create_paths(workspace_root)
    paths.run_dir.mkdir(parents=True, exist_ok=False)

    target_script = PROJECT_ROOT / "scripts" / "recovery-hard-abort-target.py"
    _expect(target_script.exists(), f"Target helper script missing: {target_script}")

    target_process: subprocess.Popen[str] | None = None
    target_stdout = ""
    target_stderr = ""
    run_id = ""

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
        run_id = str(target_state["run_id"])

        status_before_abort = _read_run_status(paths.database_path, run_id)
        _expect(
            status_before_abort == "running",
            f"Expected run status 'running' before abort, got {status_before_abort!r}.",
        )

        target_process.kill()
        target_process.wait(timeout=10)
        target_stdout, target_stderr = _consume_process_output(target_process)

        app = create_app(
            Settings(
                database_url=str(paths.database_path),
                workflow_run_timeout_seconds=1800,
                workflow_worker_poll_interval_seconds=0.05,
                workflow_startup_recovery_max_age_seconds=0,
            )
        )
        with TestClient(app) as client:
            readiness_response = client.get("/system/readiness")
            _expect(readiness_response.status_code == 200, "Readiness probe failed after restart.")
            readiness = readiness_response.json()

            run_response = client.get(f"/workflows/runs/{run_id}")
            _expect(run_response.status_code == 200, f"Failed to read run {run_id} after restart.")
            run_payload = run_response.json()["run"]

            listed_runs = client.get("/workflows/runs?limit=200").json()["runs"]
            running_run_ids = [str(item["run_id"]) for item in listed_runs if item["status"] == "running"]

            events = client.get(f"/events?entity_id={run_id}").json()

        startup_recovery = (
            readiness.get("checks", {})
            .get("workflow_worker", {})
            .get("startup_recovery", {})
        )
        recovered_event_present = any(
            str(item.get("event_type")) == "workflow.run.recovered_after_abort"
            for item in events
            if isinstance(item, dict)
        )

        status_after_restart = str(run_payload["status"])
        result_payload = run_payload.get("result_payload") or {}
        success = (
            bool(readiness.get("ready"))
            and status_after_restart == "failed"
            and str(result_payload.get("error_type")) == "ProcessAbortRecovery"
            and run_id not in running_run_ids
            and int(startup_recovery.get("recovered_count") or 0) >= 1
            and recovered_event_present
        )
        _expect(
            success,
            (
                "Hard-abort recovery validation failed. "
                f"readiness={json.dumps(readiness, sort_keys=True)} "
                f"run={json.dumps(run_payload, sort_keys=True)} "
                f"running_run_ids={running_run_ids} "
                f"startup_recovery={json.dumps(startup_recovery, sort_keys=True)} "
                f"events={json.dumps(events, sort_keys=True)}"
            ),
        )

        return {
            "label": label,
            "success": True,
            "recovery": {
                "run_id": run_id,
                "status_before_abort": status_before_abort,
                "status_after_restart": status_after_restart,
                "error_type_after_restart": result_payload.get("error_type"),
                "startup_recovery": startup_recovery,
                "recovered_event_present": recovered_event_present,
                "running_run_ids_after_restart": running_run_ids,
                "readiness_ready": bool(readiness.get("ready")),
            },
            "paths": {
                "run_dir": str(paths.run_dir),
                "database_path": str(paths.database_path),
                "target_state_file": str(paths.target_state_file),
                "target_script": str(target_script),
            },
            "target_process": {
                "stdout": target_stdout.strip(),
                "stderr": target_stderr.strip(),
            },
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }
    finally:
        if target_process is not None and target_process.poll() is None:
            target_process.kill()
            try:
                target_process.wait(timeout=5)
            except Exception:
                pass
        if not keep_artifacts:
            shutil.rmtree(paths.run_dir, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Recovery drill for hard process abort: kill worker process during running workflow, "
            "restart service, and verify startup recovery marks interrupted run as non-running."
        )
    )
    parser.add_argument("--workspace", default=str(PROJECT_ROOT / ".tmp" / "recovery-hard-abort-drills"))
    parser.add_argument("--label", default="recovery-hard-abort-drill")
    parser.add_argument("--startup-timeout-seconds", type=float, default=15.0)
    parser.add_argument("--keep-artifacts", action="store_true")
    args = parser.parse_args(argv)

    workspace_root = Path(str(args.workspace)).expanduser()
    workspace_root.mkdir(parents=True, exist_ok=True)

    try:
        report = run_drill(
            workspace_root=workspace_root,
            label=str(args.label),
            keep_artifacts=bool(args.keep_artifacts),
            startup_timeout_seconds=float(args.startup_timeout_seconds),
        )
    except Exception as exc:
        print(f"[recovery-hard-abort-drill] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
