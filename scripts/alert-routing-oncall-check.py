from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from goal_ops_console.config import Settings
from goal_ops_console.main import create_app

SEVERITY_ORDER = {"warning": 1, "critical": 2}


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _read_json_file(path: Path) -> dict[str, Any]:
    _expect(path.exists(), f"Required file not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    _expect(isinstance(payload, dict), f"Expected JSON object in {path}")
    return payload


def _fetch_slo_from_local_app(database_url: str) -> dict[str, Any]:
    app = create_app(Settings(database_url=database_url))
    with TestClient(app) as client:
        response = client.get("/system/slo")
        _expect(response.status_code == 200, "Local /system/slo returned non-200 status")
        payload = response.json()
    _expect(isinstance(payload, dict), "Local /system/slo payload is not a JSON object")
    return payload


def _fetch_slo_from_url(base_url: str) -> dict[str, Any]:
    normalized = base_url.strip().rstrip("/")
    parsed = urlparse(normalized)
    _expect(bool(parsed.scheme and parsed.netloc), f"Invalid base URL: {base_url}")
    target = f"{normalized}/system/slo"
    with urllib.request.urlopen(target, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    _expect(isinstance(payload, dict), "Remote /system/slo payload is not a JSON object")
    return payload


def _build_mock_slo_payload(status: str, alert_count: int) -> dict[str, Any]:
    normalized_status = str(status).strip().lower()
    _expect(normalized_status in {"ok", "degraded", "critical"}, f"Invalid mock status: {status!r}")
    count = max(0, int(alert_count))
    severity = "critical" if normalized_status == "critical" else "warning"
    alerts = []
    for index in range(count):
        alerts.append(
            {
                "code": f"mock.{normalized_status}.{index + 1}",
                "severity": severity,
                "message": f"Mock {normalized_status} alert {index + 1}.",
            }
        )
    return {
        "timestamp_utc": _utc_now(),
        "status": normalized_status,
        "alert_count": len(alerts),
        "alerts": alerts,
        "indicators": {
            "http_success_rate_percent": 95.0 if normalized_status != "ok" else 100.0,
            "event_failure_rate_percent": 2.0 if normalized_status == "degraded" else 6.0 if normalized_status == "critical" else 0.0,
        },
    }


def _normalize_alerts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    raw_alerts = payload.get("alerts")
    if isinstance(raw_alerts, list):
        for index, item in enumerate(raw_alerts):
            if not isinstance(item, dict):
                continue
            severity = str(item.get("severity") or "").strip().lower()
            if severity not in {"warning", "critical"}:
                continue
            normalized.append(
                {
                    "code": str(item.get("code") or f"alert.{index + 1}"),
                    "severity": severity,
                    "message": str(item.get("message") or ""),
                }
            )

    if normalized:
        return normalized

    status = str(payload.get("status") or "").strip().lower()
    if status == "critical":
        return [{"code": "synthetic.status.critical", "severity": "critical", "message": "Derived critical alert from SLO status."}]
    if status == "degraded":
        return [{"code": "synthetic.status.degraded", "severity": "warning", "message": "Derived warning alert from SLO status."}]
    return []


def _route_for_severity(policy: dict[str, Any], severity: str) -> dict[str, Any] | None:
    routes = policy.get("routes")
    if not isinstance(routes, dict):
        return None
    route = routes.get(severity)
    if not isinstance(route, dict):
        return None
    return route


def _build_action_plan(policy: dict[str, Any], alerts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    escalation = policy.get("escalation")
    if not isinstance(escalation, dict):
        escalation = {}

    critical_page_within = max(0, int(escalation.get("critical_page_within_minutes", 5)))
    critical_backup_after = max(0, int(escalation.get("critical_backup_after_minutes", 10)))
    warning_notify_within = max(0, int(escalation.get("warning_notify_within_minutes", 15)))
    warning_ticket_within = max(0, int(escalation.get("warning_ticket_within_minutes", 30)))

    actions: list[dict[str, Any]] = []
    for alert in alerts:
        severity = str(alert["severity"])
        route = _route_for_severity(policy, severity)
        if route is None:
            continue
        channel = str(route.get("channel") or "")
        primary = str(route.get("primary") or "")
        backup = str(route.get("backup") or "")
        runbook_section = str(route.get("runbook_section") or "")
        code = str(alert["code"])

        if severity == "critical":
            actions.append(
                {
                    "alert_code": code,
                    "severity": severity,
                    "step": "page_primary_oncall",
                    "target": primary,
                    "channel": channel,
                    "due_within_minutes": critical_page_within,
                    "runbook_section": runbook_section,
                }
            )
            actions.append(
                {
                    "alert_code": code,
                    "severity": severity,
                    "step": "page_backup_oncall",
                    "target": backup,
                    "channel": channel,
                    "due_within_minutes": critical_backup_after,
                    "runbook_section": runbook_section,
                }
            )
        else:
            actions.append(
                {
                    "alert_code": code,
                    "severity": severity,
                    "step": "notify_warning_channel",
                    "target": channel,
                    "channel": channel,
                    "due_within_minutes": warning_notify_within,
                    "runbook_section": runbook_section,
                }
            )
            actions.append(
                {
                    "alert_code": code,
                    "severity": severity,
                    "step": "create_warning_ticket",
                    "target": primary,
                    "channel": channel,
                    "due_within_minutes": warning_ticket_within,
                    "runbook_section": runbook_section,
                }
            )
    return actions


def run_check(
    *,
    label: str,
    deployment_profile: str,
    policy_file: Path,
    runbook_file: Path,
    database_url: str,
    base_url: str,
    slo_json_file: Path | None,
    mock_slo_status: str,
    mock_alert_count: int,
    max_critical_ack_minutes: int,
    max_warning_ack_minutes: int,
) -> dict[str, Any]:
    started = time.perf_counter()
    profile = str(deployment_profile).strip().lower() or "production"
    policy = _read_json_file(policy_file)
    runbook_text = runbook_file.read_text(encoding="utf-8")

    source: dict[str, Any]
    if mock_slo_status.strip():
        payload = _build_mock_slo_payload(mock_slo_status, mock_alert_count)
        source = {
            "type": "mock",
            "mock_slo_status": str(mock_slo_status).strip().lower(),
            "mock_alert_count": int(mock_alert_count),
        }
    elif slo_json_file is not None:
        payload = _read_json_file(slo_json_file)
        source = {"type": "slo_json_file", "path": str(slo_json_file)}
    elif base_url.strip():
        payload = _fetch_slo_from_url(base_url.strip())
        source = {"type": "http", "base_url": base_url.strip()}
    else:
        payload = _fetch_slo_from_local_app(database_url.strip())
        source = {"type": "local", "database_url": database_url.strip()}

    alerts = _normalize_alerts(payload)
    action_plan = _build_action_plan(policy, alerts)

    critical_alerts = [item for item in alerts if item["severity"] == "critical"]
    warning_alerts = [item for item in alerts if item["severity"] == "warning"]
    critical_actions_primary = [
        item for item in action_plan if item["severity"] == "critical" and item["step"] == "page_primary_oncall"
    ]
    critical_actions_backup = [
        item for item in action_plan if item["severity"] == "critical" and item["step"] == "page_backup_oncall"
    ]
    warning_actions_notify = [
        item for item in action_plan if item["severity"] == "warning" and item["step"] == "notify_warning_channel"
    ]
    warning_actions_ticket = [
        item for item in action_plan if item["severity"] == "warning" and item["step"] == "create_warning_ticket"
    ]

    warning_route = _route_for_severity(policy, "warning")
    critical_route = _route_for_severity(policy, "critical")
    critical_ack = int(critical_route.get("max_ack_minutes", 9999)) if critical_route else 9999
    warning_ack = int(warning_route.get("max_ack_minutes", 9999)) if warning_route else 9999
    critical_runbook_section = str(critical_route.get("runbook_section") or "") if critical_route else ""
    warning_runbook_section = str(warning_route.get("runbook_section") or "") if warning_route else ""

    criteria: list[dict[str, Any]] = []

    def add(name: str, passed: bool, details: str) -> None:
        criteria.append({"name": name, "passed": bool(passed), "details": details})

    if profile == "production":
        add(
            "warning_and_critical_routes_present",
            warning_route is not None and critical_route is not None,
            f"warning_route={warning_route is not None}, critical_route={critical_route is not None}",
        )
        add(
            "critical_ack_budget",
            critical_ack <= max(1, int(max_critical_ack_minutes)),
            f"critical_ack_minutes={critical_ack}, max={max_critical_ack_minutes}",
        )
        add(
            "warning_ack_budget",
            warning_ack <= max(1, int(max_warning_ack_minutes)),
            f"warning_ack_minutes={warning_ack}, max={max_warning_ack_minutes}",
        )
        add(
            "critical_runbook_section_present",
            bool(critical_runbook_section) and critical_runbook_section in runbook_text,
            f"critical_runbook_section={critical_runbook_section!r}",
        )
        add(
            "warning_runbook_section_present",
            bool(warning_runbook_section) and warning_runbook_section in runbook_text,
            f"warning_runbook_section={warning_runbook_section!r}",
        )
        add(
            "critical_alerts_routed",
            len(critical_actions_primary) >= len(critical_alerts)
            and len(critical_actions_backup) >= len(critical_alerts),
            (
                f"critical_alerts={len(critical_alerts)}, "
                f"primary_actions={len(critical_actions_primary)}, "
                f"backup_actions={len(critical_actions_backup)}"
            ),
        )
        add(
            "warning_alerts_routed",
            len(warning_actions_notify) >= len(warning_alerts)
            and len(warning_actions_ticket) >= len(warning_alerts),
            (
                f"warning_alerts={len(warning_alerts)}, "
                f"notify_actions={len(warning_actions_notify)}, "
                f"ticket_actions={len(warning_actions_ticket)}"
            ),
        )
    else:
        add(
            "non_production_profile",
            True,
            f"deployment_profile={profile!r} (hard requirements skipped)",
        )

    failed = [item for item in criteria if not item["passed"]]
    success = len(failed) == 0

    report = {
        "label": label,
        "success": bool(success),
        "config": {
            "deployment_profile": profile,
            "policy_file": str(policy_file),
            "runbook_file": str(runbook_file),
            "database_url": str(database_url),
            "base_url": str(base_url),
            "slo_json_file": str(slo_json_file) if slo_json_file else None,
            "mock_slo_status": str(mock_slo_status).strip().lower() or None,
            "mock_alert_count": int(mock_alert_count),
            "max_critical_ack_minutes": int(max_critical_ack_minutes),
            "max_warning_ack_minutes": int(max_warning_ack_minutes),
        },
        "metrics": {
            "criteria_total": len(criteria),
            "criteria_failed": len(failed),
            "criteria_passed": len(criteria) - len(failed),
            "alert_count": len(alerts),
            "critical_alert_count": len(critical_alerts),
            "warning_alert_count": len(warning_alerts),
            "action_count": len(action_plan),
            "max_observed_severity_rank": max([SEVERITY_ORDER.get(item["severity"], 0) for item in alerts], default=0),
        },
        "criteria": criteria,
        "failed_criteria": failed,
        "source": source,
        "slo": payload,
        "alerts": alerts,
        "action_plan": action_plan,
        "generated_at_utc": _utc_now(),
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate alert routing and on-call runbook automation policy against observed SLO alerts."
        )
    )
    parser.add_argument("--label", default="alert-routing-oncall-check")
    parser.add_argument("--deployment-profile", default="production")
    parser.add_argument("--database-url", default=":memory:")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--slo-json-file", default="")
    parser.add_argument("--mock-slo-status", default="")
    parser.add_argument("--mock-alert-count", type=int, default=1)
    parser.add_argument("--routing-policy-file", default="docs/oncall-alert-routing-policy.json")
    parser.add_argument("--runbook-file", default="docs/production-runbook.md")
    parser.add_argument("--max-critical-ack-minutes", type=int, default=15)
    parser.add_argument("--max-warning-ack-minutes", type=int, default=120)
    parser.add_argument("--output-file")
    parser.add_argument("--allow-failure", action="store_true")
    args = parser.parse_args(argv)

    project_root = Path(__file__).resolve().parents[1]
    policy_file = Path(str(args.routing_policy_file)).expanduser()
    if not policy_file.is_absolute():
        policy_file = (project_root / policy_file).resolve()
    runbook_file = Path(str(args.runbook_file)).expanduser()
    if not runbook_file.is_absolute():
        runbook_file = (project_root / runbook_file).resolve()

    slo_json_file = None
    if str(args.slo_json_file).strip():
        slo_json_file = Path(str(args.slo_json_file)).expanduser()
        if not slo_json_file.is_absolute():
            slo_json_file = (project_root / slo_json_file).resolve()

    custom_sources = int(bool(str(args.base_url).strip())) + int(slo_json_file is not None) + int(bool(str(args.mock_slo_status).strip()))
    if custom_sources > 1:
        print(
            "[alert-routing-oncall-check] ERROR: Provide only one custom source: --base-url, --slo-json-file, or --mock-slo-status.",
            file=sys.stderr,
        )
        return 2

    try:
        report = run_check(
            label=str(args.label),
            deployment_profile=str(args.deployment_profile),
            policy_file=policy_file,
            runbook_file=runbook_file,
            database_url=str(args.database_url),
            base_url=str(args.base_url),
            slo_json_file=slo_json_file,
            mock_slo_status=str(args.mock_slo_status),
            mock_alert_count=max(0, int(args.mock_alert_count)),
            max_critical_ack_minutes=max(1, int(args.max_critical_ack_minutes)),
            max_warning_ack_minutes=max(1, int(args.max_warning_ack_minutes)),
        )
    except Exception as exc:
        print(f"[alert-routing-oncall-check] ERROR: {exc}", file=sys.stderr)
        return 1

    if args.output_file:
        output_file = Path(str(args.output_file)).expanduser()
        if not output_file.is_absolute():
            output_file = (project_root / output_file).resolve()
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2), encoding="utf-8")

    if report["success"] is False and not bool(args.allow_failure):
        print(f"[alert-routing-oncall-check] ERROR: {json.dumps(report, sort_keys=True)}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
