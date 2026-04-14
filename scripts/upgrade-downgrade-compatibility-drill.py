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
from goal_ops_console.database import Database, new_id, now_utc
from goal_ops_console.main import create_app

LEGACY_WORKFLOW_RUNS_SCHEMA = """
PRAGMA foreign_keys = OFF;
BEGIN IMMEDIATE;
ALTER TABLE workflow_runs RENAME TO workflow_runs_vnext;
CREATE TABLE workflow_runs (
  run_id         TEXT PRIMARY KEY,
  workflow_id    TEXT NOT NULL,
  status         TEXT NOT NULL,
  requested_by   TEXT NOT NULL,
  correlation_id TEXT NOT NULL,
  input_payload  TEXT,
  result_payload TEXT,
  started_at     TEXT NOT NULL,
  finished_at    TEXT,
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL,
  FOREIGN KEY(workflow_id) REFERENCES workflow_definitions(workflow_id)
);
INSERT INTO workflow_runs
  (run_id, workflow_id, status, requested_by, correlation_id, input_payload, result_payload, started_at, finished_at, created_at, updated_at)
SELECT run_id, workflow_id, status, requested_by, correlation_id, input_payload, result_payload, started_at, finished_at, created_at, updated_at
FROM workflow_runs_vnext;
DROP TABLE workflow_runs_vnext;
CREATE INDEX IF NOT EXISTS idx_workflow_runs_workflow_created_at
ON workflow_runs(workflow_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_created_at
ON workflow_runs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_status_created_at
ON workflow_runs(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_correlation_id
ON workflow_runs(correlation_id, created_at DESC);
DELETE FROM schema_migrations WHERE version = 1;
COMMIT;
PRAGMA foreign_keys = ON;
"""


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


def _quoted_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _copy_database(source_path: Path, target_path: Path) -> int:
    started = time.perf_counter()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if target_path.exists():
        target_path.unlink()
    source_conn = _connect(source_path)
    target_conn = _connect(target_path)
    try:
        source_conn.backup(target_conn)
    finally:
        target_conn.close()
        source_conn.close()
    return int((time.perf_counter() - started) * 1000)


def _integrity_report(path: Path) -> dict[str, Any]:
    conn = _connect(path)
    try:
        quick_rows = [str(row[0]) for row in conn.execute("PRAGMA quick_check").fetchall()]
        full_rows = [str(row[0]) for row in conn.execute("PRAGMA integrity_check").fetchall()]
    finally:
        conn.close()
    quick_ok = len(quick_rows) == 1 and quick_rows[0].lower() == "ok"
    full_ok = len(full_rows) == 1 and full_rows[0].lower() == "ok"
    return {
        "quick_ok": quick_ok,
        "quick_result": "ok" if quick_ok else "; ".join(quick_rows) if quick_rows else "no result",
        "full_ok": full_ok,
        "full_result": "ok" if full_ok else "; ".join(full_rows) if full_rows else "no result",
    }


def _table_counts(path: Path) -> dict[str, int]:
    conn = _connect(path)
    try:
        rows = conn.execute(
            """SELECT name
               FROM sqlite_master
               WHERE type = 'table'
               AND name NOT LIKE 'sqlite_%'
               ORDER BY name ASC"""
        ).fetchall()
        counts: dict[str, int] = {}
        for row in rows:
            table_name = str(row[0])
            query = f"SELECT COUNT(*) FROM {_quoted_identifier(table_name)}"
            counts[table_name] = int(conn.execute(query).fetchone()[0])
        return counts
    finally:
        conn.close()


def _workflow_runs_columns(path: Path) -> list[str]:
    conn = _connect(path)
    try:
        rows = conn.execute("PRAGMA table_info(workflow_runs)").fetchall()
        return [str(row[1]) for row in rows]
    finally:
        conn.close()


def _seed_database(path: Path, *, run_rows: int, payload_bytes: int) -> dict[str, Any]:
    db = Database(str(path))
    db.initialize()
    timestamp = now_utc()
    payload_blob = "x" * max(64, int(payload_bytes))

    workflow_id = "drill.upgrade_downgrade_compatibility"
    db.execute(
        """INSERT OR IGNORE INTO workflow_definitions
           (workflow_id, name, description, entrypoint, is_enabled, version, created_at, updated_at)
           VALUES (?, ?, ?, ?, 1, 1, ?, ?)""",
        workflow_id,
        "Upgrade Downgrade Compatibility Drill",
        "Synthetic workflow used for compatibility drill",
        "maintenance.retention_cleanup",
        timestamp,
        timestamp,
    )

    status_cycle = ("succeeded", "failed", "cancelled")
    for idx in range(max(1, int(run_rows))):
        run_id = new_id()
        correlation_id = f"upgrade-downgrade:{idx}"
        status = status_cycle[idx % len(status_cycle)]
        db.execute(
            """INSERT INTO workflow_runs
               (run_id, workflow_id, status, requested_by, correlation_id, idempotency_key,
                input_payload, result_payload, started_at, finished_at, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?)""",
            run_id,
            workflow_id,
            status,
            "compatibility-drill",
            correlation_id,
            json.dumps({"seed": idx, "payload": payload_blob}, sort_keys=True),
            json.dumps({"ok": True, "seed": idx}, sort_keys=True),
            timestamp,
            timestamp,
            timestamp,
            timestamp,
        )

    goal_id = new_id()
    task_id = new_id()
    db.execute(
        """INSERT INTO goals
           (goal_id, title, description, state, blocked_reason, escalation_reason, version, created_at, updated_at)
           VALUES (?, ?, ?, 'active', NULL, NULL, 1, ?, ?)""",
        goal_id,
        "Compatibility Drill Goal",
        "Data preservation check",
        timestamp,
        timestamp,
    )
    db.execute(
        """INSERT INTO goal_queue
           (goal_id, status, created_at, updated_at)
           VALUES (?, 'active', ?, ?)""",
        goal_id,
        timestamp,
        timestamp,
    )
    db.execute(
        """INSERT INTO tasks
           (task_id, goal_id, title, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?)""",
        task_id,
        goal_id,
        "Compatibility Drill Task",
        timestamp,
        timestamp,
    )
    db.execute(
        """INSERT INTO task_state
           (task_id, goal_id, correlation_id, status, retry_count, failure_type, error_hash, version, created_at, updated_at)
           VALUES (?, ?, ?, 'pending', 0, NULL, NULL, 1, ?, ?)""",
        task_id,
        goal_id,
        f"{goal_id}:0",
        timestamp,
        timestamp,
    )
    db.execute(
        """INSERT INTO events
           (event_id, event_type, entity_id, correlation_id, payload, emitted_at)
           VALUES (?, 'compatibility.seeded', ?, ?, ?, ?)""",
        new_id(),
        goal_id,
        goal_id,
        json.dumps({"goal_id": goal_id, "task_id": task_id}, sort_keys=True),
        timestamp,
    )
    return {
        "workflow_id": workflow_id,
        "run_rows": int(run_rows),
        "goal_id": goal_id,
        "task_id": task_id,
    }


def _convert_to_n_minus_1(path: Path) -> None:
    conn = _connect(path)
    try:
        conn.executescript(LEGACY_WORKFLOW_RUNS_SCHEMA)
    finally:
        conn.close()


def _schema_snapshot(path: Path) -> dict[str, Any]:
    columns = _workflow_runs_columns(path)
    conn = _connect(path)
    try:
        migration_rows = conn.execute("SELECT version FROM schema_migrations ORDER BY version ASC").fetchall()
        migration_versions = [int(row[0]) for row in migration_rows]
    finally:
        conn.close()
    return {
        "workflow_runs_columns": columns,
        "has_idempotency_key": "idempotency_key" in columns,
        "migration_versions": migration_versions,
        "has_migration_v1": 1 in migration_versions,
    }


def _readiness_slo_probe(path: Path, *, label: str) -> dict[str, Any]:
    app = create_app(
        Settings(
            database_url=str(path),
            workflow_worker_poll_interval_seconds=0.05,
            workflow_startup_recovery_max_age_seconds=0,
        )
    )
    with TestClient(app) as client:
        readiness = client.get("/system/readiness")
        slo = client.get("/system/slo")
        integrity = client.get("/system/database/integrity?mode=quick")
    _expect(readiness.status_code == 200, f"{label}: readiness returned {readiness.status_code}")
    _expect(slo.status_code == 200, f"{label}: slo returned {slo.status_code}")
    _expect(integrity.status_code == 200, f"{label}: integrity returned {integrity.status_code}")
    readiness_payload = readiness.json()
    slo_payload = slo.json()
    integrity_payload = integrity.json()
    _expect(bool(readiness_payload.get("ready")), f"{label}: readiness false -> {readiness_payload}")
    _expect(str(slo_payload.get("status")) == "ok", f"{label}: SLO status not ok -> {slo_payload}")
    _expect(
        bool((integrity_payload.get("integrity") or {}).get("ok")),
        f"{label}: quick integrity not ok -> {integrity_payload}",
    )
    return {
        "label": label,
        "readiness": readiness_payload,
        "slo": slo_payload,
        "integrity": integrity_payload,
    }


def _legacy_probe(path: Path) -> dict[str, Any]:
    conn = _connect(path)
    try:
        columns = [str(row[1]) for row in conn.execute("PRAGMA table_info(workflow_runs)").fetchall()]
        _expect("idempotency_key" not in columns, "Legacy probe expected workflow_runs without idempotency_key")

        workflow_row = conn.execute(
            "SELECT workflow_id FROM workflow_definitions ORDER BY workflow_id ASC LIMIT 1"
        ).fetchone()
        _expect(workflow_row is not None, "Legacy probe found no workflow definitions")
        workflow_id = str(workflow_row[0])
        run_id = new_id()
        timestamp = now_utc()
        conn.execute(
            """INSERT INTO workflow_runs
               (run_id, workflow_id, status, requested_by, correlation_id, input_payload, result_payload, started_at, finished_at, created_at, updated_at)
               VALUES (?, ?, 'succeeded', ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id,
                workflow_id,
                "legacy-probe",
                f"legacy:{run_id[:8]}",
                json.dumps({"probe": "legacy"}, sort_keys=True),
                json.dumps({"ok": True}, sort_keys=True),
                timestamp,
                timestamp,
                timestamp,
                timestamp,
            ),
        )
        inserted = conn.execute(
            "SELECT status FROM workflow_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        _expect(inserted is not None, "Legacy probe failed to read inserted workflow run")
        return {
            "inserted_run_id": run_id,
            "inserted_status": str(inserted[0]),
            "workflow_runs_columns": columns,
        }
    finally:
        conn.close()


def _counts_for_data_loss_check(counts: dict[str, int]) -> dict[str, int]:
    return {
        name: int(value)
        for name, value in counts.items()
        if str(name) != "schema_migrations"
    }


@dataclass(slots=True)
class DrillPaths:
    run_dir: Path
    n_minus_1_db: Path
    upgrade_db: Path
    rollback_db: Path
    rollback_probe_db: Path
    reupgrade_db: Path
    migration_backups_dir: Path


def _create_paths(workspace_root: Path) -> DrillPaths:
    run_id = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    run_dir = workspace_root / f"upgrade-downgrade-compatibility-{run_id}"
    return DrillPaths(
        run_dir=run_dir,
        n_minus_1_db=run_dir / "n-minus-1.db",
        upgrade_db=run_dir / "upgrade.db",
        rollback_db=run_dir / "rollback.db",
        rollback_probe_db=run_dir / "rollback-legacy-probe.db",
        reupgrade_db=run_dir / "reupgrade.db",
        migration_backups_dir=run_dir / "migration-backups",
    )


def run_drill(
    *,
    workspace_root: Path,
    label: str,
    n_minus_1_runs: int,
    payload_bytes: int,
    max_upgrade_ms: int,
    max_rollback_restore_ms: int,
    max_reupgrade_ms: int,
    keep_artifacts: bool,
) -> dict[str, Any]:
    started = time.perf_counter()
    paths = _create_paths(workspace_root)
    paths.run_dir.mkdir(parents=True, exist_ok=False)

    try:
        seed = _seed_database(
            paths.n_minus_1_db,
            run_rows=int(n_minus_1_runs),
            payload_bytes=int(payload_bytes),
        )
        _convert_to_n_minus_1(paths.n_minus_1_db)

        baseline_schema = _schema_snapshot(paths.n_minus_1_db)
        baseline_counts = _table_counts(paths.n_minus_1_db)
        baseline_integrity = _integrity_report(paths.n_minus_1_db)
        _expect(
            not baseline_schema["has_idempotency_key"] and not baseline_schema["has_migration_v1"],
            f"Failed to build N-1 schema snapshot: {baseline_schema}",
        )
        _expect(
            baseline_integrity["quick_ok"] and baseline_integrity["full_ok"],
            f"N-1 baseline integrity failed: {baseline_integrity}",
        )

        _copy_database(paths.n_minus_1_db, paths.upgrade_db)

        upgrade_started = time.perf_counter()
        upgrade_db = Database(
            str(paths.upgrade_db),
            migration_backup_dir=str(paths.migration_backups_dir),
        )
        upgrade_db.initialize()
        upgrade_duration_ms = int((time.perf_counter() - upgrade_started) * 1000)
        upgrade_migration = upgrade_db.migration_status()
        upgrade_schema = _schema_snapshot(paths.upgrade_db)
        upgrade_counts = _table_counts(paths.upgrade_db)
        upgrade_integrity = _integrity_report(paths.upgrade_db)
        _expect(
            upgrade_schema["has_idempotency_key"] and upgrade_schema["has_migration_v1"],
            f"Upgrade did not reach N schema: {upgrade_schema}",
        )
        _expect(
            _counts_for_data_loss_check(baseline_counts) == _counts_for_data_loss_check(upgrade_counts),
            f"Upgrade changed table row counts unexpectedly. baseline={baseline_counts} upgraded={upgrade_counts}",
        )
        _expect(
            upgrade_integrity["quick_ok"] and upgrade_integrity["full_ok"],
            f"Upgraded integrity failed: {upgrade_integrity}",
        )
        _expect(
            upgrade_duration_ms <= int(max_upgrade_ms),
            f"Upgrade duration exceeded threshold: {upgrade_duration_ms}ms > {max_upgrade_ms}ms",
        )
        upgrade_probe = _readiness_slo_probe(paths.upgrade_db, label="upgrade")

        migration_backup_path_raw = upgrade_migration.get("last_backup_path")
        _expect(
            isinstance(migration_backup_path_raw, str) and migration_backup_path_raw.strip(),
            f"Upgrade migration did not provide rollback backup path: {upgrade_migration}",
        )
        migration_backup_path = Path(str(migration_backup_path_raw))
        _expect(migration_backup_path.exists(), f"Rollback backup path does not exist: {migration_backup_path}")

        rollback_restore_duration_ms = _copy_database(migration_backup_path, paths.rollback_db)
        _expect(
            rollback_restore_duration_ms <= int(max_rollback_restore_ms),
            (
                "Rollback restore duration exceeded threshold: "
                f"{rollback_restore_duration_ms}ms > {max_rollback_restore_ms}ms"
            ),
        )
        rollback_schema = _schema_snapshot(paths.rollback_db)
        rollback_counts = _table_counts(paths.rollback_db)
        rollback_integrity = _integrity_report(paths.rollback_db)
        _expect(
            not rollback_schema["has_idempotency_key"] and not rollback_schema["has_migration_v1"],
            f"Rollback copy is not N-1 compatible: {rollback_schema}",
        )
        _expect(
            baseline_counts == rollback_counts,
            f"Rollback lost data counts. baseline={baseline_counts} rollback={rollback_counts}",
        )
        _expect(
            rollback_integrity["quick_ok"] and rollback_integrity["full_ok"],
            f"Rollback integrity failed: {rollback_integrity}",
        )

        _copy_database(paths.rollback_db, paths.rollback_probe_db)
        legacy_probe = _legacy_probe(paths.rollback_probe_db)

        _copy_database(paths.rollback_db, paths.reupgrade_db)
        reupgrade_started = time.perf_counter()
        reupgrade_db = Database(str(paths.reupgrade_db))
        reupgrade_db.initialize()
        reupgrade_duration_ms = int((time.perf_counter() - reupgrade_started) * 1000)
        _expect(
            reupgrade_duration_ms <= int(max_reupgrade_ms),
            f"Re-upgrade duration exceeded threshold: {reupgrade_duration_ms}ms > {max_reupgrade_ms}ms",
        )
        reupgrade_probe = _readiness_slo_probe(paths.reupgrade_db, label="reupgrade")

        report = {
            "label": label,
            "success": True,
            "seed": seed,
            "durations_ms": {
                "upgrade": upgrade_duration_ms,
                "rollback_restore": rollback_restore_duration_ms,
                "reupgrade": reupgrade_duration_ms,
            },
            "thresholds_ms": {
                "max_upgrade": int(max_upgrade_ms),
                "max_rollback_restore": int(max_rollback_restore_ms),
                "max_reupgrade": int(max_reupgrade_ms),
            },
            "snapshots": {
                "n_minus_1": {
                    "schema": baseline_schema,
                    "counts": baseline_counts,
                    "integrity": baseline_integrity,
                },
                "upgrade": {
                    "schema": upgrade_schema,
                    "counts": upgrade_counts,
                    "integrity": upgrade_integrity,
                    "migration": upgrade_migration,
                },
                "rollback": {
                    "schema": rollback_schema,
                    "counts": rollback_counts,
                    "integrity": rollback_integrity,
                },
            },
            "probes": {
                "upgrade": {
                    "readiness_ready": bool(upgrade_probe["readiness"]["ready"]),
                    "slo_status": str(upgrade_probe["slo"]["status"]),
                },
                "rollback_legacy": legacy_probe,
                "reupgrade": {
                    "readiness_ready": bool(reupgrade_probe["readiness"]["ready"]),
                    "slo_status": str(reupgrade_probe["slo"]["status"]),
                },
            },
            "decision": {
                "release_blocked": False,
                "recommended_action": "proceed",
                "rollback_path": str(migration_backup_path),
            },
            "paths": {
                "run_dir": str(paths.run_dir),
                "n_minus_1_db": str(paths.n_minus_1_db),
                "upgrade_db": str(paths.upgrade_db),
                "rollback_db": str(paths.rollback_db),
                "rollback_probe_db": str(paths.rollback_probe_db),
                "reupgrade_db": str(paths.reupgrade_db),
                "migration_backups_dir": str(paths.migration_backups_dir),
            },
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }
        return report
    finally:
        if not keep_artifacts:
            shutil.rmtree(paths.run_dir, ignore_errors=True)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compatibility drill for N-1 -> N -> N-1 workflow DB copies: upgrade migration, rollback restore, "
            "legacy read/write probe, and readiness/SLO validation."
        )
    )
    parser.add_argument("--workspace", default=str(PROJECT_ROOT / ".tmp" / "upgrade-downgrade-compatibility-drills"))
    parser.add_argument("--label", default="upgrade-downgrade-compatibility-drill")
    parser.add_argument("--n-minus-1-runs", type=int, default=800)
    parser.add_argument("--payload-bytes", type=int, default=512)
    parser.add_argument("--max-upgrade-ms", type=int, default=10_000)
    parser.add_argument("--max-rollback-restore-ms", type=int, default=10_000)
    parser.add_argument("--max-reupgrade-ms", type=int, default=10_000)
    parser.add_argument("--keep-artifacts", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if int(args.n_minus_1_runs) <= 0:
        print("[upgrade-downgrade-compatibility-drill] ERROR: --n-minus-1-runs must be > 0.", file=sys.stderr)
        return 2
    if int(args.payload_bytes) <= 0:
        print("[upgrade-downgrade-compatibility-drill] ERROR: --payload-bytes must be > 0.", file=sys.stderr)
        return 2
    if int(args.max_upgrade_ms) <= 0 or int(args.max_rollback_restore_ms) <= 0 or int(args.max_reupgrade_ms) <= 0:
        print(
            "[upgrade-downgrade-compatibility-drill] ERROR: duration thresholds must be > 0.",
            file=sys.stderr,
        )
        return 2

    workspace_root = Path(str(args.workspace)).expanduser()
    workspace_root.mkdir(parents=True, exist_ok=True)
    try:
        report = run_drill(
            workspace_root=workspace_root,
            label=str(args.label),
            n_minus_1_runs=int(args.n_minus_1_runs),
            payload_bytes=int(args.payload_bytes),
            max_upgrade_ms=int(args.max_upgrade_ms),
            max_rollback_restore_ms=int(args.max_rollback_restore_ms),
            max_reupgrade_ms=int(args.max_reupgrade_ms),
            keep_artifacts=bool(args.keep_artifacts),
        )
    except Exception as exc:
        print(f"[upgrade-downgrade-compatibility-drill] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
