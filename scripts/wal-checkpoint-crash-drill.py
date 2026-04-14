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
from goal_ops_console.main import create_app


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _connect(database_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        str(database_path),
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


def _row_count(database_path: Path, marker: str) -> int:
    connection = _connect(database_path)
    try:
        row = connection.execute(
            "SELECT COUNT(*) FROM wal_checkpoint_probe WHERE marker = ?",
            (marker,),
        ).fetchone()
        return int(row[0] if row is not None else 0)
    finally:
        connection.close()


def _wal_artifacts(database_path: Path) -> dict[str, dict[str, Any]]:
    artifacts: dict[str, dict[str, Any]] = {}
    for suffix in ("-wal", "-shm"):
        artifact_path = Path(str(database_path) + suffix)
        exists = artifact_path.exists()
        artifacts[suffix] = {
            "path": str(artifact_path),
            "exists": exists,
            "size_bytes": int(artifact_path.stat().st_size) if exists else 0,
        }
    return artifacts


def _integrity_profile(database_path: Path) -> dict[str, Any]:
    connection = _connect(database_path)
    try:
        journal_mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        full_check = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
    finally:
        connection.close()
    return {
        "journal_mode": journal_mode,
        "quick_check": quick_check,
        "full_check": full_check,
        "quick_ok": quick_check.lower() == "ok",
        "full_ok": full_check.lower() == "ok",
    }


def _run_checkpoint(database_path: Path, checkpoint_mode: str) -> dict[str, Any]:
    connection = _connect(database_path)
    try:
        row = connection.execute(f"PRAGMA wal_checkpoint({checkpoint_mode})").fetchone()
    finally:
        connection.close()
    return {
        "mode": str(checkpoint_mode),
        "busy": int(row[0]) if row is not None else -1,
        "log_frames": int(row[1]) if row is not None else -1,
        "checkpointed_frames": int(row[2]) if row is not None else -1,
    }


def _running_runs(client: TestClient) -> list[str]:
    response = client.get("/workflows/runs?limit=200")
    _expect(response.status_code == 200, f"Workflow runs endpoint failed: {response.status_code}")
    runs = response.json().get("runs") or []
    running: list[str] = []
    for run in runs:
        if not isinstance(run, dict):
            continue
        if str(run.get("status")) == "running" and run.get("run_id"):
            running.append(str(run.get("run_id")))
    return running


def _app_probe(database_path: Path) -> dict[str, Any]:
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
        safe_mode = client.get("/system/safe-mode")
        integrity_quick = client.get("/system/database/integrity?mode=quick")
        integrity_full = client.get("/system/database/integrity?mode=full")
        created = client.post(
            "/goals",
            json={
                "title": "WAL Checkpoint Crash Drill Goal",
                "description": "Post-crash checkpoint recovery mutation probe",
                "urgency": 0.6,
                "value": 0.7,
                "deadline_score": 0.3,
            },
        )
        running_runs = _running_runs(client)

    _expect(readiness.status_code == 200, f"Readiness probe failed: {readiness.status_code}")
    _expect(slo.status_code == 200, f"SLO probe failed: {slo.status_code}")
    _expect(safe_mode.status_code == 200, f"Safe mode probe failed: {safe_mode.status_code}")
    _expect(integrity_quick.status_code == 200, f"Quick integrity probe failed: {integrity_quick.status_code}")
    _expect(integrity_full.status_code == 200, f"Full integrity probe failed: {integrity_full.status_code}")
    _expect(
        created.status_code == 201,
        f"Post-recovery goal write failed: {created.status_code} {created.text}",
    )

    readiness_payload = readiness.json()
    slo_payload = slo.json()
    safe_mode_payload = safe_mode.json()
    integrity_quick_payload = integrity_quick.json()
    integrity_full_payload = integrity_full.json()
    created_payload = created.json()

    _expect(
        bool(readiness_payload.get("ready")),
        f"Readiness not ready after WAL checkpoint crash recovery: {readiness_payload}",
    )
    _expect(
        str(slo_payload.get("status")) == "ok",
        f"SLO status not ok after WAL checkpoint crash recovery: {slo_payload}",
    )
    _expect(
        safe_mode_payload.get("active") is False,
        f"Safe mode unexpectedly active after WAL checkpoint crash recovery: {safe_mode_payload}",
    )
    _expect(
        bool((integrity_quick_payload.get("integrity") or {}).get("ok")),
        f"Quick integrity endpoint is not ok: {integrity_quick_payload}",
    )
    _expect(
        bool((integrity_full_payload.get("integrity") or {}).get("ok")),
        f"Full integrity endpoint is not ok: {integrity_full_payload}",
    )
    _expect(
        not running_runs,
        f"Found running workflow runs after WAL checkpoint crash recovery: {running_runs}",
    )

    return {
        "readiness": readiness_payload,
        "slo": slo_payload,
        "safe_mode": safe_mode_payload,
        "integrity_quick": integrity_quick_payload,
        "integrity_full": integrity_full_payload,
        "post_recovery_goal": created_payload,
        "post_recovery_goal_status_code": int(created.status_code),
        "running_run_ids": running_runs,
    }


@dataclass(slots=True)
class DrillPaths:
    run_dir: Path
    database_path: Path
    state_file: Path


def _create_paths(workspace_root: Path) -> DrillPaths:
    run_id = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    run_dir = workspace_root / f"wal-checkpoint-crash-{run_id}"
    return DrillPaths(
        run_dir=run_dir,
        database_path=run_dir / "wal-checkpoint-crash.db",
        state_file=run_dir / "target-state.json",
    )


def run_drill(
    *,
    workspace_root: Path,
    label: str,
    rows: int,
    payload_bytes: int,
    startup_timeout_seconds: float,
    sleep_before_checkpoint_seconds: float,
    checkpoint_mode: str,
    keep_artifacts: bool,
) -> dict[str, Any]:
    started = time.perf_counter()
    paths = _create_paths(workspace_root)
    paths.run_dir.mkdir(parents=True, exist_ok=False)

    target_script = PROJECT_ROOT / "scripts" / "wal-checkpoint-crash-target.py"
    _expect(target_script.exists(), f"Target helper script missing: {target_script}")

    marker = f"wal-checkpoint-crash-{uuid.uuid4().hex[:10]}"
    target_process: subprocess.Popen[str] | None = None
    target_return_code = -1
    target_stdout = ""
    target_stderr = ""

    try:
        target_process = subprocess.Popen(
            [
                sys.executable,
                str(target_script),
                "--database-path",
                str(paths.database_path),
                "--state-file",
                str(paths.state_file),
                "--marker",
                marker,
                "--rows",
                str(int(rows)),
                "--payload-bytes",
                str(int(payload_bytes)),
                "--sleep-before-checkpoint-seconds",
                str(float(sleep_before_checkpoint_seconds)),
                "--checkpoint-mode",
                str(checkpoint_mode),
            ],
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        pending_state = _wait_for_state(
            process=target_process,
            state_file=paths.state_file,
            expected_status="checkpoint_pending",
            timeout_seconds=max(5.0, float(startup_timeout_seconds)),
        )
        target_return_code, target_stdout, target_stderr = _stop_process(target_process)
        target_process = None

        rows_after_crash = _row_count(paths.database_path, marker)
        _expect(
            rows_after_crash == int(rows),
            (
                "Committed WAL rows were not durable after checkpoint crash simulation. "
                f"observed={rows_after_crash} expected={int(rows)} marker={marker}"
            ),
        )

        profile_after_crash = _integrity_profile(paths.database_path)
        _expect(
            bool(profile_after_crash["quick_ok"]) and bool(profile_after_crash["full_ok"]),
            f"Database integrity check failed after checkpoint crash: {profile_after_crash}",
        )
        wal_artifacts_after_crash = _wal_artifacts(paths.database_path)

        recovery_checkpoint = _run_checkpoint(paths.database_path, checkpoint_mode=str(checkpoint_mode))
        _expect(
            int(recovery_checkpoint["busy"]) == 0,
            f"Checkpoint recovery reported busy writer: {recovery_checkpoint}",
        )
        profile_after_recovery = _integrity_profile(paths.database_path)
        _expect(
            bool(profile_after_recovery["quick_ok"]) and bool(profile_after_recovery["full_ok"]),
            f"Database integrity check failed after recovery checkpoint: {profile_after_recovery}",
        )
        wal_artifacts_after_recovery = _wal_artifacts(paths.database_path)

        app_probe = _app_probe(paths.database_path)

        return {
            "label": label,
            "success": True,
            "scenario": {
                "marker": marker,
                "rows_requested": int(rows),
                "rows_persisted_before_crash": int(pending_state.get("persisted_rows") or -1),
                "rows_observed_after_crash": int(rows_after_crash),
                "target_state": pending_state,
                "target_process_return_code": int(target_return_code),
                "target_stdout": target_stdout.strip(),
                "target_stderr": target_stderr.strip(),
            },
            "checkpoint_recovery": recovery_checkpoint,
            "integrity": {
                "after_crash": profile_after_crash,
                "after_recovery_checkpoint": profile_after_recovery,
            },
            "wal_artifacts": {
                "after_crash": wal_artifacts_after_crash,
                "after_recovery_checkpoint": wal_artifacts_after_recovery,
            },
            "app_probe": {
                "readiness_ready": bool(app_probe["readiness"]["ready"]),
                "slo_status": str(app_probe["slo"]["status"]),
                "safe_mode_active": bool(app_probe["safe_mode"]["active"]),
                "integrity_quick_ok": bool((app_probe["integrity_quick"]["integrity"] or {}).get("ok")),
                "integrity_full_ok": bool((app_probe["integrity_full"]["integrity"] or {}).get("ok")),
                "post_recovery_goal_status_code": int(app_probe["post_recovery_goal_status_code"]),
                "post_recovery_goal_id": str(app_probe["post_recovery_goal"]["goal_id"]),
                "running_run_ids": app_probe["running_run_ids"],
            },
            "decision": {
                "release_blocked": False,
                "recommended_action": "proceed",
            },
            "paths": {
                "run_dir": str(paths.run_dir),
                "database_path": str(paths.database_path),
                "state_file": str(paths.state_file),
                "target_script": str(target_script),
            },
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }
    finally:
        if target_process is not None and target_process.poll() is None:
            try:
                target_process.kill()
                target_process.wait(timeout=5.0)
            except Exception:
                pass
        if not keep_artifacts:
            shutil.rmtree(paths.run_dir, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "WAL checkpoint crash drill: commit rows in WAL mode, hard-abort before checkpoint "
            "completion, then verify durability, integrity, deterministic readiness/SLO, and "
            "checkpoint recovery."
        )
    )
    parser.add_argument("--workspace", default=str(PROJECT_ROOT / ".tmp" / "wal-checkpoint-crash-drills"))
    parser.add_argument("--label", default="wal-checkpoint-crash-drill")
    parser.add_argument("--rows", type=int, default=240)
    parser.add_argument("--payload-bytes", type=int, default=1024)
    parser.add_argument("--startup-timeout-seconds", type=float, default=15.0)
    parser.add_argument("--sleep-before-checkpoint-seconds", type=float, default=30.0)
    parser.add_argument("--checkpoint-mode", default="TRUNCATE", choices=("PASSIVE", "FULL", "TRUNCATE"))
    parser.add_argument("--keep-artifacts", action="store_true")
    args = parser.parse_args(argv)

    if int(args.rows) <= 0:
        print("[wal-checkpoint-crash-drill] ERROR: --rows must be > 0.", file=sys.stderr)
        return 2
    if int(args.payload_bytes) <= 0:
        print("[wal-checkpoint-crash-drill] ERROR: --payload-bytes must be > 0.", file=sys.stderr)
        return 2
    if float(args.startup_timeout_seconds) <= 0:
        print("[wal-checkpoint-crash-drill] ERROR: --startup-timeout-seconds must be > 0.", file=sys.stderr)
        return 2
    if float(args.sleep_before_checkpoint_seconds) <= 0:
        print("[wal-checkpoint-crash-drill] ERROR: --sleep-before-checkpoint-seconds must be > 0.", file=sys.stderr)
        return 2

    workspace_root = Path(str(args.workspace)).expanduser()
    workspace_root.mkdir(parents=True, exist_ok=True)

    try:
        report = run_drill(
            workspace_root=workspace_root,
            label=str(args.label),
            rows=int(args.rows),
            payload_bytes=int(args.payload_bytes),
            startup_timeout_seconds=float(args.startup_timeout_seconds),
            sleep_before_checkpoint_seconds=float(args.sleep_before_checkpoint_seconds),
            checkpoint_mode=str(args.checkpoint_mode),
            keep_artifacts=bool(args.keep_artifacts),
        )
    except Exception as exc:
        print(f"[wal-checkpoint-crash-drill] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
