from __future__ import annotations

import argparse
import hashlib
import json
import os
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


class AtomicSwitchError(RuntimeError):
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


def _mutate_file(path: Path, *, offset: int, bytes_count: int) -> None:
    raw = bytearray(path.read_bytes())
    _expect(len(raw) > int(offset), f"File too small to mutate: {path}")
    mutate_count = min(max(1, int(bytes_count)), len(raw) - int(offset))
    for index in range(mutate_count):
        raw[int(offset) + index] ^= ((index * 31 + 7) % 251) + 1
    path.write_bytes(bytes(raw))


def _corrupt_sqlite_header(path: Path) -> None:
    raw = bytearray(path.read_bytes())
    _expect(len(raw) >= 256, f"SQLite file too small to corrupt header safely: {path}")
    raw[0:16] = b"NOT_A_SQLITE_DB!"
    for index in range(16, 96):
        raw[index] = (index * 17 + 13) % 256
    path.write_bytes(bytes(raw))


def _seed_database(*, database_path: Path, label: str, seed_rows: int, payload_bytes: int) -> dict[str, Any]:
    db = Database(str(database_path))
    db.initialize()
    workflow_id = f"drill.multi_db_atomic_switch.{label}"
    payload = (label + "-") * max(1, int(payload_bytes) // (len(label) + 1))
    seeded = {"goals": 0, "tasks": 0, "events": 0, "workflow_runs": 0}

    with db.transaction() as transaction:
        timestamp = now_utc()
        transaction.execute(
            """INSERT OR IGNORE INTO workflow_definitions
               (workflow_id, name, description, entrypoint, is_enabled, version, created_at, updated_at)
               VALUES (?, ?, ?, ?, 1, 1, ?, ?)""",
            workflow_id,
            f"Multi-DB Atomic Switch Drill ({label})",
            "Synthetic rows for atomic switch drill",
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
                f"Atomic Switch Goal {label}-{index}",
                f"Seeded for atomic switch drill ({label})",
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
                f"Atomic Switch Task {label}-{index}",
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
                   VALUES (?, 'drill.multi_db_atomic_switch.seeded', ?, ?, ?, ?)""",
                event_id,
                goal_id,
                correlation_id,
                json.dumps({"label": label, "index": index, "payload": payload}, ensure_ascii=True, sort_keys=True),
                timestamp,
            )
            seeded["goals"] += 1
            seeded["tasks"] += 1
            seeded["events"] += 1

            if index % 3 == 0:
                run_id = new_id()
                transaction.execute(
                    """INSERT INTO workflow_runs
                       (run_id, workflow_id, status, requested_by, correlation_id, idempotency_key,
                        input_payload, result_payload, started_at, finished_at, created_at, updated_at)
                       VALUES (?, ?, 'succeeded', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    run_id,
                    workflow_id,
                    "multi-db-atomic-switch-drill",
                    f"workflow:{workflow_id}:{run_id[:8]}",
                    f"multi-db-atomic-switch-{label}-{index}",
                    json.dumps({"label": label, "index": index}, ensure_ascii=True, sort_keys=True),
                    json.dumps({"ok": True}, ensure_ascii=True, sort_keys=True),
                    timestamp,
                    timestamp,
                    timestamp,
                    timestamp,
                )
                seeded["workflow_runs"] += 1
    return seeded


def _write_pointer_atomic(pointer_path: Path, pointer_payload: dict[str, Any]) -> None:
    pointer_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = pointer_path.with_name(pointer_path.name + f".tmp-{uuid.uuid4().hex[:8]}")
    try:
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(pointer_payload, handle, ensure_ascii=True, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temp_path), str(pointer_path))
    finally:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)


def _read_pointer(pointer_path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(pointer_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise AtomicSwitchError("pointer_invalid_json", f"Invalid pointer JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise AtomicSwitchError("pointer_invalid_type", "Pointer payload must be a JSON object.")
    active = str(payload.get("active") or "")
    if active not in ("primary", "candidate"):
        raise AtomicSwitchError("pointer_invalid_active", f"Invalid pointer active value: {active!r}")
    for key in ("primary_db", "candidate_db"):
        value = str(payload.get(key) or "")
        if not value:
            raise AtomicSwitchError("pointer_missing_path", f"Pointer payload missing {key}.")
    return payload


def _resolve_active_db(pointer_payload: dict[str, Any]) -> Path:
    active = str(pointer_payload.get("active"))
    if active == "primary":
        return Path(str(pointer_payload["primary_db"]))
    if active == "candidate":
        return Path(str(pointer_payload["candidate_db"]))
    raise AtomicSwitchError("pointer_invalid_active", f"Invalid active target: {active}")


def _atomic_switch(pointer_path: Path, *, target: str) -> dict[str, Any]:
    pointer_payload = _read_pointer(pointer_path)
    if str(target) not in ("primary", "candidate"):
        raise AtomicSwitchError("target_unknown", f"Unknown switch target: {target!r}")

    target_path = Path(str(pointer_payload[f"{target}_db"]))
    if not target_path.exists():
        raise AtomicSwitchError("target_missing", f"Target DB does not exist: {target_path}")

    target_snapshot: dict[str, Any]
    try:
        target_snapshot = _snapshot(target_path)
    except Exception as exc:
        raise AtomicSwitchError("target_snapshot_failed", f"Failed to read target snapshot: {exc}") from exc

    integrity = target_snapshot.get("integrity") or {}
    if not bool(integrity.get("quick_ok")) or not bool(integrity.get("full_ok")):
        raise AtomicSwitchError("target_integrity_failed", f"Target integrity failed: {integrity}")

    next_payload = dict(pointer_payload)
    next_payload["active"] = str(target)
    next_payload["updated_at_utc"] = now_utc()
    next_payload["updated_by"] = "multi-db-atomic-switch-drill"
    _write_pointer_atomic(pointer_path, next_payload)
    confirmed_payload = _read_pointer(pointer_path)

    return {
        "target": str(target),
        "target_path": str(target_path),
        "target_snapshot": target_snapshot,
        "pointer_after": confirmed_payload,
    }


def _running_run_ids(client: TestClient) -> list[str]:
    response = client.get("/workflows/runs?limit=200")
    _expect(response.status_code == 200, f"Workflow runs endpoint failed: {response.status_code}")
    runs = response.json().get("runs") or []
    result: list[str] = []
    for item in runs:
        if isinstance(item, dict) and str(item.get("status")) == "running" and item.get("run_id"):
            result.append(str(item["run_id"]))
    return result


def _app_probe(database_path: Path, *, case_name: str, mutate: bool) -> dict[str, Any]:
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
        running = _running_run_ids(client)
        created_status = None
        if bool(mutate):
            created = client.post(
                "/goals",
                json={
                    "title": f"Atomic Switch Probe ({case_name})",
                    "description": "post-switch mutating probe",
                    "urgency": 0.6,
                    "value": 0.7,
                    "deadline_score": 0.3,
                },
            )
            created_status = int(created.status_code)
            _expect(created.status_code == 201, f"Goal create failed for case {case_name}: {created.text!r}")

    readiness_payload = readiness.json()
    slo_payload = slo.json()
    integrity_payload = integrity.json()
    _expect(readiness.status_code == 200 and bool(readiness_payload.get("ready")), "Readiness probe failed.")
    _expect(slo.status_code == 200 and str(slo_payload.get("status")) == "ok", "SLO probe failed.")
    _expect(
        integrity.status_code == 200 and bool((integrity_payload.get("integrity") or {}).get("ok")),
        "Integrity probe failed.",
    )
    _expect(not running, f"Running workflow runs remained: {running}")
    return {
        "readiness_ready": bool(readiness_payload.get("ready")),
        "slo_status": str(slo_payload.get("status")),
        "integrity_ok": bool((integrity_payload.get("integrity") or {}).get("ok")),
        "goal_create_status_code": created_status,
        "running_run_ids": running,
    }


def _run_abort_target(*, pointer_path: Path, target: str, target_script: Path) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        str(target_script),
        "--pointer-path",
        str(pointer_path),
        "--target",
        str(target),
        "--mode",
        "abort-before-replace",
    ]
    return subprocess.run(command, cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=120.0)


@dataclass(slots=True)
class DrillPaths:
    run_dir: Path
    primary_db: Path
    candidate_db: Path
    candidate_pristine_db: Path
    pointer_path: Path


def _create_paths(workspace_root: Path) -> DrillPaths:
    run_id = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    run_dir = workspace_root / f"multi-db-atomic-switch-drill-{run_id}"
    return DrillPaths(
        run_dir=run_dir,
        primary_db=run_dir / "primary.db",
        candidate_db=run_dir / "candidate.db",
        candidate_pristine_db=run_dir / "candidate-pristine.db",
        pointer_path=run_dir / "active-db-pointer.json",
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
    target_script = PROJECT_ROOT / "scripts" / "multi-db-atomic-switch-target.py"
    _expect(target_script.exists(), f"Missing target helper script: {target_script}")

    try:
        seeded_primary = _seed_database(
            database_path=paths.primary_db,
            label="primary",
            seed_rows=int(seed_rows),
            payload_bytes=int(payload_bytes),
        )
        seeded_candidate = _seed_database(
            database_path=paths.candidate_db,
            label="candidate",
            seed_rows=max(1, int(seed_rows)) + 8,
            payload_bytes=int(payload_bytes),
        )
        _copy_database(paths.candidate_db, paths.candidate_pristine_db)

        primary_snapshot = _snapshot(paths.primary_db)
        candidate_snapshot = _snapshot(paths.candidate_db)
        _expect(
            bool(primary_snapshot["integrity"]["quick_ok"]) and bool(primary_snapshot["integrity"]["full_ok"]),
            f"Primary integrity failed: {primary_snapshot['integrity']}",
        )
        _expect(
            bool(candidate_snapshot["integrity"]["quick_ok"]) and bool(candidate_snapshot["integrity"]["full_ok"]),
            f"Candidate integrity failed: {candidate_snapshot['integrity']}",
        )

        pointer_payload = {
            "active": "primary",
            "primary_db": str(paths.primary_db),
            "candidate_db": str(paths.candidate_db),
            "updated_at_utc": now_utc(),
            "updated_by": "multi-db-atomic-switch-drill",
        }
        _write_pointer_atomic(paths.pointer_path, pointer_payload)

        cases: list[dict[str, Any]] = []

        aborted = _run_abort_target(pointer_path=paths.pointer_path, target="candidate", target_script=target_script)
        pointer_after_abort = _read_pointer(paths.pointer_path)
        active_after_abort = _resolve_active_db(pointer_after_abort)
        probe_abort = _app_probe(active_after_abort, case_name="abort_before_replace", mutate=False)
        case1_success = (
            int(aborted.returncode) != 0
            and str(pointer_after_abort.get("active")) == "primary"
            and bool(probe_abort["readiness_ready"])
            and str(probe_abort["slo_status"]) == "ok"
            and probe_abort["running_run_ids"] == []
        )
        _expect(case1_success, "Case abort-before-replace failed.")
        cases.append(
            {
                "name": "abort_before_pointer_replace",
                "success": case1_success,
                "aborted_return_code": int(aborted.returncode),
                "pointer_active_after": str(pointer_after_abort.get("active")),
                "app_probe": probe_abort,
            }
        )

        _corrupt_sqlite_header(paths.candidate_db)
        pointer_before_failed_switch = _read_pointer(paths.pointer_path)
        failure_reason = ""
        switch_unexpected_success = False
        try:
            _atomic_switch(paths.pointer_path, target="candidate")
            switch_unexpected_success = True
        except AtomicSwitchError as exc:
            failure_reason = str(exc.reason)
        pointer_after_failed_switch = _read_pointer(paths.pointer_path)
        _copy_database(paths.candidate_pristine_db, paths.candidate_db)
        restored_candidate_snapshot = _snapshot(paths.candidate_db)
        case2_success = (
            not switch_unexpected_success
            and failure_reason in {"target_snapshot_failed", "target_integrity_failed"}
            and str(pointer_before_failed_switch.get("active")) == "primary"
            and str(pointer_after_failed_switch.get("active")) == "primary"
            and bool(restored_candidate_snapshot["integrity"]["quick_ok"])
            and bool(restored_candidate_snapshot["integrity"]["full_ok"])
        )
        _expect(case2_success, "Case candidate-integrity-reject failed.")
        cases.append(
            {
                "name": "candidate_integrity_reject",
                "success": case2_success,
                "failure_reason": failure_reason,
                "pointer_active_before": str(pointer_before_failed_switch.get("active")),
                "pointer_active_after": str(pointer_after_failed_switch.get("active")),
            }
        )

        switch_to_candidate = _atomic_switch(paths.pointer_path, target="candidate")
        pointer_after_candidate = switch_to_candidate["pointer_after"]
        active_candidate_path = _resolve_active_db(pointer_after_candidate)
        active_candidate_snapshot = _snapshot(active_candidate_path)
        probe_candidate = _app_probe(active_candidate_path, case_name="switch_to_candidate", mutate=True)
        case3_success = (
            str(pointer_after_candidate.get("active")) == "candidate"
            and active_candidate_snapshot["counts"] == restored_candidate_snapshot["counts"]
            and active_candidate_snapshot["logical_digest"] == restored_candidate_snapshot["logical_digest"]
            and bool(probe_candidate["readiness_ready"])
            and str(probe_candidate["slo_status"]) == "ok"
            and int(probe_candidate["goal_create_status_code"] or 0) == 201
            and probe_candidate["running_run_ids"] == []
        )
        _expect(case3_success, "Case switch-to-candidate failed.")
        cases.append(
            {
                "name": "switch_to_candidate_success",
                "success": case3_success,
                "pointer_active_after": str(pointer_after_candidate.get("active")),
                "active_snapshot": active_candidate_snapshot,
                "app_probe": probe_candidate,
            }
        )

        expected_primary_snapshot = _snapshot(paths.primary_db)
        switch_to_primary = _atomic_switch(paths.pointer_path, target="primary")
        pointer_after_primary = switch_to_primary["pointer_after"]
        active_primary_path = _resolve_active_db(pointer_after_primary)
        active_primary_snapshot = _snapshot(active_primary_path)
        probe_primary = _app_probe(active_primary_path, case_name="switch_back_to_primary", mutate=True)
        case4_success = (
            str(pointer_after_primary.get("active")) == "primary"
            and active_primary_snapshot["counts"] == expected_primary_snapshot["counts"]
            and active_primary_snapshot["logical_digest"] == expected_primary_snapshot["logical_digest"]
            and bool(probe_primary["readiness_ready"])
            and str(probe_primary["slo_status"]) == "ok"
            and int(probe_primary["goal_create_status_code"] or 0) == 201
            and probe_primary["running_run_ids"] == []
        )
        _expect(case4_success, "Case switch-back-to-primary failed.")
        cases.append(
            {
                "name": "switch_back_to_primary_success",
                "success": case4_success,
                "pointer_active_after": str(pointer_after_primary.get("active")),
                "active_snapshot": active_primary_snapshot,
                "app_probe": probe_primary,
            }
        )

        success = all(bool(case.get("success")) for case in cases)
        _expect(success, "At least one multi-db atomic-switch case failed.")
        return {
            "label": label,
            "success": success,
            "config": {
                "seed_rows": int(seed_rows),
                "payload_bytes": int(payload_bytes),
                "cases": 4,
            },
            "seeded": {
                "primary": seeded_primary,
                "candidate": seeded_candidate,
            },
            "primary_snapshot": primary_snapshot,
            "candidate_snapshot": candidate_snapshot,
            "cases": cases,
            "paths": {
                "run_dir": str(paths.run_dir),
                "primary_db": str(paths.primary_db),
                "candidate_db": str(paths.candidate_db),
                "pointer_path": str(paths.pointer_path),
            },
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }
    finally:
        if not keep_artifacts:
            shutil.rmtree(paths.run_dir, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run a multi-db atomic-switch drill. Simulates hard-aborted pointer updates, "
            "candidate integrity rejects, and deterministic switch/recovery across primary/candidate DBs."
        )
    )
    parser.add_argument("--workspace", default=str(PROJECT_ROOT / ".tmp" / "multi-db-atomic-switch-drills"))
    parser.add_argument("--label", default="multi-db-atomic-switch-drill")
    parser.add_argument("--seed-rows", type=int, default=96)
    parser.add_argument("--payload-bytes", type=int, default=128)
    parser.add_argument("--keep-artifacts", action="store_true")
    args = parser.parse_args(argv)

    if int(args.seed_rows) <= 0:
        print("[multi-db-atomic-switch-drill] ERROR: --seed-rows must be > 0.", file=sys.stderr)
        return 2
    if int(args.payload_bytes) <= 0:
        print("[multi-db-atomic-switch-drill] ERROR: --payload-bytes must be > 0.", file=sys.stderr)
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
        print(f"[multi-db-atomic-switch-drill] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
