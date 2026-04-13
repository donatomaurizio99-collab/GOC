from __future__ import annotations

import argparse
import json
import sqlite3
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
            status = str(run["status"])
            if status in TERMINAL_STATUSES:
                return run
        time.sleep(0.05)
    raise RuntimeError(f"Run {run_id} did not reach terminal status within {timeout_seconds}s.")


def run_drill(*, lock_failures: int, timeout_seconds: float) -> dict:
    app = create_app(
        Settings(
            database_url=":memory:",
            workflow_worker_poll_interval_seconds=0.05,
            workflow_run_timeout_seconds=60,
        )
    )

    with TestClient(app) as client:
        services = client.app.state.services
        catalog = services.workflow_catalog
        original_claim = catalog._claim_next_queued_run
        remaining_failures = {"value": max(0, int(lock_failures))}

        def _flaky_claim():
            if remaining_failures["value"] > 0:
                remaining_failures["value"] -= 1
                raise sqlite3.OperationalError("database table is locked: workflow_runs")
            return original_claim()

        catalog._claim_next_queued_run = _flaky_claim  # type: ignore[assignment]

        started = client.post(
            "/workflows/maintenance.retention_cleanup/start",
            json={"requested_by": "lock-resilience-drill", "payload": {"source": "drill"}},
        )
        _expect(started.status_code == 201, f"Failed to queue workflow run: {started.text}")
        run_id = str(started.json()["run"]["run_id"])

        final_run = _wait_for_terminal_run(client, run_id, timeout_seconds=timeout_seconds)
        worker_status = catalog.worker_status()
        lock_conflict_metric = int(
            services.db.fetch_scalar(
                "SELECT value FROM metrics_counters WHERE metric_name = ?",
                "workflows.worker.lock_conflicts",
            )
            or 0
        )

        success = (
            final_run["status"] == "succeeded"
            and bool(worker_status["is_running"])
            and lock_conflict_metric >= int(lock_failures)
        )
        _expect(
            success,
            (
                "Workflow lock resilience drill failed. "
                f"run={json.dumps(final_run, sort_keys=True)} "
                f"worker_status={json.dumps(worker_status, sort_keys=True)} "
                f"lock_conflict_metric={lock_conflict_metric} requested_failures={lock_failures}"
            ),
        )

        return {
            "success": True,
            "run": {
                "run_id": run_id,
                "status": final_run["status"],
            },
            "worker_status": worker_status,
            "lock_conflict_metric": lock_conflict_metric,
            "requested_lock_failures": int(lock_failures),
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Inject transient SQLite lock conflicts into workflow claim path and verify "
            "worker resilience with successful completion."
        )
    )
    parser.add_argument("--lock-failures", type=int, default=5)
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    args = parser.parse_args(argv)

    if int(args.lock_failures) < 0:
        print("[workflow-lock-resilience-drill] ERROR: --lock-failures must be >= 0.", file=sys.stderr)
        return 2

    try:
        report = run_drill(
            lock_failures=int(args.lock_failures),
            timeout_seconds=float(args.timeout_seconds),
        )
    except Exception as exc:
        print(f"[workflow-lock-resilience-drill] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
