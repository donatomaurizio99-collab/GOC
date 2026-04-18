from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
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
    "release-gate-runtime-alert-age-slo": {
        "title": "[Release Gate Runtime] alert issue age SLO breached on master",
        "labels": ["ci-drift", "release-gate-runtime", "alert-age-slo"],
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
    "alert-age-slo": {
        "color": "FBCA04",
        "description": "Runtime warning alert-issue age exceeds SLO threshold",
    },
}

RECOVERY_STREAK_PATTERN = re.compile(r"<!--\s*ci-alert-recovery-streak:(\d+)\s*-->")


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _utc_now_text() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _parse_utc_timestamp(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _hours_between(started_at: datetime | None, ended_at: datetime | None) -> float | None:
    if started_at is None or ended_at is None:
        return None
    return max(0.0, float((ended_at - started_at).total_seconds()) / 3600.0)


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


def _issue_state(issue: dict[str, Any]) -> str:
    state = str(issue.get("state") or "open").strip().lower()
    return state if state in {"open", "closed"} else "open"


def _coerce_issue_list(payload: Any) -> list[dict[str, Any]]:
    issues_raw: list[Any]
    if isinstance(payload, list):
        issues_raw = payload
    elif isinstance(payload, dict):
        candidates = payload.get("items")
        _expect(isinstance(candidates, list), "Expected list payload for issue fixtures.")
        issues_raw = candidates
    else:
        raise RuntimeError("Issue payload must be a JSON list or object with an 'items' list.")

    issues: list[dict[str, Any]] = []
    for item in issues_raw:
        if not isinstance(item, dict):
            continue
        if item.get("pull_request"):
            continue
        issues.append(item)
    return issues


def _load_issues(*, repo: str, state: str, issues_file: Path | None, dry_run: bool) -> list[dict[str, Any]]:
    resolved_state = str(state or "open").strip().lower()
    _expect(resolved_state in {"open", "all"}, f"Unsupported issue state: {state}")

    if issues_file is not None:
        payload = _load_json_file(issues_file)
    elif dry_run:
        payload = []
    else:
        payload = _run_gh_api(f"repos/{repo}/issues?state={resolved_state}&per_page=100")

    issues = _coerce_issue_list(payload)
    if resolved_state == "open":
        return [item for item in issues if _issue_state(item) == "open"]
    return issues


def _signal_alert_state(
    *,
    signal_id: str,
    report: dict[str, Any],
    repo: str,
    issues: list[dict[str, Any]],
    alert_age_hours: float,
) -> tuple[bool, list[str], str]:
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

    if signal_id == "release-gate-runtime-alert-age-slo":
        decision = report.get("decision") if isinstance(report.get("decision"), dict) else {}
        warning_triggered = bool(decision.get("warning_triggered"))
        evaluation_now = _parse_utc_timestamp(str(report.get("generated_at_utc") or "")) or datetime.now(timezone.utc)

        runtime_signal_id = "release-gate-runtime-early-warning"
        runtime_signal_spec = SIGNAL_SPECS[runtime_signal_id]
        runtime_title = str(runtime_signal_spec["title"])
        runtime_marker = _build_issue_marker(signal_id=runtime_signal_id, repo=repo, branch=branch)
        runtime_open_matches = _matching_issues(
            issues=[item for item in issues if _issue_state(item) == "open"],
            marker=runtime_marker,
            title=runtime_title,
        )
        if len(runtime_open_matches) > 1:
            runtime_open_numbers = [int(item.get("number") or 0) for item in runtime_open_matches]
            raise RuntimeError(
                "Runtime warning issue invariant violated for alert-age SLO evaluation on branch "
                f"'{branch}': expected at most 1 open runtime warning issue, "
                f"found {len(runtime_open_matches)} ({runtime_open_numbers})"
            )
        runtime_open_issue = runtime_open_matches[0] if runtime_open_matches else None
        runtime_issue_number = int(runtime_open_issue.get("number") or 0) if runtime_open_issue else None
        runtime_issue_created_at = (
            _parse_utc_timestamp(str(runtime_open_issue.get("created_at") or "")) if runtime_open_issue else None
        )
        runtime_issue_age_hours = _hours_between(runtime_issue_created_at, evaluation_now)
        age_known = runtime_issue_age_hours is not None

        alert_triggered = bool(
            warning_triggered
            and runtime_open_issue is not None
            and age_known
            and float(runtime_issue_age_hours or 0.0) >= float(alert_age_hours)
        )

        if alert_triggered:
            recommended_action = "runtime_alert_issue_age_slo_breached"
        elif not warning_triggered:
            recommended_action = "runtime_warning_not_active"
        elif runtime_open_issue is None:
            recommended_action = "runtime_warning_issue_not_open"
        elif not age_known:
            recommended_action = "runtime_warning_issue_age_unavailable"
        else:
            recommended_action = "runtime_alert_issue_age_within_slo"

        runtime_issue_text = "none"
        if runtime_open_issue is not None:
            age_text = f"{float(runtime_issue_age_hours):.2f}h" if age_known else "unknown"
            runtime_issue_text = (
                f"#{runtime_issue_number} (age={age_text}, threshold={float(alert_age_hours):.2f}h)"
            )

        summary = [
            f"- Runtime warning currently active: {'yes' if warning_triggered else 'no'}",
            f"- Runtime warning open issue: {runtime_issue_text}",
            f"- Recommended action: {recommended_action}",
        ]
        return alert_triggered, summary, branch

    raise RuntimeError(f"Unsupported signal id: {signal_id}")


def _build_issue_marker(*, signal_id: str, repo: str, branch: str) -> str:
    return f"<!-- ci-alert-key:{signal_id}:{repo}:{branch} -->"


def _build_recovery_marker(streak: int) -> str:
    return f"<!-- ci-alert-recovery-streak:{max(0, int(streak))} -->"


def _extract_recovery_streak(body: str) -> int:
    text = str(body or "")
    match = RECOVERY_STREAK_PATTERN.search(text)
    if not match:
        return 0
    try:
        return max(0, int(match.group(1)))
    except ValueError:
        return 0


def _apply_recovery_marker(*, body: str, streak: int) -> str:
    text = str(body or "")
    marker = _build_recovery_marker(streak)
    if RECOVERY_STREAK_PATTERN.search(text):
        return RECOVERY_STREAK_PATTERN.sub(marker, text)
    if text.strip():
        return text.rstrip() + "\n" + marker
    return marker


def _matching_issues(*, issues: list[dict[str, Any]], marker: str, title: str) -> list[dict[str, Any]]:
    matched: list[dict[str, Any]] = []
    seen_numbers: set[int] = set()

    def _append(issue: dict[str, Any]) -> None:
        number = int(issue.get("number") or 0)
        if number <= 0:
            return
        if number in seen_numbers:
            return
        seen_numbers.add(number)
        matched.append(issue)

    for issue in issues:
        body = str(issue.get("body") or "")
        if marker in body:
            _append(issue)

    for issue in issues:
        if str(issue.get("title") or "").strip() == title:
            _append(issue)

    return matched


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
        _build_recovery_marker(0),
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


def _build_active_alert_comment(
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
        "- Recovery streak reset to `0`.",
    ]
    if run_url:
        lines.append(f"- Workflow run: {run_url}")
    return "\n".join(lines)


def _build_recovery_comment(
    *,
    signal_id: str,
    branch: str,
    summary_lines: list[str],
    report_file: Path,
    run_url: str | None,
    recovery_streak: int,
    recovery_threshold: int,
    will_close: bool,
) -> str:
    status_text = "closing issue automatically" if will_close else "keeping issue open until threshold reached"
    lines = [
        f"Signal `{signal_id}` is healthy on `{branch}` ({_utc_now_text()}).",
        "",
        "## Recovery Progress",
        f"- Recovery streak: `{recovery_streak}/{recovery_threshold}` ({status_text})",
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
    issues_file: Path | None,
    issue_oplog_file: Path | None,
    dry_run: bool,
    recovery_threshold: int,
    alert_age_hours: float,
    output_file: Path,
) -> dict[str, Any]:
    started = time.perf_counter()
    _expect(signal_id in SIGNAL_SPECS, f"Unsupported --signal-id: {signal_id}")
    _expect(int(recovery_threshold) > 0, "recovery_threshold must be > 0")
    _expect(float(alert_age_hours) > 0, "alert_age_hours must be > 0")

    report_payload = _load_json_file(report_file)
    _expect(isinstance(report_payload, dict), f"Expected JSON object in report file: {report_file}")

    issue_action = "none"
    issue_deduped = False
    issue_closed = False
    issue_number: int | None = None
    issue_url: str | None = None
    recovery_streak = 0
    actions: list[dict[str, Any]] = []

    all_issues = _load_issues(repo=repo, state="all", issues_file=issues_file, dry_run=dry_run)
    alert_triggered, summary_lines, branch = _signal_alert_state(
        signal_id=signal_id,
        report=report_payload,
        repo=repo,
        issues=all_issues,
        alert_age_hours=float(alert_age_hours),
    )
    signal_spec = SIGNAL_SPECS[signal_id]
    title = str(signal_spec["title"])
    labels = [str(item) for item in signal_spec["labels"]]
    marker = _build_issue_marker(signal_id=signal_id, repo=repo, branch=branch)
    open_matches = _matching_issues(
        issues=[item for item in all_issues if _issue_state(item) == "open"],
        marker=marker,
        title=title,
    )
    closed_matches = _matching_issues(
        issues=[item for item in all_issues if _issue_state(item) == "closed"],
        marker=marker,
        title=title,
    )
    if len(open_matches) > 1:
        open_numbers = [int(item.get("number") or 0) for item in open_matches]
        raise RuntimeError(
            "Issue state invariant violated for signal "
            f"'{signal_id}' on branch '{branch}': expected at most 1 open issue, "
            f"found {len(open_matches)} ({open_numbers})"
        )
    open_issue = open_matches[0] if open_matches else None
    closed_issue = closed_matches[0] if closed_matches else None

    if alert_triggered:
        if open_issue is not None:
            issue_deduped = True
            issue_action = "commented"
            issue_number = int(open_issue.get("number") or 0)
            issue_url = str(open_issue.get("html_url") or "") or None

            updated_body = _apply_recovery_marker(body=str(open_issue.get("body") or ""), streak=0)
            recovery_streak = 0
            actions.append(
                {
                    "action": "update_issue_body",
                    "issue_number": issue_number,
                    "body": updated_body,
                }
            )
            if not dry_run:
                _run_gh_api(
                    f"repos/{repo}/issues/{issue_number}",
                    method="PATCH",
                    payload={"body": updated_body},
                )

            comment_body = _build_active_alert_comment(
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
        elif closed_issue is not None:
            issue_deduped = True
            issue_action = "reopened"
            issue_number = int(closed_issue.get("number") or 0)
            issue_url = str(closed_issue.get("html_url") or "") or None

            updated_body = _apply_recovery_marker(body=str(closed_issue.get("body") or ""), streak=0)
            recovery_streak = 0
            actions.append(
                {
                    "action": "reopen_issue",
                    "issue_number": issue_number,
                    "body": updated_body,
                }
            )
            if not dry_run:
                _run_gh_api(
                    f"repos/{repo}/issues/{issue_number}",
                    method="PATCH",
                    payload={"state": "open", "body": updated_body},
                )

            comment_body = _build_active_alert_comment(
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
            recovery_streak = 0
            actions.append(
                {
                    "action": "create_issue",
                    "title": title,
                    "labels": labels,
                    "body": issue_body,
                }
            )
            if dry_run:
                max_existing_number = max([int(item.get("number") or 0) for item in all_issues] or [0])
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
    else:
        if open_issue is not None:
            issue_deduped = True
            issue_number = int(open_issue.get("number") or 0)
            issue_url = str(open_issue.get("html_url") or "") or None

            previous_streak = _extract_recovery_streak(str(open_issue.get("body") or ""))
            recovery_streak = previous_streak + 1
            will_close = recovery_streak >= int(recovery_threshold)

            updated_body = _apply_recovery_marker(body=str(open_issue.get("body") or ""), streak=recovery_streak)
            actions.append(
                {
                    "action": "update_issue_body",
                    "issue_number": issue_number,
                    "body": updated_body,
                }
            )
            if not dry_run:
                _run_gh_api(
                    f"repos/{repo}/issues/{issue_number}",
                    method="PATCH",
                    payload={"body": updated_body},
                )

            recovery_comment = _build_recovery_comment(
                signal_id=signal_id,
                branch=branch,
                summary_lines=summary_lines,
                report_file=report_file,
                run_url=run_url,
                recovery_streak=recovery_streak,
                recovery_threshold=int(recovery_threshold),
                will_close=will_close,
            )
            actions.append(
                {
                    "action": "add_comment",
                    "issue_number": issue_number,
                    "issue_url": issue_url,
                    "body": recovery_comment,
                }
            )
            if not dry_run:
                _run_gh_api(
                    f"repos/{repo}/issues/{issue_number}/comments",
                    method="POST",
                    payload={"body": recovery_comment},
                )

            if will_close:
                issue_action = "closed"
                issue_closed = True
                actions.append(
                    {
                        "action": "close_issue",
                        "issue_number": issue_number,
                    }
                )
                if not dry_run:
                    _run_gh_api(
                        f"repos/{repo}/issues/{issue_number}",
                        method="PATCH",
                        payload={"state": "closed", "state_reason": "completed"},
                    )
            else:
                issue_action = "recovery_progress"

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
            "issues_file": str(issues_file) if issues_file is not None else None,
            "issue_oplog_file": str(issue_oplog_file) if issue_oplog_file is not None else None,
            "dry_run": bool(dry_run),
            "recovery_threshold": int(recovery_threshold),
            "alert_age_hours": float(alert_age_hours),
            "open_issue_invariant_max": 1,
            "output_file": str(output_file),
        },
        "metrics": {
            "matching_open_issues_total": int(len(open_matches)),
            "matching_closed_issues_total": int(len(closed_matches)),
            "matching_issues_total": int(len(open_matches) + len(closed_matches)),
        },
        "decision": {
            "alert_triggered": bool(alert_triggered),
            "issue_action": issue_action,
            "issue_deduped": bool(issue_deduped),
            "issue_closed": bool(issue_closed),
            "recovery_streak": int(recovery_streak),
            "invariant_max_open_issues_ok": True,
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
            "Convert CI alert signals from JSON reports into deduplicated GitHub issues, "
            "including recovery-based auto-close lifecycle management."
        )
    )
    parser.add_argument("--label", default="ci-alert-issue-upsert")
    parser.add_argument("--signal-id", required=True, choices=sorted(SIGNAL_SPECS.keys()))
    parser.add_argument("--repo", default="donatomaurizio99-collab/GOC")
    parser.add_argument("--report-file", required=True)
    parser.add_argument("--run-url")
    parser.add_argument("--issues-file")
    parser.add_argument("--issue-oplog-file")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--recovery-threshold", type=int, default=2)
    parser.add_argument("--alert-age-hours", type=float, default=72.0)
    parser.add_argument("--output-file", default="artifacts/ci-alert-issue-upsert.json")
    args = parser.parse_args(argv)

    if float(args.alert_age_hours) <= 0:
        print("[ci-alert-issue-upsert] ERROR: --alert-age-hours must be > 0.", file=sys.stderr)
        return 2

    try:
        report = run_issue_upsert(
            label=str(args.label),
            signal_id=str(args.signal_id),
            repo=str(args.repo),
            report_file=Path(str(args.report_file)).expanduser(),
            run_url=str(args.run_url) if args.run_url else None,
            issues_file=Path(str(args.issues_file)).expanduser() if args.issues_file else None,
            issue_oplog_file=Path(str(args.issue_oplog_file)).expanduser() if args.issue_oplog_file else None,
            dry_run=bool(args.dry_run),
            recovery_threshold=int(args.recovery_threshold),
            alert_age_hours=float(args.alert_age_hours),
            output_file=Path(str(args.output_file)).expanduser(),
        )
    except Exception as exc:
        print(f"[ci-alert-issue-upsert] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
