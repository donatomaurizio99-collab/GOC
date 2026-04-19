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


BLOCKING_SIGNAL_IDS = {
    "master-branch-protection-drift",
    "master-guard-workflow-health",
    "release-gate-runtime-alert-age-slo",
    "master-watchdog-rehearsal-drill-slo",
    "master-reliability-digest-guard",
}

RESIDUAL_SIGNAL_IDS = {
    "release-gate-runtime-early-warning",
    "master-reliability-digest-warning",
}

SIGNAL_MARKER_PATTERN = re.compile(
    r"ci-alert-key:(?P<signal>[a-z0-9\-]+):(?P<repo>[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+):(?P<branch>[A-Za-z0-9_.\-/]+)"
)


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


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


def _run_gh_api(path: str) -> list[dict[str, Any]]:
    command = ["gh", "api", path]
    completed = subprocess.run(command, capture_output=True, text=True)
    _expect(
        completed.returncode == 0,
        f"gh api failed ({completed.returncode}) for '{path}': {completed.stderr.strip()}",
    )
    payload = json.loads(completed.stdout)
    _expect(isinstance(payload, list), f"Expected JSON list from gh api path '{path}'.")
    return [item for item in payload if isinstance(item, dict)]


def _load_json_file(path: Path) -> list[dict[str, Any]]:
    _expect(path.exists(), f"JSON file not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    _expect(isinstance(payload, list), f"Expected JSON list in file: {path}")
    return [item for item in payload if isinstance(item, dict)]


def _extract_signal_id(body: str) -> str:
    text = str(body or "")
    match = SIGNAL_MARKER_PATTERN.search(text)
    if not match:
        return "unknown"
    return str(match.group("signal") or "unknown")


def _coerce_label_names(labels_value: Any) -> list[str]:
    if not isinstance(labels_value, list):
        return []
    names: list[str] = []
    for item in labels_value:
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            if name:
                names.append(name)
            continue
        text = str(item or "").strip()
        if text:
            names.append(text)
    return names


def _classify_issue_status(
    *,
    signal_id: str,
    issue_age_hours: float | None,
    blocked_age_hours: float,
) -> str:
    if signal_id in BLOCKING_SIGNAL_IDS:
        return "blocked"
    if signal_id in RESIDUAL_SIGNAL_IDS:
        return "residual"
    if issue_age_hours is not None and float(issue_age_hours) >= float(blocked_age_hours):
        return "blocked"
    return "residual"


def _build_markdown_summary(
    *,
    generated_at_utc: str,
    open_issues_total: int,
    blocked_total: int,
    residual_total: int,
    unknown_signal_total: int,
    oldest_open_issue_age_hours: float | None,
    issues: list[dict[str, Any]],
) -> str:
    lines: list[str] = [
        "# Master CI Drift Status Report",
        "",
        f"- Generated at (UTC): `{generated_at_utc}`",
        f"- Open `ci-drift` issues: `{open_issues_total}`",
        f"- Blocked: `{blocked_total}`",
        f"- Residual: `{residual_total}`",
        f"- Unknown signal-id: `{unknown_signal_total}`",
        (
            f"- Oldest open issue age: `{oldest_open_issue_age_hours:.2f}h`"
            if oldest_open_issue_age_hours is not None
            else "- Oldest open issue age: `unknown`"
        ),
        "",
    ]
    if not issues:
        lines += ["No open `ci-drift` issues.", ""]
        return "\n".join(lines).strip() + "\n"

    lines += [
        "| Issue | Signal | Class | Age (h) | Updated (UTC) | Title |",
        "|---|---|---|---:|---|---|",
    ]
    for issue in issues:
        issue_number = int(issue.get("number") or 0)
        issue_url = str(issue.get("url") or "").strip()
        issue_ref = f"[#{issue_number}]({issue_url})" if issue_number > 0 and issue_url else f"#{issue_number}"
        signal_id = str(issue.get("signal_id") or "unknown")
        status_class = str(issue.get("status_class") or "residual")
        issue_age_hours = issue.get("issue_age_hours")
        age_text = f"{float(issue_age_hours):.2f}" if issue_age_hours is not None else "unknown"
        updated_at = str(issue.get("updated_at") or "")
        title = str(issue.get("title") or "")
        lines.append(f"| {issue_ref} | `{signal_id}` | `{status_class}` | {age_text} | `{updated_at}` | {title} |")
    lines.append("")
    return "\n".join(lines).strip() + "\n"


def run_master_ci_drift_status_report(
    *,
    label: str,
    repo: str,
    blocked_age_hours: float,
    per_page: int,
    issues_file: Path | None,
    output_file: Path,
    markdown_output_file: Path,
) -> dict[str, Any]:
    _expect(per_page > 0, "per_page must be > 0.")
    _expect(float(blocked_age_hours) > 0, "blocked_age_hours must be > 0.")
    started = time.perf_counter()

    if issues_file is not None:
        issues_payload = _load_json_file(issues_file)
    else:
        issues_payload = _run_gh_api(f"repos/{repo}/issues?state=open&labels=ci-drift&per_page={per_page}")

    evaluation_now = datetime.now(timezone.utc)
    issue_rows: list[dict[str, Any]] = []
    for issue in issues_payload:
        labels = _coerce_label_names(issue.get("labels"))
        if "ci-drift" not in labels:
            continue
        number = int(issue.get("number") or 0)
        created_at = _parse_utc_timestamp(str(issue.get("created_at") or ""))
        updated_at = _parse_utc_timestamp(str(issue.get("updated_at") or ""))
        issue_age_hours = _hours_between(created_at, evaluation_now)
        signal_id = _extract_signal_id(str(issue.get("body") or ""))
        status_class = _classify_issue_status(
            signal_id=signal_id,
            issue_age_hours=issue_age_hours,
            blocked_age_hours=float(blocked_age_hours),
        )
        issue_rows.append(
            {
                "number": number,
                "title": str(issue.get("title") or ""),
                "url": str(issue.get("html_url") or ""),
                "signal_id": signal_id,
                "status_class": status_class,
                "issue_age_hours": round(float(issue_age_hours), 3) if issue_age_hours is not None else None,
                "created_at": created_at.strftime("%Y-%m-%dT%H:%M:%SZ") if created_at is not None else "",
                "updated_at": updated_at.strftime("%Y-%m-%dT%H:%M:%SZ") if updated_at is not None else "",
                "labels": labels,
            }
        )

    issue_rows.sort(
        key=lambda item: (
            0 if str(item.get("status_class")) == "blocked" else 1,
            -(float(item.get("issue_age_hours") or 0.0)),
            int(item.get("number") or 0),
        )
    )

    blocked_issues = [item for item in issue_rows if str(item.get("status_class")) == "blocked"]
    residual_issues = [item for item in issue_rows if str(item.get("status_class")) == "residual"]
    unknown_signal_total = sum(1 for item in issue_rows if str(item.get("signal_id") or "unknown") == "unknown")
    oldest_open_issue_age_hours = max(
        [float(item.get("issue_age_hours") or 0.0) for item in issue_rows],
        default=None,
    )

    criteria = [
        {
            "name": "report_generated",
            "passed": True,
            "details": f"open_ci_drift_issues_total={len(issue_rows)}",
        }
    ]
    failed_criteria = [item for item in criteria if not bool(item.get("passed"))]
    success = len(failed_criteria) == 0
    generated_at_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    markdown = _build_markdown_summary(
        generated_at_utc=generated_at_utc,
        open_issues_total=int(len(issue_rows)),
        blocked_total=int(len(blocked_issues)),
        residual_total=int(len(residual_issues)),
        unknown_signal_total=int(unknown_signal_total),
        oldest_open_issue_age_hours=oldest_open_issue_age_hours,
        issues=issue_rows,
    )

    report = {
        "label": label,
        "success": bool(success),
        "config": {
            "repo": repo,
            "blocked_age_hours": float(blocked_age_hours),
            "per_page": int(per_page),
            "issues_file": str(issues_file) if issues_file is not None else None,
            "output_file": str(output_file),
            "markdown_output_file": str(markdown_output_file),
            "blocking_signal_ids": sorted(BLOCKING_SIGNAL_IDS),
            "residual_signal_ids": sorted(RESIDUAL_SIGNAL_IDS),
        },
        "metrics": {
            "open_ci_drift_issues_total": int(len(issue_rows)),
            "blocked_issues_total": int(len(blocked_issues)),
            "residual_issues_total": int(len(residual_issues)),
            "unknown_signal_issues_total": int(unknown_signal_total),
            "oldest_open_issue_age_hours": (
                round(float(oldest_open_issue_age_hours), 3)
                if oldest_open_issue_age_hours is not None
                else None
            ),
            "criteria_failed": int(len(failed_criteria)),
        },
        "criteria": criteria,
        "failed_criteria": failed_criteria,
        "decision": {
            "attention_required": bool(len(blocked_issues) > 0),
            "recommended_action": (
                "ci_drift_blocked_incident_review_required"
                if len(blocked_issues) > 0
                else (
                    "ci_drift_residual_monitoring"
                    if len(residual_issues) > 0
                    else "ci_drift_clear"
                )
            ),
        },
        "issues": issue_rows,
        "markdown_summary": markdown,
        "generated_at_utc": generated_at_utc,
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }

    output_file.parent.mkdir(parents=True, exist_ok=True)
    markdown_output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2), encoding="utf-8")
    markdown_output_file.write_text(markdown, encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build a daily CI-drift issue status report with blocked/residual classification and issue age signals."
        )
    )
    parser.add_argument("--label", default="master-ci-drift-status-report")
    parser.add_argument("--repo", default="donatomaurizio99-collab/GOC")
    parser.add_argument("--blocked-age-hours", type=float, default=24.0)
    parser.add_argument("--per-page", type=int, default=100)
    parser.add_argument("--issues-file")
    parser.add_argument("--output-file", default="artifacts/master-ci-drift-status-report.json")
    parser.add_argument("--markdown-output-file", default="artifacts/master-ci-drift-status-report.md")
    args = parser.parse_args(argv)

    if int(args.per_page) <= 0:
        print("[master-ci-drift-status-report] ERROR: --per-page must be > 0.", file=sys.stderr)
        return 2
    if float(args.blocked_age_hours) <= 0:
        print("[master-ci-drift-status-report] ERROR: --blocked-age-hours must be > 0.", file=sys.stderr)
        return 2

    try:
        report = run_master_ci_drift_status_report(
            label=str(args.label),
            repo=str(args.repo),
            blocked_age_hours=float(args.blocked_age_hours),
            per_page=int(args.per_page),
            issues_file=Path(str(args.issues_file)).expanduser() if args.issues_file else None,
            output_file=Path(str(args.output_file)).expanduser(),
            markdown_output_file=Path(str(args.markdown_output_file)).expanduser(),
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[master-ci-drift-status-report] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
