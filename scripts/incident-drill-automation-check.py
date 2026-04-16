from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _utc_now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_now() -> str:
    return _format_utc(_utc_now_dt())


def _parse_utc(text: str) -> datetime:
    candidate = str(text).strip()
    _expect(bool(candidate), "Completed-at timestamp is empty.")
    if candidate.endswith("Z"):
        candidate = f"{candidate[:-1]}+00:00"
    parsed = datetime.fromisoformat(candidate)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _safe_int(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(fallback)


def _read_json_file(path: Path) -> dict[str, Any]:
    _expect(path.exists(), f"Required file not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    _expect(isinstance(payload, dict), f"Expected JSON object in {path}")
    return payload


def _build_mock_report(
    *,
    days_since_tabletop: int,
    days_since_technical: int,
    tabletop_status: str,
    technical_status: str,
    open_followups: int,
) -> dict[str, Any]:
    now = _utc_now_dt()
    tabletop_age = max(0, int(days_since_tabletop))
    technical_age = max(0, int(days_since_technical))
    followup_count = max(0, int(open_followups))

    tabletop_completed_at = now - timedelta(days=tabletop_age)
    technical_completed_at = now - timedelta(days=technical_age)

    drills: list[dict[str, Any]] = [
        {
            "scenario_id": "tabletop.release-rollback",
            "drill_type": "tabletop",
            "status": str(tabletop_status).strip().lower() or "completed",
            "completed_at_utc": _format_utc(tabletop_completed_at),
            "participants": ["incident_commander", "scribe", "communications"],
            "postmortem_link": "https://example.invalid/tabletop-release-rollback",
            "metrics": {
                "decision_latency_seconds": 420,
                "communication_updates_sent": 3,
            },
        },
        {
            "scenario_id": "technical.incident-rollback",
            "drill_type": "technical",
            "status": str(technical_status).strip().lower() or "completed",
            "completed_at_utc": _format_utc(technical_completed_at),
            "participants": ["incident_commander", "sre_oncall", "release_manager"],
            "postmortem_link": "https://example.invalid/technical-incident-rollback",
            "metrics": {
                "load_requests": 30,
                "rollback_verified": str(technical_status).strip().lower() == "completed",
                "detection_seconds": 95,
                "mitigation_seconds": 210,
            },
        },
    ]

    followup_items: list[dict[str, Any]] = []
    for index in range(followup_count):
        followup_items.append(
            {
                "id": f"INC-FOLLOWUP-{index + 1:03d}",
                "owner": "ops-duty-manager",
                "status": "open",
                "due_in_days": 3 + index,
            }
        )

    return {
        "generated_at_utc": _utc_now(),
        "drills": drills,
        "followups": {
            "open_count": followup_count,
            "items": followup_items,
        },
    }


def _latest_drill_by_scenario(drills: list[dict[str, Any]], scenario_id: str) -> dict[str, Any] | None:
    candidates = [item for item in drills if str(item.get("scenario_id") or "") == scenario_id]
    if not candidates:
        return None

    def sort_key(item: dict[str, Any]) -> float:
        try:
            return _parse_utc(str(item.get("completed_at_utc") or "")).timestamp()
        except Exception:
            return 0.0

    return max(candidates, key=sort_key)


def run_check(
    *,
    label: str,
    deployment_profile: str,
    policy_file: Path,
    runbook_file: Path,
    drill_report_file: Path | None,
    mock_report: bool,
    mock_days_since_tabletop: int,
    mock_days_since_technical: int,
    mock_tabletop_status: str,
    mock_technical_status: str,
    mock_open_followups: int,
    max_tabletop_age_days: int,
    max_technical_age_days: int,
    min_technical_load_requests: int,
    max_open_followups: int,
) -> dict[str, Any]:
    started = time.perf_counter()
    now = _utc_now_dt()
    profile = str(deployment_profile).strip().lower() or "production"

    policy = _read_json_file(policy_file)
    runbook_text = runbook_file.read_text(encoding="utf-8")

    custom_source_count = int(drill_report_file is not None) + int(bool(mock_report))
    _expect(custom_source_count <= 1, "Provide only one source: --drill-report-file or --mock-report.")

    source: dict[str, Any]
    payload: dict[str, Any]
    if drill_report_file is not None:
        payload = _read_json_file(drill_report_file)
        source = {"type": "drill_report_file", "path": str(drill_report_file)}
    else:
        payload = _build_mock_report(
            days_since_tabletop=mock_days_since_tabletop,
            days_since_technical=mock_days_since_technical,
            tabletop_status=mock_tabletop_status,
            technical_status=mock_technical_status,
            open_followups=mock_open_followups,
        )
        source = {
            "type": "mock",
            "mock_days_since_tabletop": max(0, int(mock_days_since_tabletop)),
            "mock_days_since_technical": max(0, int(mock_days_since_technical)),
            "mock_tabletop_status": str(mock_tabletop_status).strip().lower(),
            "mock_technical_status": str(mock_technical_status).strip().lower(),
            "mock_open_followups": max(0, int(mock_open_followups)),
        }

    cadence = policy.get("cadence") if isinstance(policy.get("cadence"), dict) else {}
    quality = policy.get("quality") if isinstance(policy.get("quality"), dict) else {}
    evidence = policy.get("evidence") if isinstance(policy.get("evidence"), dict) else {}
    owners = policy.get("owners") if isinstance(policy.get("owners"), dict) else {}
    required_scenarios_raw = policy.get("required_scenarios") if isinstance(policy.get("required_scenarios"), list) else []

    policy_tabletop_max_age = max(1, _safe_int(cadence.get("tabletop_max_age_days"), 30))
    policy_technical_max_age = max(1, _safe_int(cadence.get("technical_drill_max_age_days"), 14))
    policy_max_open_followups = max(0, _safe_int(cadence.get("max_open_followup_actions"), 3))
    policy_min_technical_load = max(1, _safe_int(quality.get("technical_min_load_requests"), 20))

    tabletop_max_age_days = min(max(1, int(max_tabletop_age_days)), policy_tabletop_max_age)
    technical_max_age_days = min(max(1, int(max_technical_age_days)), policy_technical_max_age)
    allowed_open_followups = min(max(0, int(max_open_followups)), policy_max_open_followups)
    min_load_requests = max(int(min_technical_load_requests), policy_min_technical_load)

    require_postmortem_link = bool(evidence.get("require_postmortem_link", True))
    require_followup_tracking = bool(evidence.get("require_followup_tracking", True))

    drills = payload.get("drills") if isinstance(payload.get("drills"), list) else []
    normalized_drills = [item for item in drills if isinstance(item, dict)]

    followups = payload.get("followups") if isinstance(payload.get("followups"), dict) else {}
    open_followup_count = max(0, _safe_int(followups.get("open_count"), 0))

    criteria: list[dict[str, Any]] = []
    observed_ages: dict[str, float] = {}

    def add(name: str, passed: bool, details: str) -> None:
        criteria.append({"name": name, "passed": bool(passed), "details": details})

    if profile == "production":
        add("owners_configured", bool(owners), f"owner_count={len(owners)}")
        for role, value in sorted(owners.items()):
            add(
                f"owner_{role}_configured",
                bool(str(value).strip()),
                f"role={role!r}, value={str(value)!r}",
            )

        add(
            "required_scenarios_defined",
            len(required_scenarios_raw) >= 2,
            f"required_scenarios={len(required_scenarios_raw)}",
        )

        for item in required_scenarios_raw:
            scenario_id = str(item.get("id") or "").strip()
            scenario_type = str(item.get("type") or "").strip().lower()
            runbook_section = str(item.get("runbook_section") or "").strip()

            if not scenario_id:
                add("scenario_id_present", False, f"scenario_entry={json.dumps(item, sort_keys=True)}")
                continue

            add(
                f"{scenario_id}.runbook_section_present",
                bool(runbook_section) and runbook_section in runbook_text,
                f"runbook_section={runbook_section!r}",
            )

            observed = _latest_drill_by_scenario(normalized_drills, scenario_id)
            add(
                f"{scenario_id}.drill_present",
                observed is not None,
                f"scenario_id={scenario_id!r}",
            )
            if observed is None:
                continue

            observed_status = str(observed.get("status") or "").strip().lower()
            observed_type = str(observed.get("drill_type") or "").strip().lower()
            add(
                f"{scenario_id}.status_completed",
                observed_status == "completed",
                f"status={observed_status!r}",
            )
            add(
                f"{scenario_id}.type_matches",
                observed_type == scenario_type,
                f"expected={scenario_type!r}, observed={observed_type!r}",
            )

            completed_at = str(observed.get("completed_at_utc") or "")
            try:
                completed_at_dt = _parse_utc(completed_at)
                age_days = max(0.0, (now - completed_at_dt).total_seconds() / 86400.0)
                observed_ages[scenario_id] = round(age_days, 3)
                max_age = tabletop_max_age_days if scenario_type == "tabletop" else technical_max_age_days
                add(
                    f"{scenario_id}.recency_budget",
                    age_days <= float(max_age),
                    f"age_days={age_days:.3f}, max_age_days={max_age}",
                )
            except Exception as exc:
                add(
                    f"{scenario_id}.completed_at_valid",
                    False,
                    f"completed_at_utc={completed_at!r}, error={exc}",
                )

            postmortem_link = str(observed.get("postmortem_link") or "").strip()
            if require_postmortem_link:
                add(
                    f"{scenario_id}.postmortem_link_present",
                    bool(postmortem_link),
                    f"postmortem_link={postmortem_link!r}",
                )

            if scenario_type == "technical":
                metrics = observed.get("metrics") if isinstance(observed.get("metrics"), dict) else {}
                load_requests = max(0, _safe_int(metrics.get("load_requests"), 0))
                rollback_verified = bool(metrics.get("rollback_verified", False))
                add(
                    f"{scenario_id}.technical_min_load_requests",
                    load_requests >= min_load_requests,
                    f"load_requests={load_requests}, min_required={min_load_requests}",
                )
                add(
                    f"{scenario_id}.rollback_verified",
                    rollback_verified,
                    f"rollback_verified={rollback_verified}",
                )

        if require_followup_tracking:
            add(
                "followup_tracking_available",
                isinstance(followups, dict),
                f"followups_type={type(followups).__name__}",
            )
            add(
                "followup_open_budget",
                open_followup_count <= allowed_open_followups,
                f"open_followups={open_followup_count}, max_allowed={allowed_open_followups}",
            )
        else:
            add("followup_tracking_optional", True, "Policy does not require followup tracking.")
    else:
        add("non_production_profile", True, f"deployment_profile={profile!r} (hard requirements skipped)")

    failed_criteria = [item for item in criteria if item["passed"] is False]
    success = len(failed_criteria) == 0

    report = {
        "label": label,
        "success": bool(success),
        "config": {
            "deployment_profile": profile,
            "policy_file": str(policy_file),
            "runbook_file": str(runbook_file),
            "drill_report_file": str(drill_report_file) if drill_report_file else None,
            "mock_report": bool(mock_report),
            "mock_days_since_tabletop": max(0, int(mock_days_since_tabletop)),
            "mock_days_since_technical": max(0, int(mock_days_since_technical)),
            "mock_tabletop_status": str(mock_tabletop_status).strip().lower(),
            "mock_technical_status": str(mock_technical_status).strip().lower(),
            "mock_open_followups": max(0, int(mock_open_followups)),
            "max_tabletop_age_days": tabletop_max_age_days,
            "max_technical_age_days": technical_max_age_days,
            "min_technical_load_requests": min_load_requests,
            "max_open_followups": allowed_open_followups,
        },
        "metrics": {
            "criteria_total": len(criteria),
            "criteria_passed": len(criteria) - len(failed_criteria),
            "criteria_failed": len(failed_criteria),
            "required_scenario_count": len(required_scenarios_raw),
            "observed_drill_count": len(normalized_drills),
            "open_followup_count": open_followup_count,
            "tabletop_age_days": observed_ages.get("tabletop.release-rollback"),
            "technical_age_days": observed_ages.get("technical.incident-rollback"),
        },
        "criteria": criteria,
        "failed_criteria": failed_criteria,
        "source": source,
        "policy": policy,
        "drill_report": payload,
        "generated_at_utc": _utc_now(),
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate incident drill automation policy (tabletop + technical cadence, "
            "evidence completeness, and runbook anchoring)."
        )
    )
    parser.add_argument("--label", default="incident-drill-automation-check")
    parser.add_argument("--deployment-profile", default="production")
    parser.add_argument("--policy-file", default="docs/incident-drill-automation-policy.json")
    parser.add_argument("--runbook-file", default="docs/production-runbook.md")
    parser.add_argument("--drill-report-file", default="")
    parser.add_argument("--mock-report", action="store_true")
    parser.add_argument("--mock-days-since-tabletop", type=int, default=7)
    parser.add_argument("--mock-days-since-technical", type=int, default=3)
    parser.add_argument("--mock-tabletop-status", default="completed")
    parser.add_argument("--mock-technical-status", default="completed")
    parser.add_argument("--mock-open-followups", type=int, default=0)
    parser.add_argument("--max-tabletop-age-days", type=int, default=30)
    parser.add_argument("--max-technical-age-days", type=int, default=14)
    parser.add_argument("--min-technical-load-requests", type=int, default=20)
    parser.add_argument("--max-open-followups", type=int, default=3)
    parser.add_argument("--output-file")
    parser.add_argument("--allow-failure", action="store_true")
    args = parser.parse_args(argv)

    project_root = Path(__file__).resolve().parents[1]

    policy_file = Path(str(args.policy_file)).expanduser()
    if not policy_file.is_absolute():
        policy_file = (project_root / policy_file).resolve()

    runbook_file = Path(str(args.runbook_file)).expanduser()
    if not runbook_file.is_absolute():
        runbook_file = (project_root / runbook_file).resolve()

    drill_report_file = None
    if str(args.drill_report_file).strip():
        drill_report_file = Path(str(args.drill_report_file)).expanduser()
        if not drill_report_file.is_absolute():
            drill_report_file = (project_root / drill_report_file).resolve()

    try:
        report = run_check(
            label=str(args.label),
            deployment_profile=str(args.deployment_profile),
            policy_file=policy_file,
            runbook_file=runbook_file,
            drill_report_file=drill_report_file,
            mock_report=bool(args.mock_report),
            mock_days_since_tabletop=max(0, int(args.mock_days_since_tabletop)),
            mock_days_since_technical=max(0, int(args.mock_days_since_technical)),
            mock_tabletop_status=str(args.mock_tabletop_status),
            mock_technical_status=str(args.mock_technical_status),
            mock_open_followups=max(0, int(args.mock_open_followups)),
            max_tabletop_age_days=max(1, int(args.max_tabletop_age_days)),
            max_technical_age_days=max(1, int(args.max_technical_age_days)),
            min_technical_load_requests=max(1, int(args.min_technical_load_requests)),
            max_open_followups=max(0, int(args.max_open_followups)),
        )
    except Exception as exc:
        print(f"[incident-drill-automation-check] ERROR: {exc}", file=sys.stderr)
        return 1

    if args.output_file:
        output_file = Path(str(args.output_file)).expanduser()
        if not output_file.is_absolute():
            output_file = (project_root / output_file).resolve()
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2), encoding="utf-8")

    if report["success"] is False and not bool(args.allow_failure):
        print(f"[incident-drill-automation-check] ERROR: {json.dumps(report, sort_keys=True)}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
