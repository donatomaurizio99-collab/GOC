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

ALLOWED_TRIGGER_REASONS = {"auto", "critical_window", "error_budget_burn_rate", "readiness_regression"}


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


def _fetch_readiness_from_url(base_url: str) -> dict[str, Any]:
    normalized = base_url.rstrip("/") + "/"
    with urlopen(urljoin(normalized, "system/readiness"), timeout=5) as response:
        payload = json.loads(response.read().decode("utf-8"))
    _expect(isinstance(payload, dict), "Remote /system/readiness payload is not a JSON object.")
    return payload


def _extract_error_budget_burn_rate_percent(slo_payload: dict[str, Any]) -> float:
    indicators = slo_payload.get("indicators") or {}
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


def _extract_readiness_ready(readiness_payload: dict[str, Any]) -> bool:
    if isinstance(readiness_payload.get("ready"), bool):
        return bool(readiness_payload["ready"])

    checks = readiness_payload.get("checks")
    if isinstance(checks, dict):
        values: list[bool] = []
        for item in checks.values():
            if isinstance(item, dict) and isinstance(item.get("ok"), bool):
                values.append(bool(item["ok"]))
        if values:
            return all(values)

    return True


def _parse_mock_readiness_tokens(
    *,
    tokens: list[str],
    required_size: int,
) -> list[bool]:
    if not tokens:
        return [True for _ in range(required_size)]

    parsed: list[bool] = []
    for raw in tokens:
        normalized = raw.strip().lower()
        if normalized in {"1", "true", "t", "yes", "y", "ok", "ready"}:
            parsed.append(True)
            continue
        if normalized in {"0", "false", "f", "no", "n", "not_ready", "degraded", "critical"}:
            parsed.append(False)
            continue
        raise RuntimeError(f"Invalid mock readiness value {raw!r}.")

    if len(parsed) < required_size:
        parsed.extend([parsed[-1]] * (required_size - len(parsed)))
    return parsed


def _build_mock_fetcher(
    *,
    mock_statuses: list[str],
    mock_error_budget_burn_rates: list[float],
    mock_readiness_values: list[str],
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

    readiness_values = _parse_mock_readiness_tokens(
        tokens=[item.strip() for item in mock_readiness_values if item.strip()],
        required_size=len(statuses),
    )

    index = {"value": 0}

    def _next_payload() -> dict[str, Any]:
        current_index = index["value"]
        if current_index >= len(statuses):
            status = statuses[-1]
            burn_rate = burn_rates[-1]
            readiness_ready = readiness_values[-1]
        else:
            status = statuses[current_index]
            burn_rate = burn_rates[current_index]
            readiness_ready = readiness_values[current_index]
            index["value"] = current_index + 1

        return {
            "slo": {
                "timestamp_utc": _utc_iso(),
                "status": status,
                "alerts": [{"code": "mock", "severity": "critical"}] if status == "critical" else [],
                "indicators": {
                    "http_success_rate_percent": max(0.0, 100.0 - float(burn_rate)),
                    "http_429_rate_percent": max(0.0, float(burn_rate) / 2.0),
                    "event_failure_rate_percent": max(0.0, float(burn_rate) / 2.0),
                },
            },
            "readiness": {
                "ready": bool(readiness_ready),
                "checks": {"mock_guard": {"ok": bool(readiness_ready)}},
            },
        }

    return _next_payload


def _observe_policy(
    *,
    fetch_payload: Callable[[], dict[str, Any]],
    critical_window_seconds: int,
    readiness_regression_window_seconds: int,
    poll_interval_seconds: float,
    max_observation_seconds: int,
    max_error_budget_burn_rate_percent: float,
) -> dict[str, Any]:
    started = time.perf_counter()
    deadline = started + max(1, int(max_observation_seconds))
    critical_started: float | None = None
    readiness_regression_started: float | None = None
    samples: list[dict[str, Any]] = []
    trigger_sample: dict[str, Any] | None = None
    trigger_reason = "none"

    while time.perf_counter() <= deadline:
        now = time.perf_counter()
        payload = fetch_payload()
        _expect(isinstance(payload, dict), "Signal fetcher returned non-object payload.")

        slo_payload = payload.get("slo")
        readiness_payload = payload.get("readiness")
        _expect(isinstance(slo_payload, dict), "Signal payload missing 'slo' JSON object.")
        _expect(isinstance(readiness_payload, dict), "Signal payload missing 'readiness' JSON object.")

        status = str(slo_payload.get("status") or "").strip().lower()
        _expect(status in {"ok", "degraded", "critical"}, f"Unknown SLO status {status!r}")

        if status == "critical":
            if critical_started is None:
                critical_started = now
            critical_streak_seconds = now - critical_started
        else:
            critical_started = None
            critical_streak_seconds = 0.0

        readiness_ready = _extract_readiness_ready(readiness_payload)
        if not readiness_ready:
            if readiness_regression_started is None:
                readiness_regression_started = now
            readiness_regression_streak_seconds = now - readiness_regression_started
        else:
            readiness_regression_started = None
            readiness_regression_streak_seconds = 0.0

        burn_rate = _extract_error_budget_burn_rate_percent(slo_payload)

        sample = {
            "sampled_at_utc": _utc_iso(),
            "status": status,
            "critical_streak_seconds": round(float(critical_streak_seconds), 3),
            "error_budget_burn_rate_percent": round(float(burn_rate), 4),
            "readiness_ready": bool(readiness_ready),
            "readiness_regression_streak_seconds": round(float(readiness_regression_streak_seconds), 3),
            "alert_count": len(slo_payload.get("alerts") or []),
        }
        samples.append(sample)

        # Hard trigger precedence: readiness regression > error-budget spike > sustained critical.
        if (not readiness_ready) and readiness_regression_streak_seconds >= float(readiness_regression_window_seconds):
            trigger_sample = sample
            trigger_reason = "readiness_regression"
            break
        if burn_rate > float(max_error_budget_burn_rate_percent):
            trigger_sample = sample
            trigger_reason = "error_budget_burn_rate"
            break
        if status == "critical" and critical_streak_seconds >= float(critical_window_seconds):
            trigger_sample = sample
            trigger_reason = "critical_window"
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


def _checklist(
    *,
    triggered: bool,
    trigger_reason: str,
    expected_trigger_reason: str,
    expected_reason_matched: bool,
    rollback_attempted: bool,
    rollback_executed: bool,
    report_file: Path,
) -> list[dict[str, Any]]:
    return [
        {
            "step": "Confirm hard-trigger breach (critical-window, burn-rate, or readiness regression).",
            "done": triggered,
        },
        {
            "step": "Validate trigger reason against expected policy path.",
            "done": bool(expected_reason_matched) if expected_trigger_reason != "auto" else True,
            "details": {
                "observed_trigger_reason": trigger_reason,
                "expected_trigger_reason": expected_trigger_reason,
            },
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
    readiness_regression_window_seconds: int,
    poll_interval_seconds: float,
    max_observation_seconds: int,
    max_error_budget_burn_rate_percent: float,
    dry_run: bool,
    database_url: str,
    base_url: str,
    mock_statuses: list[str],
    mock_error_budget_burn_rates: list[float],
    mock_readiness_values: list[str],
    seed_previous_version: str,
    seed_incident_version: str,
    expected_trigger_reason: str,
    output_file: Path | None,
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

    expected_trigger_reason = str(expected_trigger_reason).strip().lower() or "auto"
    _expect(
        expected_trigger_reason in ALLOWED_TRIGGER_REASONS,
        f"Invalid --expected-trigger-reason={expected_trigger_reason!r}.",
    )

    try:
        if seed_previous_version and seed_incident_version:
            _expect(
                seed_previous_version != seed_incident_version,
                "--seed-previous-version and --seed-incident-version must differ.",
            )
            _rings_promote(manifest_path, ring=ring, version=seed_previous_version)
            _rings_promote(manifest_path, ring=ring, version=seed_incident_version)

        if mock_statuses:
            fetch_payload = _build_mock_fetcher(
                mock_statuses=mock_statuses,
                mock_error_budget_burn_rates=mock_error_budget_burn_rates,
                mock_readiness_values=mock_readiness_values,
            )
            source = {
                "type": "mock",
                "mock_statuses": [value.strip().lower() for value in mock_statuses if value.strip()],
                "mock_error_budget_burn_rates": [float(value) for value in mock_error_budget_burn_rates],
                "mock_readiness_values": [value.strip().lower() for value in mock_readiness_values if value.strip()],
            }
            observation = _observe_policy(
                fetch_payload=fetch_payload,
                critical_window_seconds=critical_window_seconds,
                readiness_regression_window_seconds=readiness_regression_window_seconds,
                poll_interval_seconds=poll_interval_seconds,
                max_observation_seconds=max_observation_seconds,
                max_error_budget_burn_rate_percent=max_error_budget_burn_rate_percent,
            )
        elif base_url.strip():
            source = {"type": "base_url", "base_url": base_url.strip()}
            observation = _observe_policy(
                fetch_payload=lambda: {
                    "slo": _fetch_slo_from_url(base_url.strip()),
                    "readiness": _fetch_readiness_from_url(base_url.strip()),
                },
                critical_window_seconds=critical_window_seconds,
                readiness_regression_window_seconds=readiness_regression_window_seconds,
                poll_interval_seconds=poll_interval_seconds,
                max_observation_seconds=max_observation_seconds,
                max_error_budget_burn_rate_percent=max_error_budget_burn_rate_percent,
            )
        else:
            _expect(
                database_url.strip(),
                "Either --mock-slo-statuses, --base-url, or --database-url is required.",
            )
            source = {"type": "database_url", "database_url": database_url.strip()}
            app = create_app(Settings(database_url=database_url.strip()))
            with TestClient(app) as client:
                observation = _observe_policy(
                    fetch_payload=lambda: {
                        "slo": client.get("/system/slo").json(),
                        "readiness": client.get("/system/readiness").json(),
                    },
                    critical_window_seconds=critical_window_seconds,
                    readiness_regression_window_seconds=readiness_regression_window_seconds,
                    poll_interval_seconds=poll_interval_seconds,
                    max_observation_seconds=max_observation_seconds,
                    max_error_budget_burn_rate_percent=max_error_budget_burn_rate_percent,
                )

        triggered = bool(observation["triggered"])
        observed_trigger_reason = str(observation.get("trigger_reason") or "none")
        expected_reason_matched = expected_trigger_reason == "auto" or (
            triggered and observed_trigger_reason == expected_trigger_reason
        )

        if expected_trigger_reason != "auto" and not expected_reason_matched:
            rollback_attempted = False
            rollback_executed = False
            rollback_error = (
                f"Observed trigger_reason={observed_trigger_reason!r}, expected={expected_trigger_reason!r}."
            )
            recommended_action = "unexpected_trigger_reason"
        elif triggered:
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

        success = ((not triggered) or dry_run or rollback_executed) and expected_reason_matched
        report = {
            "label": label,
            "success": success,
            "source": source,
            "policy": {
                "critical_window_seconds": int(critical_window_seconds),
                "readiness_regression_window_seconds": int(readiness_regression_window_seconds),
                "poll_interval_seconds": float(poll_interval_seconds),
                "max_observation_seconds": int(max_observation_seconds),
                "max_error_budget_burn_rate_percent": float(max_error_budget_burn_rate_percent),
                "expected_trigger_reason": expected_trigger_reason,
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
                "observed_trigger_reason": observed_trigger_reason,
                "expected_trigger_reason": expected_trigger_reason,
                "expected_reason_matched": expected_reason_matched,
                "recommended_action": recommended_action,
                "runbook_path": "docs/production-runbook.md#337-auto-rollback-hard-triggers",
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
            trigger_reason=observed_trigger_reason,
            expected_trigger_reason=expected_trigger_reason,
            expected_reason_matched=expected_reason_matched,
            rollback_attempted=rollback_attempted,
            rollback_executed=rollback_executed,
            report_file=paths.report_file,
        )
        paths.report_file.write_text(
            json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2),
            encoding="utf-8",
        )

        if output_file is not None:
            output_file.parent.mkdir(parents=True, exist_ok=True)
            output_file.write_text(
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
            "Operationalize hard auto-rollback policy: trigger stable ring rollback for sustained critical SLO, "
            "error-budget burn-rate spikes, or sustained readiness regressions."
        )
    )
    parser.add_argument("--workspace", default=str(PROJECT_ROOT / ".tmp" / "auto-rollback-policy"))
    parser.add_argument("--label", default="auto-rollback-policy")
    parser.add_argument("--manifest-path", default=str(PROJECT_ROOT / "artifacts" / "desktop-rings.json"))
    parser.add_argument("--ring", default="stable")
    parser.add_argument("--critical-window-seconds", type=int, default=300)
    parser.add_argument("--readiness-regression-window-seconds", type=int, default=120)
    parser.add_argument("--poll-interval-seconds", type=float, default=30.0)
    parser.add_argument("--max-observation-seconds", type=int, default=900)
    parser.add_argument("--max-error-budget-burn-rate-percent", type=float, default=2.0)
    parser.add_argument("--database-url", default="")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--mock-slo-statuses", default="")
    parser.add_argument("--mock-error-budget-burn-rates", default="")
    parser.add_argument("--mock-readiness-values", default="")
    parser.add_argument("--seed-previous-version", default="")
    parser.add_argument("--seed-incident-version", default="")
    parser.add_argument("--expected-trigger-reason", default="auto")
    parser.add_argument("--output-file")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--keep-artifacts", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    workspace_root = Path(str(args.workspace)).expanduser()
    workspace_root.mkdir(parents=True, exist_ok=True)
    manifest_path = Path(str(args.manifest_path)).expanduser()
    output_file = Path(str(args.output_file)).expanduser() if args.output_file else None
    mock_statuses = [part.strip() for part in str(args.mock_slo_statuses).split(",") if part.strip()]
    mock_error_budget_burn_rates = [
        float(part.strip())
        for part in str(args.mock_error_budget_burn_rates).split(",")
        if part.strip()
    ]
    mock_readiness_values = [part.strip() for part in str(args.mock_readiness_values).split(",") if part.strip()]

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
    if int(args.readiness_regression_window_seconds) <= 0:
        print("[auto-rollback-policy] ERROR: --readiness-regression-window-seconds must be positive.", file=sys.stderr)
        return 2
    if float(args.poll_interval_seconds) <= 0:
        print("[auto-rollback-policy] ERROR: --poll-interval-seconds must be positive.", file=sys.stderr)
        return 2
    if int(args.max_observation_seconds) <= 0:
        print("[auto-rollback-policy] ERROR: --max-observation-seconds must be positive.", file=sys.stderr)
        return 2
    if float(args.max_error_budget_burn_rate_percent) < 0:
        print(
            "[auto-rollback-policy] ERROR: --max-error-budget-burn-rate-percent must be >= 0.",
            file=sys.stderr,
        )
        return 2

    expected_trigger_reason = str(args.expected_trigger_reason).strip().lower() or "auto"
    if expected_trigger_reason not in ALLOWED_TRIGGER_REASONS:
        print(
            (
                "[auto-rollback-policy] ERROR: --expected-trigger-reason must be one of "
                f"{sorted(ALLOWED_TRIGGER_REASONS)}."
            ),
            file=sys.stderr,
        )
        return 2

    try:
        report = run_policy(
            workspace_root=workspace_root,
            keep_artifacts=bool(args.keep_artifacts),
            label=str(args.label),
            manifest_path=manifest_path,
            ring=str(args.ring).strip() or "stable",
            critical_window_seconds=int(args.critical_window_seconds),
            readiness_regression_window_seconds=int(args.readiness_regression_window_seconds),
            poll_interval_seconds=float(args.poll_interval_seconds),
            max_observation_seconds=int(args.max_observation_seconds),
            max_error_budget_burn_rate_percent=float(args.max_error_budget_burn_rate_percent),
            dry_run=bool(args.dry_run),
            database_url=str(args.database_url).strip(),
            base_url=str(args.base_url).strip(),
            mock_statuses=mock_statuses,
            mock_error_budget_burn_rates=mock_error_budget_burn_rates,
            mock_readiness_values=mock_readiness_values,
            seed_previous_version=str(args.seed_previous_version).strip(),
            seed_incident_version=str(args.seed_incident_version).strip(),
            expected_trigger_reason=expected_trigger_reason,
            output_file=output_file,
        )
    except Exception as exc:
        print(f"[auto-rollback-policy] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
