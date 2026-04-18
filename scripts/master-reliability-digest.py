from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _run_gh_api(path: str) -> dict[str, Any]:
    command = ["gh", "api", path]
    completed = subprocess.run(command, capture_output=True, text=True)
    _expect(
        completed.returncode == 0,
        f"gh api failed ({completed.returncode}) for '{path}': {completed.stderr.strip()}",
    )
    payload = json.loads(completed.stdout)
    _expect(isinstance(payload, dict), f"Expected JSON object from gh api path '{path}'.")
    return payload


def _run_gh_api_bytes(path: str) -> bytes:
    command = ["gh", "api", path]
    completed = subprocess.run(command, capture_output=True)
    _expect(
        completed.returncode == 0,
        (
            f"gh api (bytes) failed ({completed.returncode}) for '{path}': "
            f"{completed.stderr.decode('utf-8', errors='replace').strip()}"
        ),
    )
    return completed.stdout


def _load_json_file(path: Path) -> dict[str, Any]:
    _expect(path.exists(), f"JSON file not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    _expect(isinstance(payload, dict), f"Expected JSON object in file: {path}")
    return payload


def _load_json_from_zip_bytes(data: bytes) -> dict[str, Any]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        candidate_names = sorted(name for name in archive.namelist() if name.lower().endswith(".json"))
        _expect(candidate_names, "Artifact zip did not contain a JSON file.")
        with archive.open(candidate_names[0]) as handle:
            payload = json.loads(handle.read().decode("utf-8-sig"))
    _expect(isinstance(payload, dict), "Expected JSON object payload in artifact zip.")
    return payload


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


def _format_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _seconds_between(started_at: datetime | None, completed_at: datetime | None) -> float | None:
    if started_at is None or completed_at is None:
        return None
    delta = (completed_at - started_at).total_seconds()
    return max(0.0, float(delta))


def _resolve_run_url(*, repo: str, run: dict[str, Any]) -> str:
    direct = str(run.get("html_url") or "").strip()
    if direct:
        return direct
    run_id = int(run.get("id") or 0)
    if run_id <= 0:
        return ""
    return f"https://github.com/{repo}/actions/runs/{run_id}"


def _fetch_workflow_id(*, repo: str, workflow_name: str) -> int:
    payload = _run_gh_api(f"repos/{repo}/actions/workflows?per_page=100")
    workflows = payload.get("workflows") or []
    _expect(isinstance(workflows, list), "Invalid workflows payload format.")
    for workflow in workflows:
        if not isinstance(workflow, dict):
            continue
        if str(workflow.get("name") or "").strip() == workflow_name:
            workflow_id = int(workflow.get("id") or 0)
            _expect(workflow_id > 0, f"Workflow '{workflow_name}' has invalid id.")
            return workflow_id
    raise RuntimeError(f"Workflow '{workflow_name}' not found in repo '{repo}'.")


def _fetch_runs_from_github(
    *,
    repo: str,
    branch: str,
    workflow_name: str,
    per_page: int,
) -> list[dict[str, Any]]:
    workflow_id = _fetch_workflow_id(repo=repo, workflow_name=workflow_name)
    payload = _run_gh_api(
        f"repos/{repo}/actions/workflows/{workflow_id}/runs?branch={branch}&status=completed&per_page={per_page}"
    )
    runs = payload.get("workflow_runs") or []
    _expect(isinstance(runs, list), f"Invalid workflow runs payload for workflow '{workflow_name}'.")
    return [item for item in runs if isinstance(item, dict)]


def _fetch_run_jobs_from_github(*, repo: str, run_id: int) -> list[dict[str, Any]]:
    payload = _run_gh_api(f"repos/{repo}/actions/runs/{run_id}/jobs?per_page=100")
    jobs = payload.get("jobs") or []
    _expect(isinstance(jobs, list), f"Invalid jobs payload format for run {run_id}.")
    return [item for item in jobs if isinstance(item, dict)]


def _fetch_run_artifacts_from_github(*, repo: str, run_id: int) -> list[dict[str, Any]]:
    payload = _run_gh_api(f"repos/{repo}/actions/runs/{run_id}/artifacts?per_page=100")
    artifacts = payload.get("artifacts") or []
    _expect(isinstance(artifacts, list), f"Invalid artifacts payload format for run {run_id}.")
    return [item for item in artifacts if isinstance(item, dict)]


def _load_runs_fixture(path: Path, *, workflow_name: str) -> list[dict[str, Any]]:
    payload = _load_json_file(path)
    if isinstance(payload.get("workflow_runs"), list):
        runs = payload.get("workflow_runs") or []
    elif isinstance(payload.get("workflow_runs"), dict):
        runs = (payload.get("workflow_runs") or {}).get(workflow_name) or []
    else:
        runs = payload.get("runs") or []
    _expect(isinstance(runs, list), f"Invalid runs fixture payload in {path}")
    return [item for item in runs if isinstance(item, dict)]


def _load_jobs_fixture(*, jobs_dir: Path, run_id: int) -> list[dict[str, Any]]:
    candidate_paths = [
        jobs_dir / f"{run_id}.json",
        jobs_dir / f"run-{run_id}.json",
        jobs_dir / f"run-{run_id}-jobs.json",
    ]
    file_path = next((path for path in candidate_paths if path.exists()), None)
    _expect(file_path is not None, f"No jobs fixture file found for run id {run_id} in {jobs_dir}")
    payload = _load_json_file(file_path)
    jobs = payload.get("jobs") or []
    _expect(isinstance(jobs, list), f"Invalid jobs fixture payload format for run {run_id}: {file_path}")
    return [item for item in jobs if isinstance(item, dict)]


def _load_guard_issue_upsert_report_fixture(*, reports_dir: Path, run_id: int) -> dict[str, Any] | None:
    candidate_paths = [
        reports_dir / f"{run_id}.json",
        reports_dir / f"run-{run_id}.json",
        reports_dir / f"run-{run_id}-issue-upsert.json",
        reports_dir / f"master-guard-workflow-health-issue-upsert-{run_id}.json",
    ]
    file_path = next((path for path in candidate_paths if path.exists()), None)
    if file_path is None:
        return None
    return _load_json_file(file_path)


def _load_guard_issue_upsert_report_from_github(
    *,
    repo: str,
    run_id: int,
    artifact_name: str,
) -> dict[str, Any] | None:
    artifacts = _fetch_run_artifacts_from_github(repo=repo, run_id=run_id)
    for artifact in artifacts:
        if str(artifact.get("name") or "") != artifact_name:
            continue
        if bool(artifact.get("expired")):
            return None
        download_url = str(artifact.get("archive_download_url") or "").strip()
        if not download_url:
            return None
        archive_bytes = _run_gh_api_bytes(download_url)
        return _load_json_from_zip_bytes(archive_bytes)
    return None


def _build_release_gate_samples(
    *,
    repo: str,
    runs: list[dict[str, Any]],
    release_gate_job_name: str,
    trend_runs: int,
    jobs_dir: Path | None,
) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    for run in runs[:trend_runs]:
        run_id = int(run.get("id") or 0)
        if run_id <= 0:
            continue
        jobs = (
            _load_jobs_fixture(jobs_dir=jobs_dir, run_id=run_id)
            if jobs_dir is not None
            else _fetch_run_jobs_from_github(repo=repo, run_id=run_id)
        )
        matching_jobs = [job for job in jobs if str(job.get("name") or "").strip() == release_gate_job_name]
        durations: list[float] = []
        for job in matching_jobs:
            started_at = _parse_utc_timestamp(str(job.get("started_at") or ""))
            completed_at = _parse_utc_timestamp(str(job.get("completed_at") or ""))
            duration = _seconds_between(started_at, completed_at)
            if duration is not None:
                durations.append(float(duration))
        selected_duration = max(durations) if durations else None
        samples.append(
            {
                "run_id": run_id,
                "run_url": _resolve_run_url(repo=repo, run=run),
                "updated_at": str(run.get("updated_at") or ""),
                "release_gate_duration_seconds": (
                    round(float(selected_duration), 3) if selected_duration is not None else None
                ),
                "release_gate_job_matches_total": int(len(matching_jobs)),
            }
        )
    return samples


def _build_guard_samples(*, repo: str, runs: list[dict[str, Any]], trend_runs: int) -> list[dict[str, Any]]:
    return [
        {
            "run_id": int(run.get("id") or 0),
            "run_url": _resolve_run_url(repo=repo, run=run),
            "status": str(run.get("status") or ""),
            "conclusion": str(run.get("conclusion") or ""),
            "updated_at": str(run.get("updated_at") or ""),
        }
        for run in runs[:trend_runs]
        if int(run.get("id") or 0) > 0
    ]


def _resolve_issue_upsert_sample_timestamp(sample: dict[str, Any]) -> datetime | None:
    generated = _parse_utc_timestamp(str(sample.get("generated_at_utc") or ""))
    if generated is not None:
        return generated
    run_updated = _parse_utc_timestamp(str(sample.get("run_updated_at") or ""))
    if run_updated is not None:
        return run_updated
    return None


def _compute_mttr_summary(
    *,
    issue_upsert_samples: list[dict[str, Any]],
    evaluation_now: datetime,
) -> dict[str, Any]:
    sorted_samples = sorted(
        [item for item in issue_upsert_samples if isinstance(item, dict)],
        key=lambda item: _resolve_issue_upsert_sample_timestamp(item) or datetime.min.replace(tzinfo=timezone.utc),
    )

    mttr_hours_samples: list[float] = []
    incident_opened_at: datetime | None = None
    incident_opened_run_id: int | None = None
    for sample in sorted_samples:
        sample_time = _resolve_issue_upsert_sample_timestamp(sample)
        if sample_time is None:
            continue
        issue_action = str(sample.get("issue_action") or "none")
        alert_triggered = bool(sample.get("alert_triggered"))

        if alert_triggered and issue_action in {"created", "reopened"}:
            incident_opened_at = sample_time
            incident_opened_run_id = int(sample.get("run_id") or 0)
            continue

        if (
            (not alert_triggered)
            and issue_action == "closed"
            and incident_opened_at is not None
            and sample_time >= incident_opened_at
        ):
            mttr_hours = (sample_time - incident_opened_at).total_seconds() / 3600.0
            mttr_hours_samples.append(float(max(0.0, mttr_hours)))
            incident_opened_at = None
            incident_opened_run_id = None

    mttr_trend = "insufficient_data"
    mttr_avg_hours = None
    mttr_last_hours = None
    if mttr_hours_samples:
        mttr_avg_hours = round(sum(mttr_hours_samples) / len(mttr_hours_samples), 3)
        mttr_last_hours = round(float(mttr_hours_samples[-1]), 3)
    if len(mttr_hours_samples) >= 2:
        previous_avg = sum(mttr_hours_samples[:-1]) / max(1, len(mttr_hours_samples) - 1)
        latest = float(mttr_hours_samples[-1])
        if latest <= previous_avg * 0.9:
            mttr_trend = "improving"
        elif latest >= previous_avg * 1.1:
            mttr_trend = "degrading"
        else:
            mttr_trend = "stable"
    elif len(mttr_hours_samples) == 1:
        mttr_trend = "stable"

    open_incident_age_hours = (
        round(float((evaluation_now - incident_opened_at).total_seconds()) / 3600.0, 3)
        if incident_opened_at is not None and evaluation_now >= incident_opened_at
        else None
    )
    return {
        "mttr_hours_samples": [round(float(item), 3) for item in mttr_hours_samples],
        "mttr_samples_total": int(len(mttr_hours_samples)),
        "mttr_avg_hours": mttr_avg_hours,
        "mttr_last_hours": mttr_last_hours,
        "mttr_trend": mttr_trend,
        "open_incident_age_hours": open_incident_age_hours,
        "open_incident_started_at_utc": _format_utc(incident_opened_at),
        "open_incident_started_run_id": int(incident_opened_run_id or 0),
    }


def _build_reliability_markdown(
    *,
    generated_at_utc: str,
    release_gate_warning_seconds: int,
    warning_sustained_runs: int,
    release_gate_warning_triggered: bool,
    release_gate_consecutive_over_threshold: int,
    release_gate_samples: list[dict[str, Any]],
    guard_samples: list[dict[str, Any]],
    guard_non_success_runs: list[dict[str, Any]],
    issue_upsert_samples: list[dict[str, Any]],
    upsert_artifacts_missing_total: int,
    active_comment_suppressed_total_sum: int,
    mttr_samples_total: int,
    mttr_avg_hours: float | None,
    mttr_last_hours: float | None,
    mttr_trend: str,
    mttr_open_incident_age_hours: float | None,
) -> str:
    latest_release_sample = release_gate_samples[0] if release_gate_samples else {}
    latest_guard_degraded = guard_non_success_runs[0] if guard_non_success_runs else {}
    latest_upsert_sample = issue_upsert_samples[0] if issue_upsert_samples else {}

    release_duration_text = (
        f"{latest_release_sample.get('release_gate_duration_seconds')}s"
        if latest_release_sample and latest_release_sample.get("release_gate_duration_seconds") is not None
        else "unknown"
    )
    release_run_url = str(latest_release_sample.get("run_url") or "")
    release_run_id = int(latest_release_sample.get("run_id") or 0)

    degraded_run_url = str(latest_guard_degraded.get("run_url") or "")
    degraded_run_id = int(latest_guard_degraded.get("run_id") or 0)
    degraded_conclusion = str(latest_guard_degraded.get("conclusion") or "")

    latest_upsert_run_id = int(latest_upsert_sample.get("run_id") or 0)
    latest_upsert_issue_action = str(latest_upsert_sample.get("issue_action") or "none")
    latest_upsert_suppressed = int(latest_upsert_sample.get("active_comment_suppressed_total") or 0)

    lines = [
        "# Master Reliability Digest",
        "",
        f"- Generated at (UTC): `{generated_at_utc}`",
        "",
        "## Release Gate Runtime Trend",
        f"- Samples: {len(release_gate_samples)}",
        (
            f"- Latest Release Gate runtime: `{release_duration_text}` on run "
            f"`#{release_run_id}` ({release_run_url})"
            if release_run_id > 0 and release_run_url
            else "- Latest Release Gate runtime: unknown"
        ),
        (
            f"- Early-warning threshold: `{release_gate_warning_seconds}s` sustained for "
            f"`{warning_sustained_runs}` runs (current streak={release_gate_consecutive_over_threshold})"
        ),
        f"- Runtime warning triggered: {'yes' if release_gate_warning_triggered else 'no'}",
        "",
        "## Guard Workflow Degradations",
        f"- Guard samples: {len(guard_samples)}",
        f"- Non-success guard runs: {len(guard_non_success_runs)}",
        (
            f"- Latest degraded guard run: `#{degraded_run_id}` ({degraded_run_url}) "
            f"conclusion=`{degraded_conclusion or 'unknown'}`"
            if degraded_run_id > 0 and degraded_run_url
            else "- Latest degraded guard run: none"
        ),
        "",
        "## Active Alert Cooldown Signal",
        f"- Parsed issue-upsert reports: {len(issue_upsert_samples)}",
        f"- Missing issue-upsert artifacts/reports: {upsert_artifacts_missing_total}",
        f"- `active_comment_suppressed_total` sum: {active_comment_suppressed_total_sum}",
        (
            f"- Latest upsert sample: run `#{latest_upsert_run_id}`, issue_action=`{latest_upsert_issue_action}`, "
            f"active_comment_suppressed_total={latest_upsert_suppressed}"
            if latest_upsert_run_id > 0
            else "- Latest upsert sample: none"
        ),
        "",
        "## Alert MTTR Trend",
        f"- MTTR samples: {mttr_samples_total}",
        (
            f"- MTTR average: `{mttr_avg_hours}h`"
            if mttr_avg_hours is not None
            else "- MTTR average: unknown"
        ),
        (
            f"- Latest MTTR sample: `{mttr_last_hours}h`"
            if mttr_last_hours is not None
            else "- Latest MTTR sample: unknown"
        ),
        f"- MTTR trend: `{mttr_trend}`",
        (
            f"- Open incident age: `{mttr_open_incident_age_hours}h`"
            if mttr_open_incident_age_hours is not None
            else "- Open incident age: none"
        ),
    ]
    return "\n".join(lines).strip() + "\n"


def run_master_reliability_digest(
    *,
    label: str,
    repo: str,
    branch: str,
    ci_workflow_name: str,
    guard_workflow_name: str,
    release_gate_job_name: str,
    issue_upsert_artifact_name: str,
    ci_per_page: int,
    guard_per_page: int,
    trend_runs: int,
    guard_trend_runs: int,
    release_gate_warning_seconds: int,
    warning_sustained_runs: int,
    fail_on_warning: bool,
    ci_runs_file: Path | None,
    ci_jobs_dir: Path | None,
    guard_runs_file: Path | None,
    guard_upsert_reports_dir: Path | None,
    output_file: Path,
    markdown_output_file: Path,
) -> dict[str, Any]:
    _expect(ci_per_page > 0, "ci_per_page must be > 0.")
    _expect(guard_per_page > 0, "guard_per_page must be > 0.")
    _expect(trend_runs > 0, "trend_runs must be > 0.")
    _expect(guard_trend_runs > 0, "guard_trend_runs must be > 0.")
    _expect(release_gate_warning_seconds > 0, "release_gate_warning_seconds must be > 0.")
    _expect(warning_sustained_runs > 0, "warning_sustained_runs must be > 0.")

    started = time.perf_counter()
    ci_runs = (
        _load_runs_fixture(ci_runs_file, workflow_name=ci_workflow_name)
        if ci_runs_file is not None
        else _fetch_runs_from_github(
            repo=repo,
            branch=branch,
            workflow_name=ci_workflow_name,
            per_page=ci_per_page,
        )
    )
    guard_runs = (
        _load_runs_fixture(guard_runs_file, workflow_name=guard_workflow_name)
        if guard_runs_file is not None
        else _fetch_runs_from_github(
            repo=repo,
            branch=branch,
            workflow_name=guard_workflow_name,
            per_page=guard_per_page,
        )
    )

    release_gate_samples = _build_release_gate_samples(
        repo=repo,
        runs=ci_runs,
        release_gate_job_name=release_gate_job_name,
        trend_runs=trend_runs,
        jobs_dir=ci_jobs_dir,
    )
    release_gate_duration_samples = [
        item for item in release_gate_samples if item.get("release_gate_duration_seconds") is not None
    ]

    guard_samples = _build_guard_samples(repo=repo, runs=guard_runs, trend_runs=guard_trend_runs)
    guard_non_success_runs = [
        item
        for item in guard_samples
        if str(item.get("status") or "") == "completed"
        and str(item.get("conclusion") or "").strip().lower() != "success"
    ]

    release_gate_consecutive_over_threshold = 0
    for sample in release_gate_duration_samples:
        duration_seconds = float(sample.get("release_gate_duration_seconds") or 0.0)
        if duration_seconds >= float(release_gate_warning_seconds):
            release_gate_consecutive_over_threshold += 1
        else:
            break

    release_gate_warning_triggered = bool(
        release_gate_consecutive_over_threshold >= warning_sustained_runs
        and len(release_gate_duration_samples) >= warning_sustained_runs
    )
    if release_gate_warning_triggered:
        print(
            "::warning::Master reliability digest detected sustained Release Gate slowdown: "
            f"{release_gate_consecutive_over_threshold} consecutive runs >= {release_gate_warning_seconds}s."
        )

    issue_upsert_samples: list[dict[str, Any]] = []
    upsert_artifacts_missing_total = 0
    active_comment_suppressed_total_sum = 0
    for guard_run in guard_samples:
        run_id = int(guard_run.get("run_id") or 0)
        if run_id <= 0:
            continue
        issue_upsert_payload = (
            _load_guard_issue_upsert_report_fixture(reports_dir=guard_upsert_reports_dir, run_id=run_id)
            if guard_upsert_reports_dir is not None
            else _load_guard_issue_upsert_report_from_github(
                repo=repo,
                run_id=run_id,
                artifact_name=issue_upsert_artifact_name,
            )
        )
        if issue_upsert_payload is None:
            upsert_artifacts_missing_total += 1
            continue
        issue_metrics = (
            issue_upsert_payload.get("metrics") if isinstance(issue_upsert_payload.get("metrics"), dict) else {}
        )
        issue_decision = (
            issue_upsert_payload.get("decision") if isinstance(issue_upsert_payload.get("decision"), dict) else {}
        )
        active_comment_suppressed_total = int(issue_metrics.get("active_comment_suppressed_total") or 0)
        active_comment_suppressed_total_sum += active_comment_suppressed_total
        issue_upsert_samples.append(
            {
                "run_id": run_id,
                "run_updated_at": str(guard_run.get("updated_at") or ""),
                "generated_at_utc": str(issue_upsert_payload.get("generated_at_utc") or ""),
                "active_comment_suppressed_total": active_comment_suppressed_total,
                "issue_action": str(issue_decision.get("issue_action") or "none"),
                "alert_triggered": bool(issue_decision.get("alert_triggered")),
            }
        )

    mttr_summary = _compute_mttr_summary(
        issue_upsert_samples=issue_upsert_samples,
        evaluation_now=datetime.now(timezone.utc),
    )

    avg_release_gate_seconds = (
        round(
            sum(float(item.get("release_gate_duration_seconds") or 0.0) for item in release_gate_duration_samples)
            / len(release_gate_duration_samples),
            3,
        )
        if release_gate_duration_samples
        else None
    )
    max_release_gate_seconds = (
        max(float(item.get("release_gate_duration_seconds") or 0.0) for item in release_gate_duration_samples)
        if release_gate_duration_samples
        else None
    )

    criteria = [
        {
            "name": "release_gate_samples_present",
            "passed": bool(len(release_gate_duration_samples) > 0),
            "details": f"release_gate_duration_samples_total={len(release_gate_duration_samples)}",
        },
        {
            "name": "guard_samples_present",
            "passed": bool(len(guard_samples) > 0),
            "details": f"guard_samples_total={len(guard_samples)}",
        },
        {
            "name": "warning_policy",
            "passed": bool(
                (not fail_on_warning)
                or (not release_gate_warning_triggered and len(guard_non_success_runs) == 0)
            ),
            "details": (
                f"fail_on_warning={fail_on_warning}, "
                f"release_gate_warning_triggered={release_gate_warning_triggered}, "
                f"guard_non_success_total={len(guard_non_success_runs)}"
            ),
        },
    ]
    failed_criteria = [item for item in criteria if not bool(item.get("passed"))]
    success = len(failed_criteria) == 0

    latest_release_sample = release_gate_duration_samples[0] if release_gate_duration_samples else {}
    latest_guard_non_success = guard_non_success_runs[0] if guard_non_success_runs else {}
    if release_gate_warning_triggered and len(guard_non_success_runs) > 0:
        top_cause = "compound_runtime_and_guard_degradation"
        top_cause_detail = (
            "Sustained Release Gate slowdown and degraded guard workflow(s) observed simultaneously."
        )
    elif release_gate_warning_triggered:
        top_cause = "release_gate_runtime_sustained_over_threshold"
        top_cause_detail = (
            "Release Gate runtime stayed above threshold for sustained runs."
        )
    elif len(guard_non_success_runs) > 0:
        top_cause = "guard_workflow_degraded"
        top_cause_detail = "Guard workflow reported non-success conclusion."
    else:
        top_cause = "none"
        top_cause_detail = "No warning-level signal in digest window."

    generated_at_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    markdown = _build_reliability_markdown(
        generated_at_utc=generated_at_utc,
        release_gate_warning_seconds=release_gate_warning_seconds,
        warning_sustained_runs=warning_sustained_runs,
        release_gate_warning_triggered=release_gate_warning_triggered,
        release_gate_consecutive_over_threshold=release_gate_consecutive_over_threshold,
        release_gate_samples=release_gate_duration_samples,
        guard_samples=guard_samples,
        guard_non_success_runs=guard_non_success_runs,
        issue_upsert_samples=issue_upsert_samples,
        upsert_artifacts_missing_total=upsert_artifacts_missing_total,
        active_comment_suppressed_total_sum=active_comment_suppressed_total_sum,
        mttr_samples_total=int(mttr_summary["mttr_samples_total"]),
        mttr_avg_hours=(
            float(mttr_summary["mttr_avg_hours"])
            if mttr_summary.get("mttr_avg_hours") is not None
            else None
        ),
        mttr_last_hours=(
            float(mttr_summary["mttr_last_hours"])
            if mttr_summary.get("mttr_last_hours") is not None
            else None
        ),
        mttr_trend=str(mttr_summary.get("mttr_trend") or "insufficient_data"),
        mttr_open_incident_age_hours=(
            float(mttr_summary["open_incident_age_hours"])
            if mttr_summary.get("open_incident_age_hours") is not None
            else None
        ),
    )

    report = {
        "label": label,
        "success": bool(success),
        "config": {
            "repo": repo,
            "branch": branch,
            "ci_workflow_name": ci_workflow_name,
            "guard_workflow_name": guard_workflow_name,
            "release_gate_job_name": release_gate_job_name,
            "issue_upsert_artifact_name": issue_upsert_artifact_name,
            "ci_per_page": int(ci_per_page),
            "guard_per_page": int(guard_per_page),
            "trend_runs": int(trend_runs),
            "guard_trend_runs": int(guard_trend_runs),
            "release_gate_warning_seconds": int(release_gate_warning_seconds),
            "warning_sustained_runs": int(warning_sustained_runs),
            "fail_on_warning": bool(fail_on_warning),
            "ci_runs_file": str(ci_runs_file) if ci_runs_file is not None else None,
            "ci_jobs_dir": str(ci_jobs_dir) if ci_jobs_dir is not None else None,
            "guard_runs_file": str(guard_runs_file) if guard_runs_file is not None else None,
            "guard_upsert_reports_dir": str(guard_upsert_reports_dir) if guard_upsert_reports_dir is not None else None,
            "output_file": str(output_file),
            "markdown_output_file": str(markdown_output_file),
        },
        "metrics": {
            "release_gate_duration_samples_total": int(len(release_gate_duration_samples)),
            "release_gate_consecutive_over_threshold": int(release_gate_consecutive_over_threshold),
            "release_gate_warning_triggered": 1 if release_gate_warning_triggered else 0,
            "release_gate_duration_avg_seconds": avg_release_gate_seconds,
            "release_gate_duration_max_seconds": (
                round(float(max_release_gate_seconds), 3) if max_release_gate_seconds is not None else None
            ),
            "guard_samples_total": int(len(guard_samples)),
            "guard_non_success_total": int(len(guard_non_success_runs)),
            "issue_upsert_samples_total": int(len(issue_upsert_samples)),
            "upsert_artifacts_missing_total": int(upsert_artifacts_missing_total),
            "active_comment_suppressed_total_sum": int(active_comment_suppressed_total_sum),
            "mttr_samples_total": int(mttr_summary["mttr_samples_total"]),
            "mttr_avg_hours": (
                float(mttr_summary["mttr_avg_hours"])
                if mttr_summary.get("mttr_avg_hours") is not None
                else None
            ),
            "mttr_last_hours": (
                float(mttr_summary["mttr_last_hours"])
                if mttr_summary.get("mttr_last_hours") is not None
                else None
            ),
            "mttr_trend": str(mttr_summary.get("mttr_trend") or "insufficient_data"),
            "mttr_open_incident_age_hours": (
                float(mttr_summary["open_incident_age_hours"])
                if mttr_summary.get("open_incident_age_hours") is not None
                else None
            ),
            "criteria_failed": int(len(failed_criteria)),
        },
        "criteria": criteria,
        "failed_criteria": failed_criteria,
        "decision": {
            "release_gate_warning_triggered": bool(release_gate_warning_triggered),
            "guard_health_degraded": bool(len(guard_non_success_runs) > 0),
            "top_cause": top_cause,
            "top_cause_detail": top_cause_detail,
            "mttr_trend": str(mttr_summary.get("mttr_trend") or "insufficient_data"),
            "latest_release_sample_run_url": str(latest_release_sample.get("run_url") or ""),
            "latest_degraded_guard_run_url": str(latest_guard_non_success.get("run_url") or ""),
            "warning_level": (
                "warning"
                if release_gate_warning_triggered or len(guard_non_success_runs) > 0
                else "healthy"
            ),
            "recommended_action": (
                "investigate_master_reliability_regression"
                if release_gate_warning_triggered or len(guard_non_success_runs) > 0
                else "master_reliability_stable"
            ),
        },
        "release_gate_samples": release_gate_duration_samples,
        "guard_runs": guard_samples,
        "guard_non_success_runs": guard_non_success_runs,
        "issue_upsert_samples": issue_upsert_samples,
        "mttr": mttr_summary,
        "markdown_summary": markdown,
        "generated_at_utc": generated_at_utc,
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }

    output_file.parent.mkdir(parents=True, exist_ok=True)
    markdown_output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2), encoding="utf-8")
    markdown_output_file.write_text(markdown, encoding="utf-8")

    if not success:
        raise RuntimeError(f"Master reliability digest failed: {json.dumps(report, sort_keys=True)}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build a weekly master reliability digest with release-gate runtime trend, "
            "guard degradation summary, and active-alert cooldown suppression metrics."
        )
    )
    parser.add_argument("--label", default="master-reliability-digest")
    parser.add_argument("--repo", default="donatomaurizio99-collab/GOC")
    parser.add_argument("--branch", default="master")
    parser.add_argument("--ci-workflow-name", default="CI")
    parser.add_argument("--guard-workflow-name", default="Master Guard Workflow Health")
    parser.add_argument("--release-gate-job-name", default="Release Gate (Windows)")
    parser.add_argument("--issue-upsert-artifact-name", default="master-guard-workflow-health-issue-upsert")
    parser.add_argument("--ci-per-page", type=int, default=30)
    parser.add_argument("--guard-per-page", type=int, default=30)
    parser.add_argument("--trend-runs", type=int, default=10)
    parser.add_argument("--guard-trend-runs", type=int, default=10)
    parser.add_argument("--release-gate-warning-seconds", type=int, default=540)
    parser.add_argument("--warning-sustained-runs", type=int, default=3)
    parser.add_argument("--ci-runs-file")
    parser.add_argument("--ci-jobs-dir")
    parser.add_argument("--guard-runs-file")
    parser.add_argument("--guard-upsert-reports-dir")
    parser.add_argument("--fail-on-warning", action="store_true")
    parser.add_argument("--output-file", default="artifacts/master-reliability-digest.json")
    parser.add_argument("--markdown-output-file", default="artifacts/master-reliability-digest.md")
    args = parser.parse_args(argv)

    if int(args.ci_per_page) <= 0:
        print("[master-reliability-digest] ERROR: --ci-per-page must be > 0.", file=sys.stderr)
        return 2
    if int(args.guard_per_page) <= 0:
        print("[master-reliability-digest] ERROR: --guard-per-page must be > 0.", file=sys.stderr)
        return 2
    if int(args.trend_runs) <= 0:
        print("[master-reliability-digest] ERROR: --trend-runs must be > 0.", file=sys.stderr)
        return 2
    if int(args.guard_trend_runs) <= 0:
        print("[master-reliability-digest] ERROR: --guard-trend-runs must be > 0.", file=sys.stderr)
        return 2
    if int(args.release_gate_warning_seconds) <= 0:
        print("[master-reliability-digest] ERROR: --release-gate-warning-seconds must be > 0.", file=sys.stderr)
        return 2
    if int(args.warning_sustained_runs) <= 0:
        print("[master-reliability-digest] ERROR: --warning-sustained-runs must be > 0.", file=sys.stderr)
        return 2

    try:
        report = run_master_reliability_digest(
            label=str(args.label),
            repo=str(args.repo),
            branch=str(args.branch),
            ci_workflow_name=str(args.ci_workflow_name),
            guard_workflow_name=str(args.guard_workflow_name),
            release_gate_job_name=str(args.release_gate_job_name),
            issue_upsert_artifact_name=str(args.issue_upsert_artifact_name),
            ci_per_page=int(args.ci_per_page),
            guard_per_page=int(args.guard_per_page),
            trend_runs=int(args.trend_runs),
            guard_trend_runs=int(args.guard_trend_runs),
            release_gate_warning_seconds=int(args.release_gate_warning_seconds),
            warning_sustained_runs=int(args.warning_sustained_runs),
            fail_on_warning=bool(args.fail_on_warning),
            ci_runs_file=Path(str(args.ci_runs_file)).expanduser() if args.ci_runs_file else None,
            ci_jobs_dir=Path(str(args.ci_jobs_dir)).expanduser() if args.ci_jobs_dir else None,
            guard_runs_file=Path(str(args.guard_runs_file)).expanduser() if args.guard_runs_file else None,
            guard_upsert_reports_dir=(
                Path(str(args.guard_upsert_reports_dir)).expanduser() if args.guard_upsert_reports_dir else None
            ),
            output_file=Path(str(args.output_file)).expanduser(),
            markdown_output_file=Path(str(args.markdown_output_file)).expanduser(),
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[master-reliability-digest] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
