from __future__ import annotations

import argparse
import json
import shutil
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


def _powershell_executable() -> str | None:
    for candidate in ("pwsh", "powershell"):
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return None


def _run_manage_rings(
    *,
    manifest_path: Path,
    action: str,
    ring: str = "stable",
    version: str = "",
) -> str:
    executable = _powershell_executable()
    if executable is None:
        raise RuntimeError(
            "PowerShell executable not found; cannot run manage-desktop-rings.ps1 for rollback drill."
        )

    command = [
        executable,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(PROJECT_ROOT / "scripts" / "manage-desktop-rings.ps1"),
        "-ManifestPath",
        str(manifest_path),
        "-Action",
        action,
        "-Ring",
        ring,
    ]
    if version:
        command.extend(["-Version", version])

    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "manage-desktop-rings.ps1 failed for action "
            f"{action!r}: {completed.stderr.strip() or completed.stdout.strip()}"
        )
    return completed.stdout


def _parse_json_object(raw_text: str) -> dict[str, Any]:
    candidate = raw_text.strip()
    if not candidate:
        raise RuntimeError("Expected JSON output, but command returned empty output.")
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start < 0 or end <= start:
            raise
        payload = json.loads(candidate[start : end + 1])
    _expect(isinstance(payload, dict), "Expected JSON object output from rings command.")
    return payload


def _rings_show(manifest_path: Path) -> dict[str, Any]:
    return _parse_json_object(
        _run_manage_rings(
            manifest_path=manifest_path,
            action="show",
            ring="stable",
        )
    )


def _rings_promote(manifest_path: Path, *, version: str) -> None:
    _run_manage_rings(
        manifest_path=manifest_path,
        action="promote",
        ring="stable",
        version=version,
    )


def _rings_rollback(manifest_path: Path) -> None:
    _run_manage_rings(
        manifest_path=manifest_path,
        action="rollback",
        ring="stable",
    )


def _simulate_burst_load(client: TestClient, *, request_count: int) -> dict[str, Any]:
    status_counts: dict[str, int] = {}
    created_goal_ids: list[str] = []

    for index in range(request_count):
        response = client.post(
            "/goals",
            json={
                "title": f"Incident Drill Goal {index}",
                "description": "Synthetic burst load for rollback drill",
                "urgency": 0.7,
                "value": 0.6,
                "deadline_score": 0.2,
            },
        )
        code = str(response.status_code)
        status_counts[code] = int(status_counts.get(code, 0)) + 1
        if response.status_code == 201:
            payload = response.json()
            if isinstance(payload, dict) and payload.get("goal_id"):
                created_goal_ids.append(str(payload["goal_id"]))
        elif response.status_code != 429:
            raise RuntimeError(
                f"Unexpected status during burst load: {response.status_code} payload={response.text}"
            )

    throttled = int(status_counts.get("429", 0))
    total = sum(status_counts.values())
    throttle_rate_percent = (throttled / total) * 100.0 if total else 0.0
    return {
        "request_count": total,
        "status_counts": status_counts,
        "created_goal_count": int(status_counts.get("201", 0)),
        "throttled_count": throttled,
        "throttle_rate_percent": round(throttle_rate_percent, 3),
        "sample_goal_ids": created_goal_ids[:3],
    }


@dataclass(slots=True)
class DrillPaths:
    run_dir: Path
    rings_manifest: Path


def _create_paths(workspace_root: Path) -> DrillPaths:
    run_id = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    run_dir = workspace_root / f"incident-rollback-drill-{run_id}"
    return DrillPaths(
        run_dir=run_dir,
        rings_manifest=run_dir / "desktop-rings.json",
    )


def run_drill(
    *,
    workspace_root: Path,
    keep_artifacts: bool,
    label: str,
    load_requests: int,
    previous_version: str,
    incident_version: str,
) -> dict[str, Any]:
    started = time.perf_counter()
    paths = _create_paths(workspace_root)
    paths.run_dir.mkdir(parents=True, exist_ok=False)

    try:
        _rings_promote(paths.rings_manifest, version=previous_version)
        _rings_promote(paths.rings_manifest, version=incident_version)
        rings_before = _rings_show(paths.rings_manifest)
        stable_before = dict(rings_before.get("rings", {}).get("stable", {}) or {})
        _expect(
            stable_before.get("version") == incident_version,
            "Pre-rollback ring setup failed: stable version is not incident version.",
        )
        _expect(
            stable_before.get("rollback_version") == previous_version,
            "Pre-rollback ring setup failed: rollback version is not previous version.",
        )

        app = create_app(
            Settings(
                database_url=":memory:",
                max_pending_events=5,
                max_goal_queue_entries=10_000,
                backpressure_retry_after_seconds=2,
                slo_min_http_request_sample=20,
                slo_max_http_429_rate_percent=5.0,
                # Keep backlog utilization out of this drill to focus on HTTP pressure.
                slo_max_backlog_utilization_percent=200.0,
                slo_max_stuck_events=100,
            )
        )

        with TestClient(app) as client:
            baseline = client.get("/system/slo")
            _expect(baseline.status_code == 200, "Baseline /system/slo request failed.")
            baseline_payload = baseline.json()
            _expect(
                baseline_payload.get("status") == "ok",
                f"Expected baseline SLO status 'ok', got {baseline_payload.get('status')!r}",
            )

            load_summary = _simulate_burst_load(client, request_count=load_requests)

            incident_response = client.get("/system/slo")
            _expect(incident_response.status_code == 200, "Incident /system/slo request failed.")
            incident_payload = incident_response.json()

        incident_status = str(incident_payload.get("status") or "")
        alert_codes = {
            str(alert.get("code"))
            for alert in (incident_payload.get("alerts") or [])
            if isinstance(alert, dict)
        }
        incident_detected = incident_status in {"degraded", "critical"} and (
            "http.429_rate_high" in alert_codes
        )
        _expect(
            incident_detected,
            (
                "Incident signal not detected after burst load: "
                f"status={incident_status!r} alerts={sorted(alert_codes)} "
                f"load={json.dumps(load_summary, sort_keys=True)}"
            ),
        )

        _rings_rollback(paths.rings_manifest)
        rings_after = _rings_show(paths.rings_manifest)
        stable_after = dict(rings_after.get("rings", {}).get("stable", {}) or {})
        rollback_ok = (
            stable_after.get("version") == previous_version
            and stable_after.get("rollback_version") == incident_version
        )
        _expect(
            rollback_ok,
            (
                "Rollback validation failed: "
                f"stable_after={json.dumps(stable_after, sort_keys=True)}"
            ),
        )

        report: dict[str, Any] = {
            "label": label,
            "success": bool(incident_detected and rollback_ok),
            "incident": {
                "detected": incident_detected,
                "slo_status": incident_status,
                "alert_codes": sorted(alert_codes),
                "load": load_summary,
            },
            "rollback": {
                "ok": rollback_ok,
                "previous_version": previous_version,
                "incident_version": incident_version,
                "stable_before": stable_before,
                "stable_after": stable_after,
            },
            "paths": {
                "run_dir": str(paths.run_dir),
                "rings_manifest": str(paths.rings_manifest),
            },
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }
        return report
    finally:
        if not keep_artifacts:
            shutil.rmtree(paths.run_dir, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run a controlled incident/rollback drill: induce SLO pressure under load, "
            "then verify desktop ring rollback."
        )
    )
    parser.add_argument("--workspace", default=str(PROJECT_ROOT / ".tmp" / "incident-rollback-drills"))
    parser.add_argument("--label", default="incident-rollback-drill")
    parser.add_argument("--load-requests", type=int, default=30)
    parser.add_argument("--previous-version", default="0.0.1")
    parser.add_argument("--incident-version", default="0.0.2")
    parser.add_argument("--keep-artifacts", action="store_true")
    args = parser.parse_args()

    workspace_root = Path(str(args.workspace)).expanduser()
    workspace_root.mkdir(parents=True, exist_ok=True)
    if args.load_requests < 20:
        print(
            "[incident-rollback-drill] ERROR: --load-requests must be at least 20 "
            "(SLO sample threshold).",
            file=sys.stderr,
        )
        return 1
    if str(args.previous_version).strip() == str(args.incident_version).strip():
        print(
            "[incident-rollback-drill] ERROR: --previous-version and --incident-version must differ.",
            file=sys.stderr,
        )
        return 1

    try:
        report = run_drill(
            workspace_root=workspace_root,
            keep_artifacts=bool(args.keep_artifacts),
            label=str(args.label),
            load_requests=int(args.load_requests),
            previous_version=str(args.previous_version).strip(),
            incident_version=str(args.incident_version).strip(),
        )
    except Exception as exc:
        print(f"[incident-rollback-drill] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
