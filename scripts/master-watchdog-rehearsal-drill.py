from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


INJECTED_DEGRADED_WORKFLOW_NAME = "Master Branch Protection Drift Guard"
DEFAULT_MTTR_TARGET_SECONDS = 300.0
RUN_ID_PATTERN = re.compile(r"/actions/runs/(?P<run_id>\d+)")


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _utc_now_text() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _parse_run_id_from_url(url: str | None) -> int:
    text = str(url or "").strip()
    if not text:
        return 0
    match = RUN_ID_PATTERN.search(text)
    if match is None:
        return 0
    try:
        return int(match.group("run_id") or 0)
    except ValueError:
        return 0


def _resolve_run_reference(*, repo: str, run_id: Any, run_url: str | None) -> dict[str, Any]:
    normalized_url = str(run_url or "").strip()
    resolved_run_id = int(run_id or 0)
    if resolved_run_id <= 0 and normalized_url:
        resolved_run_id = _parse_run_id_from_url(normalized_url)
    if resolved_run_id > 0 and not normalized_url:
        normalized_url = f"https://github.com/{repo}/actions/runs/{resolved_run_id}"
    return {
        "run_id": int(resolved_run_id) if resolved_run_id > 0 else None,
        "url": normalized_url if normalized_url else None,
    }


def _format_run_reference(run_ref: dict[str, Any] | None) -> str:
    if not isinstance(run_ref, dict):
        return "none"
    run_id = int(run_ref.get("run_id") or 0)
    run_url = str(run_ref.get("url") or "").strip()
    if run_id > 0 and run_url:
        return f"[#{run_id}]({run_url})"
    if run_id > 0:
        return f"#{run_id}"
    if run_url:
        return run_url
    return "none"


def _build_markdown_summary(*, report: dict[str, Any]) -> str:
    decision = report.get("decision") if isinstance(report.get("decision"), dict) else {}
    metrics = report.get("metrics") if isinstance(report.get("metrics"), dict) else {}
    config = report.get("config") if isinstance(report.get("config"), dict) else {}
    run_links = report.get("run_links") if isinstance(report.get("run_links"), dict) else {}
    degraded_workflow = report.get("degraded_workflow") if isinstance(report.get("degraded_workflow"), dict) else {}

    degraded_reasons = (
        degraded_workflow.get("degraded_reasons") if isinstance(degraded_workflow.get("degraded_reasons"), list) else []
    )
    missing_artifacts = (
        degraded_workflow.get("missing_required_artifacts")
        if isinstance(degraded_workflow.get("missing_required_artifacts"), list)
        else []
    )

    lines = [
        "# Master Watchdog Rehearsal Drill",
        "",
        f"- Generated at (UTC): `{report.get('generated_at_utc') or ''}`",
        f"- Injected failure detected: {'yes' if bool(decision.get('injected_failure_detected')) else 'no'}",
        f"- Alert chain verified: {'yes' if bool(decision.get('alert_chain_verified')) else 'no'}",
        f"- Recommended action: `{decision.get('recommended_action') or 'watchdog_rehearsal_chain_verified'}`",
        "",
        "## Alert-Chain MTTR",
        (
            f"- Measured MTTR: `{float(metrics.get('alert_chain_mttr_seconds') or 0.0):.3f}s`"
            if metrics.get("alert_chain_mttr_seconds") is not None
            else "- Measured MTTR: `unknown`"
        ),
        f"- MTTR target: `{float(metrics.get('mttr_target_seconds') or 0.0):.3f}s`",
        f"- MTTR target breached: {'yes' if bool(decision.get('mttr_target_breached')) else 'no'}",
        "",
        "## Run Links",
        f"- Rehearsal drill run: {_format_run_reference(run_links.get('drill_run') if isinstance(run_links, dict) else None)}",
        (
            "- Injected degraded workflow latest run: "
            f"{_format_run_reference(run_links.get('injected_degraded_workflow_latest_run') if isinstance(run_links, dict) else None)}"
        ),
        "",
        "## Injected Degraded Workflow Diagnostics",
        f"- Workflow: `{degraded_workflow.get('workflow_name') or INJECTED_DEGRADED_WORKFLOW_NAME}`",
        (
            "- Reasons: "
            + ", ".join([str(item) for item in degraded_reasons if str(item).strip()])
            if degraded_reasons
            else "- Reasons: none"
        ),
        (
            "- Missing required artifacts: "
            + ", ".join([f"`{str(item)}`" for item in missing_artifacts if str(item).strip()])
            if missing_artifacts
            else "- Missing required artifacts: none"
        ),
        "",
        "## Artifacts",
        f"- Guard-health check report: `{config.get('check_report_file') or ''}`",
        f"- Issue-upsert report: `{config.get('issue_upsert_report_file') or ''}`",
        f"- Drill JSON report: `{config.get('output_file') or ''}`",
        f"- Drill markdown summary: `{config.get('markdown_output_file') or ''}`",
    ]
    return "\n".join(lines).strip() + "\n"


def _load_json_file(path: Path) -> dict[str, Any]:
    _expect(path.exists(), f"JSON file not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
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
    mttr_target_seconds: float,
    check_report_file: Path,
    issue_upsert_report_file: Path,
    output_file: Path,
    markdown_output_file: Path,
) -> dict[str, Any]:
    _expect(float(mttr_target_seconds) > 0.0, "mttr_target_seconds must be > 0.")
    started = time.perf_counter()
    project_root = Path(__file__).resolve().parents[1]

    check_report_file.parent.mkdir(parents=True, exist_ok=True)
    issue_upsert_report_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    markdown_output_file.parent.mkdir(parents=True, exist_ok=True)

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
        check_degraded_workflows = (
            check_report.get("degraded_workflows") if isinstance(check_report.get("degraded_workflows"), list) else []
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
        injected_degraded_workflow = next(
            (
                item
                for item in check_degraded_workflows
                if isinstance(item, dict) and str(item.get("workflow_name") or "") == INJECTED_DEGRADED_WORKFLOW_NAME
            ),
            None,
        )
        _expect(
            isinstance(injected_degraded_workflow, dict),
            "Injected failure drill expected degraded workflow diagnostics for injected workflow.",
        )
        injected_latest_run = (
            injected_degraded_workflow.get("latest_run")
            if isinstance(injected_degraded_workflow.get("latest_run"), dict)
            else {}
        )
        injected_missing_artifacts = (
            injected_degraded_workflow.get("missing_required_artifacts")
            if isinstance(injected_degraded_workflow.get("missing_required_artifacts"), list)
            else []
        )
        injected_degraded_reasons = (
            injected_degraded_workflow.get("degraded_reasons")
            if isinstance(injected_degraded_workflow.get("degraded_reasons"), list)
            else []
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

    alert_chain_mttr_seconds = max(0.0, float(time.perf_counter() - started))
    mttr_target_breached = bool(alert_chain_mttr_seconds > float(mttr_target_seconds))
    drill_run_ref = _resolve_run_reference(repo=repo, run_id=0, run_url=run_url)
    injected_degraded_run_ref = _resolve_run_reference(
        repo=repo,
        run_id=injected_latest_run.get("run_id") if isinstance(injected_latest_run, dict) else 0,
        run_url=None,
    )

    report = {
        "label": label,
        "success": True,
        "config": {
            "repo": repo,
            "branch": branch,
            "run_url": run_url,
            "mttr_target_seconds": float(mttr_target_seconds),
            "check_report_file": str(check_report_file),
            "issue_upsert_report_file": str(issue_upsert_report_file),
            "output_file": str(output_file),
            "markdown_output_file": str(markdown_output_file),
            "dry_run_issue_upsert": True,
            "injected_degraded_workflow_name": INJECTED_DEGRADED_WORKFLOW_NAME,
        },
        "metrics": {
            "guard_workflows_degraded_total": int(check_metrics.get("guard_workflows_degraded_total") or 0),
            "degraded_workflow_names_total": int(len(check_degraded_names)),
            "issue_actions_total": int(len(issue_actions)),
            "alert_chain_mttr_seconds": round(float(alert_chain_mttr_seconds), 3),
            "mttr_target_seconds": round(float(mttr_target_seconds), 3),
        },
        "run_links": {
            "drill_run": drill_run_ref,
            "injected_degraded_workflow_latest_run": injected_degraded_run_ref,
        },
        "degraded_workflow": {
            "workflow_name": str(injected_degraded_workflow.get("workflow_name") or INJECTED_DEGRADED_WORKFLOW_NAME),
            "degraded_reasons": [str(item) for item in injected_degraded_reasons if str(item).strip()],
            "missing_required_artifacts": [str(item) for item in injected_missing_artifacts if str(item).strip()],
            "latest_run": injected_degraded_run_ref,
        },
        "decision": {
            "injected_failure_detected": bool(check_is_degraded),
            "alert_chain_verified": bool(issue_upsert_alert_triggered and issue_upsert_action == "created"),
            "mttr_target_breached": bool(mttr_target_breached),
            "recommended_action": (
                "watchdog_rehearsal_chain_verified_mttr_breached"
                if mttr_target_breached
                else "watchdog_rehearsal_chain_verified"
            ),
        },
        "generated_at_utc": _utc_now_text(),
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }

    markdown_summary = _build_markdown_summary(report=report)
    report["markdown_summary"] = markdown_summary
    output_file.write_text(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2), encoding="utf-8")
    markdown_output_file.write_text(markdown_summary, encoding="utf-8")
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
    parser.add_argument("--mttr-target-seconds", type=float, default=DEFAULT_MTTR_TARGET_SECONDS)
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
    parser.add_argument(
        "--markdown-output-file",
        default="artifacts/master-guard-workflow-health-rehearsal-drill.md",
    )
    args = parser.parse_args(argv)

    if float(args.mttr_target_seconds) <= 0:
        print("[master-watchdog-rehearsal-drill] ERROR: --mttr-target-seconds must be > 0.", file=sys.stderr)
        return 2

    try:
        report = run_rehearsal_drill(
            label=str(args.label),
            repo=str(args.repo),
            branch=str(args.branch),
            run_url=str(args.run_url) if args.run_url else None,
            mttr_target_seconds=float(args.mttr_target_seconds),
            check_report_file=Path(str(args.check_report_file)).expanduser(),
            issue_upsert_report_file=Path(str(args.issue_upsert_report_file)).expanduser(),
            output_file=Path(str(args.output_file)).expanduser(),
            markdown_output_file=Path(str(args.markdown_output_file)).expanduser(),
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[master-watchdog-rehearsal-drill] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
