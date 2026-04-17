from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import subprocess
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


class SnapshotValidationError(RuntimeError):
    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = str(reason)


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
    connection.row_factory = sqlite3.Row
    return connection


def _integrity_report(connection: sqlite3.Connection) -> dict[str, Any]:
    quick_rows = [str(row[0]) for row in connection.execute("PRAGMA quick_check").fetchall()]
    full_rows = [str(row[0]) for row in connection.execute("PRAGMA integrity_check").fetchall()]
    quick_ok = len(quick_rows) == 1 and quick_rows[0].lower() == "ok"
    full_ok = len(full_rows) == 1 and full_rows[0].lower() == "ok"
    return {
        "quick_ok": quick_ok,
        "quick_result": "ok" if quick_ok else "; ".join(quick_rows) if quick_rows else "no result",
        "full_ok": full_ok,
        "full_result": "ok" if full_ok else "; ".join(full_rows) if full_rows else "no result",
    }


def _table_counts(connection: sqlite3.Connection) -> dict[str, int]:
    rows = connection.execute(
        """SELECT name
           FROM sqlite_master
           WHERE type = 'table'
           AND name NOT LIKE 'sqlite_%'
           ORDER BY name ASC"""
    ).fetchall()
    counts: dict[str, int] = {}
    for row in rows:
        table_name = str(row[0])
        counts[table_name] = int(connection.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0])
    return counts


def _logical_digest(connection: sqlite3.Connection) -> str:
    hasher = hashlib.sha256()
    for line in connection.iterdump():
        hasher.update(line.encode("utf-8"))
        hasher.update(b"\n")
    return hasher.hexdigest()


def _snapshot(database_path: Path) -> dict[str, Any]:
    connection = _connect(database_path)
    try:
        return {
            "counts": _table_counts(connection),
            "integrity": _integrity_report(connection),
            "logical_digest": _logical_digest(connection),
        }
    finally:
        connection.close()


def _copy_database(source_path: Path, target_path: Path) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if target_path.exists():
        target_path.unlink()
    source_connection = _connect(source_path)
    target_connection = _connect(target_path)
    try:
        source_connection.backup(target_connection)
    finally:
        target_connection.close()
        source_connection.close()


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def _mutate_file(path: Path, *, offset: int, bytes_count: int) -> None:
    raw = bytearray(path.read_bytes())
    _expect(len(raw) > int(offset), f"File too small to mutate: {path}")
    mutate_count = min(max(1, int(bytes_count)), len(raw) - int(offset))
    for index in range(mutate_count):
        raw[int(offset) + index] ^= ((index * 23 + 9) % 251) + 1
    path.write_bytes(bytes(raw))


def _seed_source_database(*, source_db: Path, seed_rows: int, payload_bytes: int) -> dict[str, Any]:
    db = Database(str(source_db))
    db.initialize()
    workflow_id = "drill.snapshot_restore_crash_consistency"
    payload = "x" * max(32, int(payload_bytes))
    seeded = {"goals": 0, "tasks": 0, "events": 0, "workflow_runs": 0}

    with db.transaction() as transaction:
        timestamp = now_utc()
        transaction.execute(
            """INSERT OR IGNORE INTO workflow_definitions
               (workflow_id, name, description, entrypoint, is_enabled, version, created_at, updated_at)
               VALUES (?, ?, ?, ?, 1, 1, ?, ?)""",
            workflow_id,
            "Snapshot Restore Crash Consistency Drill",
            "Synthetic workflow rows for snapshot/restore crash-consistency drill",
            "maintenance.retention_cleanup",
            timestamp,
            timestamp,
        )
        for index in range(max(1, int(seed_rows))):
            goal_id = new_id()
            task_id = new_id()
            event_id = new_id()
            state = "active" if index % 2 == 0 else "draft"
            queue_status = "active" if state == "active" else "queued"
            correlation_id = f"{goal_id}:{task_id}:0"
            urgency = 0.5 + ((index % 5) * 0.05)
            value = 0.4 + ((index % 3) * 0.06)
            deadline_score = 0.2 + ((index % 4) * 0.05)
            base_priority = urgency * 0.5 + value * 0.3 + deadline_score * 0.2

            transaction.execute(
                """INSERT INTO goals
                   (goal_id, title, description, state, blocked_reason, escalation_reason, version, created_at, updated_at)
                   VALUES (?, ?, ?, ?, NULL, NULL, 1, ?, ?)""",
                goal_id,
                f"Snapshot Restore Goal {index}",
                "Seeded by snapshot/restore crash consistency drill",
                state,
                timestamp,
                timestamp,
            )
            transaction.execute(
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
            transaction.execute(
                """INSERT INTO tasks
                   (task_id, goal_id, title, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                task_id,
                goal_id,
                f"Snapshot Restore Task {index}",
                timestamp,
                timestamp,
            )
            transaction.execute(
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
            transaction.execute(
                """INSERT INTO events
                   (event_id, event_type, entity_id, correlation_id, payload, emitted_at)
                   VALUES (?, 'drill.snapshot_restore.seeded', ?, ?, ?, ?)""",
                event_id,
                goal_id,
                correlation_id,
                json.dumps({"index": index, "payload": payload}, ensure_ascii=True, sort_keys=True),
                timestamp,
            )
            seeded["goals"] += 1
            seeded["tasks"] += 1
            seeded["events"] += 1

            if index % 4 == 0:
                run_id = new_id()
                transaction.execute(
                    """INSERT INTO workflow_runs
                       (run_id, workflow_id, status, requested_by, correlation_id, idempotency_key,
                        input_payload, result_payload, started_at, finished_at, created_at, updated_at)
                       VALUES (?, ?, 'succeeded', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    run_id,
                    workflow_id,
                    "snapshot-restore-crash-consistency-drill",
                    f"workflow:{workflow_id}:{run_id[:8]}",
                    f"snapshot-restore-crash-consistency-{index}",
                    json.dumps({"index": index}, ensure_ascii=True, sort_keys=True),
                    json.dumps({"ok": True}, ensure_ascii=True, sort_keys=True),
                    timestamp,
                    timestamp,
                    timestamp,
                    timestamp,
                )
                seeded["workflow_runs"] += 1
    return seeded


def _create_snapshot(*, source_db: Path, snapshot_dir: Path) -> dict[str, Any]:
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    snapshot_db = snapshot_dir / "snapshot.db"
    manifest_path = snapshot_dir / "manifest.json"
    _copy_database(source_db, snapshot_db)
    snapshot_state = _snapshot(snapshot_db)
    _expect(
        bool(snapshot_state["integrity"]["quick_ok"]) and bool(snapshot_state["integrity"]["full_ok"]),
        f"Snapshot integrity failed: {snapshot_state['integrity']}",
    )
    manifest = {
        "schema_version": 1,
        "created_at_utc": now_utc(),
        "snapshot_file": "snapshot.db",
        "snapshot_sha256": _sha256_file(snapshot_db),
        "snapshot_counts": snapshot_state["counts"],
        "snapshot_digest": snapshot_state["logical_digest"],
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=True, sort_keys=True), encoding="utf-8")
    return {"snapshot_db": snapshot_db, "manifest_path": manifest_path, "manifest": manifest}


def _validate_snapshot(snapshot_dir: Path) -> tuple[Path, dict[str, Any]]:
    manifest_path = snapshot_dir / "manifest.json"
    if not manifest_path.exists():
        raise SnapshotValidationError("manifest_missing", f"Missing manifest: {manifest_path}")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SnapshotValidationError("manifest_invalid_json", f"Invalid manifest JSON: {exc}") from exc
    if not isinstance(manifest, dict):
        raise SnapshotValidationError("manifest_invalid_type", "Manifest must be a JSON object.")

    snapshot_file = str(manifest.get("snapshot_file") or "")
    if not snapshot_file:
        raise SnapshotValidationError("manifest_missing_snapshot_file", "Manifest missing snapshot_file.")
    snapshot_db = snapshot_dir / snapshot_file
    if not snapshot_db.exists():
        raise SnapshotValidationError("snapshot_missing", f"Snapshot file missing: {snapshot_db}")

    observed_sha = _sha256_file(snapshot_db)
    expected_sha = str(manifest.get("snapshot_sha256") or "")
    if not expected_sha:
        raise SnapshotValidationError("manifest_missing_sha256", "Manifest missing snapshot_sha256.")
    if observed_sha != expected_sha:
        raise SnapshotValidationError(
            "snapshot_sha256_mismatch",
            f"Snapshot SHA mismatch expected={expected_sha} observed={observed_sha}",
        )

    snapshot_state = _snapshot(snapshot_db)
    if not bool(snapshot_state["integrity"]["quick_ok"]) or not bool(snapshot_state["integrity"]["full_ok"]):
        raise SnapshotValidationError("snapshot_integrity_failed", f"Snapshot integrity failed: {snapshot_state}")
    if snapshot_state["counts"] != manifest.get("snapshot_counts"):
        raise SnapshotValidationError("snapshot_counts_mismatch", "Snapshot counts mismatch.")
    if snapshot_state["logical_digest"] != manifest.get("snapshot_digest"):
        raise SnapshotValidationError("snapshot_digest_mismatch", "Snapshot digest mismatch.")

    return snapshot_db, manifest


def _restore_with_preflight(*, snapshot_dir: Path, restore_db: Path) -> dict[str, Any]:
    snapshot_db, manifest = _validate_snapshot(snapshot_dir)
    _copy_database(snapshot_db, restore_db)
    restored = _snapshot(restore_db)
    _expect(
        bool(restored["integrity"]["quick_ok"]) and bool(restored["integrity"]["full_ok"]),
        f"Restored integrity failed: {restored['integrity']}",
    )
    _expect(restored["counts"] == manifest["snapshot_counts"], "Restored counts mismatch.")
    _expect(restored["logical_digest"] == manifest["snapshot_digest"], "Restored digest mismatch.")
    return restored


def _running_run_ids(client: TestClient) -> list[str]:
    response = client.get("/workflows/runs?limit=200")
    _expect(response.status_code == 200, f"Workflow runs endpoint failed: {response.status_code}")
    runs = response.json().get("runs") or []
    result: list[str] = []
    for item in runs:
        if isinstance(item, dict) and str(item.get("status")) == "running" and item.get("run_id"):
            result.append(str(item["run_id"]))
    return result


def _app_probe(database_path: Path, *, case_name: str) -> dict[str, Any]:
    app = create_app(
        Settings(
            database_url=str(database_path),
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
                "title": f"Snapshot/Restore Probe ({case_name})",
                "description": "post-restore mutation probe",
                "urgency": 0.6,
                "value": 0.7,
                "deadline_score": 0.3,
            },
        )
        running = _running_run_ids(client)

    readiness_payload = readiness.json()
    slo_payload = slo.json()
    integrity_payload = integrity.json()
    _expect(readiness.status_code == 200 and bool(readiness_payload.get("ready")), "Readiness probe failed.")
    _expect(slo.status_code == 200 and str(slo_payload.get("status")) == "ok", "SLO probe failed.")
    _expect(
        integrity.status_code == 200 and bool((integrity_payload.get("integrity") or {}).get("ok")),
        "Integrity probe failed.",
    )
    _expect(created.status_code == 201, "Post-restore goal create failed.")
    _expect(not running, f"Running workflow runs remained: {running}")
    return {
        "readiness_ready": bool(readiness_payload.get("ready")),
        "slo_status": str(slo_payload.get("status")),
        "integrity_ok": bool((integrity_payload.get("integrity") or {}).get("ok")),
        "goal_create_status_code": int(created.status_code),
        "running_run_ids": running,
    }


def _abort_after_bytes(path: Path) -> int:
    size = int(path.stat().st_size)
    return max(1, min(max(256, size // 3), max(1, size - 1)))


def _run_crash_target(*, source_file: Path, target_file: Path, target_script: Path) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        str(target_script),
        "--source-path",
        str(source_file),
        "--target-path",
        str(target_file),
        "--chunk-bytes",
        "4096",
        "--abort-after-bytes",
        str(_abort_after_bytes(source_file)),
        "--sleep-seconds",
        "0.0",
    ]
    return subprocess.run(command, cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=120.0)


def _safe_quick_check(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "quick_ok": False, "quick_result": "missing"}
    try:
        connection = sqlite3.connect(str(path))
        try:
            row = connection.execute("PRAGMA quick_check").fetchone()
        finally:
            connection.close()
    except Exception as exc:
        return {"exists": True, "quick_ok": False, "quick_result": str(exc)}
    value = str(row[0]) if row is not None else "no result"
    return {"exists": True, "quick_ok": value.lower() == "ok", "quick_result": value}


@dataclass(slots=True)
class DrillPaths:
    run_dir: Path
    source_db: Path
    snapshots_root: Path
    faults_root: Path
    restores_root: Path


def _create_paths(workspace_root: Path) -> DrillPaths:
    run_id = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    run_dir = workspace_root / f"snapshot-restore-crash-consistency-{run_id}"
    return DrillPaths(
        run_dir=run_dir,
        source_db=run_dir / "source.db",
        snapshots_root=run_dir / "snapshots",
        faults_root=run_dir / "faults",
        restores_root=run_dir / "restores",
    )


def run_drill(
    *,
    workspace_root: Path,
    label: str,
    seed_rows: int,
    payload_bytes: int,
    keep_artifacts: bool,
) -> dict[str, Any]:
    started = time.perf_counter()
    paths = _create_paths(workspace_root)
    paths.run_dir.mkdir(parents=True, exist_ok=False)
    paths.snapshots_root.mkdir(parents=True, exist_ok=True)
    paths.faults_root.mkdir(parents=True, exist_ok=True)
    paths.restores_root.mkdir(parents=True, exist_ok=True)

    target_script = PROJECT_ROOT / "scripts" / "snapshot-restore-crash-target.py"
    _expect(target_script.exists(), f"Missing target helper script: {target_script}")

    try:
        seeded = _seed_source_database(source_db=paths.source_db, seed_rows=seed_rows, payload_bytes=payload_bytes)
        source_snapshot = _snapshot(paths.source_db)
        _expect(
            bool(source_snapshot["integrity"]["quick_ok"]) and bool(source_snapshot["integrity"]["full_ok"]),
            f"Source integrity failed: {source_snapshot['integrity']}",
        )

        golden_dir = paths.snapshots_root / "golden"
        golden = _create_snapshot(source_db=paths.source_db, snapshot_dir=golden_dir)
        cases: list[dict[str, Any]] = []

        case1_dir = paths.faults_root / "missing-manifest-after-abort"
        case1_dir.mkdir(parents=True, exist_ok=True)
        case1_snapshot = case1_dir / "snapshot.db"
        aborted_snapshot = _run_crash_target(source_file=paths.source_db, target_file=case1_snapshot, target_script=target_script)
        _expect(aborted_snapshot.returncode != 0, "Expected non-zero return code for aborted snapshot copy.")
        guard_target_1 = paths.restores_root / "guard-missing-manifest.db"
        _copy_database(paths.source_db, guard_target_1)
        guard_before_1 = _snapshot(guard_target_1)
        case1_reason = ""
        case1_unexpected_success = False
        try:
            _restore_with_preflight(snapshot_dir=case1_dir, restore_db=guard_target_1)
            case1_unexpected_success = True
        except SnapshotValidationError as exc:
            case1_reason = str(exc.reason)
        guard_after_1 = _snapshot(guard_target_1)
        case1_unchanged = (
            guard_before_1["logical_digest"] == guard_after_1["logical_digest"]
            and guard_before_1["counts"] == guard_after_1["counts"]
        )
        case1_success = not case1_unexpected_success and case1_reason == "manifest_missing" and case1_unchanged
        _expect(case1_success, "Case missing-manifest-after-abort failed.")
        cases.append(
            {
                "name": "missing_manifest_after_snapshot_abort",
                "success": case1_success,
                "failure_reason": case1_reason,
                "restore_target_unchanged": case1_unchanged,
                "aborted_return_code": int(aborted_snapshot.returncode),
            }
        )

        case2_dir = paths.faults_root / "tampered-snapshot-checksum"
        if case2_dir.exists():
            shutil.rmtree(case2_dir, ignore_errors=True)
        shutil.copytree(golden_dir, case2_dir)
        case2_snapshot = case2_dir / "snapshot.db"
        _mutate_file(case2_snapshot, offset=128, bytes_count=96)
        guard_target_2 = paths.restores_root / "guard-tampered-snapshot.db"
        _copy_database(paths.source_db, guard_target_2)
        guard_before_2 = _snapshot(guard_target_2)
        case2_reason = ""
        case2_unexpected_success = False
        try:
            _restore_with_preflight(snapshot_dir=case2_dir, restore_db=guard_target_2)
            case2_unexpected_success = True
        except SnapshotValidationError as exc:
            case2_reason = str(exc.reason)
        guard_after_2 = _snapshot(guard_target_2)
        case2_unchanged = (
            guard_before_2["logical_digest"] == guard_after_2["logical_digest"]
            and guard_before_2["counts"] == guard_after_2["counts"]
        )
        case2_success = not case2_unexpected_success and case2_reason == "snapshot_sha256_mismatch" and case2_unchanged
        _expect(case2_success, "Case tampered-snapshot-checksum failed.")
        cases.append(
            {
                "name": "tampered_snapshot_checksum_mismatch",
                "success": case2_success,
                "failure_reason": case2_reason,
                "restore_target_unchanged": case2_unchanged,
            }
        )

        restore_abort_target = paths.restores_root / "restore-abort-then-recover.db"
        aborted_restore = _run_crash_target(
            source_file=golden["snapshot_db"],
            target_file=restore_abort_target,
            target_script=target_script,
        )
        _expect(aborted_restore.returncode != 0, "Expected non-zero return code for aborted restore copy.")
        pre_recovery_quick = _safe_quick_check(restore_abort_target)
        restored_after_abort = _restore_with_preflight(snapshot_dir=golden_dir, restore_db=restore_abort_target)
        app_probe_after_abort = _app_probe(restore_abort_target, case_name="restore_abort_then_recover")
        case3_success = (
            restored_after_abort["logical_digest"] == source_snapshot["logical_digest"]
            and restored_after_abort["counts"] == source_snapshot["counts"]
            and app_probe_after_abort["readiness_ready"] is True
            and app_probe_after_abort["slo_status"] == "ok"
            and app_probe_after_abort["goal_create_status_code"] == 201
            and app_probe_after_abort["running_run_ids"] == []
        )
        _expect(case3_success, "Case restore-abort-then-recover failed.")
        cases.append(
            {
                "name": "restore_abort_then_recover",
                "success": case3_success,
                "aborted_return_code": int(aborted_restore.returncode),
                "pre_recovery_quick_check": pre_recovery_quick,
                "restored": restored_after_abort,
                "app_probe": app_probe_after_abort,
            }
        )

        happy_target = paths.restores_root / "happy-path-restore.db"
        restored_happy = _restore_with_preflight(snapshot_dir=golden_dir, restore_db=happy_target)
        app_probe_happy = _app_probe(happy_target, case_name="happy_path_restore")
        case4_success = (
            restored_happy["logical_digest"] == source_snapshot["logical_digest"]
            and restored_happy["counts"] == source_snapshot["counts"]
            and app_probe_happy["readiness_ready"] is True
            and app_probe_happy["slo_status"] == "ok"
            and app_probe_happy["goal_create_status_code"] == 201
            and app_probe_happy["running_run_ids"] == []
        )
        _expect(case4_success, "Case happy-path-restore failed.")
        cases.append(
            {
                "name": "happy_path_restore",
                "success": case4_success,
                "restored": restored_happy,
                "app_probe": app_probe_happy,
            }
        )

        success = all(bool(case.get("success")) for case in cases)
        _expect(success, "At least one crash-consistency matrix case failed.")
        return {
            "label": label,
            "success": success,
            "config": {
                "seed_rows": int(seed_rows),
                "payload_bytes": int(payload_bytes),
                "fault_matrix_cases": 4,
            },
            "seeded": seeded,
            "source_snapshot": source_snapshot,
            "golden_snapshot_manifest": golden["manifest"],
            "cases": cases,
            "paths": {
                "run_dir": str(paths.run_dir),
                "source_db": str(paths.source_db),
                "golden_snapshot_db": str(golden["snapshot_db"]),
                "golden_snapshot_dir": str(golden_dir),
            },
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }
    finally:
        if not keep_artifacts:
            shutil.rmtree(paths.run_dir, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run crash-consistent snapshot/restore fault matrix with hard-abort simulations, "
            "manifest preflight validation, and deterministic restore recovery checks."
        )
    )
    parser.add_argument("--workspace", default=str(PROJECT_ROOT / ".tmp" / "snapshot-restore-crash-consistency-drills"))
    parser.add_argument("--label", default="snapshot-restore-crash-consistency-drill")
    parser.add_argument("--seed-rows", type=int, default=96)
    parser.add_argument("--payload-bytes", type=int, default=128)
    parser.add_argument("--keep-artifacts", action="store_true")
    args = parser.parse_args(argv)

    if int(args.seed_rows) <= 0:
        print("[snapshot-restore-crash-consistency-drill] ERROR: --seed-rows must be > 0.", file=sys.stderr)
        return 2
    if int(args.payload_bytes) <= 0:
        print("[snapshot-restore-crash-consistency-drill] ERROR: --payload-bytes must be > 0.", file=sys.stderr)
        return 2

    workspace_root = Path(str(args.workspace)).expanduser()
    workspace_root.mkdir(parents=True, exist_ok=True)
    try:
        report = run_drill(
            workspace_root=workspace_root,
            label=str(args.label),
            seed_rows=int(args.seed_rows),
            payload_bytes=int(args.payload_bytes),
            keep_artifacts=bool(args.keep_artifacts),
        )
    except Exception as exc:
        print(f"[snapshot-restore-crash-consistency-drill] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
