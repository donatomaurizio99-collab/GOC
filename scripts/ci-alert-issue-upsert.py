from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


SIGNAL_SPECS: dict[str, dict[str, Any]] = {
    "master-branch-protection-drift": {
        "title": "[CI Drift] master branch protection required checks drift",
        "labels": ["ci-drift", "branch-protection"],
    },
    "release-gate-runtime-early-warning": {
        "title": "[Release Gate Runtime] sustained runtime warning on master",
        "labels": ["ci-drift", "release-gate-runtime"],
    },
}

LABEL_DEFINITIONS: dict[str, dict[str, str]] = {
    "ci-drift": {
        "color": "B60205",
        "description": "Automated CI drift/regression signal requiring action",
    },
    "branch-protection": {
        "color": "1D76DB",
        "description": "master branch-protection required-check drift",
    },
    "release-gate-runtime": {
        "color": "0E8A16",
        "description": "Sustained Release Gate runtime warning on master CI",
    },
}


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _utc_now_text() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _load_json_file(path: Path) -> Any:
    _expect(path.exists(), f"JSON file not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _run_gh_api(path: str, *, method: str = "GET", payload: dict[str, Any] | None = None) -> Any:
    command = ["gh", "api", path]
    resolved_method = str(method or "GET").upper()
    if resolved_method != "GET":
        command += ["-X", resolved_method]

    input_payload: str | None = None
    if payload is not None:
        input_payload = json.dumps(payload, ensure_ascii=True)
        command += ["--input", "-"]

    completed = subprocess.run(
        command,
        input=input_payload,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        raise RuntimeError(
            f"gh api failed ({completed.returncode}) for '{path}' with method '{resolved_method}': {stderr}"
        )

    stdout = completed.stdout.strip()
    if not stdout:
        return {}
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"gh api returned invalid JSON for '{path}': {exc}") from exc


def _ensure_label_exists(*, repo: str, label: str) -> None:
    definition = LABEL_DEFINITIONS.get(label, {"color": "D4C5F9", "description": "Automated CI alert label"})
    payload = {
        "name": label,
        "color": str(definition.get("color") or "D4C5F9"),
        "description": str(definition.get("description") or "Automated CI alert label"),
    }
    command = ["gh", "api", f"repos/{repo}/labels", "-X", "POST", "--input", "-"]
    completed = subprocess.run(
        command,
        input=json.dumps(payload, ensure_ascii=True),
        capture_output=True,
        text=True,
    )
    if completed.returncode == 0:
        return

    stderr = completed.stderr.strip().lower()
    if "already_exists" in stderr or "name already exists" in stderr:
        return
    if "unprocessable entity" in stderr and "already exists" in stderr:
        return
    raise RuntimeError(f"Failed to ensure label '{label}' in repo '{repo}': {completed.stderr.strip()}")


def _load_open_issues(*, repo: str, open_issues_file: Path | None, dry_run: bool) -> list[dict[str, Any]]:
    if open_issues_file is not None:
        payload = _load_json_file(open_issues_file)
    elif dry_run:
        payload = []
    else:
        payload = _run_gh_api(f"repos/{repo}/issues?state=open&per_page=100")

    issues_raw: list[Any]
    if isinstance(payload, list):
        issues_raw = payload
    elif isinstance(payload, dict):
        candidates = payload.get("items")
        _expect(isinstance(candidates, list), "Expected list payload for open issues fixtures.")
        issues_raw = candidates
    else:
        raise RuntimeError("Open issues payload must be a JSON list or object with an 'items' list.")

    issues: list[dict[str, Any]] = []
    for item in issues_raw:
        if not isinstance(item, dict):
            continue
        if item.get("pull_request"):
            continue
        issues.append(item)
    return issues


def _signal_alert_state(*, signal_id: str, report: dict[str, Any]) -> tuple[bool, list[str], str]:
    config = report.get("config") if isinstance(report.get("config"), dict) else {}
    branch = str(config.get("branch") or "master")

    if signal_id == "master-branch-protection-drift":
        decision = report.get("decision") if isinstance(report.get("decision"), dict) else {}
        drift = report.get("drift") if isinstance(report.get("drift"), dict) else {}
        missing = drift.get("missing_required_checks") if isinstance(drift.get("missing_required_checks"), list) else []
        unexpected = (
            drift.get("unexpected_required_checks") if isinstance(drift.get("unexpected_required_checks"), list) else []
        )
        alert_triggered = bool(decision.get("branch_protection_drift_detected"))
        summary = [
            f"- Missing required checks: {', '.join(str(item) for item in missing) if missing else 'none'}",
            f"- Unexpected required checks: {', '.join(str(item) for item in unexpected) if unexpected else 'none'}",
            f"- Recommended action: {decision.get('recommended_action') or 'branch_protection_in_sync'}",
        ]
        return alert_triggered, summary, branch

    if signal_id == "release-gate-runtime-early-warning":
        decision = report.get("decision") if isinstance(report.get("decision"), dict) else {}
        metrics = report.get("metrics") if isinstance(report.get("metrics"), dict) else {}
        warning_message = str(report.get("warning_message") or "")
        threshold_seconds = config.get("threshold_seconds")
        sustained_runs = config.get("sustained_runs")
        alert_triggered = bool(decision.get("warning_triggered"))
        summary = [
            (
                f"- Warning: {warning_message}"
                if warning_message
                else "- Warning: Release Gate runtime warning triggered without explicit message"
            ),
            (
                "- Consecutive runs over threshold: "
                f"{metrics.get('consecutive_runs_over_threshold', 0)} "
                f"(threshold={threshold_seconds}s, sustained_runs={sustained_runs})"
            ),
            f"- Recommended action: {decision.get('recommended_action') or 'runtime_within_warning_budget'}",
        ]
        return alert_triggered, summary, branch

    raise RuntimeError(f"Unsupported signal id: {signal_id}")


def _build_issue_marker(*, signal_id: str, repo: str, branch: str) -> str:
    return f"<!-- ci-alert-key:{signal_id}:{repo}:{branch} -->"


def _find_existing_issue(*, issues: list[dict[str, Any]], marker: str, title: str) -> dict[str, Any] | None:
    for issue in issues:
        body = str(issue.get("body") or "")
        if marker in body:
            return issue
    for issue in issues:
        if str(issue.get("title") or "").strip() == title:
            return issue
    return None


def _build_issue_body(
    *,
    marker: str,
    signal_id: str,
    branch: str,
    report_file: Path,
    report: dict[str, Any],
    summary_lines: list[str],
    run_url: str | None,
) -> str:
    generated_at = str(report.get("generated_at_utc") or _utc_now_text())
    lines = [
        marker,
        "",
        f"Automated CI alert signal `{signal_id}` detected on `{branch}`.",
        "",
        "## Summary",
        *summary_lines,
        "",
        f"- Report file: `{report_file}`",
        f"- Report generated at: `{generated_at}`",
    ]
    if run_url:
        lines.append(f"- Workflow run: {run_url}")
    lines += [
        "",
        "## Expected Action",
        "- Investigate root cause and restore stability-first green baseline.",
        "",
        "_Managed by `scripts/ci-alert-issue-upsert.py`._",
    ]
    return "\n".join(lines)


def _build_issue_comment(
    *,
    signal_id: str,
    branch: str,
    summary_lines: list[str],
    report_file: Path,
    run_url: str | None,
) -> str:
    lines = [
        f"Alert signal `{signal_id}` remains active on `{branch}` ({_utc_now_text()}).",
        "",
        "## Latest Summary",
        *summary_lines,
        "",
        f"- Report file: `{report_file}`",
    ]
    if run_url:
        lines.append(f"- Workflow run: {run_url}")
    return "\n".join(lines)


def run_issue_upsert(
    *,
    label: str,
    signal_id: str,
    repo: str,
    report_file: Path,
    run_url: str | None,
    open_issues_file: Path | None,
    issue_oplog_file: Path | None,
    dry_run: bool,
    output_file: Path,
) -> dict[str, Any]:
    started = time.perf_counter()
    _expect(signal_id in SIGNAL_SPECS, f"Unsupported --signal-id: {signal_id}")

    report_payload = _load_json_file(report_file)
    _expect(isinstance(report_payload, dict), f"Expected JSON object in report file: {report_file}")

    alert_triggered, summary_lines, branch = _signal_alert_state(signal_id=signal_id, report=report_payload)
    signal_spec = SIGNAL_SPECS[signal_id]
    title = str(signal_spec["title"])
    labels = [str(item) for item in signal_spec["labels"]]
    marker = _build_issue_marker(signal_id=signal_id, repo=repo, branch=branch)

    issue_action = "none"
    issue_deduped = False
    issue_number: int | None = None
    issue_url: str | None = None
    actions: list[dict[str, Any]] = []

    if alert_triggered:
        open_issues = _load_open_issues(repo=repo, open_issues_file=open_issues_file, dry_run=dry_run)
        existing_issue = _find_existing_issue(issues=open_issues, marker=marker, title=title)

        if existing_issue is not None:
            issue_deduped = True
            issue_action = "commented"
            issue_number = int(existing_issue.get("number") or 0)
            issue_url = str(existing_issue.get("html_url") or "") or None
            comment_body = _build_issue_comment(
                signal_id=signal_id,
                branch=branch,
                summary_lines=summary_lines,
                report_file=report_file,
                run_url=run_url,
            )
            actions.append(
                {
                    "action": "add_comment",
                    "issue_number": issue_number,
                    "issue_url": issue_url,
                    "body": comment_body,
                }
            )
            if not dry_run:
                _run_gh_api(
                    f"repos/{repo}/issues/{issue_number}/comments",
                    method="POST",
                    payload={"body": comment_body},
                )
        else:
            issue_action = "created"
            issue_body = _build_issue_body(
                marker=marker,
                signal_id=signal_id,
                branch=branch,
                report_file=report_file,
                report=report_payload,
                summary_lines=summary_lines,
                run_url=run_url,
            )
            actions.append(
                {
                    "action": "create_issue",
                    "title": title,
                    "labels": labels,
                    "body": issue_body,
                }
            )
            if dry_run:
                max_existing_number = max([int(item.get("number") or 0) for item in open_issues] or [0])
                issue_number = max_existing_number + 1
                issue_url = None
            else:
                for label_name in labels:
                    _ensure_label_exists(repo=repo, label=label_name)
                created_issue = _run_gh_api(
                    f"repos/{repo}/issues",
                    method="POST",
                    payload={
                        "title": title,
                        "body": issue_body,
                        "labels": labels,
                    },
                )
                _expect(isinstance(created_issue, dict), "Expected issue object from create issue API.")
                issue_number = int(created_issue.get("number") or 0)
                _expect(issue_number > 0, "Issue create API returned invalid issue number.")
                issue_url = str(created_issue.get("html_url") or "") or None

    if issue_oplog_file is not None:
        issue_oplog_file.parent.mkdir(parents=True, exist_ok=True)
        issue_oplog_file.write_text(
            json.dumps({"actions": actions}, ensure_ascii=True, sort_keys=True, indent=2),
            encoding="utf-8",
        )

    report = {
        "label": label,
        "success": True,
        "config": {
            "signal_id": signal_id,
            "repo": repo,
            "branch": branch,
            "report_file": str(report_file),
            "run_url": run_url,
            "open_issues_file": str(open_issues_file) if open_issues_file is not None else None,
            "issue_oplog_file": str(issue_oplog_file) if issue_oplog_file is not None else None,
            "dry_run": bool(dry_run),
            "output_file": str(output_file),
        },
        "decision": {
            "alert_triggered": bool(alert_triggered),
            "issue_action": issue_action,
            "issue_deduped": bool(issue_deduped),
        },
        "issue": {
            "number": issue_number,
            "url": issue_url,
            "title": title,
            "labels": labels,
            "marker": marker,
        },
        "actions": actions,
        "generated_at_utc": _utc_now_text(),
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2), encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Convert CI alert signals from JSON reports into deduplicated GitHub issues so "
            "drift and runtime warnings become explicit action items."
        )
    )
    parser.add_argument("--label", default="ci-alert-issue-upsert")
    parser.add_argument("--signal-id", required=True, choices=sorted(SIGNAL_SPECS.keys()))
    parser.add_argument("--repo", default="donatomaurizio99-collab/GOC")
    parser.add_argument("--report-file", required=True)
    parser.add_argument("--run-url")
    parser.add_argument("--open-issues-file")
    parser.add_argument("--issue-oplog-file")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output-file", default="artifacts/ci-alert-issue-upsert.json")
    args = parser.parse_args(argv)

    try:
        report = run_issue_upsert(
            label=str(args.label),
            signal_id=str(args.signal_id),
            repo=str(args.repo),
            report_file=Path(str(args.report_file)).expanduser(),
            run_url=str(args.run_url) if args.run_url else None,
            open_issues_file=Path(str(args.open_issues_file)).expanduser() if args.open_issues_file else None,
            issue_oplog_file=Path(str(args.issue_oplog_file)).expanduser() if args.issue_oplog_file else None,
            dry_run=bool(args.dry_run),
            output_file=Path(str(args.output_file)).expanduser(),
        )
    except Exception as exc:
        print(f"[ci-alert-issue-upsert] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
