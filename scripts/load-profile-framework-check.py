from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from goal_ops_console.config import Settings
from goal_ops_console.main import create_app

TERMINAL_RUN_STATES = {"succeeded", "failed", "timed_out", "cancelled"}


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _percentile(values: list[float], p: float) -> float:
    _expect(values, "Cannot compute percentile for empty sequence.")
    sorted_values = sorted(values)
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    rank = (len(sorted_values) - 1) * max(0.0, min(100.0, float(p))) / 100.0
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return float(sorted_values[lower])
    weight = rank - lower
    return float(sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight)


def _read_json_file(path: Path) -> dict[str, Any]:
    _expect(path.exists(), f"Required file not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    _expect(isinstance(payload, dict), f"Expected JSON object in {path}")
    return payload


def _find_profile(catalog: dict[str, Any], *, profile_name: str, profile_version: str) -> dict[str, Any]:
    profiles = catalog.get("profiles")
    _expect(isinstance(profiles, list) and profiles, "Profile catalog has no profiles.")

    normalized_name = str(profile_name).strip()
    normalized_version = str(profile_version).strip()
    _expect(bool(normalized_name), "Profile name must not be empty.")
    _expect(bool(normalized_version), "Profile version must not be empty.")

    for item in profiles:
        if not isinstance(item, dict):
            continue
        if str(item.get("name") or "") == normalized_name and str(item.get("version") or "") == normalized_version:
            return item

    raise RuntimeError(
        f"Profile not found in catalog: name={normalized_name!r} version={normalized_version!r}"
    )


def _validate_profile_shape(profile: dict[str, Any]) -> None:
    _expect(bool(str(profile.get("name") or "").strip()), "Profile field 'name' is required.")
    _expect(bool(str(profile.get("version") or "").strip()), "Profile field 'version' is required.")

    stages = profile.get("stages")
    _expect(isinstance(stages, list) and stages, "Profile field 'stages' must be a non-empty list.")

    for index, stage in enumerate(stages):
        _expect(isinstance(stage, dict), f"Stage #{index + 1} is not an object.")
        _expect(bool(str(stage.get("name") or "").strip()), f"Stage #{index + 1} missing 'name'.")
        cycles = int(stage.get("cycles") or 0)
        _expect(cycles > 0, f"Stage '{stage.get('name')}' has invalid cycles={cycles}.")

        workflow_every = int(stage.get("workflow_start_every_cycles") or 0)
        _expect(workflow_every >= 0, f"Stage '{stage.get('name')}' has invalid workflow_start_every_cycles={workflow_every}.")

        drain_batch_size = int(stage.get("drain_batch_size") or 0)
        _expect(drain_batch_size > 0, f"Stage '{stage.get('name')}' has invalid drain_batch_size={drain_batch_size}.")

        readiness_every = int(stage.get("readiness_check_every_cycles") or 0)
        _expect(readiness_every >= 0, f"Stage '{stage.get('name')}' has invalid readiness_check_every_cycles={readiness_every}.")

    budgets = profile.get("budgets")
    _expect(isinstance(budgets, dict), "Profile field 'budgets' must be an object.")
    _expect(float(budgets.get("max_p95_latency_ms") or 0) > 0, "Budget 'max_p95_latency_ms' must be > 0.")
    _expect(float(budgets.get("max_p99_latency_ms") or 0) > 0, "Budget 'max_p99_latency_ms' must be > 0.")
    _expect(float(budgets.get("max_max_latency_ms") or 0) > 0, "Budget 'max_max_latency_ms' must be > 0.")
    _expect(
        float(budgets.get("max_http_429_rate_percent") or -1) >= 0,
        "Budget 'max_http_429_rate_percent' must be >= 0.",
    )
    _expect(float(budgets.get("max_error_rate_percent") or -1) >= 0, "Budget 'max_error_rate_percent' must be >= 0.")
    _expect(int(budgets.get("min_total_requests") or 0) > 0, "Budget 'min_total_requests' must be > 0.")


def _wait_for_runs_terminal(client: TestClient, run_ids: list[str], timeout_seconds: float) -> None:
    if not run_ids:
        return
    deadline = time.time() + max(1.0, float(timeout_seconds))
    pending = set(run_ids)
    while time.time() < deadline and pending:
        finished: list[str] = []
        for run_id in list(pending):
            response = client.get(f"/workflows/runs/{run_id}")
            if response.status_code != 200:
                continue
            status = str(response.json().get("run", {}).get("status") or "")
            if status in TERMINAL_RUN_STATES:
                finished.append(run_id)
        for run_id in finished:
            pending.discard(run_id)
        if pending:
            time.sleep(0.05)
    _expect(not pending, f"Workflow runs did not reach terminal state: {sorted(pending)}")


def _drain_consumer_until_empty(
    client: TestClient,
    *,
    consumer_id: str,
    drain_batch_size: int,
    timeout_seconds: float,
) -> int:
    processed_total = 0
    deadline = time.time() + max(1.0, float(timeout_seconds))
    while time.time() < deadline:
        snapshot = client.get("/system/backpressure")
        _expect(snapshot.status_code == 200, f"/system/backpressure failed: {snapshot.text}")
        pending = int(snapshot.json().get("pending_events") or 0)
        if pending == 0:
            return processed_total
        drained = client.post(
            f"/system/consumers/{consumer_id}/drain",
            params={"batch_size": int(drain_batch_size)},
        )
        _expect(drained.status_code == 200, f"Consumer drain failed: {drained.text}")
        processed_total += int(drained.json().get("processed_count") or 0)
        time.sleep(0.02)
    raise RuntimeError("Timed out while draining event backlog to zero.")


def _compute_rate_percent(*, count: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return (float(count) / float(total)) * 100.0


def run_check(
    *,
    label: str,
    deployment_profile: str,
    profile_file: Path,
    profile_name: str,
    profile_version: str,
) -> dict[str, Any]:
    started = time.perf_counter()
    profile_mode = str(deployment_profile).strip().lower() or "production"

    catalog = _read_json_file(profile_file)
    catalog_version = str(catalog.get("catalog_version") or "")
    _expect(bool(catalog_version.strip()), "Catalog field 'catalog_version' is required.")

    selected_profile = _find_profile(
        catalog,
        profile_name=profile_name,
        profile_version=profile_version,
    )
    _validate_profile_shape(selected_profile)

    stages = list(selected_profile["stages"])
    budgets = dict(selected_profile["budgets"])

    app = create_app(
        Settings(
            database_url=":memory:",
            workflow_worker_poll_interval_seconds=0.02,
            workflow_run_timeout_seconds=120,
        )
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        consumer_id = str(client.app.state.services.settings.consumer_id)
        latencies_ms: list[float] = []
        statuses = Counter()
        run_ids: list[str] = []
        transient_retry_count = 0
        stage_summaries: list[dict[str, Any]] = []
        cycle_counter = 0

        def _request(method: str, url: str, **kwargs):
            started_at = time.perf_counter()
            response = client.request(method, url, **kwargs)
            latencies_ms.append((time.perf_counter() - started_at) * 1000.0)
            statuses[response.status_code] += 1
            return response

        def _request_expect(
            method: str,
            url: str,
            expected_status: int,
            *,
            max_retries: int = 8,
            **kwargs,
        ):
            nonlocal transient_retry_count
            attempt = 0
            last_response = None
            while attempt <= max_retries:
                response = _request(method, url, **kwargs)
                last_response = response
                if response.status_code == expected_status:
                    return response
                if response.status_code >= 500:
                    transient_retry_count += 1
                    attempt += 1
                    time.sleep(min(0.2, 0.02 * attempt))
                    continue
                break
            _expect(
                False,
                (
                    f"Request failed for {method} {url}. "
                    f"expected={expected_status} observed={last_response.status_code if last_response else 'none'} "
                    f"body={last_response.text if last_response is not None else '<none>'}"
                ),
            )
            return last_response

        for stage in stages:
            stage_name = str(stage.get("name") or "").strip()
            stage_cycles = int(stage.get("cycles") or 0)
            stage_workflow_every = int(stage.get("workflow_start_every_cycles") or 0)
            stage_drain_batch_size = int(stage.get("drain_batch_size") or 0)
            stage_readiness_every = int(stage.get("readiness_check_every_cycles") or 0)

            stage_statuses = Counter()
            stage_latencies: list[float] = []
            stage_start = time.perf_counter()

            def _request_stage(method: str, url: str, **kwargs):
                response = _request(method, url, **kwargs)
                stage_statuses[response.status_code] += 1
                if latencies_ms:
                    stage_latencies.append(latencies_ms[-1])
                return response

            def _request_expect_stage(
                method: str,
                url: str,
                expected_status: int,
                *,
                max_retries: int = 8,
                **kwargs,
            ):
                nonlocal transient_retry_count
                attempt = 0
                last_response = None
                while attempt <= max_retries:
                    response = _request_stage(method, url, **kwargs)
                    last_response = response
                    if response.status_code == expected_status:
                        return response
                    if response.status_code >= 500:
                        transient_retry_count += 1
                        attempt += 1
                        time.sleep(min(0.2, 0.02 * attempt))
                        continue
                    break
                _expect(
                    False,
                    (
                        f"Stage request failed ({stage_name}) for {method} {url}. "
                        f"expected={expected_status} "
                        f"observed={last_response.status_code if last_response else 'none'} "
                        f"body={last_response.text if last_response is not None else '<none>'}"
                    ),
                )
                return last_response

            for stage_cycle in range(1, stage_cycles + 1):
                cycle_counter += 1
                created = _request_expect_stage(
                    "POST",
                    "/goals",
                    201,
                    json={
                        "title": f"Load profile goal {stage_name} #{stage_cycle}",
                        "description": "prod-like load profile framework",
                        "urgency": 0.58,
                        "value": 0.62,
                        "deadline_score": 0.34,
                    },
                )
                goal_id = str(created.json()["goal_id"])

                _request_expect_stage("POST", f"/goals/{goal_id}/activate", 200)

                task = _request_expect_stage(
                    "POST",
                    "/tasks",
                    201,
                    json={"goal_id": goal_id, "title": f"Load profile task {stage_name} #{stage_cycle}"},
                )
                task_id = str(task.json()["task_id"])
                _request_expect_stage("POST", f"/tasks/{task_id}/success", 200)

                _request_expect_stage("POST", f"/goals/{goal_id}/block", 200)
                _request_expect_stage("POST", f"/goals/{goal_id}/archive", 200)

                if stage_workflow_every > 0 and stage_cycle % stage_workflow_every == 0:
                    started_run = _request_expect_stage(
                        "POST",
                        "/workflows/maintenance.retention_cleanup/start",
                        201,
                        json={"requested_by": "load-profile-framework", "payload": {"stage": stage_name, "cycle": stage_cycle}},
                    )
                    run_ids.append(str(started_run.json().get("run", {}).get("run_id") or ""))
                    run_ids = [item for item in run_ids if item]
                    if len(run_ids) > 100:
                        run_ids = run_ids[-100:]

                _request_expect_stage(
                    "POST",
                    f"/system/consumers/{consumer_id}/drain",
                    200,
                    params={"batch_size": int(stage_drain_batch_size)},
                )

                if stage_readiness_every > 0 and stage_cycle % stage_readiness_every == 0:
                    readiness = _request_stage("GET", "/system/readiness")
                    _expect(readiness.status_code == 200, f"Readiness check failed in stage {stage_name}: {readiness.text}")
                    _expect(bool(readiness.json().get("ready")), f"Readiness became false in stage {stage_name}: {readiness.text}")
                    slo = _request_stage("GET", "/system/slo")
                    _expect(slo.status_code == 200, f"SLO check failed in stage {stage_name}: {slo.text}")
                    _expect(str(slo.json().get("status")) == "ok", f"SLO became non-ok in stage {stage_name}: {slo.text}")

            stage_requests_total = int(sum(stage_statuses.values()))
            stage_summary = {
                "name": stage_name,
                "cycles": stage_cycles,
                "requests_total": stage_requests_total,
                "status_counts": {str(code): int(count) for code, count in sorted(stage_statuses.items())},
                "duration_ms": int((time.perf_counter() - stage_start) * 1000),
                "latency_ms": {
                    "p95": round(_percentile(stage_latencies, 95.0), 3) if stage_latencies else 0.0,
                    "p99": round(_percentile(stage_latencies, 99.0), 3) if stage_latencies else 0.0,
                    "max": round(max(stage_latencies), 3) if stage_latencies else 0.0,
                },
            }
            stage_summaries.append(stage_summary)

        _wait_for_runs_terminal(client, run_ids, timeout_seconds=30.0)
        drained_after = _drain_consumer_until_empty(
            client,
            consumer_id=consumer_id,
            drain_batch_size=max(1, int(stages[-1].get("drain_batch_size") or 100)),
            timeout_seconds=20.0,
        )

        readiness_final = _request("GET", "/system/readiness")
        slo_final = _request("GET", "/system/slo")
        health_final = _request("GET", "/system/health")
        _expect(readiness_final.status_code == 200, f"Final readiness failed: {readiness_final.text}")
        _expect(slo_final.status_code == 200, f"Final SLO failed: {slo_final.text}")
        _expect(health_final.status_code == 200, f"Final health failed: {health_final.text}")

        readiness_payload = readiness_final.json()
        slo_payload = slo_final.json()
        health_payload = health_final.json()

        worker_status = dict(readiness_payload.get("checks", {}).get("workflow_worker", {}) or {})
        queued_runs = int(worker_status.get("queued_runs") or 0)
        running_runs = int(worker_status.get("running_runs") or 0)
        invariant_violations = list(health_payload.get("invariant_violations") or [])

        total_requests = int(sum(statuses.values()))
        http_429_count = int(statuses.get(429, 0))
        error_count = int(sum(count for code, count in statuses.items() if int(code) >= 500))
        http_429_rate_percent = _compute_rate_percent(count=http_429_count, total=total_requests)
        error_rate_percent = _compute_rate_percent(count=error_count, total=total_requests)

        _expect(total_requests > 0, "No requests were executed by selected load profile.")
        _expect(latencies_ms, "No latency samples were collected by selected load profile.")

        p95_latency_ms = _percentile(latencies_ms, 95.0)
        p99_latency_ms = _percentile(latencies_ms, 99.0)
        max_latency_ms = max(latencies_ms)

    criteria: list[dict[str, Any]] = []

    def add(name: str, passed: bool, details: str) -> None:
        criteria.append({"name": name, "passed": bool(passed), "details": details})

    add("catalog_version_present", bool(catalog_version.strip()), f"catalog_version={catalog_version!r}")
    add(
        "profile_selected",
        bool(str(selected_profile.get("name") or "").strip()) and bool(str(selected_profile.get("version") or "").strip()),
        f"profile_name={selected_profile.get('name')!r}, profile_version={selected_profile.get('version')!r}",
    )
    add("stage_count_non_zero", len(stage_summaries) > 0, f"stage_count={len(stage_summaries)}")

    if profile_mode == "production":
        add(
            "min_total_requests",
            total_requests >= int(budgets["min_total_requests"]),
            f"requests_total={total_requests}, min_required={int(budgets['min_total_requests'])}",
        )
        add(
            "p95_latency_budget",
            p95_latency_ms <= float(budgets["max_p95_latency_ms"]),
            f"p95_latency_ms={p95_latency_ms:.3f}, max={float(budgets['max_p95_latency_ms']):.3f}",
        )
        add(
            "p99_latency_budget",
            p99_latency_ms <= float(budgets["max_p99_latency_ms"]),
            f"p99_latency_ms={p99_latency_ms:.3f}, max={float(budgets['max_p99_latency_ms']):.3f}",
        )
        add(
            "max_latency_budget",
            max_latency_ms <= float(budgets["max_max_latency_ms"]),
            f"max_latency_ms={max_latency_ms:.3f}, max={float(budgets['max_max_latency_ms']):.3f}",
        )
        add(
            "http_429_budget",
            http_429_rate_percent <= float(budgets["max_http_429_rate_percent"]),
            f"http_429_rate_percent={http_429_rate_percent:.4f}, max={float(budgets['max_http_429_rate_percent']):.4f}",
        )
        add(
            "error_rate_budget",
            error_rate_percent <= float(budgets["max_error_rate_percent"]),
            f"error_rate_percent={error_rate_percent:.4f}, max={float(budgets['max_error_rate_percent']):.4f}",
        )
        add("final_readiness_true", bool(readiness_payload.get("ready")), f"ready={bool(readiness_payload.get('ready'))}")
        add("final_slo_ok", str(slo_payload.get("status") or "") == "ok", f"slo_status={slo_payload.get('status')!r}")
        add(
            "workflow_queue_drained",
            queued_runs == 0 and running_runs == 0,
            f"queued_runs={queued_runs}, running_runs={running_runs}",
        )
        add(
            "invariant_violations_absent",
            len(invariant_violations) == 0,
            f"invariant_violations={len(invariant_violations)}",
        )
    else:
        add("non_production_profile", True, f"deployment_profile={profile_mode!r} (hard requirements skipped)")

    failed_criteria = [item for item in criteria if item["passed"] is False]
    success = len(failed_criteria) == 0

    report = {
        "label": label,
        "success": bool(success),
        "config": {
            "deployment_profile": profile_mode,
            "profile_file": str(profile_file),
            "profile_name": str(profile_name),
            "profile_version": str(profile_version),
        },
        "profile": {
            "catalog_version": catalog_version,
            "name": str(selected_profile.get("name") or ""),
            "version": str(selected_profile.get("version") or ""),
            "description": str(selected_profile.get("description") or ""),
            "budgets": budgets,
        },
        "metrics": {
            "criteria_total": len(criteria),
            "criteria_passed": len(criteria) - len(failed_criteria),
            "criteria_failed": len(failed_criteria),
            "stages_executed": len(stage_summaries),
            "cycles_executed": cycle_counter,
            "requests_total": total_requests,
            "workflow_runs_started": len(run_ids),
            "transient_retry_count": transient_retry_count,
            "drained_after_loop": drained_after,
            "latency_ms": {
                "p50": round(_percentile(latencies_ms, 50.0), 3),
                "p95": round(p95_latency_ms, 3),
                "p99": round(p99_latency_ms, 3),
                "max": round(max_latency_ms, 3),
            },
            "observed_rates_percent": {
                "http_429_rate": round(http_429_rate_percent, 4),
                "error_rate": round(error_rate_percent, 4),
            },
            "status_counts": {str(code): int(count) for code, count in sorted(statuses.items())},
            "final": {
                "readiness_ready": bool(readiness_payload.get("ready")),
                "slo_status": str(slo_payload.get("status") or ""),
                "queued_runs": queued_runs,
                "running_runs": running_runs,
                "invariant_violations": len(invariant_violations),
            },
        },
        "stage_summaries": stage_summaries,
        "criteria": criteria,
        "failed_criteria": failed_criteria,
        "generated_at_utc": _utc_now(),
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run versioned prod-like load profile framework checks and enforce deterministic latency/error budgets."
        )
    )
    parser.add_argument("--label", default="load-profile-framework-check")
    parser.add_argument("--deployment-profile", default="production")
    parser.add_argument("--profile-file", default="docs/load-profile-catalog.json")
    parser.add_argument("--profile-name", default="prod_like_ci_smoke")
    parser.add_argument("--profile-version", default="1.0.0")
    parser.add_argument("--output-file")
    parser.add_argument("--allow-failure", action="store_true")
    args = parser.parse_args(argv)

    project_root = Path(__file__).resolve().parents[1]
    profile_file = Path(str(args.profile_file)).expanduser()
    if not profile_file.is_absolute():
        profile_file = (project_root / profile_file).resolve()

    try:
        report = run_check(
            label=str(args.label),
            deployment_profile=str(args.deployment_profile),
            profile_file=profile_file,
            profile_name=str(args.profile_name),
            profile_version=str(args.profile_version),
        )
    except Exception as exc:
        print(f"[load-profile-framework-check] ERROR: {exc}", file=sys.stderr)
        return 1

    if args.output_file:
        output_file = Path(str(args.output_file)).expanduser()
        if not output_file.is_absolute():
            output_file = (project_root / output_file).resolve()
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2), encoding="utf-8")

    if report["success"] is False and not bool(args.allow_failure):
        print(f"[load-profile-framework-check] ERROR: {json.dumps(report, sort_keys=True)}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
