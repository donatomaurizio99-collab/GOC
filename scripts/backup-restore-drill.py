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

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from goal_ops_console.database import Database, new_id, now_utc


def _quoted_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


def _integrity_report(conn: sqlite3.Connection) -> dict[str, Any]:
    quick_rows = [str(row[0]) for row in conn.execute("PRAGMA quick_check").fetchall()]
    full_rows = [str(row[0]) for row in conn.execute("PRAGMA integrity_check").fetchall()]
    quick_ok = len(quick_rows) == 1 and quick_rows[0].lower() == "ok"
    full_ok = len(full_rows) == 1 and full_rows[0].lower() == "ok"
    return {
        "quick_ok": quick_ok,
        "quick_result": "ok" if quick_ok else "; ".join(quick_rows) if quick_rows else "no result",
        "full_ok": full_ok,
        "full_result": "ok" if full_ok else "; ".join(full_rows) if full_rows else "no result",
    }


def _table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    table_rows = conn.execute(
        """SELECT name
           FROM sqlite_master
           WHERE type = 'table'
           AND name NOT LIKE 'sqlite_%'
           ORDER BY name ASC"""
    ).fetchall()
    counts: dict[str, int] = {}
    for row in table_rows:
        table_name = str(row[0])
        query = f"SELECT COUNT(*) FROM {_quoted_identifier(table_name)}"
        counts[table_name] = int(conn.execute(query).fetchone()[0])
    return counts


def _copy_database(source_path: Path, target_path: Path) -> None:
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


def _seed_source_database(source_path: Path) -> dict[str, str]:
    db = Database(str(source_path))
    db.initialize()
    timestamp = now_utc()

    goal_id = new_id()
    task_id = new_id()
    correlation_id = f"{goal_id}:0"
    event_id = new_id()

    workflow_id = "drill.backup_restore"
    workflow_run_id = new_id()
    workflow_correlation = f"workflow:{workflow_id}:{workflow_run_id[:8]}"

    db.execute(
        """INSERT INTO goals
           (goal_id, title, description, state, blocked_reason, escalation_reason, version, created_at, updated_at)
           VALUES (?, ?, ?, 'active', NULL, NULL, 1, ?, ?)""",
        goal_id,
        "Backup drill goal",
        "Seed data for backup/restore drill",
        timestamp,
        timestamp,
    )
    db.execute(
        """INSERT INTO goal_queue
           (goal_id, urgency, value, deadline_score, base_priority, priority, wait_cycles, force_promoted,
            status, version, created_at, updated_at)
           VALUES (?, 0.6, 0.5, 0.3, 0.47, 0.47, 0, 0, 'active', 1, ?, ?)""",
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
        "Backup drill task",
        timestamp,
        timestamp,
    )
    db.execute(
        """INSERT INTO task_state
           (task_id, goal_id, correlation_id, status, retry_count, failure_type, error_hash,
            version, created_at, updated_at)
           VALUES (?, ?, ?, 'pending', 0, NULL, NULL, 1, ?, ?)""",
        task_id,
        goal_id,
        correlation_id,
        timestamp,
        timestamp,
    )
    db.execute(
        """INSERT OR IGNORE INTO workflow_definitions
           (workflow_id, name, description, entrypoint, is_enabled, version, created_at, updated_at)
           VALUES (?, ?, ?, ?, 1, 1, ?, ?)""",
        workflow_id,
        "Backup Restore Drill",
        "Synthetic workflow row used by backup drill",
        "maintenance.retention_cleanup",
        timestamp,
        timestamp,
    )
    db.execute(
        """INSERT INTO workflow_runs
           (run_id, workflow_id, status, requested_by, correlation_id, idempotency_key,
            input_payload, result_payload, started_at, finished_at, created_at, updated_at)
           VALUES (?, ?, 'succeeded', ?, ?, NULL, ?, ?, ?, ?, ?, ?)""",
        workflow_run_id,
        workflow_id,
        "backup-drill",
        workflow_correlation,
        json.dumps({"drill": True}, sort_keys=True),
        json.dumps({"ok": True}, sort_keys=True),
        timestamp,
        timestamp,
        timestamp,
        timestamp,
    )
    db.execute(
        """INSERT INTO events
           (event_id, event_type, entity_id, correlation_id, payload, emitted_at)
           VALUES (?, 'drill.seeded', ?, ?, ?, ?)""",
        event_id,
        goal_id,
        correlation_id,
        json.dumps({"goal_id": goal_id, "task_id": task_id}, sort_keys=True),
        timestamp,
    )
    return {
        "goal_id": goal_id,
        "task_id": task_id,
        "workflow_id": workflow_id,
        "workflow_run_id": workflow_run_id,
        "event_id": event_id,
    }


@dataclass(slots=True)
class DrillPaths:
    run_dir: Path
    source_db: Path
    backup_db: Path
    restored_db: Path


def _create_paths(workspace_root: Path) -> DrillPaths:
    run_id = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    run_dir = workspace_root / f"backup-restore-drill-{run_id}"
    return DrillPaths(
        run_dir=run_dir,
        source_db=run_dir / "source.db",
        backup_db=run_dir / "backup.db",
        restored_db=run_dir / "restored.db",
    )


def run_drill(*, workspace_root: Path, keep_artifacts: bool, label: str) -> dict[str, Any]:
    started = time.perf_counter()
    paths = _create_paths(workspace_root)
    paths.run_dir.mkdir(parents=True, exist_ok=False)

    try:
        seeded_ids = _seed_source_database(paths.source_db)

        source_conn = _connect(paths.source_db)
        try:
            source_counts = _table_counts(source_conn)
            source_integrity = _integrity_report(source_conn)
        finally:
            source_conn.close()

        _copy_database(paths.source_db, paths.backup_db)
        _copy_database(paths.backup_db, paths.restored_db)

        restored_conn = _connect(paths.restored_db)
        try:
            restored_counts = _table_counts(restored_conn)
            restored_integrity = _integrity_report(restored_conn)
            missing_entities: list[str] = []
            for key, entity_id in seeded_ids.items():
                if key == "goal_id":
                    row = restored_conn.execute(
                        "SELECT 1 FROM goals WHERE goal_id = ?",
                        (entity_id,),
                    ).fetchone()
                elif key == "task_id":
                    row = restored_conn.execute(
                        "SELECT 1 FROM task_state WHERE task_id = ?",
                        (entity_id,),
                    ).fetchone()
                elif key == "workflow_run_id":
                    row = restored_conn.execute(
                        "SELECT 1 FROM workflow_runs WHERE run_id = ?",
                        (entity_id,),
                    ).fetchone()
                elif key == "event_id":
                    row = restored_conn.execute(
                        "SELECT 1 FROM events WHERE event_id = ?",
                        (entity_id,),
                    ).fetchone()
                else:
                    row = restored_conn.execute(
                        "SELECT 1 FROM workflow_definitions WHERE workflow_id = ?",
                        (entity_id,),
                    ).fetchone()
                if row is None:
                    missing_entities.append(key)
        finally:
            restored_conn.close()

        restore_matches_source = source_counts == restored_counts
        seed_ok = not missing_entities
        success = (
            restore_matches_source
            and seed_ok
            and bool(source_integrity["quick_ok"])
            and bool(source_integrity["full_ok"])
            and bool(restored_integrity["quick_ok"])
            and bool(restored_integrity["full_ok"])
        )

        report: dict[str, Any] = {
            "label": label,
            "success": success,
            "restore_matches_source": restore_matches_source,
            "source_integrity": source_integrity,
            "restored_integrity": restored_integrity,
            "seed_validation": {
                "ok": seed_ok,
                "missing_entities": missing_entities,
            },
            "source_counts": source_counts,
            "restored_counts": restored_counts,
            "paths": {
                "source_db": str(paths.source_db),
                "backup_db": str(paths.backup_db),
                "restored_db": str(paths.restored_db),
                "run_dir": str(paths.run_dir),
            },
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }
        if not success:
            raise RuntimeError(f"Backup/restore drill failed: {json.dumps(report, sort_keys=True)}")
        return report
    finally:
        if not keep_artifacts:
            shutil.rmtree(paths.run_dir, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a SQLite backup/restore drill and verify integrity + row equivalence."
    )
    parser.add_argument("--workspace", default=str(PROJECT_ROOT / ".tmp" / "backup-restore-drills"))
    parser.add_argument("--label", default="backup-restore-drill")
    parser.add_argument("--keep-artifacts", action="store_true")
    args = parser.parse_args()

    workspace_root = Path(str(args.workspace)).expanduser()
    workspace_root.mkdir(parents=True, exist_ok=True)
    try:
        report = run_drill(
            workspace_root=workspace_root,
            keep_artifacts=bool(args.keep_artifacts),
            label=str(args.label),
        )
    except Exception as exc:
        print(f"[backup-restore-drill] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
