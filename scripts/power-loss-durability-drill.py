from __future__ import annotations

import argparse
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
from goal_ops_console.database import Database
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


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    _expect(isinstance(payload, dict), f"Expected JSON object in {path}")
    return payload


def _consume_process_output(process: subprocess.Popen[str]) -> tuple[str, str]:
    stdout_text = process.stdout.read() if process.stdout is not None else ""
    stderr_text = process.stderr.read() if process.stderr is not None else ""
    return stdout_text, stderr_text


def _stop_process(process: subprocess.Popen[str], *, timeout_seconds: float = 10.0) -> tuple[int, str, str]:
    if process.poll() is None:
        process.kill()
    try:
        process.wait(timeout=max(1.0, float(timeout_seconds)))
    except subprocess.TimeoutExpired:
        process.terminate()
        process.wait(timeout=5.0)
    stdout_text, stderr_text = _consume_process_output(process)
    return int(process.returncode if process.returncode is not None else -1), stdout_text, stderr_text


def _wait_for_state(
    *,
    process: subprocess.Popen[str],
    state_file: Path,
    expected_status: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    deadline = time.time() + max(1.0, float(timeout_seconds))
    while time.time() < deadline:
        if state_file.exists():
            try:
                payload = _read_json(state_file)
            except Exception:
                payload = {}
            status = str(payload.get("status") or "")
            if status == "error":
                raise RuntimeError(
                    f"Target process reported error state: {json.dumps(payload, sort_keys=True)}"
                )
            if status == expected_status:
                return payload
        if process.poll() is not None:
            stdout_text, stderr_text = _consume_process_output(process)
            raise RuntimeError(
                "Target process exited before expected state. "
                f"expected={expected_status!r} rc={process.returncode} "
                f"stdout={stdout_text!r} stderr={stderr_text!r}"
            )
        time.sleep(0.05)
    raise RuntimeError(
        f"Target process did not report state {expected_status!r} within {timeout_seconds}s."
    )


def _ensure_probe_table(database_path: Path) -> None:
    database = Database(str(database_path))
    database.initialize()
    connection = _connect(database_path)
    try:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS durability_probe (
                   marker      TEXT NOT NULL,
                   seq         INTEGER NOT NULL,
                   payload     TEXT NOT NULL,
                   phase       TEXT NOT NULL,
                   created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                   PRIMARY KEY (marker, seq)
               )"""
        )
        connection.execute(
            """INSERT OR IGNORE INTO durability_probe (marker, seq, payload, phase)
               VALUES ('baseline', 0, '{"baseline":true}', 'seed')"""
        )
    finally:
        connection.close()


def _probe_row_count(database_path: Path, marker: str) -> int:
    connection = _connect(database_path)
    try:
        row = connection.execute(
            "SELECT COUNT(*) FROM durability_probe WHERE marker = ?",
            (marker,),
        ).fetchone()
        return int(row[0] if row is not None else 0)
    finally:
        connection.close()


def _database_profile(database_path: Path) -> dict[str, Any]:
    connection = _connect(database_path)
    try:
        journal_mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0])
        synchronous = int(connection.execute("PRAGMA synchronous").fetchone()[0])
        page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
        quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        full_check = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
    finally:
        connection.close()
    return {
        "journal_mode": journal_mode,
        "synchronous": synchronous,
        "page_size": page_size,
        "quick_check": quick_check,
        "full_check": full_check,
        "quick_ok": quick_check.lower() == "ok",
        "full_ok": full_check.lower() == "ok",
    }


def _insert_post_recovery_row(database_path: Path) -> int:
    connection = _connect(database_path)
    try:
        connection.execute(
            """INSERT OR REPLACE INTO durability_probe (marker, seq, payload, phase)
               VALUES ('post_recovery_write', 0, '{"write":"ok"}', 'post-recovery')"""
        )
        row = connection.execute(
            "SELECT COUNT(*) FROM durability_probe WHERE marker = 'post_recovery_write'"
        ).fetchone()
        return int(row[0] if row is not None else 0)
    finally:
        connection.close()


def _journal_artifacts(database_path: Path) -> dict[str, dict[str, Any]]:
    artifacts: dict[str, dict[str, Any]] = {}
    suffixes = ("-journal", "-wal", "-shm")
    for suffix in suffixes:
        artifact_path = Path(str(database_path) + suffix)
        exists = artifact_path.exists()
        artifacts[suffix] = {
            "path": str(artifact_path),
            "exists": exists,
            "size_bytes": int(artifact_path.stat().st_size) if exists else 0,
        }
    return artifacts


def _app_probe(database_path: Path) -> dict[str, Any]:
    app = create_app(
        Settings(
            database_url=str(database_path),
            workflow_worker_poll_interval_seconds=0.05,
            workflow_startup_recovery_max_age_seconds=0,
        )
    )
    with TestClient(app) as client:
        readiness = client.get("/system/readiness")
        slo = client.get("/system/slo")
        integrity = client.get("/system/database/integrity?mode=quick")
        created = client.post(
            "/goals",
            json={
                "title": "Power Loss Durability Drill Goal",
                "description": "Post-abort mutating write probe",
                "urgency": 0.6,
                "value": 0.7,
                "deadline_score": 0.3,
            },
        )

    _expect(readiness.status_code == 200, f"Readiness probe failed: {readiness.status_code}")
    _expect(slo.status_code == 200, f"SLO probe failed: {slo.status_code}")
    _expect(integrity.status_code == 200, f"Integrity probe failed: {integrity.status_code}")
    _expect(created.status_code == 201, f"Post-recovery goal write failed: {created.status_code} {created.text}")

    readiness_payload = readiness.json()
    slo_payload = slo.json()
    integrity_payload = integrity.json()
    created_payload = created.json()
    _expect(bool(readiness_payload.get("ready")), f"Readiness not ready after recovery: {readiness_payload}")
    _expect(str(slo_payload.get("status")) == "ok", f"SLO status not ok after recovery: {slo_payload}")
    _expect(
        bool((integrity_payload.get("integrity") or {}).get("ok")),
        f"Integrity not ok after recovery: {integrity_payload}",
    )
    return {
        "readiness": readiness_payload,
        "slo": slo_payload,
        "integrity": integrity_payload,
        "post_recovery_goal": created_payload,
        "post_recovery_goal_status_code": int(created.status_code),
    }


@dataclass(slots=True)
class DrillPaths:
    run_dir: Path
    database_path: Path
    abort_before_state_file: Path
    abort_after_state_file: Path


def _create_paths(workspace_root: Path) -> DrillPaths:
    run_id = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    run_dir = workspace_root / f"power-loss-durability-{run_id}"
    return DrillPaths(
        run_dir=run_dir,
        database_path=run_dir / "durability.db",
        abort_before_state_file=run_dir / "abort-before-commit-state.json",
        abort_after_state_file=run_dir / "abort-after-commit-state.json",
    )


def run_drill(
    *,
    workspace_root: Path,
    label: str,
    transaction_rows: int,
    payload_bytes: int,
    startup_timeout_seconds: float,
    keep_artifacts: bool,
) -> dict[str, Any]:
    started = time.perf_counter()
    paths = _create_paths(workspace_root)
    paths.run_dir.mkdir(parents=True, exist_ok=False)

    target_script = PROJECT_ROOT / "scripts" / "power-loss-durability-target.py"
    _expect(target_script.exists(), f"Target helper script missing: {target_script}")

    active_processes: list[subprocess.Popen[str]] = []
    try:
        _ensure_probe_table(paths.database_path)

        abort_before_process = subprocess.Popen(
            [
                sys.executable,
                str(target_script),
                "--database-path",
                str(paths.database_path),
                "--state-file",
                str(paths.abort_before_state_file),
                "--mode",
                "abort-before-commit",
                "--transaction-label",
                "abort_before_commit",
                "--rows",
                str(int(transaction_rows)),
                "--payload-bytes",
                str(int(payload_bytes)),
            ],
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        active_processes.append(abort_before_process)
        abort_before_state = _wait_for_state(
            process=abort_before_process,
            state_file=paths.abort_before_state_file,
            expected_status="pending_commit",
            timeout_seconds=float(startup_timeout_seconds),
        )
        abort_before_rc, abort_before_stdout, abort_before_stderr = _stop_process(abort_before_process)
        active_processes.remove(abort_before_process)

        abort_before_rows = _probe_row_count(paths.database_path, "abort_before_commit")
        _expect(
            abort_before_rows == 0,
            (
                "Rows from pre-commit aborted transaction leaked into durable state. "
                f"observed={abort_before_rows} expected=0"
            ),
        )

        abort_after_process = subprocess.Popen(
            [
                sys.executable,
                str(target_script),
                "--database-path",
                str(paths.database_path),
                "--state-file",
                str(paths.abort_after_state_file),
                "--mode",
                "abort-after-commit",
                "--transaction-label",
                "abort_after_commit",
                "--rows",
                str(int(transaction_rows)),
                "--payload-bytes",
                str(int(payload_bytes)),
            ],
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        active_processes.append(abort_after_process)
        abort_after_state = _wait_for_state(
            process=abort_after_process,
            state_file=paths.abort_after_state_file,
            expected_status="committed",
            timeout_seconds=float(startup_timeout_seconds),
        )
        abort_after_rc, abort_after_stdout, abort_after_stderr = _stop_process(abort_after_process)
        active_processes.remove(abort_after_process)

        abort_after_rows = _probe_row_count(paths.database_path, "abort_after_commit")
        _expect(
            abort_after_rows == int(transaction_rows),
            (
                "Committed rows were not durable after hard-abort. "
                f"observed={abort_after_rows} expected={int(transaction_rows)}"
            ),
        )
        _expect(
            int(abort_after_state.get("persisted_rows") or -1) == int(transaction_rows),
            (
                "Target helper reported unexpected persisted row count. "
                f"state={json.dumps(abort_after_state, sort_keys=True)}"
            ),
        )

        post_recovery_write_rows = _insert_post_recovery_row(paths.database_path)
        _expect(
            post_recovery_write_rows == 1,
            (
                "Post-recovery write probe failed. "
                f"observed={post_recovery_write_rows} expected=1"
            ),
        )

        profile = _database_profile(paths.database_path)
        _expect(
            bool(profile["quick_ok"]) and bool(profile["full_ok"]),
            f"Database integrity check failed after durability drill: {profile}",
        )
        app_probe = _app_probe(paths.database_path)

        report = {
            "label": label,
            "success": True,
            "scenarios": {
                "abort_before_commit": {
                    "expected_rows": 0,
                    "observed_rows": abort_before_rows,
                    "state": abort_before_state,
                    "process_return_code": abort_before_rc,
                    "stdout": abort_before_stdout.strip(),
                    "stderr": abort_before_stderr.strip(),
                },
                "abort_after_commit": {
                    "expected_rows": int(transaction_rows),
                    "observed_rows": abort_after_rows,
                    "state": abort_after_state,
                    "process_return_code": abort_after_rc,
                    "stdout": abort_after_stdout.strip(),
                    "stderr": abort_after_stderr.strip(),
                },
            },
            "post_recovery_write_rows": post_recovery_write_rows,
            "database_profile": profile,
            "journal_artifacts": _journal_artifacts(paths.database_path),
            "app_probe": {
                "readiness_ready": bool(app_probe["readiness"]["ready"]),
                "slo_status": str(app_probe["slo"]["status"]),
                "integrity_ok": bool((app_probe["integrity"]["integrity"] or {}).get("ok")),
                "post_recovery_goal_status_code": int(app_probe["post_recovery_goal_status_code"]),
                "post_recovery_goal_id": str(app_probe["post_recovery_goal"]["goal_id"]),
            },
            "decision": {
                "release_blocked": False,
                "recommended_action": "proceed",
            },
            "paths": {
                "run_dir": str(paths.run_dir),
                "database_path": str(paths.database_path),
                "target_script": str(target_script),
                "abort_before_state_file": str(paths.abort_before_state_file),
                "abort_after_state_file": str(paths.abort_after_state_file),
            },
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }
        return report
    finally:
        for process in active_processes:
            if process.poll() is None:
                try:
                    process.kill()
                    process.wait(timeout=5.0)
                except Exception:
                    pass
        if not keep_artifacts:
            shutil.rmtree(paths.run_dir, ignore_errors=True)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Power-loss durability drill: hard-abort transaction process before and after commit, "
            "then validate durable persistence, integrity, and post-recovery write/readiness behavior."
        )
    )
    parser.add_argument("--workspace", default=str(PROJECT_ROOT / ".tmp" / "power-loss-durability-drills"))
    parser.add_argument("--label", default="power-loss-durability-drill")
    parser.add_argument("--transaction-rows", type=int, default=240)
    parser.add_argument("--payload-bytes", type=int, default=256)
    parser.add_argument("--startup-timeout-seconds", type=float, default=15.0)
    parser.add_argument("--keep-artifacts", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if int(args.transaction_rows) <= 0:
        print("[power-loss-durability-drill] ERROR: --transaction-rows must be > 0.", file=sys.stderr)
        return 2
    if int(args.payload_bytes) <= 0:
        print("[power-loss-durability-drill] ERROR: --payload-bytes must be > 0.", file=sys.stderr)
        return 2
    if float(args.startup_timeout_seconds) <= 0:
        print("[power-loss-durability-drill] ERROR: --startup-timeout-seconds must be > 0.", file=sys.stderr)
        return 2

    workspace_root = Path(str(args.workspace)).expanduser()
    workspace_root.mkdir(parents=True, exist_ok=True)
    try:
        report = run_drill(
            workspace_root=workspace_root,
            label=str(args.label),
            transaction_rows=int(args.transaction_rows),
            payload_bytes=int(args.payload_bytes),
            startup_timeout_seconds=float(args.startup_timeout_seconds),
            keep_artifacts=bool(args.keep_artifacts),
        )
    except Exception as exc:
        print(f"[power-loss-durability-drill] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
