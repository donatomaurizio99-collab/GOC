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


def _faulty_goal_payload(*, title: str) -> dict[str, Any]:
    return {
        "title": title,
        "description": "Disk pressure/fault injection drill",
        "urgency": 0.6,
        "value": 0.7,
        "deadline_score": 0.3,
    }


def _inject_goal_fault(
    *,
    client: TestClient,
    error_message: str,
    fault_injections: int,
) -> dict[str, int]:
    services = client.app.state.services
    original_create_goal = services.state_manager.create_goal
    state = {"remaining": max(1, int(fault_injections)), "triggered": 0}

    def _faulty_create_goal(
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
            raise sqlite3.OperationalError(error_message)
        return original_create_goal(
            title=title,
            description=description,
            urgency=urgency,
            value=value,
            deadline_score=deadline_score,
        )

    services.state_manager.create_goal = _faulty_create_goal
    try:
        for index in range(max(1, int(fault_injections))):
            response = client.post(
                "/goals",
                json=_faulty_goal_payload(title=f"Fault Injection Goal {index}"),
            )
            _expect(
                response.status_code == 500,
                (
                    "Fault injection request did not produce expected 500 response. "
                    f"status={response.status_code} body={_json_or_text(response.text)}"
                ),
            )
    finally:
        services.state_manager.create_goal = original_create_goal

    return {"triggered": int(state["triggered"]), "remaining": int(state["remaining"])}


def _run_case(
    *,
    case_name: str,
    error_message: str,
    fault_injections: int,
    case_db_path: Path,
) -> dict[str, Any]:
    app = create_app(
        Settings(
            database_url=str(case_db_path),
            safe_mode_lock_error_threshold=6,
            safe_mode_lock_error_window_seconds=60,
            safe_mode_io_error_threshold=max(1, int(fault_injections)),
            safe_mode_io_error_window_seconds=60,
            workflow_worker_poll_interval_seconds=0.05,
            workflow_startup_recovery_max_age_seconds=0,
        )
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        readiness_before = client.get("/system/readiness")
        slo_before = client.get("/system/slo")
        _expect(readiness_before.status_code == 200, "Initial readiness probe failed.")
        _expect(slo_before.status_code == 200, "Initial SLO probe failed.")
        _expect(readiness_before.json().get("ready") is True, f"Initial readiness not ready: {readiness_before.json()}")
        _expect(str(slo_before.json().get("status")) == "ok", f"Initial SLO not ok: {slo_before.json()}")

        injection_state = _inject_goal_fault(
            client=client,
            error_message=error_message,
            fault_injections=int(fault_injections),
        )
        _expect(
            int(injection_state["triggered"]) == int(fault_injections),
            f"Fault injection count mismatch for {case_name}: {injection_state}",
        )

        safe_mode = client.get("/system/safe-mode")
        _expect(safe_mode.status_code == 200, f"Safe mode endpoint failed for {case_name}.")
        safe_mode_payload = safe_mode.json()
        _expect(
            safe_mode_payload.get("active") is True,
            f"Safe mode not active after fault injection ({case_name}): {safe_mode_payload}",
        )

        blocked = client.post(
            "/goals",
            json=_faulty_goal_payload(title=f"Blocked Goal ({case_name})"),
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
            f"SLO status should be critical while safe mode is active: {slo_during_payload}",
        )

        integrity_quick_during = _integrity_probe(client, mode="quick")
        integrity_full_during = _integrity_probe(client, mode="full")
        running_during = _running_run_ids(client)
        _expect(
            not running_during,
            f"Found running workflow runs during fault scenario ({case_name}): {running_during}",
        )

        disable = client.post(
            "/system/safe-mode/disable",
            json={"reason": f"Fault injection resolved ({case_name})"},
        )
        _expect(
            disable.status_code == 200,
            f"Safe mode disable failed for {case_name}: status={disable.status_code} body={_json_or_text(disable.text)}",
        )
        safe_mode_after_disable = client.get("/system/safe-mode").json()
        _expect(
            safe_mode_after_disable.get("active") is False,
            f"Safe mode remained active after disable for {case_name}: {safe_mode_after_disable}",
        )

        recovered_goal = client.post(
            "/goals",
            json=_faulty_goal_payload(title=f"Recovered Goal ({case_name})"),
        )
        _expect(
            recovered_goal.status_code == 201,
            (
                "Recovery goal create failed after disabling safe mode. "
                f"status={recovered_goal.status_code} body={_json_or_text(recovered_goal.text)}"
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
            f"Readiness did not recover after fault scenario ({case_name}): {readiness_after_payload}",
        )
        _expect(
            str(slo_after_payload.get("status")) == "ok",
            f"SLO did not recover to ok after fault scenario ({case_name}): {slo_after_payload}",
        )

        integrity_quick_after = _integrity_probe(client, mode="quick")
        integrity_full_after = _integrity_probe(client, mode="full")
        running_after = _running_run_ids(client)
        _expect(
            not running_after,
            f"Found running workflow runs after recovery ({case_name}): {running_after}",
        )

        return {
            "name": case_name,
            "success": True,
            "fault_message": error_message,
            "fault_injections": int(fault_injections),
            "safe_mode": {
                "active_after_faults": bool(safe_mode_payload.get("active")),
                "source_after_faults": safe_mode_payload.get("source"),
                "active_after_disable": bool(safe_mode_after_disable.get("active")),
            },
            "status_codes": {
                "blocked_mutation": int(blocked.status_code),
                "post_recovery_goal_create": int(recovered_goal.status_code),
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
            "workflow_runs": {
                "running_during_fault": running_during,
                "running_after_recovery": running_after,
            },
            "recovered_goal_id": str(recovered_goal_payload.get("goal_id")),
            "database_path": str(case_db_path),
        }


@dataclass(slots=True)
class DrillPaths:
    run_dir: Path
    cases_root: Path


def _create_paths(workspace_root: Path) -> DrillPaths:
    run_id = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    run_dir = workspace_root / f"disk-pressure-fault-injection-{run_id}"
    return DrillPaths(run_dir=run_dir, cases_root=run_dir / "cases")


def run_drill(
    *,
    workspace_root: Path,
    label: str,
    fault_injections: int,
    keep_artifacts: bool,
) -> dict[str, Any]:
    started = time.perf_counter()
    paths = _create_paths(workspace_root)
    paths.cases_root.mkdir(parents=True, exist_ok=False)

    cases = (
        ("sqlite_full", "database or disk is full"),
        ("sqlite_ioerr", "disk i/o error"),
        ("readonly_permission_flip", "attempt to write a readonly database"),
    )

    try:
        reports: list[dict[str, Any]] = []
        for case_name, message in cases:
            case_path = paths.cases_root / case_name
            case_path.mkdir(parents=True, exist_ok=False)
            report = _run_case(
                case_name=case_name,
                error_message=message,
                fault_injections=int(fault_injections),
                case_db_path=case_path / "drill.db",
            )
            reports.append(report)

        return {
            "label": label,
            "success": True,
            "fault_injections_per_case": int(fault_injections),
            "cases": reports,
            "paths": {
                "run_dir": str(paths.run_dir),
                "cases_root": str(paths.cases_root),
            },
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }
    finally:
        if not keep_artifacts:
            shutil.rmtree(paths.run_dir, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Disk-pressure/fault-injection drill for SQLite: simulate SQLITE_FULL, IOERR, and "
            "readonly permission-flip signatures; verify deterministic safe-mode degradation, "
            "no integrity/running-run regressions, and clean recovery."
        )
    )
    parser.add_argument("--workspace", default=str(PROJECT_ROOT / ".tmp" / "disk-pressure-fault-injection-drills"))
    parser.add_argument("--label", default="disk-pressure-fault-injection-drill")
    parser.add_argument("--fault-injections", type=int, default=2)
    parser.add_argument("--keep-artifacts", action="store_true")
    args = parser.parse_args(argv)

    if int(args.fault_injections) <= 0:
        print("[disk-pressure-fault-injection-drill] ERROR: --fault-injections must be > 0.", file=sys.stderr)
        return 2

    workspace_root = Path(str(args.workspace)).expanduser()
    workspace_root.mkdir(parents=True, exist_ok=True)

    try:
        report = run_drill(
            workspace_root=workspace_root,
            label=str(args.label),
            fault_injections=int(args.fault_injections),
            keep_artifacts=bool(args.keep_artifacts),
        )
    except Exception as exc:
        print(f"[disk-pressure-fault-injection-drill] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
