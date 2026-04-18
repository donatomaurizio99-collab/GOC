from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


INJECTED_DEGRADED_WORKFLOW_NAME = "Master Branch Protection Drift Guard"


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _utc_now_text() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _load_json_file(path: Path) -> dict[str, Any]:
    _expect(path.exists(), f"JSON file not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    _expect(isinstance(payload, dict), f"Expected JSON object in file: {path}")
    return payload


def _run_python_command(*, project_root: Path, command: list[str]) -> None:
    completed = subprocess.run(
        [sys.executable, *command],
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    if completed.returncode == 0:
        return
    stderr = completed.stderr.strip()
    stdout = completed.stdout.strip()
    raise RuntimeError(
        "Subprocess failed with exit code "
        f"{completed.returncode}: {' '.join(command)}; stdout={stdout}; stderr={stderr}"
    )


def _build_guard_health_fixtures() -> dict[str, Any]:
    return {
        "workflow_runs": {
            "Master Required Checks 24h": [
                {
                    "id": 9701,
                    "status": "completed",
                    "conclusion": "success",
                    "updated_at": "2026-04-18T02:50:00Z",
                }
            ],
            "Master Branch Protection Drift Guard": [
                {
                    "id": 9702,
                    "status": "completed",
                    "conclusion": "success",
                    "updated_at": "2026-04-18T03:10:00Z",
                }
            ],
            "Master Release Gate Runtime Early Warning": [
                {
                    "id": 9703,
                    "status": "completed",
                    "conclusion": "success",
                    "updated_at": "2026-04-18T03:20:00Z",
                }
            ],
            "Master Guard Workflow Health": [
                {
                    "id": 9704,
                    "status": "completed",
                    "conclusion": "success",
                    "updated_at": "2026-04-18T03:35:00Z",
                }
            ],
        },
        "run_artifacts": {
            "9701": {
                "artifacts": [
                    {"name": "master-required-checks-24h-report", "expired": False},
                ]
            },
            "9702": {
                "artifacts": [
                    {
                        "name": "master-branch-protection-drift-guard",
                        "expired": False,
                    },
                ]
            },
            "9703": {
                "artifacts": [
                    {"name": "release-gate-runtime-early-warning", "expired": False},
                    {
                        "name": "release-gate-runtime-early-warning-issue-upsert",
                        "expired": False,
                    },
                    {
                        "name": "release-gate-runtime-alert-age-slo-issue-upsert",
                        "expired": False,
                    },
                ]
            },
            "9704": {
                "artifacts": [
                    {"name": "master-guard-workflow-health-check", "expired": False},
                    {
                        "name": "master-guard-workflow-health-issue-upsert",
                        "expired": False,
                    },
                ]
            },
        },
    }


def run_rehearsal_drill(
    *,
    label: str,
    repo: str,
    branch: str,
    run_url: str | None,
    check_report_file: Path,
    issue_upsert_report_file: Path,
    output_file: Path,
) -> dict[str, Any]:
    started = time.perf_counter()
    project_root = Path(__file__).resolve().parents[1]

    check_report_file.parent.mkdir(parents=True, exist_ok=True)
    issue_upsert_report_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="master-watchdog-rehearsal-drill-") as temp_root_text:
        temp_root = Path(temp_root_text)
        fixtures_file = temp_root / "master-guard-workflow-health-fixtures.json"
        issues_file = temp_root / "issues.json"
        issue_oplog_file = temp_root / "issue-oplog.json"
        fixtures_file.write_text(
            json.dumps(_build_guard_health_fixtures(), ensure_ascii=True, sort_keys=True),
            encoding="utf-8",
        )
        issues_file.write_text("[]", encoding="utf-8")

        _run_python_command(
            project_root=project_root,
            command=[
                str(project_root / "scripts" / "master-guard-workflow-health-check.py"),
                "--label",
                f"{label}-guard-health",
                "--repo",
                repo,
                "--branch",
                branch,
                "--fixtures-file",
                str(fixtures_file.resolve()),
                "--lookback-hours",
                "30",
                "--now-utc",
                "2026-04-18T04:00:00Z",
                "--allow-degraded",
                "--output-file",
                str(check_report_file.resolve()),
            ],
        )

        check_report = _load_json_file(check_report_file)
        check_decision = check_report.get("decision") if isinstance(check_report.get("decision"), dict) else {}
        check_metrics = check_report.get("metrics") if isinstance(check_report.get("metrics"), dict) else {}
        check_degraded_names = (
            check_report.get("degraded_workflow_names") if isinstance(check_report.get("degraded_workflow_names"), list) else []
        )
        check_is_degraded = bool(check_decision.get("guard_workflow_health_degraded"))
        _expect(check_is_degraded, "Injected failure drill expected guard workflow health degradation signal.")
        _expect(
            INJECTED_DEGRADED_WORKFLOW_NAME in [str(item) for item in check_degraded_names],
            "Injected failure drill did not surface expected degraded workflow name.",
        )
        _expect(
            int(check_metrics.get("guard_workflows_degraded_total") or 0) >= 1,
            "Injected failure drill expected at least one degraded guard workflow.",
        )

        issue_upsert_command = [
            str(project_root / "scripts" / "ci-alert-issue-upsert.py"),
            "--label",
            f"{label}-issue-upsert",
            "--signal-id",
            "master-guard-workflow-health",
            "--repo",
            repo,
            "--report-file",
            str(check_report_file.resolve()),
            "--issues-file",
            str(issues_file.resolve()),
            "--issue-oplog-file",
            str(issue_oplog_file.resolve()),
            "--dry-run",
            "--output-file",
            str(issue_upsert_report_file.resolve()),
        ]
        if run_url:
            issue_upsert_command += ["--run-url", run_url]
        _run_python_command(project_root=project_root, command=issue_upsert_command)

        issue_upsert_report = _load_json_file(issue_upsert_report_file)
        issue_upsert_decision = (
            issue_upsert_report.get("decision") if isinstance(issue_upsert_report.get("decision"), dict) else {}
        )
        issue_upsert_alert_triggered = bool(issue_upsert_decision.get("alert_triggered"))
        issue_upsert_action = str(issue_upsert_decision.get("issue_action") or "")
        _expect(issue_upsert_alert_triggered, "Injected failure drill expected alert chain to trigger.")
        _expect(
            issue_upsert_action == "created",
            f"Injected failure drill expected issue action 'created', got '{issue_upsert_action or 'none'}'.",
        )

        issue_oplog = _load_json_file(issue_oplog_file)
        issue_actions = issue_oplog.get("actions") if isinstance(issue_oplog.get("actions"), list) else []
        _expect(len(issue_actions) >= 1, "Injected failure drill expected at least one issue-oplog action.")
        create_issue_action = issue_actions[0] if isinstance(issue_actions[0], dict) else {}
        _expect(
            str(create_issue_action.get("action") or "") == "create_issue",
            "Injected failure drill expected first issue-oplog action to be 'create_issue'.",
        )
        create_issue_body = str(create_issue_action.get("body") or "")
        _expect(
            "Degraded detail:" in create_issue_body and "latest_run=#" in create_issue_body,
            "Injected failure drill expected per-workflow degraded diagnostics in issue body.",
        )

    report = {
        "label": label,
        "success": True,
        "config": {
            "repo": repo,
            "branch": branch,
            "run_url": run_url,
            "check_report_file": str(check_report_file),
            "issue_upsert_report_file": str(issue_upsert_report_file),
            "output_file": str(output_file),
            "dry_run_issue_upsert": True,
            "injected_degraded_workflow_name": INJECTED_DEGRADED_WORKFLOW_NAME,
        },
        "metrics": {
            "guard_workflows_degraded_total": int(check_metrics.get("guard_workflows_degraded_total") or 0),
            "degraded_workflow_names_total": int(len(check_degraded_names)),
            "issue_actions_total": int(len(issue_actions)),
        },
        "decision": {
            "injected_failure_detected": bool(check_is_degraded),
            "alert_chain_verified": bool(issue_upsert_alert_triggered and issue_upsert_action == "created"),
            "recommended_action": "watchdog_rehearsal_chain_verified",
        },
        "generated_at_utc": _utc_now_text(),
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }

    output_file.write_text(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2), encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run a deterministic watchdog rehearsal drill by injecting a guard-workflow failure and "
            "verifying the guard-health alert chain end-to-end in dry-run mode."
        )
    )
    parser.add_argument("--label", default="master-watchdog-rehearsal-drill")
    parser.add_argument("--repo", default="donatomaurizio99-collab/GOC")
    parser.add_argument("--branch", default="master")
    parser.add_argument("--run-url")
    parser.add_argument(
        "--check-report-file",
        default="artifacts/master-guard-workflow-health-rehearsal-check.json",
    )
    parser.add_argument(
        "--issue-upsert-report-file",
        default="artifacts/master-guard-workflow-health-rehearsal-issue-upsert.json",
    )
    parser.add_argument(
        "--output-file",
        default="artifacts/master-guard-workflow-health-rehearsal-drill.json",
    )
    args = parser.parse_args(argv)

    try:
        report = run_rehearsal_drill(
            label=str(args.label),
            repo=str(args.repo),
            branch=str(args.branch),
            run_url=str(args.run_url) if args.run_url else None,
            check_report_file=Path(str(args.check_report_file)).expanduser(),
            issue_upsert_report_file=Path(str(args.issue_upsert_report_file)).expanduser(),
            output_file=Path(str(args.output_file)).expanduser(),
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[master-watchdog-rehearsal-drill] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
