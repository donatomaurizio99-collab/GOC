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
    reason: str = "",
    expect_success: bool = True,
) -> subprocess.CompletedProcess[str]:
    executable = _powershell_executable()
    if executable is None:
        raise RuntimeError(
            "PowerShell executable not found; cannot run manage-desktop-rings.ps1 for release freeze policy."
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
    if reason:
        command.extend(["-Reason", reason])

    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    if expect_success and completed.returncode != 0:
        raise RuntimeError(
            "manage-desktop-rings.ps1 failed for action "
            f"{action!r}: {completed.stderr.strip() or completed.stdout.strip()}"
        )
    return completed


def _parse_json_object(raw_text: str) -> dict[str, Any]:
    candidate = raw_text.strip()
    _expect(bool(candidate), "Expected JSON output, but command returned empty output.")
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        start = candidate.find("{")
        end = candidate.rfind("}")
        _expect(start >= 0 and end > start, "Command output did not contain JSON object.")
        payload = json.loads(candidate[start : end + 1])
    _expect(isinstance(payload, dict), "Expected JSON object output.")
    return payload


def _rings_show(manifest_path: Path, *, ring: str) -> dict[str, Any]:
    payload = _run_manage_rings(
        manifest_path=manifest_path,
        action="show",
        ring=ring,
    )
    return _parse_json_object(payload.stdout)


def _rings_promote(manifest_path: Path, *, ring: str, version: str, expect_success: bool) -> subprocess.CompletedProcess[str]:
    return _run_manage_rings(
        manifest_path=manifest_path,
        action="promote",
        ring=ring,
        version=version,
        expect_success=expect_success,
    )


def _rings_freeze(manifest_path: Path, *, ring: str, reason: str) -> None:
    _run_manage_rings(
        manifest_path=manifest_path,
        action="freeze",
        ring=ring,
        reason=reason,
    )


def _rings_unfreeze(manifest_path: Path, *, ring: str, reason: str) -> None:
    _run_manage_rings(
        manifest_path=manifest_path,
        action="unfreeze",
        ring=ring,
        reason=reason,
    )


@dataclass(slots=True)
class DrillPaths:
    run_dir: Path
    report_file: Path


def _create_paths(workspace_root: Path) -> DrillPaths:
    run_id = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    run_dir = workspace_root / f"release-freeze-policy-{run_id}"
    return DrillPaths(
        run_dir=run_dir,
        report_file=run_dir / "release-freeze-policy-report.json",
    )


def _fetch_slo_from_url(base_url: str) -> dict[str, Any]:
    normalized = base_url.rstrip("/") + "/"
    with urlopen(urljoin(normalized, "system/slo"), timeout=5) as response:
        payload = json.loads(response.read().decode("utf-8"))
    _expect(isinstance(payload, dict), "Remote /system/slo payload is not a JSON object.")
    return payload


def _build_mock_fetcher(
    *,
    mock_statuses: list[str],
    mock_error_budget_burn_rates: list[float],
) -> Callable[[], dict[str, Any]]:
    statuses = [item.strip().lower() for item in mock_statuses if item.strip()]
    _expect(statuses, "Mock SLO statuses are empty.")
    for status in statuses:
        _expect(status in {"ok", "degraded", "critical"}, f"Invalid mock status {status!r}")

    burn_rates = [float(item) for item in mock_error_budget_burn_rates]
    if not burn_rates:
        burn_rates = [0.0 for _ in statuses]
    if len(burn_rates) < len(statuses):
        burn_rates.extend([burn_rates[-1]] * (len(statuses) - len(burn_rates)))

    index = {"value": 0}

    def _next_payload() -> dict[str, Any]:
        current_index = index["value"]
        if current_index >= len(statuses):
            status = statuses[-1]
            burn = burn_rates[-1]
        else:
            status = statuses[current_index]
            burn = burn_rates[current_index]
            index["value"] = current_index + 1

        return {
            "timestamp_utc": _utc_iso(),
            "status": status,
            "alerts": [{"code": "mock", "severity": "critical"}] if status == "critical" else [],
            "indicators": {
                "http_success_rate_percent": max(0.0, 100.0 - float(burn)),
                "http_429_rate_percent": max(0.0, float(burn) / 2.0),
                "event_failure_rate_percent": max(0.0, float(burn) / 2.0),
            },
        }

    return _next_payload


def _extract_error_budget_burn_rate_percent(payload: dict[str, Any]) -> float:
    indicators = payload.get("indicators") or {}
    success_rate = indicators.get("http_success_rate_percent")
    http_429_rate = indicators.get("http_429_rate_percent")
    event_failure_rate = indicators.get("event_failure_rate_percent")

    candidates: list[float] = []
    if isinstance(success_rate, (int, float)):
        candidates.append(max(0.0, 100.0 - float(success_rate)))
    if isinstance(http_429_rate, (int, float)):
        candidates.append(max(0.0, float(http_429_rate)))
    if isinstance(event_failure_rate, (int, float)):
        candidates.append(max(0.0, float(event_failure_rate)))
    if not candidates:
        return 0.0
    return max(candidates)


def _observe_policy(
    *,
    fetch_payload: Callable[[], dict[str, Any]],
    non_ok_window_seconds: int,
    poll_interval_seconds: float,
    max_observation_seconds: int,
    max_error_budget_burn_rate_percent: float,
) -> dict[str, Any]:
    started = time.perf_counter()
    deadline = started + max(1, int(max_observation_seconds))
    non_ok_started: float | None = None
    samples: list[dict[str, Any]] = []
    trigger_sample: dict[str, Any] | None = None
    trigger_reason = "none"

    while time.perf_counter() <= deadline:
        now = time.perf_counter()
        payload = fetch_payload()
        status = str(payload.get("status") or "").strip().lower()
        _expect(status in {"ok", "degraded", "critical"}, f"Unknown SLO status {status!r}")

        if status != "ok":
            if non_ok_started is None:
                non_ok_started = now
            non_ok_streak_seconds = now - non_ok_started
        else:
            non_ok_started = None
            non_ok_streak_seconds = 0.0

        burn_rate = _extract_error_budget_burn_rate_percent(payload)
        sample = {
            "sampled_at_utc": _utc_iso(),
            "status": status,
            "non_ok_streak_seconds": round(float(non_ok_streak_seconds), 3),
            "error_budget_burn_rate_percent": round(float(burn_rate), 4),
            "alert_count": len(payload.get("alerts") or []),
        }
        samples.append(sample)

        if status != "ok" and non_ok_streak_seconds >= float(non_ok_window_seconds):
            trigger_sample = sample
            trigger_reason = "non_ok_window"
            break
        if burn_rate > float(max_error_budget_burn_rate_percent):
            trigger_sample = sample
            trigger_reason = "error_budget_burn_rate"
            break

        time.sleep(max(0.05, float(poll_interval_seconds)))

    observed_duration_seconds = time.perf_counter() - started
    triggered = trigger_sample is not None
    return {
        "triggered": triggered,
        "trigger_reason": trigger_reason,
        "trigger_sample": trigger_sample,
        "sample_count": len(samples),
        "samples": samples,
        "observed_duration_seconds": round(float(observed_duration_seconds), 3),
    }


def run_policy(
    *,
    workspace_root: Path,
    keep_artifacts: bool,
    label: str,
    manifest_path: Path,
    ring: str,
    non_ok_window_seconds: int,
    poll_interval_seconds: float,
    max_observation_seconds: int,
    max_error_budget_burn_rate_percent: float,
    dry_run: bool,
    database_url: str,
    base_url: str,
    mock_statuses: list[str],
    mock_error_budget_burn_rates: list[float],
    seed_previous_version: str,
    seed_incident_version: str,
    promotion_test_version: str,
) -> dict[str, Any]:
    started = time.perf_counter()
    paths = _create_paths(workspace_root)
    paths.run_dir.mkdir(parents=True, exist_ok=False)

    report: dict[str, Any]
    freeze_pre_state: dict[str, Any] | None = None
    freeze_post_state: dict[str, Any] | None = None
    freeze_attempted = False
    freeze_executed = False
    freeze_error = ""
    promotion_blocked = False
    promotion_probe_output = ""

    try:
        if seed_previous_version and seed_incident_version:
            _expect(
                seed_previous_version != seed_incident_version,
                "--seed-previous-version and --seed-incident-version must differ.",
            )
            _rings_unfreeze(
                manifest_path,
                ring=ring,
                reason="Reset freeze state before release-freeze drill seeding.",
            )
            _rings_promote(manifest_path, ring=ring, version=seed_previous_version, expect_success=True)
            _rings_promote(manifest_path, ring=ring, version=seed_incident_version, expect_success=True)

        if mock_statuses:
            fetch_payload = _build_mock_fetcher(
                mock_statuses=mock_statuses,
                mock_error_budget_burn_rates=mock_error_budget_burn_rates,
            )
            source = {
                "type": "mock",
                "mock_statuses": [value.strip().lower() for value in mock_statuses if value.strip()],
                "mock_error_budget_burn_rates": [float(value) for value in mock_error_budget_burn_rates],
            }
            observation = _observe_policy(
                fetch_payload=fetch_payload,
                non_ok_window_seconds=non_ok_window_seconds,
                poll_interval_seconds=poll_interval_seconds,
                max_observation_seconds=max_observation_seconds,
                max_error_budget_burn_rate_percent=max_error_budget_burn_rate_percent,
            )
        elif base_url.strip():
            source = {"type": "base_url", "base_url": base_url.strip()}
            observation = _observe_policy(
                fetch_payload=lambda: _fetch_slo_from_url(base_url.strip()),
                non_ok_window_seconds=non_ok_window_seconds,
                poll_interval_seconds=poll_interval_seconds,
                max_observation_seconds=max_observation_seconds,
                max_error_budget_burn_rate_percent=max_error_budget_burn_rate_percent,
            )
        else:
            _expect(database_url.strip(), "Either --mock-slo-statuses, --base-url, or --database-url is required.")
            source = {"type": "database_url", "database_url": database_url.strip()}
            app = create_app(Settings(database_url=database_url.strip()))
            with TestClient(app) as client:
                observation = _observe_policy(
                    fetch_payload=lambda: client.get("/system/slo").json(),
                    non_ok_window_seconds=non_ok_window_seconds,
                    poll_interval_seconds=poll_interval_seconds,
                    max_observation_seconds=max_observation_seconds,
                    max_error_budget_burn_rate_percent=max_error_budget_burn_rate_percent,
                )

        triggered = bool(observation["triggered"])
        recommended_action = "no_action"
        freeze_reason = ""

        if triggered:
            freeze_attempted = True
            freeze_pre_state = _rings_show(manifest_path, ring=ring)
            freeze_reason = (
                "Auto release freeze: "
                f"trigger_reason={observation['trigger_reason']} "
                f"status={observation['trigger_sample']['status'] if observation['trigger_sample'] else 'unknown'} "
                f"burn_rate={observation['trigger_sample']['error_budget_burn_rate_percent'] if observation['trigger_sample'] else 'n/a'}"
            )
            if dry_run:
                recommended_action = "manual_freeze_required"
            else:
                try:
                    _rings_freeze(
                        manifest_path,
                        ring=ring,
                        reason=freeze_reason,
                    )
                    freeze_executed = True
                    freeze_post_state = _rings_show(manifest_path, ring=ring)
                    recommended_action = "release_freeze_executed"
                except Exception as exc:
                    freeze_error = str(exc)
                    recommended_action = "release_freeze_failed_escalate_immediately"
        else:
            recommended_action = "no_action"

        if freeze_executed and promotion_test_version.strip():
            promotion_probe = _rings_promote(
                manifest_path,
                ring=ring,
                version=promotion_test_version.strip(),
                expect_success=False,
            )
            combined_output = "\n".join(
                part for part in [promotion_probe.stdout.strip(), promotion_probe.stderr.strip()] if part
            )
            promotion_probe_output = combined_output
            promotion_blocked = promotion_probe.returncode != 0 and "release freeze is active" in combined_output.lower()
            _expect(
                promotion_blocked,
                (
                    "Promotion was not blocked by release freeze policy. "
                    f"probe_output={combined_output or '<empty>'}"
                ),
            )

        success = (not triggered) or dry_run or (freeze_executed and (not promotion_test_version or promotion_blocked))
        report = {
            "label": label,
            "success": success,
            "source": source,
            "policy": {
                "non_ok_window_seconds": int(non_ok_window_seconds),
                "poll_interval_seconds": float(poll_interval_seconds),
                "max_observation_seconds": int(max_observation_seconds),
                "max_error_budget_burn_rate_percent": float(max_error_budget_burn_rate_percent),
                "dry_run": bool(dry_run),
            },
            "observation": observation,
            "freeze": {
                "attempted": freeze_attempted,
                "executed": freeze_executed,
                "error": freeze_error or None,
                "reason": freeze_reason or None,
                "ring": ring,
                "manifest_path": str(manifest_path),
                "pre_state": freeze_pre_state,
                "post_state": freeze_post_state,
            },
            "promotion_block_verification": {
                "promotion_test_version": promotion_test_version or None,
                "blocked": promotion_blocked,
                "probe_output": promotion_probe_output or None,
            },
            "decision": {
                "triggered": triggered,
                "recommended_action": recommended_action,
                "runbook_path": "docs/production-runbook.md#1-release-checklist",
            },
            "paths": {
                "run_dir": str(paths.run_dir),
                "report_file": str(paths.report_file),
            },
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }

        paths.report_file.write_text(
            json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2),
            encoding="utf-8",
        )

        if not success:
            raise RuntimeError(f"Release freeze policy failed: {json.dumps(report, sort_keys=True)}")
        return report
    finally:
        if not keep_artifacts:
            shutil.rmtree(paths.run_dir, ignore_errors=True)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Operationalize release freeze policy: block ring promotion when /system/slo stays non-ok "
            "for a sustained window or error budget burn rate spikes."
        )
    )
    parser.add_argument("--workspace", default=str(PROJECT_ROOT / ".tmp" / "release-freeze-policy"))
    parser.add_argument("--label", default="release-freeze-policy")
    parser.add_argument("--manifest-path", default=str(PROJECT_ROOT / "artifacts" / "desktop-rings.json"))
    parser.add_argument("--ring", default="stable")
    parser.add_argument("--non-ok-window-seconds", type=int, default=300)
    parser.add_argument("--poll-interval-seconds", type=float, default=30.0)
    parser.add_argument("--max-observation-seconds", type=int, default=900)
    parser.add_argument("--max-error-budget-burn-rate-percent", type=float, default=2.0)
    parser.add_argument("--base-url", default="")
    parser.add_argument("--database-url", default="")
    parser.add_argument("--mock-slo-statuses", default="")
    parser.add_argument("--mock-error-budget-burn-rates", default="")
    parser.add_argument("--seed-previous-version", default="")
    parser.add_argument("--seed-incident-version", default="")
    parser.add_argument("--promotion-test-version", default="0.0.3")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--keep-artifacts", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if int(args.non_ok_window_seconds) <= 0:
        print("[release-freeze-policy] ERROR: --non-ok-window-seconds must be > 0.", file=sys.stderr)
        return 2
    if float(args.poll_interval_seconds) <= 0:
        print("[release-freeze-policy] ERROR: --poll-interval-seconds must be > 0.", file=sys.stderr)
        return 2
    if int(args.max_observation_seconds) <= 0:
        print("[release-freeze-policy] ERROR: --max-observation-seconds must be > 0.", file=sys.stderr)
        return 2
    if float(args.max_error_budget_burn_rate_percent) < 0:
        print(
            "[release-freeze-policy] ERROR: --max-error-budget-burn-rate-percent must be >= 0.",
            file=sys.stderr,
        )
        return 2
    if args.base_url.strip() and args.database_url.strip():
        print("[release-freeze-policy] ERROR: Specify either --base-url or --database-url, not both.", file=sys.stderr)
        return 2

    mock_statuses = [item.strip() for item in str(args.mock_slo_statuses).split(",") if item.strip()]
    mock_burn_rates = [
        float(item.strip())
        for item in str(args.mock_error_budget_burn_rates).split(",")
        if item.strip()
    ]

    try:
        report = run_policy(
            workspace_root=Path(args.workspace).expanduser(),
            keep_artifacts=bool(args.keep_artifacts),
            label=str(args.label),
            manifest_path=Path(args.manifest_path).expanduser(),
            ring=str(args.ring),
            non_ok_window_seconds=int(args.non_ok_window_seconds),
            poll_interval_seconds=float(args.poll_interval_seconds),
            max_observation_seconds=int(args.max_observation_seconds),
            max_error_budget_burn_rate_percent=float(args.max_error_budget_burn_rate_percent),
            dry_run=bool(args.dry_run),
            database_url=str(args.database_url),
            base_url=str(args.base_url),
            mock_statuses=mock_statuses,
            mock_error_budget_burn_rates=mock_burn_rates,
            seed_previous_version=str(args.seed_previous_version),
            seed_incident_version=str(args.seed_incident_version),
            promotion_test_version=str(args.promotion_test_version),
        )
    except Exception as exc:
        print(f"[release-freeze-policy] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
