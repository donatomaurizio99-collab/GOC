from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from goal_ops_console.config import Settings
from goal_ops_console.main import create_app

TERMINAL_STATUSES = {"succeeded", "failed", "timed_out", "cancelled"}


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _wait_for_terminal_run(client: TestClient, run_id: str, timeout_seconds: float) -> dict:
    deadline = time.time() + max(1.0, float(timeout_seconds))
    while time.time() < deadline:
        response = client.get(f"/workflows/runs/{run_id}")
        if response.status_code == 200:
            run = response.json()["run"]
            if str(run["status"]) in TERMINAL_STATUSES:
                return run
        time.sleep(0.05)
    raise RuntimeError(f"Run {run_id} did not reach terminal state in {timeout_seconds}s.")


def run_drill(*, timeout_seconds: float) -> dict:
    app = create_app(
        Settings(
            database_url=":memory:",
            workflow_worker_poll_interval_seconds=0.02,
            workflow_run_timeout_seconds=60,
        )
    )

    with TestClient(app) as client:
        catalog = client.app.state.services.workflow_catalog
        catalog.stop_worker()

        readiness_before = client.get("/system/readiness").json()
        _expect(
            bool(readiness_before["ready"]) is False,
            f"Expected readiness false after worker stop, got: {json.dumps(readiness_before, sort_keys=True)}",
        )
        _expect(
            bool(readiness_before["checks"]["workflow_worker"]["is_running"]) is False,
            "Expected worker to be stopped before restart drill.",
        )

        started = client.post(
            "/workflows/maintenance.retention_cleanup/start",
            json={"requested_by": "worker-restart-drill", "payload": {"source": "drill"}},
        )
        _expect(started.status_code == 201, f"Failed to start workflow: {started.text}")
        run_id = str(started.json()["run"]["run_id"])

        final_run = _wait_for_terminal_run(client, run_id, timeout_seconds=timeout_seconds)
        readiness_after = client.get("/system/readiness").json()
        worker_after = readiness_after["checks"]["workflow_worker"]

        success = (
            final_run["status"] == "succeeded"
            and bool(readiness_after["ready"])
            and bool(worker_after["is_running"])
            and bool(worker_after.get("startup_recovery_ok", True))
        )
        _expect(
            success,
            (
                "Workflow worker restart drill failed. "
                f"final_run={json.dumps(final_run, sort_keys=True)} "
                f"readiness_before={json.dumps(readiness_before, sort_keys=True)} "
                f"readiness_after={json.dumps(readiness_after, sort_keys=True)}"
            ),
        )

        return {
            "success": True,
            "run": {"run_id": run_id, "status": final_run["status"]},
            "before": {
                "ready": bool(readiness_before["ready"]),
                "worker_running": bool(readiness_before["checks"]["workflow_worker"]["is_running"]),
            },
            "after": {
                "ready": bool(readiness_after["ready"]),
                "worker_running": bool(worker_after["is_running"]),
                "startup_recovery_ok": bool(worker_after.get("startup_recovery_ok", True)),
            },
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Stop the workflow worker, then start a workflow run and verify automatic worker restart "
            "plus successful run completion."
        )
    )
    parser.add_argument("--timeout-seconds", type=float, default=12.0)
    args = parser.parse_args(argv)

    if float(args.timeout_seconds) <= 0:
        print("[workflow-worker-restart-drill] ERROR: --timeout-seconds must be > 0.", file=sys.stderr)
        return 2

    try:
        report = run_drill(timeout_seconds=float(args.timeout_seconds))
    except Exception as exc:
        print(f"[workflow-worker-restart-drill] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
