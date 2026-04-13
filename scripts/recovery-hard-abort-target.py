from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from goal_ops_console.config import Settings
from goal_ops_console.main import create_app


def _utc_iso() -> str:
    return datetime.now(UTC).isoformat()


def _wait_for_running(client: TestClient, run_id: str, timeout_seconds: float) -> None:
    deadline = time.time() + max(1.0, float(timeout_seconds))
    while time.time() < deadline:
        response = client.get(f"/workflows/runs/{run_id}")
        if response.status_code == 200:
            status = str(response.json()["run"]["status"])
            if status == "running":
                return
            if status in {"succeeded", "failed", "timed_out", "cancelled"}:
                raise RuntimeError(
                    f"Run {run_id} reached terminal status {status!r} before hard-abort preparation."
                )
        time.sleep(0.05)
    raise RuntimeError(f"Run {run_id} did not reach running state within {timeout_seconds}s.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Internal helper for recovery hard-abort drill: start a long-running workflow and "
            "wait to be killed by parent process."
        )
    )
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--state-file", required=True)
    parser.add_argument("--startup-timeout-seconds", type=float, default=15.0)
    args = parser.parse_args(argv)

    state_file = Path(args.state_file).expanduser()
    state_file.parent.mkdir(parents=True, exist_ok=True)

    app = create_app(
        Settings(
            database_url=str(args.database_url),
            workflow_run_timeout_seconds=1800,
            workflow_worker_poll_interval_seconds=0.05,
            workflow_startup_recovery_max_age_seconds=0,
        )
    )

    started = threading.Event()

    def _blocking_handler(_: dict) -> dict:
        started.set()
        while True:
            time.sleep(0.5)

    with TestClient(app) as client:
        services = client.app.state.services
        services.workflow_catalog.handlers["maintenance.retention_cleanup"] = _blocking_handler

        response = client.post(
            "/workflows/maintenance.retention_cleanup/start",
            json={"requested_by": "hard-abort-drill", "payload": {"source": "hard-abort-target"}},
        )
        if response.status_code != 201:
            raise RuntimeError(
                f"Failed to queue workflow run. status={response.status_code} body={response.text}"
            )
        run_id = str(response.json()["run"]["run_id"])

        if not started.wait(timeout=max(1.0, float(args.startup_timeout_seconds))):
            raise RuntimeError("Worker handler did not start before timeout.")
        _wait_for_running(client, run_id, timeout_seconds=float(args.startup_timeout_seconds))

        state_file.write_text(
            json.dumps(
                {
                    "status": "running",
                    "run_id": run_id,
                    "pid": os.getpid(),
                    "timestamp_utc": _utc_iso(),
                },
                ensure_ascii=True,
                sort_keys=True,
            ),
            encoding="utf-8",
        )

        while True:
            time.sleep(1.0)


if __name__ == "__main__":
    raise SystemExit(main())
