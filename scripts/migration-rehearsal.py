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


def _seed_source_database(
    *,
    source_path: Path,
    run_rows: int,
    payload_bytes: int,
) -> dict[str, Any]:
    db = Database(str(source_path))
    db.initialize()

    timestamp = now_utc()
    workflow_id = "drill.migration_rehearsal"
    payload_blob = "x" * max(16, int(payload_bytes))
    input_payload = json.dumps({"seed": payload_blob}, sort_keys=True)
    result_payload = json.dumps({"ok": True, "seed": payload_blob}, sort_keys=True)

    conn = _connect(source_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """INSERT OR IGNORE INTO workflow_definitions
               (workflow_id, name, description, entrypoint, is_enabled, version, created_at, updated_at)
               VALUES (?, ?, ?, ?, 1, 1, ?, ?)""",
            (
                workflow_id,
                "Migration Rehearsal Drill",
                "Synthetic workflow used by migration rehearsal drill",
                "maintenance.retention_cleanup",
                timestamp,
                timestamp,
            ),
        )

        rows: list[tuple[Any, ...]] = []
        for index in range(int(run_rows)):
            run_id = new_id()
            rows.append(
                (
                    run_id,
                    workflow_id,
                    "succeeded",
                    "migration-drill",
                    f"migration-rehearsal:{index}",
                    input_payload,
                    result_payload,
                    timestamp,
                    timestamp,
                    timestamp,
                    timestamp,
                )
            )

        conn.executemany(
            """INSERT INTO workflow_runs
               (run_id, workflow_id, status, requested_by, correlation_id,
                input_payload, result_payload, started_at, finished_at, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()

    file_info = db.database_file_info()
    return {
        "workflow_id": workflow_id,
        "seeded_run_count": int(run_rows),
        "source_size_bytes": int(file_info.get("size_bytes") or 0),
    }


def _mark_migration_pending(path: Path, *, version: int) -> None:
    conn = _connect(path)
    try:
        conn.execute("DELETE FROM schema_migrations WHERE version = ?", (int(version),))
    finally:
        conn.close()


@dataclass(slots=True)
class DrillPaths:
    run_dir: Path
    scenarios_dir: Path


@dataclass(slots=True)
class ScenarioSpec:
    name: str
    run_rows: int
    payload_bytes: int


@dataclass(slots=True)
class Thresholds:
    backup_ms: int
    restore_ms: int
    migration_ms: int


def _create_paths(workspace_root: Path) -> DrillPaths:
    run_id = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    run_dir = workspace_root / f"migration-rehearsal-{run_id}"
    return DrillPaths(
        run_dir=run_dir,
        scenarios_dir=run_dir / "scenarios",
    )


def _run_scenario(
    *,
    scenario_root: Path,
    spec: ScenarioSpec,
    thresholds: Thresholds,
) -> dict[str, Any]:
    scenario_root.mkdir(parents=True, exist_ok=False)
    source_db = scenario_root / "source.db"
    backup_db = scenario_root / "backup.db"
    restored_db = scenario_root / "restored.db"
    migration_candidate_db = scenario_root / "migration-candidate.db"
    migration_backup_dir = scenario_root / "migration-backups"

    seeded = _seed_source_database(
        source_path=source_db,
        run_rows=spec.run_rows,
        payload_bytes=spec.payload_bytes,
    )

    source_conn = _connect(source_db)
    try:
        source_counts = _table_counts(source_conn)
        source_integrity = _integrity_report(source_conn)
    finally:
        source_conn.close()

    backup_duration_ms = _copy_database(source_db, backup_db)
    restore_duration_ms = _copy_database(backup_db, restored_db)

    restored_conn = _connect(restored_db)
    try:
        restored_counts = _table_counts(restored_conn)
        restored_integrity = _integrity_report(restored_conn)
    finally:
        restored_conn.close()

    _copy_database(source_db, migration_candidate_db)
    _mark_migration_pending(migration_candidate_db, version=1)

    migration_db = Database(
        str(migration_candidate_db),
        migration_backup_dir=str(migration_backup_dir),
    )
    migration_started = time.perf_counter()
    migration_db.initialize()
    migration_duration_ms = int((time.perf_counter() - migration_started) * 1000)
    migration_state = migration_db.migration_status()

    migrated_conn = _connect(migration_candidate_db)
    try:
        migrated_counts = _table_counts(migrated_conn)
        migrated_integrity = _integrity_report(migrated_conn)
    finally:
        migrated_conn.close()

    rehearsal_backup_path_raw = migration_state.get("last_backup_path")
    rehearsal_backup_path = (
        Path(str(rehearsal_backup_path_raw))
        if isinstance(rehearsal_backup_path_raw, str) and rehearsal_backup_path_raw.strip()
        else None
    )

    checks = {
        "restore_matches_source": source_counts == restored_counts,
        "source_integrity_ok": bool(source_integrity["quick_ok"] and source_integrity["full_ok"]),
        "restored_integrity_ok": bool(restored_integrity["quick_ok"] and restored_integrity["full_ok"]),
        "migrated_integrity_ok": bool(migrated_integrity["quick_ok"] and migrated_integrity["full_ok"]),
        "pending_migrations_cleared": migration_state.get("pending_versions") == [],
        "migration_backup_created": bool(rehearsal_backup_path and rehearsal_backup_path.exists()),
        "migration_backup_versions_match": migration_state.get("last_backup_versions") == [1],
        "workflow_runs_preserved": int(migrated_counts.get("workflow_runs", 0)) == int(spec.run_rows),
        "backup_within_threshold": backup_duration_ms <= int(thresholds.backup_ms),
        "restore_within_threshold": restore_duration_ms <= int(thresholds.restore_ms),
        "migration_within_threshold": migration_duration_ms <= int(thresholds.migration_ms),
    }
    failed_checks = [name for name, passed in checks.items() if not passed]

    return {
        "scenario": spec.name,
        "success": len(failed_checks) == 0,
        "seed": {
            "run_rows": int(spec.run_rows),
            "payload_bytes": int(spec.payload_bytes),
            "source_size_bytes": int(seeded["source_size_bytes"]),
        },
        "durations_ms": {
            "backup": backup_duration_ms,
            "restore": restore_duration_ms,
            "migration": migration_duration_ms,
        },
        "thresholds_ms": {
            "backup": int(thresholds.backup_ms),
            "restore": int(thresholds.restore_ms),
            "migration": int(thresholds.migration_ms),
        },
        "checks": checks,
        "failed_checks": failed_checks,
        "counts": {
            "source": source_counts,
            "restored": restored_counts,
            "migrated": migrated_counts,
        },
        "integrity": {
            "source": source_integrity,
            "restored": restored_integrity,
            "migrated": migrated_integrity,
        },
        "migration": {
            "pending_versions": list(migration_state.get("pending_versions") or []),
            "last_backup_versions": list(migration_state.get("last_backup_versions") or []),
            "last_backup_path": str(rehearsal_backup_path) if rehearsal_backup_path else None,
        },
        "paths": {
            "scenario_root": str(scenario_root),
            "source_db": str(source_db),
            "backup_db": str(backup_db),
            "restored_db": str(restored_db),
            "migration_candidate_db": str(migration_candidate_db),
            "migration_backup_dir": str(migration_backup_dir),
        },
    }


def run_drill(
    *,
    workspace_root: Path,
    keep_artifacts: bool,
    label: str,
    thresholds: Thresholds,
    scenarios: list[ScenarioSpec],
) -> dict[str, Any]:
    started = time.perf_counter()
    paths = _create_paths(workspace_root)
    paths.scenarios_dir.mkdir(parents=True, exist_ok=False)

    try:
        scenario_reports: list[dict[str, Any]] = []
        for spec in scenarios:
            scenario_root = paths.scenarios_dir / spec.name
            scenario_reports.append(
                _run_scenario(
                    scenario_root=scenario_root,
                    spec=spec,
                    thresholds=thresholds,
                )
            )

        release_blocked = any(not report["success"] for report in scenario_reports)
        status = "abort_release_and_prepare_rollback" if release_blocked else "proceed"

        report: dict[str, Any] = {
            "label": label,
            "success": not release_blocked,
            "decision": {
                "release_blocked": release_blocked,
                "recommended_action": status,
                "rollback_trigger": (
                    "If any scenario exceeds threshold or fails integrity/backup checks, "
                    "block release and prepare rollback from pre-migration backup."
                ),
            },
            "thresholds_ms": {
                "backup": int(thresholds.backup_ms),
                "restore": int(thresholds.restore_ms),
                "migration": int(thresholds.migration_ms),
            },
            "scenarios": scenario_reports,
            "paths": {
                "run_dir": str(paths.run_dir),
                "scenarios_dir": str(paths.scenarios_dir),
            },
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }
        if release_blocked:
            raise RuntimeError(f"Migration rehearsal failed: {json.dumps(report, sort_keys=True)}")
        return report
    finally:
        if not keep_artifacts:
            shutil.rmtree(paths.run_dir, ignore_errors=True)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run migration rehearsal across small/medium/large/(optional xlarge) DB copies, measure backup+restore+migration "
            "durations, and enforce release abort thresholds."
        )
    )
    parser.add_argument("--workspace", default=str(PROJECT_ROOT / ".tmp" / "migration-rehearsals"))
    parser.add_argument("--label", default="migration-rehearsal")
    parser.add_argument("--small-runs", type=int, default=500)
    parser.add_argument("--medium-runs", type=int, default=2500)
    parser.add_argument("--large-runs", type=int, default=6000)
    parser.add_argument("--xlarge-runs", type=int, default=0)
    parser.add_argument("--payload-bytes", type=int, default=1024)
    parser.add_argument("--max-backup-ms", type=int, default=15_000)
    parser.add_argument("--max-restore-ms", type=int, default=15_000)
    parser.add_argument("--max-migration-ms", type=int, default=20_000)
    parser.add_argument("--keep-artifacts", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    workspace_root = Path(str(args.workspace)).expanduser()
    workspace_root.mkdir(parents=True, exist_ok=True)

    scenario_values = [int(args.small_runs), int(args.medium_runs), int(args.large_runs)]
    if any(value <= 0 for value in scenario_values):
        print("[migration-rehearsal] ERROR: scenario run counts must be positive integers.", file=sys.stderr)
        return 2
    if int(args.xlarge_runs) < 0:
        print("[migration-rehearsal] ERROR: --xlarge-runs must be >= 0.", file=sys.stderr)
        return 2
    if int(args.payload_bytes) <= 0:
        print("[migration-rehearsal] ERROR: --payload-bytes must be positive.", file=sys.stderr)
        return 2

    thresholds = Thresholds(
        backup_ms=max(1, int(args.max_backup_ms)),
        restore_ms=max(1, int(args.max_restore_ms)),
        migration_ms=max(1, int(args.max_migration_ms)),
    )
    scenarios = [
        ScenarioSpec(name="small", run_rows=int(args.small_runs), payload_bytes=int(args.payload_bytes)),
        ScenarioSpec(name="medium", run_rows=int(args.medium_runs), payload_bytes=int(args.payload_bytes)),
        ScenarioSpec(name="large", run_rows=int(args.large_runs), payload_bytes=int(args.payload_bytes)),
    ]
    if int(args.xlarge_runs) > 0:
        scenarios.append(
            ScenarioSpec(name="xlarge", run_rows=int(args.xlarge_runs), payload_bytes=int(args.payload_bytes))
        )

    try:
        report = run_drill(
            workspace_root=workspace_root,
            keep_artifacts=bool(args.keep_artifacts),
            label=str(args.label),
            thresholds=thresholds,
            scenarios=scenarios,
        )
    except Exception as exc:
        print(f"[migration-rehearsal] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
