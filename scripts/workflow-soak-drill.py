from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
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


def _wait_for_runs_terminal(client: TestClient, run_ids: list[str], timeout_seconds: float) -> list[dict]:
    deadline = time.time() + max(1.0, float(timeout_seconds))
    terminal_by_id: dict[str, dict] = {}

    while time.time() < deadline:
        pending = [run_id for run_id in run_ids if run_id not in terminal_by_id]
        if not pending:
            return [terminal_by_id[run_id] for run_id in run_ids]

        for run_id in pending:
            response = client.get(f"/workflows/runs/{run_id}")
            if response.status_code != 200:
                continue
            run = response.json()["run"]
            if str(run["status"]) in TERMINAL_STATUSES:
                terminal_by_id[run_id] = run
        time.sleep(0.05)

    still_pending = [run_id for run_id in run_ids if run_id not in terminal_by_id]
    raise RuntimeError(
        f"Workflow soak drill timed out; pending runs={still_pending} after {timeout_seconds}s."
    )


def run_drill(*, run_count: int, timeout_seconds: float) -> dict:
    app = create_app(
        Settings(
            database_url=":memory:",
            workflow_worker_poll_interval_seconds=0.02,
            workflow_run_timeout_seconds=120,
        )
    )

    with TestClient(app) as client:
        run_ids: list[str] = []
        for index in range(int(run_count)):
            response = client.post(
                "/workflows/maintenance.retention_cleanup/start",
                json={
                    "requested_by": "workflow-soak-drill",
                    "payload": {"index": index, "source": "soak"},
                },
            )
            _expect(response.status_code == 201, f"Failed to enqueue run #{index}: {response.text}")
            run_ids.append(str(response.json()["run"]["run_id"]))

        terminal_runs = _wait_for_runs_terminal(client, run_ids, timeout_seconds=timeout_seconds)
        status_counts = Counter(str(item["status"]) for item in terminal_runs)

        readiness = client.get("/system/readiness").json()
        slo = client.get("/system/slo").json()
        worker_status = readiness["checks"]["workflow_worker"]

        success = (
            status_counts.get("succeeded", 0) == int(run_count)
            and status_counts.get("failed", 0) == 0
            and status_counts.get("timed_out", 0) == 0
            and status_counts.get("cancelled", 0) == 0
            and bool(readiness["ready"])
            and bool(worker_status["is_running"])
            and int(worker_status["running_runs"]) == 0
            and int(worker_status["queued_runs"]) == 0
            and str(slo["status"]) == "ok"
        )
        _expect(
            success,
            (
                "Workflow soak drill failed. "
                f"status_counts={json.dumps(dict(status_counts), sort_keys=True)} "
                f"readiness={json.dumps(readiness, sort_keys=True)} "
                f"slo={json.dumps(slo, sort_keys=True)}"
            ),
        )

        return {
            "success": True,
            "run_count": int(run_count),
            "status_counts": dict(status_counts),
            "readiness": {
                "ready": bool(readiness["ready"]),
                "worker_status": worker_status,
            },
            "slo_status": str(slo["status"]),
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run workflow soak drill: enqueue a burst of workflow runs and verify terminal completion, "
            "readiness true, and zero hanging runs."
        )
    )
    parser.add_argument("--run-count", type=int, default=40)
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    args = parser.parse_args(argv)

    if int(args.run_count) <= 0:
        print("[workflow-soak-drill] ERROR: --run-count must be > 0.", file=sys.stderr)
        return 2

    try:
        report = run_drill(
            run_count=int(args.run_count),
            timeout_seconds=float(args.timeout_seconds),
        )
    except Exception as exc:
        print(f"[workflow-soak-drill] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
