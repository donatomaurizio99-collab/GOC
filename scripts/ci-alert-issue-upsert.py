from __future__ import annotations

import argparse
import hashlib
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
    "master-guard-workflow-health": {
        "title": "[CI Drift] master guard workflow health degraded",
        "labels": ["ci-drift", "guard-workflow-health"],
    },
    "release-gate-runtime-early-warning": {
        "title": "[Release Gate Runtime] sustained runtime warning on master",
        "labels": ["ci-drift", "release-gate-runtime"],
    },
    "release-gate-runtime-alert-age-slo": {
        "title": "[Release Gate Runtime] alert issue age SLO breached on master",
        "labels": ["ci-drift", "release-gate-runtime", "alert-age-slo"],
    },
    "master-watchdog-rehearsal-drill-slo": {
        "title": "[CI Drift] watchdog rehearsal drill SLO breached on master",
        "labels": ["ci-drift", "watchdog-rehearsal"],
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
    "guard-workflow-health": {
        "color": "D93F0B",
        "description": "Nightly guard-workflow watchdog detected degraded health",
    },
    "release-gate-runtime": {
        "color": "0E8A16",
        "description": "Sustained Release Gate runtime warning on master CI",
    },
    "alert-age-slo": {
        "color": "FBCA04",
        "description": "Runtime warning alert-issue age exceeds SLO threshold",
    },
    "watchdog-rehearsal": {
        "color": "5319E7",
        "description": "Watchdog rehearsal drill freshness/health SLO breached",
    },
}

RECOVERY_STREAK_PATTERN = re.compile(r"<!--\s*ci-alert-recovery-streak:(\d+)\s*-->")
PARENT_RUNTIME_ISSUE_MARKER_PATTERN = re.compile(r"<!--\s*ci-alert-parent-runtime-warning-issue:(\d+)\s*-->")
PARENT_RUNTIME_ISSUE_LINE_PATTERN = re.compile(r"^- Parent runtime warning issue: #\d+.*$", re.MULTILINE)
ACTIVE_ALERT_STREAK_PATTERN = re.compile(r"<!--\s*ci-alert-active-alert-streak:(\d+)\s*-->")
ACTIVE_ALERT_SUMMARY_SHA_PATTERN = re.compile(r"<!--\s*ci-alert-active-alert-summary-sha:([0-9a-f]{64})\s*-->")


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


def _coerce_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    coerced: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            coerced.append(text)
    return coerced


def _resolve_guard_workflow_run_url(*, repo: str, latest_run: dict[str, Any]) -> str:
    direct_url = str(latest_run.get("url") or latest_run.get("html_url") or "").strip()
    if direct_url:
        return direct_url

    run_id = int(latest_run.get("run_id") or 0)
    if run_id <= 0:
        return ""
    return f"https://github.com/{repo}/actions/runs/{run_id}"


def _format_guard_workflow_degraded_detail_lines(
    *,
    repo: str,
    degraded_workflows: list[dict[str, Any]],
) -> list[str]:
    detail_lines: list[str] = []
    for workflow in degraded_workflows:
        workflow_name = str(workflow.get("workflow_name") or "").strip() or "unknown_workflow"
        degraded_reasons = _coerce_string_list(workflow.get("degraded_reasons"))
        missing_required_artifacts = _coerce_string_list(workflow.get("missing_required_artifacts"))
        latest_run = workflow.get("latest_run") if isinstance(workflow.get("latest_run"), dict) else {}
        run_id = int(latest_run.get("run_id") or 0)
        run_url = _resolve_guard_workflow_run_url(repo=repo, latest_run=latest_run)

        reasons_text = ", ".join(degraded_reasons) if degraded_reasons else "none"
        missing_text = ", ".join(missing_required_artifacts) if missing_required_artifacts else "none"
        if run_id > 0 and run_url:
            latest_run_text = f"#{run_id} ({run_url})"
        elif run_id > 0:
            latest_run_text = f"#{run_id}"
        elif run_url:
            latest_run_text = run_url
        else:
            latest_run_text = "none"

        detail_lines.append(
            (
                f"- Degraded detail: {workflow_name} | "
                f"reasons={reasons_text} | "
                f"missing_required_artifacts={missing_text} | "
                f"latest_run={latest_run_text}"
            )
        )
    return detail_lines


def _format_guard_workflow_latest_run_reference(
    *,
    repo: str,
    workflow_name: str,
    latest_run: dict[str, Any],
) -> str:
    run_id = int(latest_run.get("run_id") or 0)
    run_url = _resolve_guard_workflow_run_url(repo=repo, latest_run=latest_run)
    if run_id > 0 and run_url:
        return f"{workflow_name}: #{run_id} ({run_url})"
    if run_id > 0:
        return f"{workflow_name}: #{run_id}"
    if run_url:
        return f"{workflow_name}: {run_url}"
    return f"{workflow_name}: none"


def _build_summary_fingerprint(summary_lines: list[str]) -> str:
    normalized = "\n".join([str(item or "").strip() for item in summary_lines]).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _build_active_alert_streak_marker(streak: int) -> str:
    return f"<!-- ci-alert-active-alert-streak:{max(0, int(streak))} -->"


def _build_active_alert_summary_sha_marker(summary_sha: str) -> str:
    token = str(summary_sha or "").strip().lower()
    return f"<!-- ci-alert-active-alert-summary-sha:{token} -->"


def _extract_active_alert_streak(body: str) -> int:
    match = ACTIVE_ALERT_STREAK_PATTERN.search(str(body or ""))
    if not match:
        return 0
    try:
        return max(0, int(match.group(1)))
    except ValueError:
        return 0


def _extract_active_alert_summary_sha(body: str) -> str:
    match = ACTIVE_ALERT_SUMMARY_SHA_PATTERN.search(str(body or ""))
    if not match:
        return ""
    return str(match.group(1) or "").strip().lower()


def _apply_active_alert_state_markers(*, body: str, streak: int, summary_sha: str) -> str:
    text = str(body or "")
    streak_marker = _build_active_alert_streak_marker(streak)
    summary_marker = _build_active_alert_summary_sha_marker(summary_sha)

    if ACTIVE_ALERT_STREAK_PATTERN.search(text):
        text = ACTIVE_ALERT_STREAK_PATTERN.sub(streak_marker, text)
    elif text.strip():
        text = text.rstrip() + "\n" + streak_marker
    else:
        text = streak_marker

    if ACTIVE_ALERT_SUMMARY_SHA_PATTERN.search(text):
        text = ACTIVE_ALERT_SUMMARY_SHA_PATTERN.sub(summary_marker, text)
    elif text.strip():
        text = text.rstrip() + "\n" + summary_marker
    else:
        text = summary_marker
    return text


def _clear_active_alert_state_markers(*, body: str) -> str:
    text = str(body or "")
    text = ACTIVE_ALERT_STREAK_PATTERN.sub("", text)
    text = ACTIVE_ALERT_SUMMARY_SHA_PATTERN.sub("", text)
    return "\n".join([line for line in text.splitlines() if line.strip()]).strip()


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
) -> tuple[bool, list[str], str, dict[str, Any]]:
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
        return alert_triggered, summary, branch, {}

    if signal_id == "master-guard-workflow-health":
        decision = report.get("decision") if isinstance(report.get("decision"), dict) else {}
        metrics = report.get("metrics") if isinstance(report.get("metrics"), dict) else {}
        degraded_workflows = (
            report.get("degraded_workflow_names") if isinstance(report.get("degraded_workflow_names"), list) else []
        )
        degraded_workflow_details = (
            report.get("degraded_workflows") if isinstance(report.get("degraded_workflows"), list) else []
        )
        missing_required_artifacts_total = int(metrics.get("missing_required_artifacts_total") or 0)
        alert_triggered = bool(decision.get("guard_workflow_health_degraded"))
        degraded_detail_lines = _format_guard_workflow_degraded_detail_lines(
            repo=repo,
            degraded_workflows=[item for item in degraded_workflow_details if isinstance(item, dict)],
        )
        summary = [
            (
                f"- Degraded guard workflows: {', '.join(str(item) for item in degraded_workflows)}"
                if degraded_workflows
                else "- Degraded guard workflows: none"
            ),
            (
                "- Guard workflows degraded total: "
                f"{metrics.get('guard_workflows_degraded_total', 0)} "
                f"(total={metrics.get('guard_workflows_total', 0)})"
            ),
            f"- Missing required artifacts total: {missing_required_artifacts_total}",
            *degraded_detail_lines,
            f"- Recommended action: {decision.get('recommended_action') or 'guard_workflow_health_green'}",
        ]
        return alert_triggered, summary, branch, {}

    if signal_id == "master-watchdog-rehearsal-drill-slo":
        decision = report.get("decision") if isinstance(report.get("decision"), dict) else {}
        metrics = report.get("metrics") if isinstance(report.get("metrics"), dict) else {}
        latest_run = report.get("latest_run") if isinstance(report.get("latest_run"), dict) else {}

        latest_run_id = int(latest_run.get("run_id") or 0)
        latest_run_url = str(latest_run.get("url") or latest_run.get("html_url") or "").strip()
        latest_run_status = str(latest_run.get("status") or "")
        latest_run_conclusion = str(latest_run.get("conclusion") or "")
        latest_run_age_hours = metrics.get("latest_run_age_hours")
        max_age_hours = metrics.get("max_age_hours")
        breach_reason = str(decision.get("breach_reason") or "none")
        alert_triggered = bool(decision.get("watchdog_rehearsal_slo_breached"))

        if latest_run_id > 0 and latest_run_url:
            latest_run_text = f"#{latest_run_id} ({latest_run_url})"
        elif latest_run_id > 0:
            latest_run_text = f"#{latest_run_id}"
        elif latest_run_url:
            latest_run_text = latest_run_url
        else:
            latest_run_text = "none"
        age_text = (
            f"{float(latest_run_age_hours):.2f}h"
            if latest_run_age_hours is not None
            else "unknown"
        )

        summary = [
            f"- Rehearsal drill SLO breached: {'yes' if alert_triggered else 'no'}",
            f"- Breach reason: {breach_reason}",
            f"- Latest rehearsal run: {latest_run_text}",
            f"- Latest rehearsal run status: {latest_run_status or 'unknown'} / {latest_run_conclusion or 'unknown'}",
            f"- Latest rehearsal run age: {age_text} (threshold={max_age_hours}h)",
            f"- Recommended action: {decision.get('recommended_action') or 'watchdog_rehearsal_slo_healthy'}",
        ]
        return alert_triggered, summary, branch, {}

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
        return alert_triggered, summary, branch, {}

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
        runtime_issue_url = str(runtime_open_issue.get("html_url") or "") if runtime_open_issue else ""
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
            (
                f"- Parent runtime warning issue: #{runtime_issue_number} ({runtime_issue_url})"
                if runtime_open_issue is not None and runtime_issue_url
                else (
                    f"- Parent runtime warning issue: #{runtime_issue_number}"
                    if runtime_open_issue is not None
                    else "- Parent runtime warning issue: none"
                )
            ),
            f"- Recommended action: {recommended_action}",
        ]
        return (
            alert_triggered,
            summary,
            branch,
            {
                "runtime_warning_active": bool(warning_triggered),
                "runtime_open_issue_number": int(runtime_issue_number or 0),
                "runtime_open_issue_url": runtime_issue_url or None,
                "runtime_open_issue_age_hours": float(runtime_issue_age_hours) if age_known else None,
                "runtime_open_issue_age_known": bool(age_known),
                "recommended_action": recommended_action,
            },
        )

    raise RuntimeError(f"Unsupported signal id: {signal_id}")


def _build_issue_marker(*, signal_id: str, repo: str, branch: str) -> str:
    return f"<!-- ci-alert-key:{signal_id}:{repo}:{branch} -->"


def _build_recovery_marker(streak: int) -> str:
    return f"<!-- ci-alert-recovery-streak:{max(0, int(streak))} -->"


def _build_parent_runtime_issue_marker(parent_issue_number: int) -> str:
    return f"<!-- ci-alert-parent-runtime-warning-issue:{max(1, int(parent_issue_number))} -->"


def _apply_parent_runtime_issue_reference(
    *,
    body: str,
    parent_issue_number: int,
    parent_issue_url: str | None,
) -> str:
    text = str(body or "")
    marker = _build_parent_runtime_issue_marker(parent_issue_number)
    marker_applied = (
        PARENT_RUNTIME_ISSUE_MARKER_PATTERN.sub(marker, text)
        if PARENT_RUNTIME_ISSUE_MARKER_PATTERN.search(text)
        else (text.rstrip() + "\n" + marker if text.strip() else marker)
    )

    line = (
        f"- Parent runtime warning issue: #{int(parent_issue_number)} ({parent_issue_url})"
        if parent_issue_url
        else f"- Parent runtime warning issue: #{int(parent_issue_number)}"
    )
    if PARENT_RUNTIME_ISSUE_LINE_PATTERN.search(marker_applied):
        return PARENT_RUNTIME_ISSUE_LINE_PATTERN.sub(line, marker_applied, count=1)
    if marker_applied.strip():
        return marker_applied.rstrip() + "\n" + line
    return line


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


def _signal_immediate_actions(
    *,
    signal_id: str,
    report: dict[str, Any],
    repo: str,
    branch: str,
) -> list[str]:
    if signal_id != "master-guard-workflow-health":
        return []

    degraded_workflow_details = (
        report.get("degraded_workflows") if isinstance(report.get("degraded_workflows"), list) else []
    )
    degraded_items = [item for item in degraded_workflow_details if isinstance(item, dict)]

    latest_run_references: list[str] = []
    missing_required_artifacts: list[str] = []
    for item in degraded_items:
        workflow_name = str(item.get("workflow_name") or "").strip() or "unknown_workflow"
        latest_run = item.get("latest_run") if isinstance(item.get("latest_run"), dict) else {}
        latest_run_references.append(
            _format_guard_workflow_latest_run_reference(
                repo=repo,
                workflow_name=workflow_name,
                latest_run=latest_run,
            )
        )
        for artifact_name in _coerce_string_list(item.get("missing_required_artifacts")):
            if artifact_name not in missing_required_artifacts:
                missing_required_artifacts.append(artifact_name)

    run_references_text = ", ".join(latest_run_references) if latest_run_references else "none"
    missing_artifacts_text = ", ".join(missing_required_artifacts) if missing_required_artifacts else "none"

    return [
        f"- Open degraded guard workflow run(s) immediately: {run_references_text}",
        f"- Verify and restore missing required artifacts: {missing_artifacts_text}",
        (
            "- Re-run watchdog check locally for confirmation: "
            "`.\\scripts\\run-master-guard-workflow-health-check.ps1 "
            "-LookbackHours 30 -PerPage 50 "
            "-OutputFile artifacts\\master-guard-workflow-health-check.json`"
        ),
    ]


def _build_issue_body(
    *,
    marker: str,
    signal_id: str,
    branch: str,
    report_file: Path,
    report: dict[str, Any],
    summary_lines: list[str],
    immediate_action_lines: list[str],
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
    if immediate_action_lines:
        lines += [
            "",
            "## Immediate Actions",
            *immediate_action_lines,
        ]
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
    immediate_action_lines: list[str],
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
    if immediate_action_lines:
        lines += [
            "",
            "## Immediate Actions",
            *immediate_action_lines,
        ]
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


def _build_immediate_close_comment(
    *,
    signal_id: str,
    branch: str,
    summary_lines: list[str],
    report_file: Path,
    run_url: str | None,
) -> str:
    lines = [
        f"Signal `{signal_id}` no longer requires escalation on `{branch}` ({_utc_now_text()}).",
        "",
        "## Resolution",
        "- Closing immediately because parent-coupled escalation criteria are no longer met.",
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
    active_comment_cooldown: int,
    output_file: Path,
) -> dict[str, Any]:
    started = time.perf_counter()
    _expect(signal_id in SIGNAL_SPECS, f"Unsupported --signal-id: {signal_id}")
    _expect(int(recovery_threshold) > 0, "recovery_threshold must be > 0")
    _expect(float(alert_age_hours) > 0, "alert_age_hours must be > 0")
    _expect(int(active_comment_cooldown) > 0, "active_comment_cooldown must be > 0")

    report_payload = _load_json_file(report_file)
    _expect(isinstance(report_payload, dict), f"Expected JSON object in report file: {report_file}")

    issue_action = "none"
    issue_deduped = False
    issue_closed = False
    issue_number: int | None = None
    issue_url: str | None = None
    recovery_streak = 0
    actions: list[dict[str, Any]] = []
    active_alert_streak = 0
    active_alert_summary_sha = ""
    active_comment_suppressed = False
    active_alert_state_changed = False

    all_issues = _load_issues(repo=repo, state="all", issues_file=issues_file, dry_run=dry_run)
    alert_triggered, summary_lines, branch, signal_context = _signal_alert_state(
        signal_id=signal_id,
        report=report_payload,
        repo=repo,
        issues=all_issues,
        alert_age_hours=float(alert_age_hours),
    )
    immediate_action_lines = _signal_immediate_actions(
        signal_id=signal_id,
        report=report_payload,
        repo=repo,
        branch=branch,
    )
    signal_spec = SIGNAL_SPECS[signal_id]
    title = str(signal_spec["title"])
    labels = [str(item) for item in signal_spec["labels"]]
    marker = _build_issue_marker(signal_id=signal_id, repo=repo, branch=branch)
    parent_runtime_issue_number: int | None = None
    parent_runtime_issue_url: str | None = None
    if signal_id == "release-gate-runtime-alert-age-slo":
        resolved_parent_issue_number = int(signal_context.get("runtime_open_issue_number") or 0)
        if resolved_parent_issue_number > 0:
            parent_runtime_issue_number = resolved_parent_issue_number
            parent_runtime_issue_url = str(signal_context.get("runtime_open_issue_url") or "") or None

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
        active_alert_summary_sha = _build_summary_fingerprint(summary_lines)
        if open_issue is not None:
            issue_deduped = True
            issue_number = int(open_issue.get("number") or 0)
            issue_url = str(open_issue.get("html_url") or "") or None
            existing_body = str(open_issue.get("body") or "")
            previous_active_alert_streak = _extract_active_alert_streak(existing_body)
            previous_active_alert_summary_sha = _extract_active_alert_summary_sha(existing_body)
            active_alert_state_changed = previous_active_alert_summary_sha != active_alert_summary_sha
            active_alert_streak = (
                1
                if active_alert_state_changed or previous_active_alert_streak <= 0
                else previous_active_alert_streak + 1
            )

            updated_body = _clear_active_alert_state_markers(body=existing_body)
            updated_body = _apply_recovery_marker(body=updated_body, streak=0)
            updated_body = _apply_active_alert_state_markers(
                body=updated_body,
                streak=active_alert_streak,
                summary_sha=active_alert_summary_sha,
            )
            if parent_runtime_issue_number is not None:
                updated_body = _apply_parent_runtime_issue_reference(
                    body=updated_body,
                    parent_issue_number=int(parent_runtime_issue_number),
                    parent_issue_url=parent_runtime_issue_url,
                )
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

            should_comment = bool(
                active_alert_state_changed
                or int(active_alert_streak) % int(active_comment_cooldown) == 0
            )

            if should_comment:
                issue_action = "commented"
                comment_body = _build_active_alert_comment(
                    signal_id=signal_id,
                    branch=branch,
                    summary_lines=summary_lines,
                    immediate_action_lines=immediate_action_lines,
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
                issue_action = "comment_suppressed_cooldown"
                active_comment_suppressed = True
        elif closed_issue is not None:
            issue_deduped = True
            issue_action = "reopened"
            issue_number = int(closed_issue.get("number") or 0)
            issue_url = str(closed_issue.get("html_url") or "") or None

            existing_body = str(closed_issue.get("body") or "")
            active_alert_streak = 1
            active_alert_state_changed = True
            updated_body = _clear_active_alert_state_markers(body=existing_body)
            updated_body = _apply_recovery_marker(body=updated_body, streak=0)
            updated_body = _apply_active_alert_state_markers(
                body=updated_body,
                streak=active_alert_streak,
                summary_sha=active_alert_summary_sha,
            )
            if parent_runtime_issue_number is not None:
                updated_body = _apply_parent_runtime_issue_reference(
                    body=updated_body,
                    parent_issue_number=int(parent_runtime_issue_number),
                    parent_issue_url=parent_runtime_issue_url,
                )
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
                immediate_action_lines=immediate_action_lines,
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
            active_alert_streak = 1
            active_alert_state_changed = True
            issue_body = _build_issue_body(
                marker=marker,
                signal_id=signal_id,
                branch=branch,
                report_file=report_file,
                report=report_payload,
                summary_lines=summary_lines,
                immediate_action_lines=immediate_action_lines,
                run_url=run_url,
            )
            issue_body = _apply_active_alert_state_markers(
                body=issue_body,
                streak=active_alert_streak,
                summary_sha=active_alert_summary_sha,
            )
            if parent_runtime_issue_number is not None:
                issue_body = _apply_parent_runtime_issue_reference(
                    body=issue_body,
                    parent_issue_number=int(parent_runtime_issue_number),
                    parent_issue_url=parent_runtime_issue_url,
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

            immediate_close_signal = signal_id == "release-gate-runtime-alert-age-slo"
            if immediate_close_signal:
                issue_action = "closed"
                issue_closed = True
                recovery_streak = 0

                updated_body = _clear_active_alert_state_markers(body=str(open_issue.get("body") or ""))
                updated_body = _apply_recovery_marker(body=updated_body, streak=0)
                if parent_runtime_issue_number is not None:
                    updated_body = _apply_parent_runtime_issue_reference(
                        body=updated_body,
                        parent_issue_number=int(parent_runtime_issue_number),
                        parent_issue_url=parent_runtime_issue_url,
                    )
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

                close_comment = _build_immediate_close_comment(
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
                        "body": close_comment,
                    }
                )
                if not dry_run:
                    _run_gh_api(
                        f"repos/{repo}/issues/{issue_number}/comments",
                        method="POST",
                        payload={"body": close_comment},
                    )

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
                previous_streak = _extract_recovery_streak(str(open_issue.get("body") or ""))
                recovery_streak = previous_streak + 1
                will_close = recovery_streak >= int(recovery_threshold)

                updated_body = _clear_active_alert_state_markers(body=str(open_issue.get("body") or ""))
                updated_body = _apply_recovery_marker(body=updated_body, streak=recovery_streak)
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
            "active_comment_cooldown": int(active_comment_cooldown),
            "open_issue_invariant_max": 1,
            "output_file": str(output_file),
        },
        "metrics": {
            "matching_open_issues_total": int(len(open_matches)),
            "matching_closed_issues_total": int(len(closed_matches)),
            "matching_issues_total": int(len(open_matches) + len(closed_matches)),
            "runtime_parent_issue_number": int(parent_runtime_issue_number or 0),
            "immediate_action_lines_total": int(len(immediate_action_lines)),
            "active_alert_streak": int(active_alert_streak),
            "active_comment_suppressed_total": int(1 if active_comment_suppressed else 0),
        },
        "decision": {
            "alert_triggered": bool(alert_triggered),
            "issue_action": issue_action,
            "issue_deduped": bool(issue_deduped),
            "issue_closed": bool(issue_closed),
            "recovery_streak": int(recovery_streak),
            "active_alert_streak": int(active_alert_streak),
            "active_alert_summary_sha": active_alert_summary_sha or None,
            "active_alert_state_changed": bool(active_alert_state_changed),
            "active_comment_suppressed": bool(active_comment_suppressed),
            "invariant_max_open_issues_ok": True,
            "immediate_close_mode": bool(signal_id == "release-gate-runtime-alert-age-slo"),
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
    parser.add_argument("--active-comment-cooldown", type=int, default=3)
    parser.add_argument("--output-file", default="artifacts/ci-alert-issue-upsert.json")
    args = parser.parse_args(argv)

    if float(args.alert_age_hours) <= 0:
        print("[ci-alert-issue-upsert] ERROR: --alert-age-hours must be > 0.", file=sys.stderr)
        return 2
    if int(args.active_comment_cooldown) <= 0:
        print("[ci-alert-issue-upsert] ERROR: --active-comment-cooldown must be > 0.", file=sys.stderr)
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
            active_comment_cooldown=int(args.active_comment_cooldown),
            output_file=Path(str(args.output_file)).expanduser(),
        )
    except Exception as exc:
        print(f"[ci-alert-issue-upsert] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
