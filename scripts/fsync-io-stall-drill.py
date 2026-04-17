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


def _integrity_probe(client: TestClient, *, mode: str) -> dict[str, Any]:
    response = client.get(f"/system/database/integrity?mode={mode}")
    _expect(response.status_code == 200, f"Integrity probe ({mode}) failed: {response.status_code}")
    payload = response.json()
    _expect(bool((payload.get("integrity") or {}).get("ok")), f"Integrity probe ({mode}) not ok: {payload}")
    return payload


def _running_run_ids(client: TestClient, *, limit: int = 200) -> list[str]:
    response = client.get(f"/workflows/runs?limit={int(limit)}")
    _expect(response.status_code == 200, f"Failed to list workflow runs: {response.status_code}")
    runs = response.json().get("runs") or []
    run_ids: list[str] = []
    for item in runs:
        if not isinstance(item, dict):
            continue
        if str(item.get("status")) == "running" and item.get("run_id"):
            run_ids.append(str(item["run_id"]))
    return run_ids


def _goal_payload(*, title: str) -> dict[str, Any]:
    return {
        "title": title,
        "description": "fsync/I/O stall drill",
        "urgency": 0.6,
        "value": 0.7,
        "deadline_score": 0.3,
    }


def _inject_stalled_io_faults(
    *,
    client: TestClient,
    stall_seconds: float,
    fault_injections: int,
    max_stall_request_seconds: float,
) -> dict[str, Any]:
    services = client.app.state.services
    original_create_goal = services.state_manager.create_goal
    state = {"remaining": max(1, int(fault_injections)), "triggered": 0}
    request_latencies_ms: list[int] = []
    synthetic_stall_ms: list[int] = []

    def _stalled_faulty_create_goal(
        *,
        title: str,
        description: str | None,
        urgency: float,
        value: float,
        deadline_score: float,
    ) -> dict[str, Any]:
        if state["remaining"] > 0:
            state["remaining"] -= 1
            state["triggered"] += 1
            stall_started = time.perf_counter()
            time.sleep(float(stall_seconds))
            synthetic_stall_ms.append(int((time.perf_counter() - stall_started) * 1000))
            raise sqlite3.OperationalError("disk i/o error (simulated fsync stall)")
        return original_create_goal(
            title=title,
            description=description,
            urgency=urgency,
            value=value,
            deadline_score=deadline_score,
        )

    services.state_manager.create_goal = _stalled_faulty_create_goal
    try:
        for index in range(max(1, int(fault_injections))):
            started = time.perf_counter()
            response = client.post(
                "/goals",
                json=_goal_payload(title=f"fsync stall fault {index}"),
            )
            elapsed = float(time.perf_counter() - started)
            elapsed_ms = int(elapsed * 1000)
            request_latencies_ms.append(elapsed_ms)
            _expect(
                response.status_code == 500,
                (
                    "Fault injection request did not produce expected 500 response. "
                    f"status={response.status_code} body={_json_or_text(response.text)}"
                ),
            )
            _expect(
                elapsed >= float(stall_seconds) * 0.9,
                (
                    "Observed request latency was below expected injected stall duration. "
                    f"elapsed_seconds={elapsed:.3f} stall_seconds={float(stall_seconds):.3f}"
                ),
            )
            _expect(
                elapsed <= float(max_stall_request_seconds),
                (
                    "Injected stall request exceeded maximum bounded latency budget. "
                    f"elapsed_seconds={elapsed:.3f} max_stall_request_seconds={float(max_stall_request_seconds):.3f}"
                ),
            )
    finally:
        services.state_manager.create_goal = original_create_goal

    return {
        "triggered": int(state["triggered"]),
        "remaining": int(state["remaining"]),
        "request_latencies_ms": request_latencies_ms,
        "synthetic_stall_ms": synthetic_stall_ms,
    }


@dataclass(slots=True)
class DrillPaths:
    run_dir: Path
    database_path: Path


def _create_paths(workspace_root: Path) -> DrillPaths:
    run_id = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    run_dir = workspace_root / f"fsync-io-stall-{run_id}"
    return DrillPaths(
        run_dir=run_dir,
        database_path=run_dir / "fsync-io-stall.db",
    )


def run_drill(
    *,
    workspace_root: Path,
    label: str,
    fault_injections: int,
    stall_seconds: float,
    max_stall_request_seconds: float,
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
            safe_mode_io_error_threshold=max(1, int(fault_injections)),
            safe_mode_io_error_window_seconds=60,
            workflow_worker_poll_interval_seconds=0.05,
            workflow_startup_recovery_max_age_seconds=0,
        )
    )

    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            readiness_before = client.get("/system/readiness")
            slo_before = client.get("/system/slo")
            _expect(readiness_before.status_code == 200, "Initial readiness probe failed.")
            _expect(slo_before.status_code == 200, "Initial SLO probe failed.")
            _expect(readiness_before.json().get("ready") is True, f"Initial readiness not ready: {readiness_before.json()}")
            _expect(str(slo_before.json().get("status")) == "ok", f"Initial SLO not ok: {slo_before.json()}")

            injection = _inject_stalled_io_faults(
                client=client,
                stall_seconds=float(stall_seconds),
                fault_injections=int(fault_injections),
                max_stall_request_seconds=float(max_stall_request_seconds),
            )
            _expect(
                int(injection["triggered"]) == int(fault_injections),
                f"Fault injection count mismatch: {injection}",
            )

            safe_mode = client.get("/system/safe-mode")
            _expect(safe_mode.status_code == 200, "Safe mode endpoint failed.")
            safe_mode_payload = safe_mode.json()
            _expect(
                safe_mode_payload.get("active") is True,
                f"Safe mode not active after fsync/I/O stall faults: {safe_mode_payload}",
            )

            blocked = client.post(
                "/goals",
                json=_goal_payload(title="blocked while safe mode"),
            )
            _expect(
                blocked.status_code == 503,
                (
                    "Mutating endpoint was not blocked while safe mode was active. "
                    f"status={blocked.status_code} body={_json_or_text(blocked.text)}"
                ),
            )

            readiness_during = client.get("/system/readiness")
            slo_during = client.get("/system/slo")
            _expect(readiness_during.status_code == 200, "Readiness probe during fault failed.")
            _expect(slo_during.status_code == 200, "SLO probe during fault failed.")
            readiness_during_payload = readiness_during.json()
            slo_during_payload = slo_during.json()
            _expect(
                readiness_during_payload.get("ready") is False,
                f"Readiness unexpectedly ready while safe mode active: {readiness_during_payload}",
            )
            _expect(
                str(slo_during_payload.get("status")) == "critical",
                f"SLO should be critical while safe mode is active: {slo_during_payload}",
            )

            integrity_quick_during = _integrity_probe(client, mode="quick")
            integrity_full_during = _integrity_probe(client, mode="full")
            running_during = _running_run_ids(client)
            _expect(not running_during, f"Found running workflow runs during fault: {running_during}")

            io_errors = _metric_value(client, "runtime.db_errors.io")
            _expect(
                int(io_errors) >= int(fault_injections),
                f"I/O error metric did not increment as expected: metric={io_errors} expected>={fault_injections}",
            )

            disable = client.post(
                "/system/safe-mode/disable",
                json={"reason": "fsync/io stall fault resolved"},
            )
            _expect(
                disable.status_code == 200,
                f"Safe mode disable failed: status={disable.status_code} body={_json_or_text(disable.text)}",
            )
            safe_mode_after_disable = client.get("/system/safe-mode").json()
            _expect(
                safe_mode_after_disable.get("active") is False,
                f"Safe mode remained active after disable: {safe_mode_after_disable}",
            )

            recovery_started = time.perf_counter()
            recovered_goal = client.post(
                "/goals",
                json=_goal_payload(title="recovered after fsync/io stall"),
            )
            recovery_elapsed_ms = int((time.perf_counter() - recovery_started) * 1000)
            _expect(
                recovered_goal.status_code == 201,
                (
                    "Recovery goal create failed after disabling safe mode. "
                    f"status={recovered_goal.status_code} body={_json_or_text(recovered_goal.text)}"
                ),
            )
            _expect(
                recovery_elapsed_ms <= int(float(max_stall_request_seconds) * 1000),
                (
                    "Post-recovery write latency exceeded bounded budget. "
                    f"elapsed_ms={recovery_elapsed_ms} max_ms={int(float(max_stall_request_seconds) * 1000)}"
                ),
            )
            recovered_goal_payload = recovered_goal.json()

            readiness_after = client.get("/system/readiness")
            slo_after = client.get("/system/slo")
            _expect(readiness_after.status_code == 200, "Readiness probe after recovery failed.")
            _expect(slo_after.status_code == 200, "SLO probe after recovery failed.")
            readiness_after_payload = readiness_after.json()
            slo_after_payload = slo_after.json()
            _expect(
                readiness_after_payload.get("ready") is True,
                f"Readiness did not recover: {readiness_after_payload}",
            )
            _expect(
                str(slo_after_payload.get("status")) == "ok",
                f"SLO did not recover to ok: {slo_after_payload}",
            )

            integrity_quick_after = _integrity_probe(client, mode="quick")
            integrity_full_after = _integrity_probe(client, mode="full")
            running_after = _running_run_ids(client)
            _expect(not running_after, f"Found running workflow runs after recovery: {running_after}")

            return {
                "label": label,
                "success": True,
                "fault_profile": {
                    "fault_injections": int(fault_injections),
                    "stall_seconds": float(stall_seconds),
                    "max_stall_request_seconds": float(max_stall_request_seconds),
                },
                "stall_observations": injection,
                "safe_mode": {
                    "active_after_faults": bool(safe_mode_payload.get("active")),
                    "source_after_faults": safe_mode_payload.get("source"),
                    "active_after_disable": bool(safe_mode_after_disable.get("active")),
                },
                "status_codes": {
                    "blocked_mutation": int(blocked.status_code),
                    "post_recovery_goal_create": int(recovered_goal.status_code),
                },
                "latency": {
                    "recovery_goal_create_ms": int(recovery_elapsed_ms),
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
                    "during_fault_quick_ok": bool((integrity_quick_during.get("integrity") or {}).get("ok")),
                    "during_fault_full_ok": bool((integrity_full_during.get("integrity") or {}).get("ok")),
                    "after_recovery_quick_ok": bool((integrity_quick_after.get("integrity") or {}).get("ok")),
                    "after_recovery_full_ok": bool((integrity_full_after.get("integrity") or {}).get("ok")),
                },
                "runtime_metrics": {
                    "io_error_count": int(io_errors),
                },
                "workflow_runs": {
                    "running_during_fault": running_during,
                    "running_after_recovery": running_after,
                },
                "recovered_goal_id": str(recovered_goal_payload.get("goal_id")),
                "paths": {
                    "run_dir": str(paths.run_dir),
                    "database_path": str(paths.database_path),
                },
                "duration_ms": int((time.perf_counter() - started) * 1000),
            }
    finally:
        if not keep_artifacts:
            shutil.rmtree(paths.run_dir, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "fsync/I/O stall drill: inject bounded write stalls followed by SQLite I/O errors, "
            "verify deterministic safe-mode degradation, integrity/no-hang behavior, and clean "
            "recovery once the fault path is removed."
        )
    )
    parser.add_argument("--workspace", default=str(PROJECT_ROOT / ".tmp" / "fsync-io-stall-drills"))
    parser.add_argument("--label", default="fsync-io-stall-drill")
    parser.add_argument("--fault-injections", type=int, default=2)
    parser.add_argument("--stall-seconds", type=float, default=0.35)
    parser.add_argument("--max-stall-request-seconds", type=float, default=3.0)
    parser.add_argument("--keep-artifacts", action="store_true")
    args = parser.parse_args(argv)

    if int(args.fault_injections) <= 0:
        print("[fsync-io-stall-drill] ERROR: --fault-injections must be > 0.", file=sys.stderr)
        return 2
    if float(args.stall_seconds) <= 0:
        print("[fsync-io-stall-drill] ERROR: --stall-seconds must be > 0.", file=sys.stderr)
        return 2
    if float(args.max_stall_request_seconds) <= 0:
        print("[fsync-io-stall-drill] ERROR: --max-stall-request-seconds must be > 0.", file=sys.stderr)
        return 2
    if float(args.max_stall_request_seconds) < float(args.stall_seconds):
        print(
            "[fsync-io-stall-drill] ERROR: --max-stall-request-seconds must be >= --stall-seconds.",
            file=sys.stderr,
        )
        return 2

    workspace_root = Path(str(args.workspace)).expanduser()
    workspace_root.mkdir(parents=True, exist_ok=True)

    try:
        report = run_drill(
            workspace_root=workspace_root,
            label=str(args.label),
            fault_injections=int(args.fault_injections),
            stall_seconds=float(args.stall_seconds),
            max_stall_request_seconds=float(args.max_stall_request_seconds),
            keep_artifacts=bool(args.keep_artifacts),
        )
    except Exception as exc:
        print(f"[fsync-io-stall-drill] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
