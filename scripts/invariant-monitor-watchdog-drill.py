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
from goal_ops_console.database import new_id, now_utc
from goal_ops_console.main import create_app


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def run_drill(*, timeout_seconds: float) -> dict:
    app = create_app(
        Settings(
            database_url=":memory:",
            invariant_monitor_interval_seconds=1,
            invariant_monitor_auto_safe_mode=True,
        )
    )

    with TestClient(app) as client:
        monitor_before = client.get("/system/invariants").json()["monitor"]
        _expect(bool(monitor_before["is_running"]), f"Invariant monitor is not running: {monitor_before}")

        goal_id = new_id()
        timestamp = now_utc()
        services = client.app.state.services
        services.db.execute(
            """INSERT INTO goals
               (goal_id, title, description, state, blocked_reason, escalation_reason, version, created_at, updated_at)
               VALUES (?, 'Invariant monitor drill goal', 'Injected violation', 'archived', NULL, NULL, 1, ?, ?)""",
            goal_id,
            timestamp,
            timestamp,
        )
        services.db.execute(
            """INSERT INTO goal_queue
               (goal_id, urgency, value, deadline_score, base_priority, priority, wait_cycles, force_promoted,
                status, version, created_at, updated_at)
               VALUES (?, 0.1, 0.1, 0.1, 0.1, 0.1, 0, 0, 'queued', 1, ?, ?)""",
            goal_id,
            timestamp,
            timestamp,
        )

        deadline = time.time() + max(1.0, float(timeout_seconds))
        detected_payload: dict | None = None
        while time.time() < deadline:
            payload = client.get("/system/invariants").json()
            monitor = payload["monitor"]
            if int(monitor.get("violation_count") or 0) > 0:
                detected_payload = payload
                break
            time.sleep(0.1)
        _expect(detected_payload is not None, "Invariant monitor did not detect injected violation in time.")

        safe_mode = client.get("/system/safe-mode").json()
        _expect(
            bool(safe_mode["active"]),
            f"Safe mode was not activated by invariant monitor auto-safe-mode: {json.dumps(safe_mode, sort_keys=True)}",
        )

        blocked = client.post(
            "/goals",
            json={
                "title": "blocked by invariant monitor",
                "description": "should be blocked",
                "urgency": 0.4,
                "value": 0.4,
                "deadline_score": 0.2,
            },
        )
        _expect(
            blocked.status_code == 503,
            f"Mutating endpoint was not blocked after monitor-triggered safe mode: {blocked.status_code} {blocked.text}",
        )

        disable = client.post("/system/safe-mode/disable", json={"reason": "Invariant monitor drill cleanup"})
        _expect(disable.status_code == 200, f"Failed to disable safe mode: {disable.text}")

        return {
            "success": True,
            "detected_violation_count": int(detected_payload["monitor"]["violation_count"]),
            "safe_mode_active_after_detection": bool(safe_mode["active"]),
            "blocked_status_code": int(blocked.status_code),
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Invariant monitor watchdog drill: inject a queue/state consistency violation and verify "
            "periodic monitor detection plus auto-safe-mode activation."
        )
    )
    parser.add_argument("--timeout-seconds", type=float, default=8.0)
    args = parser.parse_args(argv)

    if float(args.timeout_seconds) <= 0:
        print("[invariant-monitor-watchdog-drill] ERROR: --timeout-seconds must be > 0.", file=sys.stderr)
        return 2

    try:
        report = run_drill(timeout_seconds=float(args.timeout_seconds))
    except Exception as exc:
        print(f"[invariant-monitor-watchdog-drill] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
