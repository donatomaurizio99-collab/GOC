from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin
from urllib.request import urlopen

from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from goal_ops_console.config import Settings
from goal_ops_console.main import create_app


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _utc_iso() -> str:
    return datetime.now(UTC).isoformat()


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
            "PowerShell executable not found; cannot run manage-desktop-rings.ps1 for auto rollback policy."
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
    _expect(isinstance(payload, dict), "Expected JSON object output.")
    return payload


def _rings_show(manifest_path: Path, *, ring: str) -> dict[str, Any]:
    payload = _parse_json_object(
        _run_manage_rings(
            manifest_path=manifest_path,
            action="show",
            ring=ring,
        )
    )
    return payload


def _rings_promote(manifest_path: Path, *, ring: str, version: str) -> None:
    _run_manage_rings(
        manifest_path=manifest_path,
        action="promote",
        ring=ring,
        version=version,
    )


def _rings_rollback(manifest_path: Path, *, ring: str) -> None:
    _run_manage_rings(
        manifest_path=manifest_path,
        action="rollback",
        ring=ring,
    )


@dataclass(slots=True)
class DrillPaths:
    run_dir: Path
    report_file: Path


def _create_paths(workspace_root: Path) -> DrillPaths:
    run_id = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    run_dir = workspace_root / f"auto-rollback-policy-{run_id}"
    return DrillPaths(
        run_dir=run_dir,
        report_file=run_dir / "auto-rollback-policy-report.json",
    )


def _fetch_slo_from_url(base_url: str) -> dict[str, Any]:
    normalized = base_url.rstrip("/") + "/"
    with urlopen(urljoin(normalized, "system/slo"), timeout=5) as response:
        payload = json.loads(response.read().decode("utf-8"))
    _expect(isinstance(payload, dict), "Remote /system/slo payload is not a JSON object.")
    return payload


def _build_mock_fetcher(mock_statuses: list[str]) -> Callable[[], dict[str, Any]]:
    values = [item.strip().lower() for item in mock_statuses if item.strip()]
    _expect(values, "Mock SLO statuses are empty.")
    for status in values:
        _expect(status in {"ok", "degraded", "critical"}, f"Invalid mock status {status!r}")

    index = {"value": 0}

    def _next_payload() -> dict[str, Any]:
        current_index = index["value"]
        if current_index >= len(values):
            status = values[-1]
        else:
            status = values[current_index]
            index["value"] = current_index + 1
        return {
            "timestamp_utc": _utc_iso(),
            "status": status,
            "alerts": [{"code": "mock", "severity": "critical"}] if status == "critical" else [],
        }

    return _next_payload


def _observe_slo(
    *,
    fetch_payload: Callable[[], dict[str, Any]],
    critical_window_seconds: int,
    poll_interval_seconds: float,
    max_observation_seconds: int,
) -> dict[str, Any]:
    started = time.perf_counter()
    deadline = started + max(1, int(max_observation_seconds))
    critical_started: float | None = None
    samples: list[dict[str, Any]] = []
    trigger_sample: dict[str, Any] | None = None

    while time.perf_counter() <= deadline:
        now = time.perf_counter()
        payload = fetch_payload()
        status = str(payload.get("status") or "").strip().lower()
        _expect(status in {"ok", "degraded", "critical"}, f"Unknown SLO status {status!r}")

        if status == "critical":
            if critical_started is None:
                critical_started = now
            critical_streak_seconds = now - critical_started
        else:
            critical_started = None
            critical_streak_seconds = 0.0

        sample = {
            "sampled_at_utc": _utc_iso(),
            "status": status,
            "critical_streak_seconds": round(float(critical_streak_seconds), 3),
            "alert_count": len(payload.get("alerts") or []),
        }
        samples.append(sample)

        if status == "critical" and critical_streak_seconds >= float(critical_window_seconds):
            trigger_sample = sample
            break

        time.sleep(max(0.05, float(poll_interval_seconds)))

    observed_duration_seconds = time.perf_counter() - started
    triggered = trigger_sample is not None
    return {
        "triggered": triggered,
        "trigger_sample": trigger_sample,
        "sample_count": len(samples),
        "samples": samples,
        "observed_duration_seconds": round(float(observed_duration_seconds), 3),
    }


def _checklist(
    *,
    triggered: bool,
    rollback_attempted: bool,
    rollback_executed: bool,
    report_file: Path,
) -> list[dict[str, Any]]:
    return [
        {
            "step": "Confirm sustained critical SLO window breach.",
            "done": triggered,
        },
        {
            "step": "Capture pre-rollback ring state.",
            "done": rollback_attempted or triggered,
        },
        {
            "step": "Execute stable ring rollback according to runbook.",
            "done": rollback_executed,
        },
        {
            "step": "Capture post-rollback ring state and verify swap.",
            "done": rollback_executed,
        },
        {
            "step": "Store policy decision + incident checklist artifact.",
            "done": report_file.exists(),
        },
    ]


def run_policy(
    *,
    workspace_root: Path,
    keep_artifacts: bool,
    label: str,
    manifest_path: Path,
    ring: str,
    critical_window_seconds: int,
    poll_interval_seconds: float,
    max_observation_seconds: int,
    dry_run: bool,
    database_url: str,
    base_url: str,
    mock_statuses: list[str],
    seed_previous_version: str,
    seed_incident_version: str,
) -> dict[str, Any]:
    started = time.perf_counter()
    paths = _create_paths(workspace_root)
    paths.run_dir.mkdir(parents=True, exist_ok=False)

    report: dict[str, Any]
    rollback_pre_state: dict[str, Any] | None = None
    rollback_post_state: dict[str, Any] | None = None
    rollback_attempted = False
    rollback_executed = False
    rollback_error = ""

    try:
        if seed_previous_version and seed_incident_version:
            _expect(
                seed_previous_version != seed_incident_version,
                "--seed-previous-version and --seed-incident-version must differ.",
            )
            _rings_promote(manifest_path, ring=ring, version=seed_previous_version)
            _rings_promote(manifest_path, ring=ring, version=seed_incident_version)

        if mock_statuses:
            fetch_payload = _build_mock_fetcher(mock_statuses)
            source = {
                "type": "mock",
                "mock_statuses": [value.strip().lower() for value in mock_statuses if value.strip()],
            }
            observation = _observe_slo(
                fetch_payload=fetch_payload,
                critical_window_seconds=critical_window_seconds,
                poll_interval_seconds=poll_interval_seconds,
                max_observation_seconds=max_observation_seconds,
            )
        elif base_url.strip():
            source = {"type": "base_url", "base_url": base_url.strip()}
            observation = _observe_slo(
                fetch_payload=lambda: _fetch_slo_from_url(base_url.strip()),
                critical_window_seconds=critical_window_seconds,
                poll_interval_seconds=poll_interval_seconds,
                max_observation_seconds=max_observation_seconds,
            )
        else:
            _expect(database_url.strip(), "Either --mock-slo-statuses, --base-url, or --database-url is required.")
            source = {"type": "database_url", "database_url": database_url.strip()}
            app = create_app(Settings(database_url=database_url.strip()))
            with TestClient(app) as client:
                observation = _observe_slo(
                    fetch_payload=lambda: client.get("/system/slo").json(),
                    critical_window_seconds=critical_window_seconds,
                    poll_interval_seconds=poll_interval_seconds,
                    max_observation_seconds=max_observation_seconds,
                )

        triggered = bool(observation["triggered"])
        recommended_action = "no_action"

        if triggered:
            rollback_attempted = True
            rollback_pre_state = _rings_show(manifest_path, ring=ring)
            if dry_run:
                recommended_action = "manual_rollback_required"
            else:
                try:
                    _rings_rollback(manifest_path, ring=ring)
                    rollback_executed = True
                    rollback_post_state = _rings_show(manifest_path, ring=ring)
                    recommended_action = "rollback_executed"
                except Exception as exc:
                    rollback_error = str(exc)
                    recommended_action = "rollback_failed_escalate_immediately"
        else:
            recommended_action = "no_action"

        success = (not triggered) or dry_run or rollback_executed
        report = {
            "label": label,
            "success": success,
            "source": source,
            "policy": {
                "critical_window_seconds": int(critical_window_seconds),
                "poll_interval_seconds": float(poll_interval_seconds),
                "max_observation_seconds": int(max_observation_seconds),
                "dry_run": bool(dry_run),
            },
            "observation": observation,
            "rollback": {
                "attempted": rollback_attempted,
                "executed": rollback_executed,
                "error": rollback_error or None,
                "ring": ring,
                "manifest_path": str(manifest_path),
                "pre_state": rollback_pre_state,
                "post_state": rollback_post_state,
            },
            "decision": {
                "triggered": triggered,
                "recommended_action": recommended_action,
                "runbook_path": "docs/production-runbook.md#2-rollback",
            },
            "paths": {
                "run_dir": str(paths.run_dir),
                "report_file": str(paths.report_file),
            },
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }

        report["checklist"] = []
        paths.report_file.write_text(
            json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2),
            encoding="utf-8",
        )
        report["checklist"] = _checklist(
            triggered=triggered,
            rollback_attempted=rollback_attempted,
            rollback_executed=rollback_executed,
            report_file=paths.report_file,
        )
        paths.report_file.write_text(
            json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2),
            encoding="utf-8",
        )

        if not success:
            raise RuntimeError(f"Auto rollback policy failed: {json.dumps(report, sort_keys=True)}")
        return report
    finally:
        if not keep_artifacts:
            shutil.rmtree(paths.run_dir, ignore_errors=True)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Operationalize auto rollback policy: if /system/slo stays critical for a sustained window, "
            "execute ring rollback and emit incident checklist artifact."
        )
    )
    parser.add_argument("--workspace", default=str(PROJECT_ROOT / ".tmp" / "auto-rollback-policy"))
    parser.add_argument("--label", default="auto-rollback-policy")
    parser.add_argument("--manifest-path", default=str(PROJECT_ROOT / "artifacts" / "desktop-rings.json"))
    parser.add_argument("--ring", default="stable")
    parser.add_argument("--critical-window-seconds", type=int, default=300)
    parser.add_argument("--poll-interval-seconds", type=float, default=30.0)
    parser.add_argument("--max-observation-seconds", type=int, default=900)
    parser.add_argument("--database-url", default="")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--mock-slo-statuses", default="")
    parser.add_argument("--seed-previous-version", default="")
    parser.add_argument("--seed-incident-version", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--keep-artifacts", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    workspace_root = Path(str(args.workspace)).expanduser()
    workspace_root.mkdir(parents=True, exist_ok=True)
    manifest_path = Path(str(args.manifest_path)).expanduser()
    mock_statuses = [part.strip() for part in str(args.mock_slo_statuses).split(",") if part.strip()]

    source_count = int(bool(mock_statuses)) + int(bool(str(args.base_url).strip())) + int(
        bool(str(args.database_url).strip())
    )
    if source_count != 1:
        print(
            "[auto-rollback-policy] ERROR: Provide exactly one source: --mock-slo-statuses OR --base-url OR --database-url.",
            file=sys.stderr,
        )
        return 2

    if int(args.critical_window_seconds) <= 0:
        print("[auto-rollback-policy] ERROR: --critical-window-seconds must be positive.", file=sys.stderr)
        return 2
    if float(args.poll_interval_seconds) <= 0:
        print("[auto-rollback-policy] ERROR: --poll-interval-seconds must be positive.", file=sys.stderr)
        return 2
    if int(args.max_observation_seconds) <= 0:
        print("[auto-rollback-policy] ERROR: --max-observation-seconds must be positive.", file=sys.stderr)
        return 2

    try:
        report = run_policy(
            workspace_root=workspace_root,
            keep_artifacts=bool(args.keep_artifacts),
            label=str(args.label),
            manifest_path=manifest_path,
            ring=str(args.ring).strip() or "stable",
            critical_window_seconds=int(args.critical_window_seconds),
            poll_interval_seconds=float(args.poll_interval_seconds),
            max_observation_seconds=int(args.max_observation_seconds),
            dry_run=bool(args.dry_run),
            database_url=str(args.database_url).strip(),
            base_url=str(args.base_url).strip(),
            mock_statuses=mock_statuses,
            seed_previous_version=str(args.seed_previous_version).strip(),
            seed_incident_version=str(args.seed_incident_version).strip(),
        )
    except Exception as exc:
        print(f"[auto-rollback-policy] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
