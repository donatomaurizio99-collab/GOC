from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any


SIGNAL_ALERT_DECISION_KEY = {
    "master-guard-workflow-health": "guard_workflow_health_degraded",
    "master-watchdog-rehearsal-drill-slo": "watchdog_rehearsal_slo_breached",
    "master-reliability-digest-guard": "reliability_digest_guard_breached",
}

ACTIVE_ALERT_ACTIONS = {"created", "reopened", "commented", "comment_suppressed_cooldown"}
RECOVERY_ALERT_ACTIONS = {"closed", "recovery_progress", "none"}


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _load_json_file(path: Path) -> dict[str, Any]:
    _expect(path.exists(), f"JSON file not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    _expect(isinstance(payload, dict), f"Expected JSON object in file: {path}")
    return payload


def _expected_alert_state(*, signal_id: str, guard_report: dict[str, Any]) -> bool:
    decision = guard_report.get("decision") if isinstance(guard_report.get("decision"), dict) else {}
    key = SIGNAL_ALERT_DECISION_KEY.get(signal_id)
    _expect(bool(key), f"Unsupported signal id for selftest: {signal_id}")
    return bool(decision.get(str(key)))


def run_guard_chain_selftest(
    *,
    label: str,
    signal_id: str,
    guard_report_file: Path,
    issue_upsert_report_file: Path,
    output_file: Path,
) -> dict[str, Any]:
    started = time.perf_counter()
    _expect(signal_id in SIGNAL_ALERT_DECISION_KEY, f"Unsupported signal id for selftest: {signal_id}")

    guard_report = _load_json_file(guard_report_file)
    issue_upsert_report = _load_json_file(issue_upsert_report_file)

    expected_alert_triggered = _expected_alert_state(signal_id=signal_id, guard_report=guard_report)
    issue_config = (
        issue_upsert_report.get("config") if isinstance(issue_upsert_report.get("config"), dict) else {}
    )
    issue_decision = (
        issue_upsert_report.get("decision") if isinstance(issue_upsert_report.get("decision"), dict) else {}
    )
    issue_metrics = issue_upsert_report.get("metrics") if isinstance(issue_upsert_report.get("metrics"), dict) else {}
    issue_actions = (
        issue_upsert_report.get("actions") if isinstance(issue_upsert_report.get("actions"), list) else []
    )

    issue_signal_id = str(issue_config.get("signal_id") or "")
    issue_alert_triggered = bool(issue_decision.get("alert_triggered"))
    issue_action = str(issue_decision.get("issue_action") or "none")
    immediate_action_lines_total = int(issue_metrics.get("immediate_action_lines_total") or 0)

    expected_report_basename = guard_report_file.name
    issue_report_file_text = str(issue_config.get("report_file") or "")
    issue_report_basename = Path(issue_report_file_text).name if issue_report_file_text else ""
    report_file_matches = bool(
        issue_report_basename and issue_report_basename.lower() == expected_report_basename.lower()
    )

    issue_action_allowed = (
        issue_action in ACTIVE_ALERT_ACTIONS if expected_alert_triggered else issue_action in RECOVERY_ALERT_ACTIONS
    )

    criteria = [
        {
            "name": "signal_id_matches",
            "passed": bool(issue_signal_id == signal_id),
            "details": f"expected={signal_id}, actual={issue_signal_id or 'none'}",
        },
        {
            "name": "issue_upsert_report_file_matches_guard_report",
            "passed": bool(report_file_matches),
            "details": (
                f"expected_report={expected_report_basename}, "
                f"issue_upsert_report={issue_report_basename or 'none'}"
            ),
        },
        {
            "name": "alert_state_propagated",
            "passed": bool(issue_alert_triggered == expected_alert_triggered),
            "details": (
                f"expected_alert_triggered={expected_alert_triggered}, "
                f"issue_alert_triggered={issue_alert_triggered}"
            ),
        },
        {
            "name": "issue_action_consistent_with_alert_state",
            "passed": bool(issue_action_allowed),
            "details": (
                f"expected_alert_triggered={expected_alert_triggered}, "
                f"issue_action={issue_action or 'none'}"
            ),
        },
        {
            "name": "issue_actions_recorded",
            "passed": bool(isinstance(issue_upsert_report.get("actions"), list)),
            "details": f"issue_actions_total={len(issue_actions)}",
        },
        {
            "name": "guard_health_immediate_actions_present_when_alerted",
            "passed": bool(
                signal_id not in {"master-guard-workflow-health", "master-reliability-digest-guard"}
                or (not expected_alert_triggered)
                or immediate_action_lines_total > 0
            ),
            "details": (
                f"signal_id={signal_id}, expected_alert_triggered={expected_alert_triggered}, "
                f"immediate_action_lines_total={immediate_action_lines_total}"
            ),
        },
    ]
    failed_criteria = [item for item in criteria if not bool(item.get("passed"))]
    success = len(failed_criteria) == 0

    report = {
        "label": label,
        "success": bool(success),
        "config": {
            "signal_id": signal_id,
            "guard_report_file": str(guard_report_file),
            "issue_upsert_report_file": str(issue_upsert_report_file),
            "output_file": str(output_file),
        },
        "metrics": {
            "criteria_failed": int(len(failed_criteria)),
            "guard_decision_keys_total": int(
                len(guard_report.get("decision")) if isinstance(guard_report.get("decision"), dict) else 0
            ),
            "issue_actions_total": int(len(issue_actions)),
            "immediate_action_lines_total": int(immediate_action_lines_total),
        },
        "criteria": criteria,
        "failed_criteria": failed_criteria,
        "decision": {
            "expected_alert_triggered": bool(expected_alert_triggered),
            "issue_alert_triggered": bool(issue_alert_triggered),
            "issue_action": issue_action,
            "signal_chain_consistent": bool(success),
            "recommended_action": (
                "guard_chain_healthy"
                if success
                else "investigate_guard_chain_signal_mismatch"
            ),
        },
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2), encoding="utf-8")

    if not success:
        raise RuntimeError(f"Master guard-chain selftest failed: {json.dumps(report, sort_keys=True)}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify guard report -> issue upsert chain consistency for master guard workflows "
            "using generated report artifacts."
        )
    )
    parser.add_argument("--label", default="master-guard-chain-selftest")
    parser.add_argument(
        "--signal-id",
        choices=sorted(SIGNAL_ALERT_DECISION_KEY.keys()),
        required=True,
    )
    parser.add_argument("--guard-report-file", required=True)
    parser.add_argument("--issue-upsert-report-file", required=True)
    parser.add_argument("--output-file", default="artifacts/master-guard-chain-selftest.json")
    args = parser.parse_args(argv)

    try:
        report = run_guard_chain_selftest(
            label=str(args.label),
            signal_id=str(args.signal_id),
            guard_report_file=Path(str(args.guard_report_file)).expanduser(),
            issue_upsert_report_file=Path(str(args.issue_upsert_report_file)).expanduser(),
            output_file=Path(str(args.output_file)).expanduser(),
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[master-guard-chain-selftest] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
