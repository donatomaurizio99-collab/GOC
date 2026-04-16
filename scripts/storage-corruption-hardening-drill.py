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


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        str(path),
        check_same_thread=False,
        isolation_level=None,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def _artifact_info(database_path: Path) -> dict[str, dict[str, Any]]:
    artifacts: dict[str, dict[str, Any]] = {}
    for suffix in ("-wal", "-shm", "-journal"):
        artifact_path = Path(str(database_path) + suffix)
        exists = artifact_path.exists()
        artifacts[suffix] = {
            "path": str(artifact_path),
            "exists": exists,
            "size_bytes": int(artifact_path.stat().st_size) if exists else 0,
        }
    return artifacts


def _seed_case_database(
    *,
    database_path: Path,
    marker: str,
    journal_mode: str,
    rows: int,
    payload_bytes: int,
) -> dict[str, Any]:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = _connect(database_path)
    try:
        observed_journal_mode = str(connection.execute(f"PRAGMA journal_mode={journal_mode}").fetchone()[0]).lower()
        connection.execute("PRAGMA synchronous = FULL")
        if observed_journal_mode == "wal":
            connection.execute("PRAGMA wal_autocheckpoint = 0")

        connection.execute(
            """CREATE TABLE IF NOT EXISTS storage_corruption_probe (
                   marker      TEXT NOT NULL,
                   seq         INTEGER NOT NULL,
                   payload     TEXT NOT NULL,
                   created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                   PRIMARY KEY (marker, seq)
               )"""
        )
        payload_blob = "x" * max(64, int(payload_bytes))
        connection.execute("BEGIN IMMEDIATE")
        for index in range(max(1, int(rows))):
            payload = json.dumps(
                {"marker": marker, "seq": index, "payload": payload_blob},
                ensure_ascii=True,
                sort_keys=True,
            )
            connection.execute(
                """INSERT INTO storage_corruption_probe (marker, seq, payload)
                   VALUES (?, ?, ?)""",
                (marker, index, payload),
            )
        connection.commit()
        persisted_rows = int(
            connection.execute(
                "SELECT COUNT(*) FROM storage_corruption_probe WHERE marker = ?",
                (marker,),
            ).fetchone()[0]
        )
        return {
            "journal_mode": observed_journal_mode,
            "persisted_rows": persisted_rows,
        }
    finally:
        connection.close()


def _ensure_anomaly_file(*, database_path: Path, suffix: str, bytes_count: int) -> Path:
    target = Path(str(database_path) + str(suffix))
    payload = bytes(((index * 41 + 13) % 256 for index in range(max(64, int(bytes_count)))))
    if target.exists():
        with target.open("ab") as handle:
            handle.write(payload)
    else:
        target.write_bytes(payload)
    return target


def _corrupt_main_database(*, database_path: Path, corruption_bytes: int, offset: int = 96) -> None:
    raw = bytearray(database_path.read_bytes())
    _expect(len(raw) > int(offset), f"Database file too small to corrupt: {database_path}")
    mutate_count = min(max(1, int(corruption_bytes)), len(raw) - int(offset))
    for idx in range(mutate_count):
        raw[int(offset) + idx] ^= ((idx * 31 + 17) % 251) + 1
    database_path.write_bytes(bytes(raw))


def _assert_sqlite_healthy(database_path: Path) -> None:
    connection = sqlite3.connect(str(database_path))
    try:
        row = connection.execute("PRAGMA quick_check").fetchone()
        _expect(row is not None, "PRAGMA quick_check returned no rows after recovery.")
        _expect(str(row[0]).lower() == "ok", f"Recovered database integrity is not ok: {row[0]!r}")
    finally:
        connection.close()


def _exercise_startup_recovery(
    *,
    case_name: str,
    database_path: Path,
    quarantine_dir: Path,
) -> dict[str, Any]:
    app = create_app(
        Settings(
            database_url=str(database_path),
            db_quarantine_dir=str(quarantine_dir),
            db_startup_corruption_recovery_enabled=True,
            workflow_worker_poll_interval_seconds=0.05,
            workflow_startup_recovery_max_age_seconds=0,
        )
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        readiness_before = client.get("/system/readiness")
        health = client.get("/system/health")
        integrity = client.get("/system/database/integrity?mode=quick")

        _expect(readiness_before.status_code == 200, f"Readiness endpoint failed for {case_name}.")
        _expect(health.status_code == 200, f"Health endpoint failed for {case_name}.")
        _expect(integrity.status_code == 200, f"Integrity endpoint failed for {case_name}.")

        blocked_goal = client.post(
            "/goals",
            json={
                "title": f"Blocked During Startup Recovery ({case_name})",
                "description": "safe mode should block this mutation",
                "urgency": 0.5,
                "value": 0.5,
                "deadline_score": 0.2,
            },
        )
        _expect(
            blocked_goal.status_code == 503,
            f"Mutating endpoint was not blocked while safe mode active ({case_name}).",
        )

        disable = client.post(
            "/system/safe-mode/disable",
            json={"reason": f"Storage corruption hardening drill validated ({case_name})."},
        )
        _expect(
            disable.status_code == 200,
            f"Safe mode disable failed for {case_name}: status={disable.status_code} body={disable.text!r}",
        )

        created_goal = client.post(
            "/goals",
            json={
                "title": f"Allowed After Recovery ({case_name})",
                "description": "safe mode disabled after validation",
                "urgency": 0.6,
                "value": 0.7,
                "deadline_score": 0.3,
            },
        )
        _expect(
            created_goal.status_code == 201,
            f"Mutating endpoint not restored after safe mode disable ({case_name}): {created_goal.text!r}",
        )

        readiness_after = client.get("/system/readiness")
        _expect(readiness_after.status_code == 200, f"Readiness endpoint failed after recovery ({case_name}).")

    readiness_before_payload = readiness_before.json()
    readiness_after_payload = readiness_after.json()
    health_payload = health.json()
    integrity_payload = integrity.json()

    startup_recovery = integrity_payload.get("startup_recovery") if isinstance(integrity_payload, dict) else {}
    if not isinstance(startup_recovery, dict):
        startup_recovery = {}
    quarantined_path = str(startup_recovery.get("quarantined_path") or "")

    _expect(
        bool(startup_recovery.get("triggered")),
        f"Startup recovery not triggered for corrupted case {case_name}.",
    )
    _expect(
        bool(startup_recovery.get("recovered")),
        f"Startup recovery did not recover for case {case_name}.",
    )
    _expect(
        bool(startup_recovery.get("quarantined_exists")),
        f"Quarantined DB file not found for case {case_name}.",
    )
    _expect(bool(quarantined_path), f"Missing quarantined_path for case {case_name}.")
    _expect(Path(quarantined_path).exists(), f"Quarantined file does not exist: {quarantined_path}")
    _expect(
        readiness_before_payload.get("ready") is False,
        f"Readiness should be false while safe mode active ({case_name}): {readiness_before_payload}",
    )
    _expect(
        readiness_after_payload.get("ready") is True,
        f"Readiness should recover to true after safe mode disable ({case_name}): {readiness_after_payload}",
    )

    _assert_sqlite_healthy(database_path)
    return {
        "startup_recovery": startup_recovery,
        "blocked_status_code": int(blocked_goal.status_code),
        "post_disable_goal_create_status_code": int(created_goal.status_code),
        "readiness_before": readiness_before_payload,
        "readiness_after": readiness_after_payload,
        "health_safe_mode": health_payload.get("safe_mode"),
        "database_integrity": integrity_payload.get("integrity"),
        "recovered_goal_id": str((created_goal.json() or {}).get("goal_id")),
    }


def _run_case(
    *,
    case_name: str,
    case_dir: Path,
    journal_mode: str,
    anomaly_suffix: str,
    corruption_bytes: int,
    rows: int,
    payload_bytes: int,
) -> dict[str, Any]:
    case_dir.mkdir(parents=True, exist_ok=False)
    database_path = case_dir / "corrupted.db"
    quarantine_dir = case_dir / "quarantine"
    marker = f"{case_name}-{uuid.uuid4().hex[:10]}"

    seeded = _seed_case_database(
        database_path=database_path,
        marker=marker,
        journal_mode=journal_mode,
        rows=int(rows),
        payload_bytes=int(payload_bytes),
    )
    artifacts_before = _artifact_info(database_path)
    anomaly_file = _ensure_anomaly_file(
        database_path=database_path,
        suffix=anomaly_suffix,
        bytes_count=max(64, int(corruption_bytes)),
    )
    _corrupt_main_database(
        database_path=database_path,
        corruption_bytes=int(corruption_bytes),
    )
    artifacts_after_corruption = _artifact_info(database_path)
    recovery = _exercise_startup_recovery(
        case_name=case_name,
        database_path=database_path,
        quarantine_dir=quarantine_dir,
    )
    artifacts_after_recovery = _artifact_info(database_path)

    return {
        "name": case_name,
        "success": True,
        "seeded": seeded,
        "anomaly_suffix": anomaly_suffix,
        "anomaly_file": str(anomaly_file),
        "artifacts": {
            "before_corruption": artifacts_before,
            "after_corruption": artifacts_after_corruption,
            "after_recovery": artifacts_after_recovery,
        },
        "recovery": recovery,
        "paths": {
            "case_dir": str(case_dir),
            "database_path": str(database_path),
            "quarantine_dir": str(quarantine_dir),
        },
    }


@dataclass(slots=True)
class DrillPaths:
    run_dir: Path
    cases_root: Path


def _create_paths(workspace_root: Path) -> DrillPaths:
    run_id = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    run_dir = workspace_root / f"storage-corruption-hardening-{run_id}"
    return DrillPaths(run_dir=run_dir, cases_root=run_dir / "cases")


def run_drill(
    *,
    workspace_root: Path,
    label: str,
    corruption_bytes: int,
    rows: int,
    payload_bytes: int,
    keep_artifacts: bool,
) -> dict[str, Any]:
    started = time.perf_counter()
    paths = _create_paths(workspace_root)
    paths.cases_root.mkdir(parents=True, exist_ok=False)

    cases = (
        ("wal_file_anomaly", "WAL", "-wal"),
        ("rollback_journal_anomaly", "DELETE", "-journal"),
    )

    try:
        reports: list[dict[str, Any]] = []
        for case_name, journal_mode, anomaly_suffix in cases:
            case_report = _run_case(
                case_name=case_name,
                case_dir=paths.cases_root / case_name,
                journal_mode=journal_mode,
                anomaly_suffix=anomaly_suffix,
                corruption_bytes=int(corruption_bytes),
                rows=int(rows),
                payload_bytes=int(payload_bytes),
            )
            reports.append(case_report)

        return {
            "label": label,
            "success": True,
            "cases": reports,
            "paths": {
                "run_dir": str(paths.run_dir),
                "cases_root": str(paths.cases_root),
            },
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }
    finally:
        if not keep_artifacts:
            shutil.rmtree(paths.run_dir, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Storage corruption hardening drill: exercise WAL and rollback-journal anomaly scenarios "
            "with deterministic startup quarantine recovery, safe-mode gating, and post-recovery integrity."
        )
    )
    parser.add_argument("--workspace", default=str(PROJECT_ROOT / ".tmp" / "storage-corruption-hardening-drills"))
    parser.add_argument("--label", default="storage-corruption-hardening-drill")
    parser.add_argument("--corruption-bytes", type=int, default=192)
    parser.add_argument("--rows", type=int, default=80)
    parser.add_argument("--payload-bytes", type=int, default=128)
    parser.add_argument("--keep-artifacts", action="store_true")
    args = parser.parse_args(argv)

    if int(args.corruption_bytes) <= 0:
        print("[storage-corruption-hardening-drill] ERROR: --corruption-bytes must be > 0.", file=sys.stderr)
        return 2
    if int(args.rows) <= 0:
        print("[storage-corruption-hardening-drill] ERROR: --rows must be > 0.", file=sys.stderr)
        return 2
    if int(args.payload_bytes) <= 0:
        print("[storage-corruption-hardening-drill] ERROR: --payload-bytes must be > 0.", file=sys.stderr)
        return 2

    workspace_root = Path(str(args.workspace)).expanduser()
    workspace_root.mkdir(parents=True, exist_ok=True)
    try:
        report = run_drill(
            workspace_root=workspace_root,
            label=str(args.label),
            corruption_bytes=int(args.corruption_bytes),
            rows=int(args.rows),
            payload_bytes=int(args.payload_bytes),
            keep_artifacts=bool(args.keep_artifacts),
        )
    except Exception as exc:
        print(f"[storage-corruption-hardening-drill] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
