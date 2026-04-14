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


def _json_or_text(response_text: str) -> str:
    text = str(response_text or "").strip()
    if not text:
        return "<empty>"
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text
    return json.dumps(payload, ensure_ascii=True, sort_keys=True)


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


def _page_stats(database_path: Path) -> dict[str, int]:
    connection = _connect(database_path)
    try:
        page_count = int(connection.execute("PRAGMA page_count").fetchone()[0])
        page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
        max_page_count = int(connection.execute("PRAGMA max_page_count").fetchone()[0])
    finally:
        connection.close()
    return {
        "page_count": page_count,
        "page_size": page_size,
        "max_page_count": max_page_count,
    }


def _page_stats_from_connection(connect_fn: Any) -> dict[str, int]:
    connection = connect_fn()
    try:
        page_count = int(connection.execute("PRAGMA page_count").fetchone()[0])
        page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
        max_page_count = int(connection.execute("PRAGMA max_page_count").fetchone()[0])
    finally:
        connection.close()
    return {
        "page_count": page_count,
        "page_size": page_size,
        "max_page_count": max_page_count,
    }


def _metric_value(client: TestClient, metric_name: str) -> int:
    response = client.get(f"/system/metrics?prefix={metric_name}&limit=200")
    _expect(response.status_code == 200, f"Failed to fetch metrics: status={response.status_code}")
    metrics = response.json().get("metrics") or []
    for item in metrics:
        if not isinstance(item, dict):
            continue
        if str(item.get("metric_name")) == metric_name:
            return int(item.get("value") or 0)
    return 0


def _integrity_ok(client: TestClient, mode: str) -> bool:
    response = client.get(f"/system/database/integrity?mode={mode}")
    _expect(response.status_code == 200, f"Integrity endpoint ({mode}) failed: {response.status_code}")
    payload = response.json()
    return bool((payload.get("integrity") or {}).get("ok"))


def _running_runs(client: TestClient) -> list[str]:
    response = client.get("/workflows/runs?limit=200")
    _expect(response.status_code == 200, f"Workflow runs endpoint failed: {response.status_code}")
    runs = response.json().get("runs") or []
    run_ids: list[str] = []
    for item in runs:
        if not isinstance(item, dict):
            continue
        if str(item.get("status")) == "running" and item.get("run_id"):
            run_ids.append(str(item.get("run_id")))
    return run_ids


def _goal_payload(index: int, payload_bytes: int) -> dict[str, Any]:
    blob = "x" * max(128, min(int(payload_bytes), 900))
    description = f"real-sqlite-full:{index}:{blob}"
    if len(description) > 1000:
        description = description[:1000]
    return {
        "title": f"SQLite Full Drill Goal {index}",
        "description": description,
        "urgency": 0.7,
        "value": 0.7,
        "deadline_score": 0.4,
    }


@dataclass(slots=True)
class DrillPaths:
    run_dir: Path
    database_path: Path


def _create_paths(workspace_root: Path) -> DrillPaths:
    run_id = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    run_dir = workspace_root / f"sqlite-real-full-{run_id}"
    return DrillPaths(
        run_dir=run_dir,
        database_path=run_dir / "sqlite-real-full.db",
    )


def run_drill(
    *,
    workspace_root: Path,
    label: str,
    payload_bytes: int,
    max_write_attempts: int,
    max_page_growth: int,
    recovery_page_growth: int,
    keep_artifacts: bool,
) -> dict[str, Any]:
    started = time.perf_counter()
    paths = _create_paths(workspace_root)
    paths.run_dir.mkdir(parents=True, exist_ok=False)

    app = create_app(
        Settings(
            database_url=str(paths.database_path),
            safe_mode_lock_error_threshold=6,
            safe_mode_lock_error_window_seconds=60,
            safe_mode_io_error_threshold=1,
            safe_mode_io_error_window_seconds=60,
            slo_min_http_request_sample=5000,
            slo_min_event_attempt_sample=5000,
            workflow_worker_poll_interval_seconds=0.05,
            workflow_startup_recovery_max_age_seconds=0,
        )
    )

    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            services = client.app.state.services
            db = services.db
            original_connect = db._connect

            initial_readiness = client.get("/system/readiness")
            initial_slo = client.get("/system/slo")
            _expect(initial_readiness.status_code == 200, "Initial readiness probe failed.")
            _expect(initial_slo.status_code == 200, "Initial SLO probe failed.")
            _expect(initial_readiness.json().get("ready") is True, f"Initial readiness not ready: {initial_readiness.json()}")
            _expect(str(initial_slo.json().get("status")) == "ok", f"Initial SLO not ok: {initial_slo.json()}")

            before_stats = _page_stats_from_connection(original_connect)
            cap_state: dict[str, int] = {
                "max_page_count": int(before_stats["page_count"]) + max(8, int(max_page_growth))
            }

            def _capped_connect() -> sqlite3.Connection:
                connection = original_connect()
                connection.execute(f"PRAGMA max_page_count = {int(cap_state['max_page_count'])}")
                return connection

            db._connect = _capped_connect
            constrained_target = int(cap_state["max_page_count"])
            constrained_stats = _page_stats_from_connection(db._connect)
            constrained_effective = int(constrained_stats["max_page_count"])

            status_counts: dict[str, int] = {}
            first_failure_status: int | None = None
            first_failure_body = ""
            safe_mode_after_failure: dict[str, Any] | None = None

            for index in range(max(1, int(max_write_attempts))):
                response = client.post("/goals", json=_goal_payload(index=index, payload_bytes=int(payload_bytes)))
                code = str(response.status_code)
                status_counts[code] = int(status_counts.get(code, 0)) + 1
                if response.status_code == 201:
                    continue
                if response.status_code == 422:
                    raise RuntimeError(
                        "Received validation error during fill loop. "
                        f"body={_json_or_text(response.text)}"
                    )
                if response.status_code == 429:
                    raise RuntimeError(
                        "Backpressure triggered before SQLITE_FULL. "
                        "Increase write attempts or reduce max-page-growth. "
                        f"status_counts={status_counts} body={_json_or_text(response.text)}"
                    )

                if first_failure_status is None:
                    first_failure_status = int(response.status_code)
                    first_failure_body = _json_or_text(response.text)

                safe_mode_probe = client.get("/system/safe-mode")
                _expect(safe_mode_probe.status_code == 200, "Safe mode probe failed during fill.")
                safe_mode_payload = safe_mode_probe.json()
                if bool(safe_mode_payload.get("active")):
                    safe_mode_after_failure = safe_mode_payload
                    break

            _expect(
                first_failure_status is not None,
                (
                    "Did not observe write failure while filling constrained database. "
                    f"status_counts={status_counts} constrained_stats={constrained_stats}"
                ),
            )
            _expect(
                int(first_failure_status) == 500,
                (
                    "First write failure was not a 500 SQLite write-path error. "
                    f"first_failure_status={first_failure_status} "
                    f"first_failure_body={first_failure_body} status_counts={status_counts}"
                ),
            )
            if safe_mode_after_failure is None:
                safe_mode_probe = client.get("/system/safe-mode")
                _expect(safe_mode_probe.status_code == 200, "Safe mode probe failed after fill loop.")
                safe_mode_after_failure = safe_mode_probe.json()
            _expect(
                bool(safe_mode_after_failure.get("active")),
                f"Safe mode not active after real SQLITE_FULL trigger: {safe_mode_after_failure}",
            )

            blocked = client.post(
                "/goals",
                json=_goal_payload(index=999_001, payload_bytes=int(payload_bytes)),
            )
            _expect(
                blocked.status_code == 503,
                (
                    "Mutating endpoint not blocked while safe mode active after SQLITE_FULL. "
                    f"status={blocked.status_code} body={_json_or_text(blocked.text)}"
                ),
            )

            readiness_during = client.get("/system/readiness")
            slo_during = client.get("/system/slo")
            _expect(readiness_during.status_code == 200, "Readiness probe failed during SQLITE_FULL scenario.")
            _expect(slo_during.status_code == 200, "SLO probe failed during SQLITE_FULL scenario.")
            readiness_during_payload = readiness_during.json()
            slo_during_payload = slo_during.json()
            _expect(
                readiness_during_payload.get("ready") is False,
                f"Readiness unexpectedly true during safe mode: {readiness_during_payload}",
            )
            _expect(
                str(slo_during_payload.get("status")) == "critical",
                f"SLO status should be critical while safe mode active: {slo_during_payload}",
            )

            integrity_quick_during = _integrity_ok(client, mode="quick")
            integrity_full_during = _integrity_ok(client, mode="full")
            running_during = _running_runs(client)
            _expect(not running_during, f"Found running workflow runs during SQLITE_FULL drill: {running_during}")

            io_metric_value = _metric_value(client, "runtime.db_errors.io")
            _expect(io_metric_value >= 1, f"I/O error metric did not increment after SQLITE_FULL: {io_metric_value}")

            expanded_target = int(constrained_effective) + max(32, int(recovery_page_growth))
            cap_state["max_page_count"] = int(expanded_target)
            expanded_stats = _page_stats_from_connection(db._connect)
            expanded_effective = int(expanded_stats["max_page_count"])

            disable = client.post(
                "/system/safe-mode/disable",
                json={"reason": "Recovered capacity after real SQLITE_FULL drill."},
            )
            _expect(
                disable.status_code == 200,
                f"Safe mode disable failed after capacity recovery: status={disable.status_code} body={_json_or_text(disable.text)}",
            )
            safe_mode_after_disable = client.get("/system/safe-mode").json()
            _expect(
                safe_mode_after_disable.get("active") is False,
                f"Safe mode remained active after disable: {safe_mode_after_disable}",
            )

            recovery_goal = client.post(
                "/goals",
                json=_goal_payload(index=999_999, payload_bytes=max(1024, int(payload_bytes // 2))),
            )
            _expect(
                recovery_goal.status_code == 201,
                (
                    "Post-recovery goal create failed after expanding max_page_count. "
                    f"status={recovery_goal.status_code} body={_json_or_text(recovery_goal.text)}"
                ),
            )
            recovery_goal_payload = recovery_goal.json()

            readiness_after = client.get("/system/readiness")
            slo_after = client.get("/system/slo")
            _expect(readiness_after.status_code == 200, "Readiness probe failed after recovery.")
            _expect(slo_after.status_code == 200, "SLO probe failed after recovery.")
            readiness_after_payload = readiness_after.json()
            slo_after_payload = slo_after.json()
            _expect(
                readiness_after_payload.get("ready") is True,
                f"Readiness did not recover to true: {readiness_after_payload}",
            )
            _expect(
                str(slo_after_payload.get("status")) == "ok",
                f"SLO did not recover to ok: {slo_after_payload}",
            )

            integrity_quick_after = _integrity_ok(client, mode="quick")
            integrity_full_after = _integrity_ok(client, mode="full")
            running_after = _running_runs(client)
            _expect(not running_after, f"Found running workflow runs after recovery: {running_after}")

            return {
                "label": label,
                "success": True,
                "fill": {
                    "payload_bytes": int(payload_bytes),
                    "max_write_attempts": int(max_write_attempts),
                    "status_counts": status_counts,
                    "first_failure_status": int(first_failure_status),
                    "first_failure_body": first_failure_body,
                },
                "page_stats": {
                    "before": before_stats,
                    "constrained_target": int(constrained_target),
                    "constrained_effective": int(constrained_effective),
                    "constrained": constrained_stats,
                    "expanded_target": int(expanded_target),
                    "expanded_effective": int(expanded_effective),
                    "expanded": expanded_stats,
                },
                "safe_mode": {
                    "after_full_trigger": safe_mode_after_failure,
                    "after_disable": safe_mode_after_disable,
                },
                "status_codes": {
                    "blocked_mutation_during_safe_mode": int(blocked.status_code),
                    "post_recovery_goal_create": int(recovery_goal.status_code),
                },
                "readiness": {
                    "during_fault": bool(readiness_during_payload.get("ready")),
                    "after_recovery": bool(readiness_after_payload.get("ready")),
                },
                "slo": {
                    "during_fault": str(slo_during_payload.get("status")),
                    "after_recovery": str(slo_after_payload.get("status")),
                },
                "integrity": {
                    "during_fault_quick_ok": bool(integrity_quick_during),
                    "during_fault_full_ok": bool(integrity_full_during),
                    "after_recovery_quick_ok": bool(integrity_quick_after),
                    "after_recovery_full_ok": bool(integrity_full_after),
                },
                "runtime_metrics": {
                    "io_error_count": int(io_metric_value),
                },
                "workflow_runs": {
                    "running_during_fault": running_during,
                    "running_after_recovery": running_after,
                },
                "recovery_goal_id": str(recovery_goal_payload.get("goal_id")),
                "paths": {
                    "run_dir": str(paths.run_dir),
                    "database_path": str(paths.database_path),
                },
                "duration_ms": int((time.perf_counter() - started) * 1000),
            }
            
    finally:
        try:
            services = locals().get("services")
            db = getattr(services, "db", None) if services is not None else None
            original_connect = locals().get("original_connect")
            if db is not None and original_connect is not None:
                db._connect = original_connect
        except Exception:
            pass
        if not keep_artifacts:
            shutil.rmtree(paths.run_dir, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Real SQLITE_FULL drill using max_page_count: saturate file-backed SQLite until "
            "writes fail naturally, verify deterministic safe-mode degradation, then recover by "
            "expanding page budget and validating readiness/SLO/integrity restoration."
        )
    )
    parser.add_argument("--workspace", default=str(PROJECT_ROOT / ".tmp" / "sqlite-real-full-drills"))
    parser.add_argument("--label", default="sqlite-real-full-drill")
    parser.add_argument("--payload-bytes", type=int, default=8192)
    parser.add_argument("--max-write-attempts", type=int, default=240)
    parser.add_argument("--max-page-growth", type=int, default=24)
    parser.add_argument("--recovery-page-growth", type=int, default=160)
    parser.add_argument("--keep-artifacts", action="store_true")
    args = parser.parse_args(argv)

    if int(args.payload_bytes) <= 0:
        print("[sqlite-real-full-drill] ERROR: --payload-bytes must be > 0.", file=sys.stderr)
        return 2
    if int(args.max_write_attempts) <= 0:
        print("[sqlite-real-full-drill] ERROR: --max-write-attempts must be > 0.", file=sys.stderr)
        return 2
    if int(args.max_page_growth) <= 0:
        print("[sqlite-real-full-drill] ERROR: --max-page-growth must be > 0.", file=sys.stderr)
        return 2
    if int(args.recovery_page_growth) <= 0:
        print("[sqlite-real-full-drill] ERROR: --recovery-page-growth must be > 0.", file=sys.stderr)
        return 2

    workspace_root = Path(str(args.workspace)).expanduser()
    workspace_root.mkdir(parents=True, exist_ok=True)

    try:
        report = run_drill(
            workspace_root=workspace_root,
            label=str(args.label),
            payload_bytes=int(args.payload_bytes),
            max_write_attempts=int(args.max_write_attempts),
            max_page_growth=int(args.max_page_growth),
            recovery_page_growth=int(args.recovery_page_growth),
            keep_artifacts=bool(args.keep_artifacts),
        )
    except Exception as exc:
        print(f"[sqlite-real-full-drill] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
