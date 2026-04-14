from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections import Counter
from pathlib import Path

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
            status = str(response.json()["run"]["status"])
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
    def _request_with_retry(
        method: str,
        url: str,
        *,
        expected_status: int,
        max_retries: int = 8,
        **kwargs,
    ):
        last_response = None
        for attempt in range(max_retries + 1):
            response = client.request(method, url, **kwargs)
            last_response = response
            if response.status_code == expected_status:
                return response
            if response.status_code >= 500:
                time.sleep(min(0.2, 0.02 * (attempt + 1)))
                continue
            break
        _expect(
            False,
            (
                f"Consumer request failed for {method} {url}. "
                f"expected={expected_status} "
                f"observed={last_response.status_code if last_response else 'none'} "
                f"body={last_response.text if last_response is not None else '<none>'}"
            ),
        )
        return last_response

    processed_total = 0
    deadline = time.time() + max(1.0, float(timeout_seconds))
    while time.time() < deadline:
        snapshot_response = _request_with_retry("GET", "/system/backpressure", expected_status=200)
        snapshot = snapshot_response.json()
        pending = int(snapshot.get("pending_events") or 0)
        if pending == 0:
            return processed_total
        drained = _request_with_retry(
            "POST",
            f"/system/consumers/{consumer_id}/drain",
            expected_status=200,
            params={"batch_size": int(drain_batch_size)},
        )
        processed_total += int(drained.json()["processed_count"])
        time.sleep(0.02)
    raise RuntimeError("Timed out while draining event backlog to zero.")


def run_drill(
    *,
    duration_seconds: float,
    max_p95_latency_ms: float,
    max_p99_latency_ms: float,
    max_max_latency_ms: float,
    max_http_429_rate_percent: float,
    max_error_rate_percent: float,
    min_requests: int,
    drain_batch_size: int,
    workflow_start_every_cycles: int,
) -> dict:
    app = create_app(
        Settings(
            database_url=":memory:",
            workflow_worker_poll_interval_seconds=0.02,
            workflow_run_timeout_seconds=120,
        )
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        consumer_id = str(client.app.state.services.settings.consumer_id)
        deadline = time.time() + max(1.0, float(duration_seconds))
        latencies_ms: list[float] = []
        statuses = Counter()
        cycle_count = 0
        run_ids: list[str] = []
        transient_retry_count = 0

        def _request(method: str, url: str, **kwargs):
            started = time.perf_counter()
            response = client.request(method, url, **kwargs)
            latencies_ms.append((time.perf_counter() - started) * 1000.0)
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

        while time.time() < deadline:
            cycle_count += 1
            created = _request_expect(
                "POST",
                "/goals",
                201,
                json={
                    "title": f"Long soak goal {cycle_count}",
                    "description": "long soak budget drill",
                    "urgency": 0.55,
                    "value": 0.65,
                    "deadline_score": 0.35,
                },
            )
            goal_id = str(created.json()["goal_id"])

            _request_expect("POST", f"/goals/{goal_id}/activate", 200)

            task = _request_expect(
                "POST",
                "/tasks",
                201,
                json={"goal_id": goal_id, "title": f"Long soak task {cycle_count}"},
            )
            task_id = str(task.json()["task_id"])

            _request_expect("POST", f"/tasks/{task_id}/success", 200)

            _request_expect("POST", f"/goals/{goal_id}/block", 200)

            _request_expect("POST", f"/goals/{goal_id}/archive", 200)

            if int(workflow_start_every_cycles) > 0 and cycle_count % int(workflow_start_every_cycles) == 0:
                started_run = _request_expect(
                    "POST",
                    "/workflows/maintenance.retention_cleanup/start",
                    201,
                    json={"requested_by": "long-soak-budget-drill", "payload": {"cycle": cycle_count}},
                )
                run_ids.append(str(started_run.json()["run"]["run_id"]))
                # Keep the verification set bounded so terminal polling remains deterministic
                # even during very long soak windows.
                if len(run_ids) > 50:
                    run_ids = run_ids[-50:]

            _request_expect(
                "POST",
                f"/system/consumers/{consumer_id}/drain",
                200,
                params={"batch_size": int(drain_batch_size)},
            )

            if cycle_count % 5 == 0:
                readiness = _request("GET", "/system/readiness")
                _expect(readiness.status_code == 200, f"Readiness check failed: {readiness.text}")
                _expect(
                    bool(readiness.json().get("ready")),
                    f"Readiness became false in cycle {cycle_count}: {readiness.text}",
                )
                slo = _request("GET", "/system/slo")
                _expect(slo.status_code == 200, f"SLO check failed: {slo.text}")
                _expect(
                    str(slo.json().get("status")) == "ok",
                    f"SLO became non-ok in cycle {cycle_count}: {slo.text}",
                )

        _wait_for_runs_terminal(client, run_ids, timeout_seconds=20.0)
        drained_after = _drain_consumer_until_empty(
            client,
            consumer_id=consumer_id,
            drain_batch_size=int(drain_batch_size),
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
        invariant_violations = list(health_payload.get("invariant_violations") or [])
        _expect(
            bool(readiness_payload.get("ready")),
            f"Final readiness is false: {json.dumps(readiness_payload, sort_keys=True)}",
        )
        worker_status = readiness_payload["checks"]["workflow_worker"]
        _expect(
            int(worker_status.get("queued_runs") or 0) == 0
            and int(worker_status.get("running_runs") or 0) == 0,
            f"Workflow runs still queued/running at end of soak: {json.dumps(worker_status, sort_keys=True)}",
        )
        _expect(
            str(slo_payload.get("status")) == "ok",
            f"Final SLO is not ok: {json.dumps(slo_payload, sort_keys=True)}",
        )
        _expect(
            not invariant_violations,
            f"Invariant violations after soak: {json.dumps(invariant_violations, sort_keys=True)}",
        )

        total_requests = int(sum(statuses.values()))
        _expect(total_requests >= int(min_requests), f"Insufficient sample size: {total_requests} < {min_requests}")
        _expect(latencies_ms, "No latency samples collected.")

        http_429_count = int(statuses.get(429, 0))
        error_count = int(sum(count for code, count in statuses.items() if int(code) >= 500))
        http_429_rate_percent = (http_429_count / total_requests) * 100.0
        error_rate_percent = (error_count / total_requests) * 100.0
        p95_latency_ms = _percentile(latencies_ms, 95.0)
        p99_latency_ms = _percentile(latencies_ms, 99.0)
        max_latency_ms = max(latencies_ms)

        _expect(
            p95_latency_ms <= float(max_p95_latency_ms),
            f"Latency budget exceeded: p95={p95_latency_ms:.3f}ms > {max_p95_latency_ms:.3f}ms",
        )
        _expect(
            p99_latency_ms <= float(max_p99_latency_ms),
            f"Latency budget exceeded: p99={p99_latency_ms:.3f}ms > {max_p99_latency_ms:.3f}ms",
        )
        _expect(
            max_latency_ms <= float(max_max_latency_ms),
            f"Latency budget exceeded: max={max_latency_ms:.3f}ms > {max_max_latency_ms:.3f}ms",
        )
        _expect(
            http_429_rate_percent <= float(max_http_429_rate_percent),
            (
                "HTTP 429 budget exceeded: "
                f"{http_429_rate_percent:.3f}% > {max_http_429_rate_percent:.3f}%"
            ),
        )
        _expect(
            error_rate_percent <= float(max_error_rate_percent),
            f"Error budget exceeded: {error_rate_percent:.3f}% > {max_error_rate_percent:.3f}%",
        )

        return {
            "success": True,
            "duration_seconds": float(duration_seconds),
            "cycle_count": cycle_count,
            "requests_total": total_requests,
            "status_counts": {str(code): int(count) for code, count in sorted(statuses.items())},
            "latency_ms": {
                "p50": round(_percentile(latencies_ms, 50.0), 3),
                "p95": round(p95_latency_ms, 3),
                "p99": round(p99_latency_ms, 3),
                "max": round(max_latency_ms, 3),
            },
            "budgets": {
                "max_p95_latency_ms": float(max_p95_latency_ms),
                "max_p99_latency_ms": float(max_p99_latency_ms),
                "max_max_latency_ms": float(max_max_latency_ms),
                "max_http_429_rate_percent": float(max_http_429_rate_percent),
                "max_error_rate_percent": float(max_error_rate_percent),
            },
            "observed_rates_percent": {
                "http_429_rate": round(http_429_rate_percent, 4),
                "error_rate": round(error_rate_percent, 4),
            },
            "workflow_runs_started": len(run_ids),
            "transient_retry_count": transient_retry_count,
            "drained_after_loop": drained_after,
            "readiness_ready": bool(readiness_payload["ready"]),
            "slo_status": str(slo_payload["status"]),
            "invariant_violations": invariant_violations,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Long soak budget drill: sustain mixed API load and enforce p95 latency, HTTP 429, and error-rate budgets."
        )
    )
    parser.add_argument("--duration-seconds", type=float, default=900.0)
    parser.add_argument("--max-p95-latency-ms", type=float, default=250.0)
    parser.add_argument("--max-p99-latency-ms", type=float, default=400.0)
    parser.add_argument("--max-max-latency-ms", type=float, default=5000.0)
    parser.add_argument("--max-http-429-rate-percent", type=float, default=1.0)
    parser.add_argument("--max-error-rate-percent", type=float, default=1.0)
    parser.add_argument("--min-requests", type=int, default=300)
    parser.add_argument("--drain-batch-size", type=int, default=150)
    parser.add_argument("--workflow-start-every-cycles", type=int, default=0)
    args = parser.parse_args(argv)

    if float(args.duration_seconds) <= 0:
        print("[long-soak-budget-drill] ERROR: --duration-seconds must be > 0.", file=sys.stderr)
        return 2
    if float(args.max_p95_latency_ms) <= 0:
        print("[long-soak-budget-drill] ERROR: --max-p95-latency-ms must be > 0.", file=sys.stderr)
        return 2
    if float(args.max_p99_latency_ms) <= 0:
        print("[long-soak-budget-drill] ERROR: --max-p99-latency-ms must be > 0.", file=sys.stderr)
        return 2
    if float(args.max_max_latency_ms) <= 0:
        print("[long-soak-budget-drill] ERROR: --max-max-latency-ms must be > 0.", file=sys.stderr)
        return 2
    if float(args.max_http_429_rate_percent) < 0:
        print(
            "[long-soak-budget-drill] ERROR: --max-http-429-rate-percent must be >= 0.",
            file=sys.stderr,
        )
        return 2
    if float(args.max_error_rate_percent) < 0:
        print("[long-soak-budget-drill] ERROR: --max-error-rate-percent must be >= 0.", file=sys.stderr)
        return 2
    if int(args.min_requests) <= 0:
        print("[long-soak-budget-drill] ERROR: --min-requests must be > 0.", file=sys.stderr)
        return 2
    if int(args.drain_batch_size) <= 0:
        print("[long-soak-budget-drill] ERROR: --drain-batch-size must be > 0.", file=sys.stderr)
        return 2
    if int(args.workflow_start_every_cycles) < 0:
        print(
            "[long-soak-budget-drill] ERROR: --workflow-start-every-cycles must be >= 0.",
            file=sys.stderr,
        )
        return 2

    try:
        report = run_drill(
            duration_seconds=float(args.duration_seconds),
            max_p95_latency_ms=float(args.max_p95_latency_ms),
            max_p99_latency_ms=float(args.max_p99_latency_ms),
            max_max_latency_ms=float(args.max_max_latency_ms),
            max_http_429_rate_percent=float(args.max_http_429_rate_percent),
            max_error_rate_percent=float(args.max_error_rate_percent),
            min_requests=int(args.min_requests),
            drain_batch_size=int(args.drain_batch_size),
            workflow_start_every_cycles=int(args.workflow_start_every_cycles),
        )
    except Exception as exc:
        print(f"[long-soak-budget-drill] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
