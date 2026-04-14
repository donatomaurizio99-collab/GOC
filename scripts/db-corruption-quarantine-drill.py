from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import time
import uuid
from dataclasses import dataclass
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


@dataclass(slots=True)
class DrillPaths:
    run_dir: Path
    database_path: Path
    quarantine_dir: Path


def _create_paths(workspace_root: Path) -> DrillPaths:
    run_id = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    run_dir = workspace_root / f"db-corruption-quarantine-{run_id}"
    return DrillPaths(
        run_dir=run_dir,
        database_path=run_dir / "corrupted-startup.db",
        quarantine_dir=run_dir / "quarantine",
    )


def _seed_corrupted_database(path: Path, *, bytes_count: int) -> None:
    payload = bytes(((idx * 37 + 19) % 256 for idx in range(max(64, int(bytes_count)))))
    path.write_bytes(payload)


def _assert_sqlite_healthy(database_path: Path) -> None:
    connection = sqlite3.connect(str(database_path))
    try:
        row = connection.execute("PRAGMA quick_check").fetchone()
        _expect(row is not None, "PRAGMA quick_check returned no rows after recovery.")
        _expect(str(row[0]).lower() == "ok", f"Recovered database integrity is not ok: {row[0]!r}")
    finally:
        connection.close()


def run_drill(
    *,
    workspace_root: Path,
    label: str,
    keep_artifacts: bool,
    corruption_bytes: int,
) -> dict:
    started = time.perf_counter()
    paths = _create_paths(workspace_root)
    paths.run_dir.mkdir(parents=True, exist_ok=False)
    paths.quarantine_dir.mkdir(parents=True, exist_ok=True)
    _seed_corrupted_database(paths.database_path, bytes_count=corruption_bytes)

    try:
        app = create_app(
            Settings(
                database_url=str(paths.database_path),
                db_quarantine_dir=str(paths.quarantine_dir),
                db_startup_corruption_recovery_enabled=True,
                workflow_worker_poll_interval_seconds=0.05,
            )
        )
        with TestClient(app) as client:
            readiness_before = client.get("/system/readiness")
            _expect(readiness_before.status_code == 200, "Readiness endpoint failed after startup recovery.")
            readiness_before_payload = readiness_before.json()

            health = client.get("/system/health")
            _expect(health.status_code == 200, "Health endpoint failed after startup recovery.")
            health_payload = health.json()

            integrity = client.get("/system/database/integrity?mode=quick")
            _expect(integrity.status_code == 200, "Database integrity endpoint failed after startup recovery.")
            integrity_payload = integrity.json()

            blocked_goal = client.post(
                "/goals",
                json={
                    "title": "Blocked During Startup Recovery",
                    "description": "safe mode should block this mutation",
                    "urgency": 0.5,
                    "value": 0.5,
                    "deadline_score": 0.2,
                },
            )

            disable_safe_mode = client.post(
                "/system/safe-mode/disable",
                json={"reason": "Startup DB corruption drill completed and validated."},
            )
            _expect(
                disable_safe_mode.status_code == 200,
                f"Safe-mode disable failed: {disable_safe_mode.text}",
            )

            post_disable_goal = client.post(
                "/goals",
                json={
                    "title": "Allowed After Startup Recovery",
                    "description": "safe mode disabled after validation",
                    "urgency": 0.6,
                    "value": 0.7,
                    "deadline_score": 0.3,
                },
            )

            readiness_after = client.get("/system/readiness")
            _expect(readiness_after.status_code == 200, "Readiness endpoint failed after safe-mode disable.")
            readiness_after_payload = readiness_after.json()

        startup_recovery = (
            integrity_payload.get("startup_recovery")
            if isinstance(integrity_payload, dict)
            else {}
        )
        if not isinstance(startup_recovery, dict):
            startup_recovery = {}
        quarantined_path = str(startup_recovery.get("quarantined_path") or "")
        _expect(bool(startup_recovery.get("triggered")), "Startup recovery was not triggered by corrupted DB.")
        _expect(bool(startup_recovery.get("recovered")), "Startup recovery did not report successful recovery.")
        _expect(bool(startup_recovery.get("quarantined_exists")), "Quarantined DB file was not found.")
        _expect(quarantined_path, "Startup recovery did not report quarantined_path.")
        _expect(
            Path(quarantined_path).exists(),
            f"Reported quarantined database file does not exist: {quarantined_path}",
        )
        _expect(blocked_goal.status_code == 503, "Mutating endpoint was not blocked while safe mode was active.")
        _expect(
            post_disable_goal.status_code == 201,
            f"Mutating endpoint not restored after disabling safe mode: {post_disable_goal.text}",
        )
        _expect(
            readiness_before_payload.get("ready") is False,
            "Readiness should be false while safe mode is active after startup recovery.",
        )
        _expect(
            readiness_after_payload.get("ready") is True,
            "Readiness should recover to true after explicit safe-mode disable.",
        )

        _assert_sqlite_healthy(paths.database_path)

        return {
            "label": label,
            "success": True,
            "startup_recovery": startup_recovery,
            "blocked_status_code": int(blocked_goal.status_code),
            "post_disable_goal_create_status_code": int(post_disable_goal.status_code),
            "readiness_before": readiness_before_payload,
            "readiness_after": readiness_after_payload,
            "health_safe_mode": health_payload.get("safe_mode"),
            "database_integrity": integrity_payload.get("integrity"),
            "paths": {
                "run_dir": str(paths.run_dir),
                "database_path": str(paths.database_path),
                "quarantine_dir": str(paths.quarantine_dir),
            },
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }
    finally:
        if not keep_artifacts:
            shutil.rmtree(paths.run_dir, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Startup DB corruption quarantine drill: seed a corrupted SQLite file, verify automatic "
            "quarantine + safe-mode activation, then validate operator recovery path."
        )
    )
    parser.add_argument("--workspace", default=str(PROJECT_ROOT / ".tmp" / "db-corruption-quarantine-drills"))
    parser.add_argument("--label", default="db-corruption-quarantine-drill")
    parser.add_argument("--corruption-bytes", type=int, default=256)
    parser.add_argument("--keep-artifacts", action="store_true")
    args = parser.parse_args(argv)

    workspace_root = Path(str(args.workspace)).expanduser()
    workspace_root.mkdir(parents=True, exist_ok=True)

    try:
        report = run_drill(
            workspace_root=workspace_root,
            label=str(args.label),
            keep_artifacts=bool(args.keep_artifacts),
            corruption_bytes=int(args.corruption_bytes),
        )
    except Exception as exc:
        print(f"[db-corruption-quarantine-drill] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
