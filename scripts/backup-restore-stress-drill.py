from __future__ import annotations

import argparse
import base64
import hashlib
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


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


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


def _normalize_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"__bytes__": base64.b64encode(value).decode("ascii")}
    return value


def _logical_digest(conn: sqlite3.Connection) -> str:
    hasher = hashlib.sha256()
    table_rows = conn.execute(
        """SELECT name
           FROM sqlite_master
           WHERE type = 'table'
           AND name NOT LIKE 'sqlite_%'
           ORDER BY name ASC"""
    ).fetchall()
    for row in table_rows:
        table_name = str(row[0])
        hasher.update(f"TABLE:{table_name}\n".encode("utf-8"))
        info_rows = conn.execute(f"PRAGMA table_info({_quoted_identifier(table_name)})").fetchall()
        columns = [str(info_row[1]) for info_row in info_rows]
        if not columns:
            continue
        select_cols = ", ".join(_quoted_identifier(column) for column in columns)
        order_cols = ", ".join(_quoted_identifier(column) for column in columns)
        query = (
            f"SELECT {select_cols} FROM {_quoted_identifier(table_name)} "
            f"ORDER BY {order_cols}"
        )
        for data_row in conn.execute(query).fetchall():
            normalized = [_normalize_value(item) for item in tuple(data_row)]
            hasher.update(json.dumps(normalized, ensure_ascii=True, sort_keys=True).encode("utf-8"))
            hasher.update(b"\n")
    return hasher.hexdigest()


def _snapshot(database_path: Path) -> dict[str, Any]:
    conn = _connect(database_path)
    try:
        counts = _table_counts(conn)
        integrity = _integrity_report(conn)
        digest = _logical_digest(conn)
    finally:
        conn.close()
    return {
        "counts": counts,
        "integrity": integrity,
        "logical_digest": digest,
    }


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


def _seed_round(
    *,
    db: Database,
    round_index: int,
    goals_per_round: int,
    tasks_per_goal: int,
    workflow_runs_per_round: int,
) -> dict[str, int]:
    created = {
        "goals": 0,
        "tasks": 0,
        "events": 0,
        "workflow_runs": 0,
    }
    workflow_id = "drill.backup_restore_stress"
    with db.transaction() as tx:
        timestamp = now_utc()
        tx.execute(
            """INSERT OR IGNORE INTO workflow_definitions
               (workflow_id, name, description, entrypoint, is_enabled, version, created_at, updated_at)
               VALUES (?, ?, ?, ?, 1, 1, ?, ?)""",
            workflow_id,
            "Backup Restore Stress Drill",
            "Synthetic workflow rows used by backup/restore stress drill",
            "maintenance.retention_cleanup",
            timestamp,
            timestamp,
        )

        for goal_idx in range(max(1, int(goals_per_round))):
            goal_id = new_id()
            state = "active" if goal_idx % 2 == 0 else "draft"
            queue_status = "active" if state == "active" else "queued"
            urgency = 0.5 + ((goal_idx % 5) * 0.05)
            value = 0.4 + ((goal_idx % 3) * 0.07)
            deadline_score = 0.2 + ((goal_idx % 4) * 0.06)
            base_priority = urgency * 0.5 + value * 0.3 + deadline_score * 0.2

            tx.execute(
                """INSERT INTO goals
                   (goal_id, title, description, state, blocked_reason, escalation_reason, version, created_at, updated_at)
                   VALUES (?, ?, ?, ?, NULL, NULL, 1, ?, ?)""",
                goal_id,
                f"Backup Stress Goal r{round_index}-{goal_idx}",
                "Seeded by backup/restore stress drill",
                state,
                timestamp,
                timestamp,
            )
            tx.execute(
                """INSERT INTO goal_queue
                   (goal_id, urgency, value, deadline_score, base_priority, priority, wait_cycles, force_promoted,
                    status, version, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, 0, 0, ?, 1, ?, ?)""",
                goal_id,
                float(urgency),
                float(value),
                float(deadline_score),
                float(base_priority),
                float(base_priority),
                queue_status,
                timestamp,
                timestamp,
            )
            created["goals"] += 1

            event_id = new_id()
            tx.execute(
                """INSERT INTO events
                   (event_id, event_type, entity_id, correlation_id, payload, emitted_at)
                   VALUES (?, 'drill.backup_stress.goal_seeded', ?, ?, ?, ?)""",
                event_id,
                goal_id,
                goal_id,
                json.dumps({"round": round_index, "goal_index": goal_idx}, ensure_ascii=True, sort_keys=True),
                timestamp,
            )
            created["events"] += 1

            for task_idx in range(max(1, int(tasks_per_goal))):
                task_id = new_id()
                correlation_id = f"{goal_id}:{task_id}:0"
                tx.execute(
                    """INSERT INTO tasks
                       (task_id, goal_id, title, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    task_id,
                    goal_id,
                    f"Backup Stress Task r{round_index}-{goal_idx}-{task_idx}",
                    timestamp,
                    timestamp,
                )
                tx.execute(
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
                created["tasks"] += 1

        for workflow_idx in range(max(1, int(workflow_runs_per_round))):
            run_id = new_id()
            correlation_id = f"workflow:{workflow_id}:{run_id[:8]}"
            tx.execute(
                """INSERT INTO workflow_runs
                   (run_id, workflow_id, status, requested_by, correlation_id, idempotency_key,
                    input_payload, result_payload, started_at, finished_at, created_at, updated_at)
                   VALUES (?, ?, 'succeeded', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                run_id,
                workflow_id,
                "backup-restore-stress-drill",
                correlation_id,
                f"backup-stress-{round_index}-{workflow_idx}",
                json.dumps({"round": round_index, "idx": workflow_idx}, ensure_ascii=True, sort_keys=True),
                json.dumps({"ok": True, "round": round_index}, ensure_ascii=True, sort_keys=True),
                timestamp,
                timestamp,
                timestamp,
                timestamp,
            )
            created["workflow_runs"] += 1

    return created


def _running_run_ids(client: TestClient) -> list[str]:
    response = client.get("/workflows/runs?limit=200")
    _expect(response.status_code == 200, f"Failed to list workflow runs: {response.status_code}")
    runs = response.json().get("runs") or []
    run_ids: list[str] = []
    for item in runs:
        if not isinstance(item, dict):
            continue
        if str(item.get("status")) == "running" and item.get("run_id"):
            run_ids.append(str(item["run_id"]))
    return run_ids


def _restored_app_probe(restored_db: Path, *, round_index: int) -> dict[str, Any]:
    app = create_app(
        Settings(
            database_url=str(restored_db),
            workflow_worker_poll_interval_seconds=0.05,
            workflow_startup_recovery_max_age_seconds=0,
        )
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        readiness = client.get("/system/readiness")
        slo = client.get("/system/slo")
        integrity = client.get("/system/database/integrity?mode=quick")
        created = client.post(
            "/goals",
            json={
                "title": f"Backup/Restore Stress Probe Goal Round {round_index}",
                "description": "post-restore mutating probe",
                "urgency": 0.6,
                "value": 0.7,
                "deadline_score": 0.3,
            },
        )
        running_runs = _running_run_ids(client)

    _expect(readiness.status_code == 200, "Readiness probe failed on restored DB.")
    _expect(slo.status_code == 200, "SLO probe failed on restored DB.")
    _expect(integrity.status_code == 200, "Integrity probe failed on restored DB.")
    _expect(created.status_code == 201, f"Post-restore goal create failed: {created.status_code} {created.text!r}")

    readiness_payload = readiness.json()
    slo_payload = slo.json()
    integrity_payload = integrity.json()
    _expect(bool(readiness_payload.get("ready")), f"Readiness is not true on restored DB: {readiness_payload}")
    _expect(str(slo_payload.get("status")) == "ok", f"SLO is not ok on restored DB: {slo_payload}")
    _expect(
        bool((integrity_payload.get("integrity") or {}).get("ok")),
        f"Integrity endpoint not ok on restored DB: {integrity_payload}",
    )
    _expect(not running_runs, f"Found running workflow runs on restored DB: {running_runs}")

    created_payload = created.json()
    return {
        "readiness_ready": bool(readiness_payload.get("ready")),
        "slo_status": str(slo_payload.get("status")),
        "integrity_ok": bool((integrity_payload.get("integrity") or {}).get("ok")),
        "post_restore_goal_status_code": int(created.status_code),
        "post_restore_goal_id": str(created_payload.get("goal_id")),
        "running_run_ids": running_runs,
    }


@dataclass(slots=True)
class DrillPaths:
    run_dir: Path
    source_db: Path


def _create_paths(workspace_root: Path) -> DrillPaths:
    run_id = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    run_dir = workspace_root / f"backup-restore-stress-drill-{run_id}"
    return DrillPaths(
        run_dir=run_dir,
        source_db=run_dir / "source-stress.db",
    )


def run_drill(
    *,
    workspace_root: Path,
    label: str,
    rounds: int,
    goals_per_round: int,
    tasks_per_goal: int,
    workflow_runs_per_round: int,
    keep_artifacts: bool,
) -> dict[str, Any]:
    started = time.perf_counter()
    paths = _create_paths(workspace_root)
    paths.run_dir.mkdir(parents=True, exist_ok=False)
    db = Database(str(paths.source_db))
    db.initialize()

    round_reports: list[dict[str, Any]] = []
    try:
        for round_index in range(1, max(1, int(rounds)) + 1):
            seeded = _seed_round(
                db=db,
                round_index=round_index,
                goals_per_round=int(goals_per_round),
                tasks_per_goal=int(tasks_per_goal),
                workflow_runs_per_round=int(workflow_runs_per_round),
            )
            source_snapshot = _snapshot(paths.source_db)
            _expect(
                bool(source_snapshot["integrity"]["quick_ok"]) and bool(source_snapshot["integrity"]["full_ok"]),
                f"Source DB integrity failed before backup in round {round_index}: {source_snapshot['integrity']}",
            )

            backup_path = paths.run_dir / f"round-{round_index:02d}-backup.db"
            restored_a = paths.run_dir / f"round-{round_index:02d}-restored-a.db"
            restored_b = paths.run_dir / f"round-{round_index:02d}-restored-b.db"
            restored_idempotent = paths.run_dir / f"round-{round_index:02d}-restored-idempotent.db"

            _copy_database(paths.source_db, backup_path)
            _copy_database(backup_path, restored_a)
            _copy_database(backup_path, restored_b)
            _copy_database(backup_path, restored_idempotent)
            idempotent_first = _snapshot(restored_idempotent)
            _copy_database(backup_path, restored_idempotent)
            idempotent_second = _snapshot(restored_idempotent)

            restored_a_snapshot = _snapshot(restored_a)
            restored_b_snapshot = _snapshot(restored_b)
            restore_matches_source = (
                restored_a_snapshot["counts"] == source_snapshot["counts"]
                and restored_a_snapshot["logical_digest"] == source_snapshot["logical_digest"]
                and restored_b_snapshot["counts"] == source_snapshot["counts"]
                and restored_b_snapshot["logical_digest"] == source_snapshot["logical_digest"]
            )
            restore_idempotent = (
                idempotent_first["logical_digest"] == idempotent_second["logical_digest"]
                and idempotent_first["counts"] == idempotent_second["counts"]
            )
            _expect(
                restore_matches_source,
                (
                    f"Restore mismatch against source in round {round_index}. "
                    f"source_digest={source_snapshot['logical_digest']} "
                    f"restored_a_digest={restored_a_snapshot['logical_digest']} "
                    f"restored_b_digest={restored_b_snapshot['logical_digest']}"
                ),
            )
            _expect(
                restore_idempotent,
                (
                    f"Idempotent restore check failed in round {round_index}. "
                    f"first_digest={idempotent_first['logical_digest']} "
                    f"second_digest={idempotent_second['logical_digest']}"
                ),
            )
            _expect(
                bool(restored_a_snapshot["integrity"]["quick_ok"]) and bool(restored_a_snapshot["integrity"]["full_ok"]),
                f"Restored A integrity failed in round {round_index}: {restored_a_snapshot['integrity']}",
            )
            _expect(
                bool(restored_b_snapshot["integrity"]["quick_ok"]) and bool(restored_b_snapshot["integrity"]["full_ok"]),
                f"Restored B integrity failed in round {round_index}: {restored_b_snapshot['integrity']}",
            )

            app_probe = _restored_app_probe(restored_a, round_index=round_index)
            round_reports.append(
                {
                    "round": round_index,
                    "seeded": seeded,
                    "restore_matches_source": bool(restore_matches_source),
                    "restore_idempotent": bool(restore_idempotent),
                    "source": source_snapshot,
                    "restored_a": restored_a_snapshot,
                    "restored_b": restored_b_snapshot,
                    "restored_idempotent_first": idempotent_first,
                    "restored_idempotent_second": idempotent_second,
                    "app_probe": app_probe,
                    "paths": {
                        "backup_db": str(backup_path),
                        "restored_a_db": str(restored_a),
                        "restored_b_db": str(restored_b),
                        "restored_idempotent_db": str(restored_idempotent),
                    },
                }
            )

        return {
            "label": label,
            "success": True,
            "rounds": round_reports,
            "config": {
                "rounds": int(rounds),
                "goals_per_round": int(goals_per_round),
                "tasks_per_goal": int(tasks_per_goal),
                "workflow_runs_per_round": int(workflow_runs_per_round),
            },
            "paths": {
                "run_dir": str(paths.run_dir),
                "source_db": str(paths.source_db),
            },
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }
    finally:
        if not keep_artifacts:
            shutil.rmtree(paths.run_dir, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Backup/restore stress drill: seed high-volume SQLite data in rounds, execute repeated "
            "backup+restore cycles, verify logical equivalence, restore idempotence, and runtime probes."
        )
    )
    parser.add_argument("--workspace", default=str(PROJECT_ROOT / ".tmp" / "backup-restore-stress-drills"))
    parser.add_argument("--label", default="backup-restore-stress-drill")
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--goals-per-round", type=int, default=120)
    parser.add_argument("--tasks-per-goal", type=int, default=2)
    parser.add_argument("--workflow-runs-per-round", type=int, default=24)
    parser.add_argument("--keep-artifacts", action="store_true")
    args = parser.parse_args(argv)

    if int(args.rounds) <= 0:
        print("[backup-restore-stress-drill] ERROR: --rounds must be > 0.", file=sys.stderr)
        return 2
    if int(args.goals_per_round) <= 0:
        print("[backup-restore-stress-drill] ERROR: --goals-per-round must be > 0.", file=sys.stderr)
        return 2
    if int(args.tasks_per_goal) <= 0:
        print("[backup-restore-stress-drill] ERROR: --tasks-per-goal must be > 0.", file=sys.stderr)
        return 2
    if int(args.workflow_runs_per_round) <= 0:
        print("[backup-restore-stress-drill] ERROR: --workflow-runs-per-round must be > 0.", file=sys.stderr)
        return 2

    workspace_root = Path(str(args.workspace)).expanduser()
    workspace_root.mkdir(parents=True, exist_ok=True)
    try:
        report = run_drill(
            workspace_root=workspace_root,
            label=str(args.label),
            rounds=int(args.rounds),
            goals_per_round=int(args.goals_per_round),
            tasks_per_goal=int(args.tasks_per_goal),
            workflow_runs_per_round=int(args.workflow_runs_per_round),
            keep_artifacts=bool(args.keep_artifacts),
        )
    except Exception as exc:
        print(f"[backup-restore-stress-drill] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
