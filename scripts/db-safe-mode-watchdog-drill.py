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


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def run_drill(*, lock_error_injections: int) -> dict:
    app = create_app(
        Settings(
            database_url=":memory:",
            safe_mode_lock_error_threshold=3,
            safe_mode_lock_error_window_seconds=60,
        )
    )

    with TestClient(app) as client:
        guard = client.app.state.services.runtime_guard
        initial = client.get("/system/safe-mode").json()
        _expect(initial["active"] is False, f"Safe mode unexpectedly active: {json.dumps(initial, sort_keys=True)}")

        for _ in range(max(1, int(lock_error_injections))):
            guard.record_database_error(
                message="database table is locked: goals",
                source="safe_mode_watchdog_drill",
            )
            time.sleep(0.01)

        active = client.get("/system/safe-mode").json()
        _expect(active["active"] is True, f"Safe mode was not activated: {json.dumps(active, sort_keys=True)}")

        blocked = client.post(
            "/goals",
            json={
                "title": "Should be blocked",
                "description": "safe mode drill",
                "urgency": 0.5,
                "value": 0.5,
                "deadline_score": 0.2,
            },
        )
        _expect(
            blocked.status_code == 503,
            f"Mutating endpoint was not blocked in safe mode: status={blocked.status_code} body={blocked.text}",
        )

        reclaim = client.post("/system/consumers/drill/reclaim")
        _expect(
            reclaim.status_code == 200,
            f"Allowed reclaim endpoint failed in safe mode: status={reclaim.status_code} body={reclaim.text}",
        )

        disable = client.post(
            "/system/safe-mode/disable",
            json={"reason": "Drill cleanup"},
        )
        _expect(
            disable.status_code == 200,
            f"Failed to disable safe mode after drill: status={disable.status_code} body={disable.text}",
        )
        after_disable = client.get("/system/safe-mode").json()
        _expect(
            after_disable["active"] is False,
            f"Safe mode remained active after disable: {json.dumps(after_disable, sort_keys=True)}",
        )

        unblocked = client.post(
            "/goals",
            json={
                "title": "Allowed after safe mode",
                "description": "safe mode drill",
                "urgency": 0.5,
                "value": 0.5,
                "deadline_score": 0.2,
            },
        )
        _expect(
            unblocked.status_code == 201,
            f"Mutating endpoint stayed blocked after safe mode disable: status={unblocked.status_code} body={unblocked.text}",
        )

        return {
            "success": True,
            "lock_error_injections": int(lock_error_injections),
            "safe_mode_active_after_injection": bool(active["active"]),
            "blocked_status_code": int(blocked.status_code),
            "allowed_reclaim_status_code": int(reclaim.status_code),
            "safe_mode_active_after_disable": bool(after_disable["active"]),
            "post_disable_goal_create_status_code": int(unblocked.status_code),
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Watchdog drill for DB lock bursts: trigger runtime safe mode, verify mutating API block, "
            "ensure diagnostics/reclaim path remains available, then recover."
        )
    )
    parser.add_argument("--lock-error-injections", type=int, default=4)
    args = parser.parse_args(argv)

    if int(args.lock_error_injections) <= 0:
        print("[db-safe-mode-watchdog-drill] ERROR: --lock-error-injections must be > 0.", file=sys.stderr)
        return 2

    try:
        report = run_drill(lock_error_injections=int(args.lock_error_injections))
    except Exception as exc:
        print(f"[db-safe-mode-watchdog-drill] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
