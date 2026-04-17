from __future__ import annotations

import json
import sqlite3
import subprocess
import shutil
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from goal_ops_console.config import Settings
from goal_ops_console.database import Database, new_id, now_utc
from goal_ops_console.failure_intelligence import compute_error_hash
from goal_ops_console.main import create_app
from goal_ops_console.models import OptimisticLockError, RetryBudgetExceeded
from goal_ops_console.scheduler import RetryBudget, write_with_retry


def create_active_goal(client, title: str = "Goal") -> dict:
    created = client.post(
        "/goals",
        json={"title": title, "description": "demo", "urgency": 0.8, "value": 0.7, "deadline_score": 0.2},
    )
    goal = created.json()
    activated = client.post(f"/goals/{goal['goal_id']}/activate")
    return activated.json()


def create_task(client, goal_id: str, title: str = "Task") -> dict:
    response = client.post("/tasks", json={"goal_id": goal_id, "title": title})
    return response.json()


def _local_test_dir(prefix: str) -> Path:
    base = Path(".tmp") / prefix
    base.mkdir(parents=True, exist_ok=True)
    target = base / f"case-{time.time_ns()}"
    target.mkdir(parents=True, exist_ok=False)
    return target


def test_01_goal_creation_inserts_goal_and_queue_atomically(client):
    response = client.post(
        "/goals",
        json={"title": "Atomic goal", "description": "check", "urgency": 0.6, "value": 0.4, "deadline_score": 0.1},
    )
    assert response.status_code == 201
    goal = response.json()
    queue_row = client.app.state.services.db.fetch_one(
        "SELECT status FROM goal_queue WHERE goal_id = ?",
        goal["goal_id"],
    )
    assert goal["state"] == "draft"
    assert queue_row["status"] == "queued"


def test_02_goal_creation_emits_event_in_same_transaction(client):
    goal = client.post(
        "/goals",
        json={"title": "Event goal", "description": "check", "urgency": 0.5, "value": 0.5, "deadline_score": 0.0},
    ).json()
    events = client.get(f"/events?correlation_id={goal['goal_id']}").json()
    assert len(events) == 1
    assert events[0]["seq"] == 1
    assert events[0]["event_type"] == "goal.created"


def test_03_scheduler_pick_ages_queue_and_activates_highest_priority_goal(services):
    low = services.state_manager.create_goal(
        title="Low",
        description=None,
        urgency=0.2,
        value=0.2,
        deadline_score=0.0,
    )
    high = services.state_manager.create_goal(
        title="High",
        description=None,
        urgency=0.9,
        value=0.8,
        deadline_score=0.4,
    )
    picked = services.scheduler.pick_next_goal()
    low_queue = services.db.fetch_one("SELECT wait_cycles, version FROM goal_queue WHERE goal_id = ?", low["goal_id"])
    high_goal = services.state_manager.get_goal(high["goal_id"])
    assert picked["goal_id"] == high["goal_id"]
    assert picked["state"] == "active"
    assert low_queue["wait_cycles"] == 1
    assert low_queue["version"] > 1
    assert high_goal["queue_status"] == "active"


def test_04_task_creation_uses_attempt_zero_correlation(client):
    goal = create_active_goal(client)
    task = create_task(client, goal["goal_id"])
    events = client.get(f"/events?entity_id={task['task_id']}").json()
    assert task["correlation_id"].endswith(":0")
    assert events[0]["correlation_id"] == task["correlation_id"]


def test_05_failure_simulation_computes_error_hash(client):
    goal = create_active_goal(client)
    task = create_task(client, goal["goal_id"])
    result = client.post(
        f"/tasks/{task['task_id']}/fail",
        json={"failure_type": "SkillFailure", "error_message": "Tool exploded"},
    ).json()
    assert result["error_hash"] == compute_error_hash("SkillFailure", "Tool exploded")


def test_06_retry_increments_count_and_advances_attempt_correlation(client):
    goal = create_active_goal(client)
    task = create_task(client, goal["goal_id"])
    result = client.post(
        f"/tasks/{task['task_id']}/fail",
        json={"failure_type": "SkillFailure", "error_message": "Same error"},
    ).json()
    assert result["status"] == "failed"
    assert result["retry_count"] == 1
    assert result["correlation_id"].endswith(":1")


def test_07_external_failure_below_threshold_does_not_escalate(client):
    goal = create_active_goal(client)
    task = create_task(client, goal["goal_id"])
    result = client.post(
        f"/tasks/{task['task_id']}/fail",
        json={"failure_type": "ExternalFailure", "error_message": "Vendor timeout"},
    ).json()
    goal_after = client.get(f"/goals/{goal['goal_id']}").json()
    assert result["status"] == "failed"
    assert goal_after["state"] == "active"


def test_08_external_failure_systemic_rate_escalates_goal(services):
    goal = services.state_manager.create_goal(
        title="Systemic goal",
        description=None,
        urgency=0.8,
        value=0.8,
        deadline_score=0.2,
    )
    services.state_manager.transition_goal(
        goal["goal_id"],
        to_state="active",
        owner="scheduler",
        event_type="goal.activated",
        correlation_id=goal["goal_id"],
    )
    task = services.execution_layer.create_task(goal_id=goal["goal_id"], title="External task")
    with services.db.transaction() as tx:
        for _ in range(19):
            services.failure_intelligence.log_failure(
                tx,
                task_id=task["task_id"],
                goal_id=goal["goal_id"],
                correlation_id=f"{goal['goal_id']}:{task['task_id']}:0",
                failure_type="ExternalFailure",
                fingerprint="externalhash",
                retry_count=1,
                error_message="already failing",
            )
    result = services.execution_layer.simulate_failure(
        task["task_id"],
        failure_type="ExternalFailure",
        error_message="already failing",
    )
    goal_after = services.state_manager.get_goal(goal["goal_id"])
    assert result["status"] == "poison"
    assert goal_after["state"] == "escalation_pending"


def test_09_skill_failure_repeated_hash_escalates_on_exhaustion(client):
    goal = create_active_goal(client)
    task = create_task(client, goal["goal_id"])
    client.post(
        f"/tasks/{task['task_id']}/fail",
        json={"failure_type": "SkillFailure", "error_message": "Repeated skill failure"},
    )
    result = client.post(
        f"/tasks/{task['task_id']}/fail",
        json={"failure_type": "SkillFailure", "error_message": "Repeated skill failure"},
    ).json()
    goal_after = client.get(f"/goals/{goal['goal_id']}").json()
    assert result["status"] == "poison"
    assert goal_after["state"] == "escalation_pending"


def test_10_exhaustion_blocks_goal(client):
    goal = create_active_goal(client)
    task = create_task(client, goal["goal_id"])
    for idx in range(3):
        result = client.post(
            f"/tasks/{task['task_id']}/fail",
            json={"failure_type": "ExecutionFailure", "error_message": f"Execution failure {idx}"},
        )
    task_after = result.json()
    goal_after = client.get(f"/goals/{goal['goal_id']}").json()
    assert task_after["status"] == "exhausted"
    assert goal_after["state"] == "blocked"


def test_11_poison_simulation_emits_poison_event_and_escalates_goal(client):
    goal = create_active_goal(client)
    task = create_task(client, goal["goal_id"])
    client.post(
        f"/tasks/{task['task_id']}/fail",
        json={"failure_type": "SkillFailure", "error_message": "Sticky poison"},
    )
    client.post(
        f"/tasks/{task['task_id']}/fail",
        json={"failure_type": "SkillFailure", "error_message": "Sticky poison"},
    )
    events = client.get(f"/events?entity_id={task['task_id']}").json()
    goal_after = client.get(f"/goals/{goal['goal_id']}").json()
    assert any(event["event_type"] == "task.poison.detected" for event in events)
    assert goal_after["state"] == "escalation_pending"


def test_12_hitl_approval_reactivates_goal(client):
    goal = create_active_goal(client)
    task = create_task(client, goal["goal_id"])
    client.post(
        f"/tasks/{task['task_id']}/fail",
        json={"failure_type": "SkillFailure", "error_message": "Same error"},
    )
    client.post(
        f"/tasks/{task['task_id']}/fail",
        json={"failure_type": "SkillFailure", "error_message": "Same error"},
    )
    approved = client.post(f"/goals/{goal['goal_id']}/hitl_approve").json()
    assert approved["state"] == "active"
    assert approved["queue_status"] == "active"


def test_13_pending_event_processing_can_be_reprocessed_after_restart(services):
    event_id = services.event_bus.record_event("probe.event", "entity-1", "goal-1")
    services.db.execute(
        "INSERT INTO event_processing (event_id, consumer_id, status, version) VALUES (?, ?, 'pending', 1)",
        event_id,
        "worker-a",
    )
    handled = []
    processed = services.event_bus.consume_batch("worker-a", lambda event: handled.append(event["event_id"]))
    row = services.db.fetch_one(
        "SELECT status FROM event_processing WHERE event_id = ? AND consumer_id = ?",
        event_id,
        "worker-a",
    )
    assert processed == 1
    assert handled == [event_id]
    assert row["status"] == "processed"


def test_14_duplicate_event_injection_is_idempotent(services):
    duplicate_id = new_id()
    services.event_bus.record_event("probe.event", "entity-1", "goal-1", event_id=duplicate_id)
    services.event_bus.record_event("probe.event", "entity-1", "goal-1", event_id=duplicate_id)
    count = services.db.fetch_scalar("SELECT COUNT(*) FROM events WHERE event_id = ?", duplicate_id)
    assert count == 1


def test_15_consumer_race_has_single_winner(services):
    event_id = services.event_bus.record_event("probe.event", "entity-1", "goal-1")
    barrier = threading.Barrier(2)
    handled: list[str] = []
    errors: list[Exception] = []
    lock = threading.Lock()

    def handler(event):
        with lock:
            handled.append(event["event_id"])

    def worker():
        barrier.wait()
        try:
            services.event_bus.process_event(event_id, "race-consumer", handler)
        except Exception as exc:  # pragma: no cover - only used for thread diagnostics
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    row = services.db.fetch_one(
        "SELECT status FROM event_processing WHERE event_id = ? AND consumer_id = ?",
        event_id,
        "race-consumer",
    )
    assert handled == [event_id]
    assert row["status"] == "processed"


def test_16_processing_timeout_reclaims_stuck_event(services):
    event_id = services.event_bus.record_event("probe.event", "entity-1", "goal-1")
    services.db.execute(
        "INSERT INTO event_processing "
        "(event_id, consumer_id, status, processing_started_at, version) "
        "VALUES (?, ?, 'processing', datetime('now', '-120 seconds'), 1)",
        event_id,
        "worker-a",
    )
    handled = []
    processed = services.event_bus.consume_batch("worker-a", lambda event: handled.append(event["event_id"]))
    row = services.db.fetch_one(
        "SELECT status FROM event_processing WHERE event_id = ? AND consumer_id = ?",
        event_id,
        "worker-a",
    )
    assert processed == 1
    assert handled == [event_id]
    assert row["status"] == "processed"


def test_17_retry_budget_ends_cycle_cleanly():
    budget = RetryBudget(max_retries=2)

    def write_fn():
        raise OptimisticLockError("forced conflict")

    with pytest.raises(RetryBudgetExceeded):
        write_with_retry(write_fn, lambda: None, budget)


def test_18_ordering_integrity_preserves_seq_order(services):
    services.event_bus.record_event("event.a1", "A", "goal-a")
    services.event_bus.record_event("event.b1", "B", "goal-b")
    services.event_bus.record_event("event.a2", "A", "goal-a")
    seen = []
    services.event_bus.consume_batch("ordering-consumer", lambda event: seen.append((event["seq"], event["event_type"])))
    assert seen == [(1, "event.a1"), (2, "event.b1"), (3, "event.a2")]


def test_19_flow_trace_lists_all_attempts_by_goal_prefix(client):
    goal = create_active_goal(client)
    task = create_task(client, goal["goal_id"])
    client.post(
        f"/tasks/{task['task_id']}/fail",
        json={"failure_type": "SkillFailure", "error_message": "Same trace error"},
    )
    client.post(
        f"/tasks/{task['task_id']}/fail",
        json={"failure_type": "SkillFailure", "error_message": "Same trace error"},
    )
    events = client.get(f"/events?correlation_id={goal['goal_id']}").json()
    correlation_ids = {event["correlation_id"] for event in events}
    assert goal["goal_id"] in correlation_ids
    assert f"{goal['goal_id']}:{task['task_id']}:0" in correlation_ids
    assert f"{goal['goal_id']}:{task['task_id']}:1" in correlation_ids


def test_20_queue_endpoint_returns_priority_order(client):
    low = client.post(
        "/goals",
        json={"title": "Low", "description": "check", "urgency": 0.2, "value": 0.2, "deadline_score": 0.1},
    ).json()
    high = client.post(
        "/goals",
        json={"title": "High", "description": "check", "urgency": 0.9, "value": 0.8, "deadline_score": 0.4},
    ).json()
    queue = client.get("/system/queue").json()
    assert [item["goal_id"] for item in queue] == [high["goal_id"], low["goal_id"]]
    assert queue[0]["queue_status"] == "queued"


def test_21_scheduler_age_endpoint_increments_wait_cycles(client):
    goal = client.post(
        "/goals",
        json={"title": "Age me", "description": "check", "urgency": 0.5, "value": 0.5, "deadline_score": 0.0},
    ).json()
    before = client.get(f"/goals/{goal['goal_id']}").json()
    response = client.post("/system/scheduler/age")
    after = client.get(f"/goals/{goal['goal_id']}").json()
    assert response.status_code == 200
    assert response.json()["aged_count"] == 1
    assert after["wait_cycles"] == before["wait_cycles"] + 1
    assert after["priority"] > before["priority"]


def test_22_scheduler_pick_endpoint_activates_highest_priority_goal(client):
    client.post(
        "/goals",
        json={"title": "Low", "description": "check", "urgency": 0.2, "value": 0.2, "deadline_score": 0.0},
    )
    high = client.post(
        "/goals",
        json={"title": "High", "description": "check", "urgency": 0.9, "value": 0.8, "deadline_score": 0.4},
    ).json()
    response = client.post("/system/scheduler/pick")
    picked = response.json()["picked_goal"]
    assert picked["goal_id"] == high["goal_id"]
    assert picked["state"] == "active"


def test_23_consumer_drain_endpoint_processes_pending_events(client):
    services = client.app.state.services
    event_id = services.event_bus.record_event("probe.event", "entity-1", "goal-1")
    response = client.post("/system/consumers/manual/drain?batch_size=10")
    row = services.db.fetch_one(
        "SELECT status FROM event_processing WHERE event_id = ? AND consumer_id = ?",
        event_id,
        "manual",
    )
    assert response.status_code == 200
    assert response.json()["processed_count"] == 1
    assert row["status"] == "processed"


def test_24_consumer_reclaim_endpoint_resets_stuck_processing(client):
    services = client.app.state.services
    event_id = services.event_bus.record_event("probe.event", "entity-1", "goal-1")
    services.db.execute(
        "INSERT INTO event_processing "
        "(event_id, consumer_id, status, processing_started_at, version) "
        "VALUES (?, ?, 'processing', datetime('now', '-120 seconds'), 1)",
        event_id,
        "manual",
    )
    response = client.post("/system/consumers/manual/reclaim")
    row = services.db.fetch_one(
        "SELECT status FROM event_processing WHERE event_id = ? AND consumer_id = ?",
        event_id,
        "manual",
    )
    assert response.status_code == 200
    assert response.json()["reclaimed_count"] == 1
    assert row["status"] == "pending"


def test_25_backpressure_blocks_new_events_with_retry_hint():
    app = create_app(
        Settings(
            database_url=":memory:",
            max_pending_events=1,
            backpressure_retry_after_seconds=7,
        )
    )
    with TestClient(app) as local_client:
        first = local_client.post(
            "/goals",
            json={"title": "A", "description": "check", "urgency": 0.5, "value": 0.5, "deadline_score": 0.0},
        )
        blocked = local_client.post(
            "/goals",
            json={"title": "B", "description": "check", "urgency": 0.5, "value": 0.5, "deadline_score": 0.0},
        )
        assert first.status_code == 201
        assert blocked.status_code == 429
        assert blocked.json()["retry_after_seconds"] == 7
        assert "Event backlog limit reached" in blocked.json()["detail"]


def test_26_goal_queue_limit_returns_429():
    app = create_app(
        Settings(
            database_url=":memory:",
            max_pending_events=10_000,
            max_goal_queue_entries=1,
            backpressure_retry_after_seconds=9,
        )
    )
    with TestClient(app) as local_client:
        first = local_client.post(
            "/goals",
            json={"title": "A", "description": "check", "urgency": 0.5, "value": 0.5, "deadline_score": 0.0},
        )
        blocked = local_client.post(
            "/goals",
            json={"title": "B", "description": "check", "urgency": 0.5, "value": 0.5, "deadline_score": 0.0},
        )
        assert first.status_code == 201
        assert blocked.status_code == 429
        assert blocked.json()["retry_after_seconds"] == 9
        assert "Goal queue limit reached" in blocked.json()["detail"]


def test_27_scheduler_endpoints_return_429_under_backpressure():
    app = create_app(
        Settings(
            database_url=":memory:",
            max_pending_events=1,
            backpressure_retry_after_seconds=6,
        )
    )
    with TestClient(app) as local_client:
        created = local_client.post(
            "/goals",
            json={"title": "A", "description": "check", "urgency": 0.4, "value": 0.4, "deadline_score": 0.0},
        )
        response = local_client.post("/system/scheduler/age")
        assert created.status_code == 201
        assert response.status_code == 429
        assert response.json()["retry_after_seconds"] == 6


def test_28_consumer_drain_enforces_batch_limit_with_429(client):
    response = client.post("/system/consumers/manual/drain?batch_size=9999")
    assert response.status_code == 429
    assert "exceeds safe limit" in response.json()["detail"]


def test_29_retention_cleanup_deletes_old_records(client):
    services = client.app.state.services
    old_event_id = new_id()
    old_failure_id = new_id()
    services.db.execute(
        "INSERT INTO events (event_id, event_type, entity_id, correlation_id, payload, emitted_at) "
        "VALUES (?, 'retention.old', 'entity-1', 'goal-1', '{}', datetime('now', '-120 days'))",
        old_event_id,
    )
    services.db.execute(
        "INSERT INTO event_processing "
        "(event_id, consumer_id, status, processing_started_at, processed_at, version) "
        "VALUES (?, ?, 'processed', datetime('now', '-120 days'), datetime('now', '-120 days'), 1)",
        old_event_id,
        services.settings.consumer_id,
    )
    services.db.execute(
        "INSERT INTO failure_log "
        "(id, task_id, goal_id, correlation_id, failure_type, fingerprint, retry_count, "
        " last_error, status, version, created_at, updated_at) "
        "VALUES (?, 'task-old', 'goal-old', 'goal-old:task-old:0', 'ExecutionFailure', "
        " 'old-fingerprint', 1, 'old', 'recorded', 1, datetime('now', '-120 days'), datetime('now', '-120 days'))",
        old_failure_id,
    )
    old_audit_id = services.observability.record_audit(
        action="retention.old.audit",
        actor="test",
        status="success",
        entity_type="retention",
        entity_id="old-audit-entry",
    )
    services.db.execute(
        "UPDATE audit_log SET created_at = datetime('now', '-400 days') WHERE audit_id = ?",
        old_audit_id,
    )
    services.db.execute(
        "UPDATE audit_log_integrity SET created_at = datetime('now', '-400 days') WHERE audit_id = ?",
        old_audit_id,
    )
    response = client.post("/system/maintenance/retention")
    assert response.status_code == 200
    payload = response.json()
    assert payload["event_processing_deleted"] == 1
    assert payload["events_deleted"] == 1
    assert payload["failure_log_deleted"] == 1
    assert payload["audit_log_deleted"] == 1
    assert payload["audit_integrity_deleted"] == 1
    assert payload["retention_days"]["audit_log"] == services.settings.audit_log_retention_days
    assert services.db.fetch_scalar("SELECT COUNT(*) FROM events WHERE event_id = ?", old_event_id) == 0
    assert (
        services.db.fetch_scalar("SELECT COUNT(*) FROM event_processing WHERE event_id = ?", old_event_id)
        == 0
    )
    assert services.db.fetch_scalar("SELECT COUNT(*) FROM failure_log WHERE id = ?", old_failure_id) == 0
    assert services.db.fetch_scalar("SELECT COUNT(*) FROM audit_log WHERE audit_id = ?", old_audit_id) == 0
    assert services.db.fetch_scalar("SELECT COUNT(*) FROM audit_log_integrity WHERE audit_id = ?", old_audit_id) == 0


def test_30_backpressure_endpoint_and_health_include_limits(client):
    snapshot = client.get("/system/backpressure")
    health = client.get("/system/health")
    assert snapshot.status_code == 200
    assert health.status_code == 200
    assert snapshot.json()["max_pending_events"] == client.app.state.services.settings.max_pending_events
    assert "backpressure" in health.json()
    assert "retention" in health.json()


def test_31_metrics_endpoint_exposes_hook_counters(client):
    created = client.post(
        "/goals",
        json={"title": "Metrics goal", "description": "check", "urgency": 0.5, "value": 0.5, "deadline_score": 0.0},
    )
    metrics = client.get("/system/metrics")
    assert created.status_code == 201
    assert metrics.status_code == 200
    payload = metrics.json()
    lookup = {item["metric_name"]: item["value"] for item in payload["metrics"]}
    assert lookup["goals.created"] >= 1
    assert lookup["events.emitted"] >= 1


def test_32_audit_endpoint_lists_mutating_requests(client):
    response = client.post(
        "/goals",
        json={"title": "Audit goal", "description": "check", "urgency": 0.4, "value": 0.4, "deadline_score": 0.0},
    )
    audit = client.get("/system/audit")
    assert response.status_code == 201
    assert audit.status_code == 200
    entries = audit.json()["entries"]
    assert any(
        item["action"] == "http.mutation"
        and item["status"] == "success"
        and item["entity_id"] == "/goals"
        and item["details"]["method"] == "POST"
        for item in entries
    )


def test_33_rejected_transition_updates_error_metrics_and_audit(client):
    goal = client.post(
        "/goals",
        json={"title": "Reject me", "description": "check", "urgency": 0.5, "value": 0.5, "deadline_score": 0.1},
    ).json()
    rejected = client.post(f"/goals/{goal['goal_id']}/archive")
    metrics = client.get("/system/metrics?prefix=errors.domain")
    audit_error = client.get("/system/audit?status=error")
    assert rejected.status_code == 409
    assert metrics.status_code == 200
    assert audit_error.status_code == 200
    metric_names = {item["metric_name"] for item in metrics.json()["metrics"]}
    assert "errors.domain.ConflictError" in metric_names
    assert any(
        item["entity_id"] == "/goals/{goal_id}/archive"
        and item["details"]["status_code"] == 409
        for item in audit_error.json()["entries"]
    )


def test_34_health_includes_metrics_and_audit_summaries(client):
    client.post(
        "/goals",
        json={"title": "Health goal", "description": "check", "urgency": 0.6, "value": 0.5, "deadline_score": 0.0},
    )
    health = client.get("/system/health")
    assert health.status_code == 200
    payload = health.json()
    assert "metrics" in payload
    assert "audit" in payload
    assert payload["metrics"]["goals.created"] >= 1
    assert payload["audit"]["entries_last_24h"] >= 1


def test_35_flow_trace_endpoint_groups_attempts(client):
    goal = create_active_goal(client)
    task = create_task(client, goal["goal_id"])

    client.post(
        f"/tasks/{task['task_id']}/fail",
        json={"failure_type": "SkillFailure", "error_message": "Trace error"},
    )
    client.post(
        f"/tasks/{task['task_id']}/fail",
        json={"failure_type": "SkillFailure", "error_message": "Trace error"},
    )

    response = client.get(f"/events/trace/{goal['goal_id']}")
    assert response.status_code == 200
    trace = response.json()
    assert trace["goal_id"] == goal["goal_id"]
    assert trace["event_count"] >= 9

    attempts = {(item["task_id"], item["attempt"]) for item in trace["attempts"]}
    assert (task["task_id"], 0) in attempts
    assert (task["task_id"], 1) in attempts

    seqs = [event["seq"] for event in trace["events"]]
    assert seqs == sorted(seqs)


def test_36_flow_trace_empty_when_goal_has_no_events(client):
    response = client.get("/events/trace/non-existing-goal-id")
    assert response.status_code == 200
    trace = response.json()
    assert trace["event_count"] == 0
    assert trace["attempt_count"] == 0
    assert trace["attempts"] == []


def test_37_fault_explorer_dead_letter_filter_returns_poison_task(client):
    goal = create_active_goal(client, title="Fault Goal")
    task = create_task(client, goal["goal_id"], title="Fault Task")
    client.post(
        f"/tasks/{task['task_id']}/fail",
        json={"failure_type": "SkillFailure", "error_message": "Persistent issue"},
    )
    client.post(
        f"/tasks/{task['task_id']}/fail",
        json={"failure_type": "SkillFailure", "error_message": "Persistent issue"},
    )

    response = client.get("/system/faults?dead_letter_only=true")
    assert response.status_code == 200
    entries = response.json()["entries"]
    assert any(item["task_id"] == task["task_id"] for item in entries)
    assert any(item["task_status"] == "poison" for item in entries)


def test_38_fault_explorer_filters_by_failure_type_and_hash(client):
    goal = create_active_goal(client, title="Filter Goal")
    task_a = create_task(client, goal["goal_id"], title="Exec Task")
    task_b = create_task(client, goal["goal_id"], title="Skill Task")

    exec_message = "Execution exploded"
    client.post(
        f"/tasks/{task_a['task_id']}/fail",
        json={"failure_type": "ExecutionFailure", "error_message": exec_message},
    )
    client.post(
        f"/tasks/{task_b['task_id']}/fail",
        json={"failure_type": "SkillFailure", "error_message": "Skill exploded"},
    )

    exec_hash = compute_error_hash("ExecutionFailure", exec_message)
    response = client.get(
        f"/system/faults?failure_type=ExecutionFailure&error_hash={exec_hash}&dead_letter_only=false"
    )
    assert response.status_code == 200
    entries = response.json()["entries"]
    assert entries
    assert all(item["failure_type"] == "ExecutionFailure" for item in entries)
    assert all(item["error_hash"] == exec_hash for item in entries)


def test_39_health_and_fault_summary_include_fault_snapshot(client):
    goal = create_active_goal(client, title="Snapshot Goal")
    task = create_task(client, goal["goal_id"], title="Snapshot Task")
    client.post(
        f"/tasks/{task['task_id']}/fail",
        json={"failure_type": "SkillFailure", "error_message": "Snapshot issue"},
    )
    client.post(
        f"/tasks/{task['task_id']}/fail",
        json={"failure_type": "SkillFailure", "error_message": "Snapshot issue"},
    )

    summary = client.get("/system/faults/summary?dead_letter_only=true")
    health = client.get("/system/health")
    assert summary.status_code == 200
    assert health.status_code == 200

    summary_payload = summary.json()
    health_payload = health.json()
    assert summary_payload["dead_letter_tasks"] >= 1
    assert summary_payload["poison_tasks"] >= 1
    assert "systemic_external_failures_last_window" in summary_payload
    assert "faults" in health_payload
    assert health_payload["faults"]["dead_letter_tasks"] >= 1


def test_40_fault_retry_endpoint_creates_pending_retry_task_and_requeues_goal(client):
    goal = create_active_goal(client, title="Retry Goal")
    task = create_task(client, goal["goal_id"], title="Retry Source Task")

    for idx in range(3):
        client.post(
            f"/tasks/{task['task_id']}/fail",
            json={"failure_type": "ExecutionFailure", "error_message": f"Execution issue {idx}"},
        )

    source_task = client.get(f"/tasks/{task['task_id']}").json()
    goal_before = client.get(f"/goals/{goal['goal_id']}").json()
    faults = client.get("/system/faults?dead_letter_only=true").json()["entries"]
    failure_id = next(item["failure_id"] for item in faults if item["task_id"] == task["task_id"])

    response = client.post(
        f"/system/faults/{failure_id}/retry",
        json={"reason": "Manual remediation after dependency fix", "dry_run": False},
    )
    assert response.status_code == 200
    payload = response.json()

    assert source_task["status"] == "exhausted"
    assert goal_before["state"] == "blocked"
    assert payload["goal_requeued"] is True
    assert payload["source_task_id"] == task["task_id"]
    assert payload["retry_task"]["status"] == "pending"
    assert payload["retry_task"]["task_id"] != task["task_id"]

    goal_after = client.get(f"/goals/{goal['goal_id']}").json()
    assert goal_after["state"] == "active"

    failure_row = client.app.state.services.db.fetch_one(
        "SELECT status FROM failure_log WHERE id = ?",
        failure_id,
    )
    assert failure_row["status"] == "retry_queued"


def test_41_fault_retry_dry_run_keeps_state_unchanged(client):
    goal = create_active_goal(client, title="Dry run goal")
    task = create_task(client, goal["goal_id"], title="Dry run task")
    client.post(
        f"/tasks/{task['task_id']}/fail",
        json={"failure_type": "SkillFailure", "error_message": "persistent skill issue"},
    )
    client.post(
        f"/tasks/{task['task_id']}/fail",
        json={"failure_type": "SkillFailure", "error_message": "persistent skill issue"},
    )

    services = client.app.state.services
    task_count_before = services.db.fetch_scalar("SELECT COUNT(*) FROM task_state")
    fault = client.get("/system/faults?dead_letter_only=true").json()["entries"][0]
    failure_id = fault["failure_id"]

    response = client.post(
        f"/system/faults/{failure_id}/retry",
        json={"reason": "Preview remediation only", "dry_run": True},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["dry_run"] is True
    assert payload["allowed"] is True
    assert payload["will_requeue_goal"] is True

    task_count_after = services.db.fetch_scalar("SELECT COUNT(*) FROM task_state")
    failure_status = services.db.fetch_scalar("SELECT status FROM failure_log WHERE id = ?", failure_id)
    assert task_count_after == task_count_before
    assert failure_status == "recorded"


def test_42_fault_requeue_goal_endpoint_reactivates_escalation_pending_goal(client):
    goal = create_active_goal(client, title="Requeue Goal")
    task = create_task(client, goal["goal_id"], title="Requeue Task")
    client.post(
        f"/tasks/{task['task_id']}/fail",
        json={"failure_type": "SkillFailure", "error_message": "requeue me"},
    )
    client.post(
        f"/tasks/{task['task_id']}/fail",
        json={"failure_type": "SkillFailure", "error_message": "requeue me"},
    )

    fault = client.get("/system/faults?dead_letter_only=true").json()["entries"][0]
    failure_id = fault["failure_id"]

    response = client.post(
        f"/system/faults/{failure_id}/requeue_goal",
        json={"reason": "Supervisor approved unblock", "dry_run": False},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["goal"]["state"] == "active"

    failure_status = client.app.state.services.db.fetch_scalar(
        "SELECT status FROM failure_log WHERE id = ?",
        failure_id,
    )
    assert failure_status == "goal_requeued"


def test_43_fault_requeue_goal_rejects_when_goal_is_not_blocked_or_escalated(client):
    goal = create_active_goal(client, title="Active Goal")
    task = create_task(client, goal["goal_id"], title="Active Task")
    client.post(
        f"/tasks/{task['task_id']}/fail",
        json={"failure_type": "SkillFailure", "error_message": "single failure"},
    )
    fault = client.get("/system/faults?dead_letter_only=false").json()["entries"][0]

    response = client.post(
        f"/system/faults/{fault['failure_id']}/requeue_goal",
        json={"reason": "Should not be needed", "dry_run": False},
    )
    assert response.status_code == 409
    assert "cannot be requeued" in response.json()["detail"]


def test_44_fault_resolve_endpoint_marks_failure_resolved_and_records_audit(client):
    goal = create_active_goal(client, title="Resolve Goal")
    task = create_task(client, goal["goal_id"], title="Resolve Task")
    client.post(
        f"/tasks/{task['task_id']}/fail",
        json={"failure_type": "SkillFailure", "error_message": "resolve me"},
    )
    client.post(
        f"/tasks/{task['task_id']}/fail",
        json={"failure_type": "SkillFailure", "error_message": "resolve me"},
    )

    fault = client.get("/system/faults?dead_letter_only=true").json()["entries"][0]
    failure_id = fault["failure_id"]

    response = client.post(
        f"/system/faults/{failure_id}/resolve",
        json={"reason": "Supervisor accepted residual risk", "dry_run": False},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "resolved"
    assert payload["failure_id"] == failure_id

    status = client.app.state.services.db.fetch_scalar(
        "SELECT status FROM failure_log WHERE id = ?",
        failure_id,
    )
    assert status == "resolved"

    metrics = client.get("/system/metrics?prefix=faults.remediation").json()["metrics"]
    metric_map = {item["metric_name"]: item["value"] for item in metrics}
    assert metric_map["faults.remediation.resolved"] >= 1

    audit_entries = client.get("/system/audit?action=fault.remediation.resolve").json()["entries"]
    assert any(item["entity_id"] == failure_id and item["status"] == "success" for item in audit_entries)

    resolved_faults = client.get("/system/faults?failure_status=resolved&dead_letter_only=false").json()["entries"]
    assert any(item["failure_id"] == failure_id for item in resolved_faults)


def test_45_fault_resolve_dry_run_and_conflict_when_already_resolved(client):
    goal = create_active_goal(client, title="Resolve Dry Run Goal")
    task = create_task(client, goal["goal_id"], title="Resolve Dry Run Task")
    client.post(
        f"/tasks/{task['task_id']}/fail",
        json={"failure_type": "ExecutionFailure", "error_message": "resolve dry run"},
    )
    client.post(
        f"/tasks/{task['task_id']}/fail",
        json={"failure_type": "ExecutionFailure", "error_message": "resolve dry run 2"},
    )
    client.post(
        f"/tasks/{task['task_id']}/fail",
        json={"failure_type": "ExecutionFailure", "error_message": "resolve dry run 3"},
    )

    fault = client.get("/system/faults?dead_letter_only=true").json()["entries"][0]
    failure_id = fault["failure_id"]

    dry_run = client.post(
        f"/system/faults/{failure_id}/resolve",
        json={"reason": "Preview close action", "dry_run": True},
    )
    assert dry_run.status_code == 200
    dry_payload = dry_run.json()
    assert dry_payload["dry_run"] is True
    assert dry_payload["allowed"] is True
    assert dry_payload["target_status"] == "resolved"

    apply_response = client.post(
        f"/system/faults/{failure_id}/resolve",
        json={"reason": "Apply close action", "dry_run": False},
    )
    assert apply_response.status_code == 200

    conflict = client.post(
        f"/system/faults/{failure_id}/resolve",
        json={"reason": "Second close attempt", "dry_run": False},
    )
    assert conflict.status_code == 409
    assert "already resolved" in conflict.json()["detail"]


def test_46_fault_resolve_bulk_dry_run_reports_candidates_without_writes(client):
    goal_a = create_active_goal(client, title="Bulk Dry Goal A")
    goal_b = create_active_goal(client, title="Bulk Dry Goal B")
    task_a = create_task(client, goal_a["goal_id"], title="Bulk Dry Task A")
    task_b = create_task(client, goal_b["goal_id"], title="Bulk Dry Task B")

    client.post(
        f"/tasks/{task_a['task_id']}/fail",
        json={"failure_type": "SkillFailure", "error_message": "bulk dry issue a"},
    )
    client.post(
        f"/tasks/{task_b['task_id']}/fail",
        json={"failure_type": "SkillFailure", "error_message": "bulk dry issue b"},
    )

    response = client.post(
        "/system/faults/resolve_bulk",
        json={
            "reason": "Preview bulk resolution",
            "dry_run": True,
            "failure_type": "SkillFailure",
            "task_status": "failed",
            "dead_letter_only": False,
            "limit": 10,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["dry_run"] is True
    assert payload["allowed"] is True
    assert payload["matched_count"] == 2
    assert payload["will_resolve_count"] == 2
    assert len(payload["candidate_failure_ids"]) == 2
    assert payload["skipped_already_resolved_count"] == 0

    recorded_count = client.app.state.services.db.fetch_scalar(
        "SELECT COUNT(*) FROM failure_log WHERE status = 'recorded'"
    )
    assert recorded_count == 2


def test_47_fault_resolve_bulk_applies_filtered_resolution_and_tracks_skip(client):
    goal_a = create_active_goal(client, title="Bulk Apply Goal A")
    goal_b = create_active_goal(client, title="Bulk Apply Goal B")
    task_a = create_task(client, goal_a["goal_id"], title="Bulk Apply Task A")
    task_b = create_task(client, goal_b["goal_id"], title="Bulk Apply Task B")

    client.post(
        f"/tasks/{task_a['task_id']}/fail",
        json={"failure_type": "ExecutionFailure", "error_message": "bulk apply issue a"},
    )
    client.post(
        f"/tasks/{task_b['task_id']}/fail",
        json={"failure_type": "ExecutionFailure", "error_message": "bulk apply issue b"},
    )

    faults = client.get(
        "/system/faults?failure_type=ExecutionFailure&task_status=failed&dead_letter_only=false"
    ).json()["entries"]
    assert len(faults) == 2
    first_failure_id = faults[0]["failure_id"]
    second_failure_id = faults[1]["failure_id"]

    single_resolve = client.post(
        f"/system/faults/{first_failure_id}/resolve",
        json={"reason": "Resolve one before bulk", "dry_run": False},
    )
    assert single_resolve.status_code == 200

    bulk_response = client.post(
        "/system/faults/resolve_bulk",
        json={
            "reason": "Resolve filtered execution failures",
            "dry_run": False,
            "failure_type": "ExecutionFailure",
            "task_status": "failed",
            "dead_letter_only": False,
            "limit": 10,
        },
    )
    assert bulk_response.status_code == 200
    payload = bulk_response.json()
    assert payload["dry_run"] is False
    assert payload["matched_count"] == 2
    assert payload["resolved_count"] == 1
    assert payload["skipped_already_resolved_count"] == 1
    assert set(payload["resolved_failure_ids"]) == {second_failure_id}
    assert set(payload["skipped_failure_ids"]) == {first_failure_id}

    unresolved_after = client.app.state.services.db.fetch_scalar(
        "SELECT COUNT(*) FROM failure_log WHERE status <> 'resolved'"
    )
    assert unresolved_after == 0

    metrics = client.get("/system/metrics?prefix=faults.remediation.bulk_resolved").json()["metrics"]
    assert metrics
    assert metrics[0]["value"] >= 1

    audit_entries = client.get("/system/audit?action=fault.remediation.resolve_bulk").json()["entries"]
    assert any(item["status"] == "success" for item in audit_entries)


def test_48_workflow_catalog_lists_seeded_definitions(client):
    response = client.get("/workflows")
    assert response.status_code == 200
    workflows = response.json()["workflows"]
    workflow_ids = {item["workflow_id"] for item in workflows}
    assert "scheduler.age_queue" in workflow_ids
    assert "scheduler.pick_next_goal" in workflow_ids
    assert "maintenance.retention_cleanup" in workflow_ids
    assert all(item["is_enabled"] is True for item in workflows)


def _wait_for_run_in_states(
    client,
    run_id: str,
    states: set[str],
    *,
    timeout_seconds: float = 2.0,
) -> dict:
    deadline = time.time() + timeout_seconds
    latest: dict | None = None
    while time.time() < deadline:
        response = client.get(f"/workflows/runs/{run_id}")
        assert response.status_code == 200
        latest = response.json()["run"]
        if latest["status"] in states:
            return latest
        time.sleep(0.02)
    raise AssertionError(f"Run {run_id} did not reach {states}. Last snapshot: {latest}")


def test_49_workflow_start_queues_and_worker_completes_run(client):
    response = client.post(
        "/workflows/maintenance.retention_cleanup/start",
        json={"requested_by": "workflow-test", "payload": {"source": "test"}},
    )
    assert response.status_code == 201
    queued_run = response.json()["run"]
    assert queued_run["workflow_id"] == "maintenance.retention_cleanup"
    assert queued_run["status"] in {"queued", "running", "succeeded"}
    assert queued_run["requested_by"] == "workflow-test"

    run = _wait_for_run_in_states(
        client,
        queued_run["run_id"],
        {"succeeded", "failed", "timed_out", "cancelled"},
    )
    assert run["status"] == "succeeded"
    assert run["result_payload"]["events_deleted"] >= 0

    runs = client.get("/workflows/runs").json()["runs"]
    assert any(item["run_id"] == run["run_id"] for item in runs)

    events = client.get(f"/events?entity_id={run['run_id']}").json()
    event_types = {item["event_type"] for item in events}
    assert "workflow.run.queued" in event_types
    assert "workflow.run.started" in event_types
    assert "workflow.run.succeeded" in event_types


def test_50_workflow_start_returns_404_for_unknown_definition(client):
    response = client.post(
        "/workflows/does.not.exist/start",
        json={"requested_by": "workflow-test", "payload": {}},
    )
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_51_disabled_workflow_cannot_be_started(client):
    services = client.app.state.services
    services.db.execute(
        "UPDATE workflow_definitions SET is_enabled = 0 WHERE workflow_id = ?",
        "scheduler.age_queue",
    )
    response = client.post(
        "/workflows/scheduler.age_queue/start",
        json={"requested_by": "workflow-test", "payload": {}},
    )
    assert response.status_code == 409
    assert "disabled" in response.json()["detail"].lower()


def test_52_workflow_run_listing_supports_workflow_filter(client):
    first = client.post(
        "/workflows/maintenance.retention_cleanup/start",
        json={"requested_by": "workflow-test", "payload": {"run": 1}},
    )
    second = client.post(
        "/workflows/scheduler.age_queue/start",
        json={"requested_by": "workflow-test", "payload": {"run": 2}},
    )
    assert first.status_code == 201
    assert second.status_code == 201
    _wait_for_run_in_states(
        client,
        first.json()["run"]["run_id"],
        {"succeeded", "failed", "timed_out", "cancelled"},
    )
    _wait_for_run_in_states(
        client,
        second.json()["run"]["run_id"],
        {"succeeded", "failed", "timed_out", "cancelled"},
    )

    response = client.get("/workflows/runs?workflow_id=maintenance.retention_cleanup")
    assert response.status_code == 200
    runs = response.json()["runs"]
    assert runs
    assert all(item["workflow_id"] == "maintenance.retention_cleanup" for item in runs)


def test_53_schema_migrations_record_workflow_hardening(services):
    row = services.db.fetch_one(
        "SELECT version, name FROM schema_migrations WHERE version = 1"
    )
    assert row is not None
    assert row["name"] == "workflow_runs_hardening"


def test_54_workflow_start_is_idempotent_with_idempotency_key(client):
    first = client.post(
        "/workflows/maintenance.retention_cleanup/start",
        headers={"Idempotency-Key": "workflow-start-abc"},
        json={"requested_by": "workflow-test", "payload": {"source": "first"}},
    )
    second = client.post(
        "/workflows/maintenance.retention_cleanup/start",
        headers={"Idempotency-Key": "workflow-start-abc"},
        json={"requested_by": "workflow-test", "payload": {"source": "second"}},
    )

    assert first.status_code == 201
    assert second.status_code == 201
    first_run = first.json()["run"]
    second_run = second.json()["run"]
    assert first_run["run_id"] == second_run["run_id"]
    assert first_run["idempotency_replay"] is False
    assert second_run["idempotency_replay"] is True
    _wait_for_run_in_states(
        client,
        first_run["run_id"],
        {"succeeded", "failed", "timed_out", "cancelled"},
    )

    count = client.app.state.services.db.fetch_scalar(
        "SELECT COUNT(*) FROM workflow_runs WHERE workflow_id = ? AND idempotency_key = ?",
        "maintenance.retention_cleanup",
        "workflow-start-abc",
    )
    assert count == 1


def test_55_workflow_reaper_marks_stale_runs_timed_out(client):
    services = client.app.state.services
    run_id = new_id()
    workflow_id = "maintenance.retention_cleanup"
    correlation_id = f"workflow:{workflow_id}:manual"
    services.db.execute(
        """INSERT INTO workflow_runs
           (run_id, workflow_id, status, requested_by, correlation_id, idempotency_key,
            input_payload, result_payload, started_at, finished_at, created_at, updated_at)
           VALUES (?, ?, 'running', 'operator', ?, NULL, '{}', NULL,
                   datetime('now', '-600 seconds'), NULL, datetime('now', '-600 seconds'), datetime('now', '-600 seconds'))""",
        run_id,
        workflow_id,
        correlation_id,
    )

    response = client.post("/workflows/runs/reap?timeout_seconds=60&limit=10")
    assert response.status_code == 200
    payload = response.json()
    assert payload["reaped_count"] >= 0

    row = services.db.fetch_one("SELECT status FROM workflow_runs WHERE run_id = ?", run_id)
    assert row["status"] == "timed_out"

    events = client.get(f"/events?entity_id={run_id}").json()
    assert any(item["event_type"] == "workflow.run.timed_out" for item in events)


def test_56_workflow_runs_status_constraint_blocks_invalid_value(services):
    with pytest.raises(sqlite3.IntegrityError):
        services.db.execute(
            """INSERT INTO workflow_runs
               (run_id, workflow_id, status, requested_by, correlation_id, idempotency_key,
                input_payload, result_payload, started_at, finished_at, created_at, updated_at)
               VALUES (?, ?, ?, 'operator', ?, NULL, '{}', NULL, ?, NULL, ?, ?)""",
            new_id(),
            "maintenance.retention_cleanup",
            "invalid_status",
            f"workflow:maintenance.retention_cleanup:{new_id()}",
            now_utc(),
            now_utc(),
            now_utc(),
        )


def test_57_workflow_run_timeout_marks_run_timed_out():
    app = create_app(Settings(database_url=":memory:", workflow_run_timeout_seconds=0))
    with TestClient(app) as local_client:
        services = local_client.app.state.services

        def slow_handler(_: dict) -> dict:
            time.sleep(0.01)
            return {"ok": True}

        services.workflow_catalog.handlers["maintenance.retention_cleanup"] = slow_handler
        response = local_client.post(
            "/workflows/maintenance.retention_cleanup/start",
            json={"requested_by": "workflow-test", "payload": {"source": "timeout"}},
        )
        assert response.status_code == 201
        run = _wait_for_run_in_states(
            local_client,
            response.json()["run"]["run_id"],
            {"timed_out", "failed", "succeeded", "cancelled"},
            timeout_seconds=3.0,
        )
        assert run["status"] == "timed_out"
        assert run["result_payload"]["error_type"] == "TimeoutError"


def test_58_workflow_runs_indexes_include_hardening_indexes(services):
    indexes = services.db.fetch_all("PRAGMA index_list('workflow_runs')")
    names = {row["name"] for row in indexes}
    assert "idx_workflow_runs_status_created_at" in names
    assert "idx_workflow_runs_correlation_id" in names
    assert "idx_workflow_runs_idempotency" in names


def test_59_cancel_workflow_run_while_running():
    app = create_app(
        Settings(
            database_url=":memory:",
            workflow_worker_poll_interval_seconds=0.05,
            workflow_run_timeout_seconds=5,
        )
    )
    with TestClient(app) as local_client:
        services = local_client.app.state.services
        started = threading.Event()
        release = threading.Event()

        def blocking_handler(_: dict) -> dict:
            started.set()
            release.wait(timeout=1.0)
            return {"ok": True}

        services.workflow_catalog.handlers["maintenance.retention_cleanup"] = blocking_handler
        queued = local_client.post(
            "/workflows/maintenance.retention_cleanup/start",
            json={"requested_by": "workflow-test", "payload": {"source": "cancel"}},
        )
        assert queued.status_code == 201
        run_id = queued.json()["run"]["run_id"]
        assert started.wait(timeout=1.0)

        cancelled = local_client.post(
            f"/workflows/runs/{run_id}/cancel",
            json={"requested_by": "workflow-test", "reason": "Manual cancel"},
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["run"]["status"] == "cancelled"

        release.set()
        final_run = _wait_for_run_in_states(
            local_client,
            run_id,
            {"cancelled", "succeeded", "failed", "timed_out"},
            timeout_seconds=2.0,
        )
        assert final_run["status"] == "cancelled"

        events = local_client.get(f"/events?entity_id={run_id}").json()
        event_types = {item["event_type"] for item in events}
        assert "workflow.run.cancelled" in event_types


def test_60_cancel_terminal_workflow_run_returns_409(client):
    started = client.post(
        "/workflows/maintenance.retention_cleanup/start",
        json={"requested_by": "workflow-test", "payload": {"source": "terminal"}},
    )
    assert started.status_code == 201
    run_id = started.json()["run"]["run_id"]
    final_run = _wait_for_run_in_states(
        client,
        run_id,
        {"succeeded", "failed", "timed_out", "cancelled"},
    )
    assert final_run["status"] == "succeeded"

    cancel = client.post(
        f"/workflows/runs/{run_id}/cancel",
        json={"requested_by": "workflow-test", "reason": "too late"},
    )
    assert cancel.status_code == 409


def test_61_system_readiness_reports_ready(client):
    deadline = time.time() + 2.0
    payload: dict | None = None
    while time.time() < deadline:
        response = client.get("/system/readiness")
        assert response.status_code == 200
        payload = response.json()
        if payload["ready"]:
            break
        time.sleep(0.05)

    assert payload is not None
    assert payload["ready"] is True
    assert payload["checks"]["database"]["ok"] is True
    assert payload["checks"]["workflow_worker"]["ok"] is True
    assert payload["checks"]["workflow_worker"]["is_running"] is True


def test_62_system_readiness_reports_not_ready_when_worker_stopped():
    app = create_app(Settings(database_url=":memory:"))
    with TestClient(app) as local_client:
        local_client.app.state.services.workflow_catalog.stop_worker()
        response = local_client.get("/system/readiness")
        assert response.status_code == 200
        payload = response.json()
        assert payload["ready"] is False
        assert payload["checks"]["database"]["ok"] is True
        assert payload["checks"]["workflow_worker"]["ok"] is False
        assert payload["checks"]["workflow_worker"]["is_running"] is False


def test_63_system_diagnostics_exports_snapshot():
    diagnostics_dir = _local_test_dir("pytest-system-diagnostics")
    try:
        app = create_app(Settings(database_url=":memory:", diagnostics_dir=str(diagnostics_dir)))
        with TestClient(app) as local_client:
            response = local_client.post("/system/diagnostics")
            assert response.status_code == 200
            payload = response.json()

            file_path = Path(payload["file_path"])
            assert file_path.exists()
            assert file_path.parent == diagnostics_dir
            assert payload["ready"] is True

            snapshot = json.loads(file_path.read_text(encoding="utf-8"))
            assert snapshot["readiness"]["ready"] is True
            assert snapshot["slo"]["status"] == "ok"
            assert "health" in snapshot
            assert "recent_workflow_runs" in snapshot
            assert snapshot["database"]["integrity"]["ok"] is True
            assert snapshot["database"]["migrations"]["pending_versions"] == []
    finally:
        shutil.rmtree(diagnostics_dir, ignore_errors=True)


def test_64_workflow_worker_survives_claim_lock_conflict(monkeypatch):
    app = create_app(
        Settings(
            database_url=":memory:",
            workflow_worker_poll_interval_seconds=0.05,
        )
    )
    with TestClient(app) as local_client:
        catalog = local_client.app.state.services.workflow_catalog
        original_claim = catalog._claim_next_queued_run
        fail_once = {"remaining": 1}

        def _flaky_claim():
            if fail_once["remaining"] > 0:
                fail_once["remaining"] -= 1
                raise sqlite3.OperationalError("database table is locked: workflow_runs")
            return original_claim()

        monkeypatch.setattr(catalog, "_claim_next_queued_run", _flaky_claim)

        started = local_client.post(
            "/workflows/maintenance.retention_cleanup/start",
            json={"requested_by": "workflow-test", "payload": {"source": "lock-conflict"}},
        )
        assert started.status_code == 201
        run_id = started.json()["run"]["run_id"]

        final_run = _wait_for_run_in_states(
            local_client,
            run_id,
            {"succeeded", "failed", "timed_out", "cancelled"},
            timeout_seconds=3.0,
        )
        assert final_run["status"] == "succeeded"
        assert fail_once["remaining"] == 0
        assert catalog.worker_status()["is_running"] is True


def test_65_database_creates_backup_before_pending_migration():
    temp_dir = _local_test_dir("pytest-db-migration-backup")
    db_path = temp_dir / "goal_ops.db"
    backup_dir = temp_dir / "migration-backups"
    try:
        baseline = Database(str(db_path))
        try:
            baseline.initialize()
        except sqlite3.OperationalError as exc:
            if "disk i/o error" in str(exc).lower():
                pytest.skip("File-backed SQLite is unavailable in this sandbox")
            raise
        baseline.execute("DELETE FROM schema_migrations WHERE version = 1")

        migrating = Database(str(db_path), migration_backup_dir=str(backup_dir))
        migrating.initialize()

        migration_state = migrating.migration_status()
        assert migration_state["pending_versions"] == []
        assert migration_state["last_backup_versions"] == [1]
        assert migration_state["last_backup_path"] is not None
        backup_path = Path(str(migration_state["last_backup_path"]))
        assert backup_path.exists()
        assert backup_path.parent == backup_dir
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_66_system_database_integrity_endpoint_reports_status(client):
    response = client.get("/system/database/integrity")
    assert response.status_code == 200
    payload = response.json()
    assert payload["integrity"]["ok"] is True
    assert payload["integrity"]["mode"] == "quick"
    assert payload["integrity"]["result"] == "ok"
    assert payload["migrations"]["pending_versions"] == []

    full = client.get("/system/database/integrity?mode=full")
    assert full.status_code == 200
    full_payload = full.json()
    assert full_payload["integrity"]["ok"] is True
    assert full_payload["integrity"]["mode"] == "full"


def test_67_system_database_integrity_rejects_invalid_mode(client):
    response = client.get("/system/database/integrity?mode=invalid")
    assert response.status_code == 422


def test_68_release_gate_probe_reports_memory_database():
    project_root = Path(__file__).resolve().parents[1]
    command = [
        sys.executable,
        str(project_root / "scripts" / "release-gate-probe.py"),
        "--database-url",
        ":memory:",
        "--expected-db-kind",
        "memory",
        "--label",
        "pytest-memory",
    ]
    completed = subprocess.run(
        command,
        cwd=project_root,
        capture_output=True,
        text=True,
        check=True,
    )
    output_lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    payload = json.loads(output_lines[-1])
    assert payload["database_kind"] == "memory"
    assert payload["readiness_ready"] is True
    assert payload["integrity_quick_ok"] is True
    assert payload["integrity_full_ok"] is True
    assert payload["pending_migrations"] == []


def test_69_backup_restore_drill_reports_success():
    workspace = _local_test_dir("pytest-backup-restore-drill")
    project_root = Path(__file__).resolve().parents[1]
    command = [
        sys.executable,
        str(project_root / "scripts" / "backup-restore-drill.py"),
        "--workspace",
        str(workspace),
        "--label",
        "pytest-drill",
    ]
    completed = subprocess.run(
        command,
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0 and "disk i/o error" in completed.stderr.lower():
        shutil.rmtree(workspace, ignore_errors=True)
        pytest.skip("File-backed SQLite is unavailable in this sandbox")
    assert completed.returncode == 0, completed.stderr

    output_lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    payload = json.loads(output_lines[-1])
    assert payload["success"] is True
    assert payload["restore_matches_source"] is True
    assert payload["restored_integrity"]["quick_ok"] is True
    assert payload["restored_integrity"]["full_ok"] is True
    assert payload["seed_validation"]["ok"] is True
    shutil.rmtree(workspace, ignore_errors=True)


def test_70_system_slo_reports_ok_by_default(client):
    response = client.get("/system/slo")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["alert_count"] == 0
    assert payload["checks"]["readiness"]["ready"] is True
    assert payload["checks"]["database_integrity"]["ok"] is True


def test_71_system_slo_reports_degraded_when_429_rate_exceeds_threshold(client):
    services = client.app.state.services
    services.observability.increment_metric("http.requests.total", delta=200)
    services.observability.increment_metric("http.requests.status.429", delta=20)

    response = client.get("/system/slo")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "degraded"
    assert any(alert["code"] == "http.429_rate_high" for alert in payload["alerts"])


def test_72_system_slo_reports_critical_when_readiness_fails():
    app = create_app(Settings(database_url=":memory:"))
    with TestClient(app) as local_client:
        local_client.app.state.services.workflow_catalog.stop_worker()
        response = local_client.get("/system/slo")
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "critical"
        assert any(alert["code"] == "readiness.not_ready" for alert in payload["alerts"])


def test_73_slo_alert_check_reports_ok_memory():
    project_root = Path(__file__).resolve().parents[1]
    command = [
        sys.executable,
        str(project_root / "scripts" / "slo-alert-check.py"),
        "--database-url",
        ":memory:",
        "--allowed-status",
        "ok",
    ]
    completed = subprocess.run(
        command,
        cwd=project_root,
        capture_output=True,
        text=True,
        check=True,
    )
    output_lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    payload = json.loads(output_lines[-1])
    assert payload["observed_status"] == "ok"
    assert payload["allowed_status"] == "ok"


def test_74_incident_rollback_drill_reports_success():
    workspace = _local_test_dir("pytest-incident-rollback-drill")
    project_root = Path(__file__).resolve().parents[1]
    command = [
        sys.executable,
        str(project_root / "scripts" / "incident-rollback-drill.py"),
        "--workspace",
        str(workspace),
        "--label",
        "pytest-drill",
        "--load-requests",
        "30",
    ]
    completed = subprocess.run(
        command,
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0 and "powershell executable not found" in completed.stderr.lower():
        shutil.rmtree(workspace, ignore_errors=True)
        pytest.skip("PowerShell is unavailable in this environment")
    assert completed.returncode == 0, completed.stderr

    output_lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    payload = json.loads(output_lines[-1])
    assert payload["success"] is True
    assert payload["incident"]["detected"] is True
    assert payload["rollback"]["ok"] is True
    assert payload["incident"]["slo_status"] in {"degraded", "critical"}
    assert payload["incident"]["load"]["throttled_count"] > 0
    shutil.rmtree(workspace, ignore_errors=True)


def test_75_migration_rehearsal_reports_success():
    workspace = _local_test_dir("pytest-migration-rehearsal")
    project_root = Path(__file__).resolve().parents[1]
    command = [
        sys.executable,
        str(project_root / "scripts" / "migration-rehearsal.py"),
        "--workspace",
        str(workspace),
        "--label",
        "pytest-drill",
        "--small-runs",
        "120",
        "--medium-runs",
        "360",
        "--large-runs",
        "720",
        "--payload-bytes",
        "256",
    ]
    completed = subprocess.run(
        command,
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0 and "disk i/o error" in completed.stderr.lower():
        shutil.rmtree(workspace, ignore_errors=True)
        pytest.skip("File-backed SQLite is unavailable in this sandbox")
    assert completed.returncode == 0, completed.stderr

    output_lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    payload = json.loads(output_lines[-1])
    assert payload["success"] is True
    assert payload["decision"]["release_blocked"] is False
    assert payload["decision"]["recommended_action"] == "proceed"
    assert len(payload["scenarios"]) == 3
    for scenario in payload["scenarios"]:
        assert scenario["success"] is True
        assert scenario["checks"]["migration_backup_created"] is True
        assert scenario["checks"]["pending_migrations_cleared"] is True
        assert scenario["checks"]["backup_within_threshold"] is True
        assert scenario["checks"]["restore_within_threshold"] is True
        assert scenario["checks"]["migration_within_threshold"] is True
    shutil.rmtree(workspace, ignore_errors=True)


def test_76_auto_rollback_policy_triggers_and_executes_rollback():
    workspace = _local_test_dir("pytest-auto-rollback-policy")
    project_root = Path(__file__).resolve().parents[1]
    manifest_path = workspace / "desktop-rings.json"
    command = [
        sys.executable,
        str(project_root / "scripts" / "auto-rollback-policy.py"),
        "--workspace",
        str(workspace),
        "--label",
        "pytest-drill",
        "--manifest-path",
        str(manifest_path),
        "--ring",
        "stable",
        "--mock-slo-statuses",
        "critical,critical,critical,critical",
        "--critical-window-seconds",
        "2",
        "--poll-interval-seconds",
        "1",
        "--max-observation-seconds",
        "8",
        "--seed-previous-version",
        "0.0.1",
        "--seed-incident-version",
        "0.0.2",
    ]
    completed = subprocess.run(
        command,
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0 and "powershell executable not found" in completed.stderr.lower():
        shutil.rmtree(workspace, ignore_errors=True)
        pytest.skip("PowerShell is unavailable in this environment")
    assert completed.returncode == 0, completed.stderr

    output_lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    payload = json.loads(output_lines[-1])
    assert payload["success"] is True
    assert payload["observation"]["triggered"] is True
    assert payload["rollback"]["attempted"] is True
    assert payload["rollback"]["executed"] is True
    assert payload["decision"]["recommended_action"] == "rollback_executed"
    stable_pre = payload["rollback"]["pre_state"]["rings"]["stable"]
    stable_post = payload["rollback"]["post_state"]["rings"]["stable"]
    assert stable_pre["version"] == "0.0.2"
    assert stable_pre["rollback_version"] == "0.0.1"
    assert stable_post["version"] == "0.0.1"
    assert stable_post["rollback_version"] == "0.0.2"
    shutil.rmtree(workspace, ignore_errors=True)


def test_77_desktop_update_safety_drill_reports_success():
    workspace = _local_test_dir("pytest-desktop-update-safety-drill")
    project_root = Path(__file__).resolve().parents[1]
    command = [
        sys.executable,
        str(project_root / "scripts" / "desktop-update-safety-drill.py"),
        "--workspace",
        str(workspace),
        "--label",
        "pytest-drill",
    ]
    completed = subprocess.run(
        command,
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0 and "powershell executable not found" in completed.stderr.lower():
        shutil.rmtree(workspace, ignore_errors=True)
        pytest.skip("PowerShell is unavailable in this environment")
    assert completed.returncode == 0, completed.stderr

    output_lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    payload = json.loads(output_lines[-1])
    assert payload["success"] is True
    assert payload["cases"]["successful_update"]["ok"] is True
    assert payload["cases"]["tampered_hash_blocked"]["ok"] is True
    assert payload["cases"]["fallback_after_copy_failure"]["ok"] is True
    shutil.rmtree(workspace, ignore_errors=True)


def test_78_recover_interrupted_runs_marks_running_status_failed(client):
    services = client.app.state.services
    services.workflow_catalog.stop_worker()

    run_id = new_id()
    created_at = now_utc()
    services.db.execute(
        """INSERT INTO workflow_runs
           (run_id, workflow_id, status, requested_by, correlation_id, idempotency_key,
            input_payload, result_payload, started_at, finished_at, created_at, updated_at)
           VALUES (?, ?, 'running', ?, ?, NULL, '{}', NULL, ?, NULL, ?, ?)""",
        run_id,
        "maintenance.retention_cleanup",
        "startup-recovery-test",
        f"workflow:maintenance.retention_cleanup:{run_id[:8]}",
        created_at,
        created_at,
        created_at,
    )

    recovered = services.workflow_catalog.recover_interrupted_runs(max_age_seconds=0, limit=10)
    run = client.get(f"/workflows/runs/{run_id}").json()["run"]

    assert recovered["recovered_count"] == 1
    assert recovered["run_ids"] == [run_id]
    assert run["status"] == "failed"
    assert run["result_payload"]["error_type"] == "ProcessAbortRecovery"

    services.workflow_catalog.start_worker()


def test_79_recovery_hard_abort_drill_reports_success():
    workspace = _local_test_dir("pytest-recovery-hard-abort-drill")
    project_root = Path(__file__).resolve().parents[1]
    command = [
        sys.executable,
        str(project_root / "scripts" / "recovery-hard-abort-drill.py"),
        "--workspace",
        str(workspace),
        "--label",
        "pytest-drill",
    ]
    completed = subprocess.run(
        command,
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0 and "disk i/o error" in completed.stderr.lower():
        shutil.rmtree(workspace, ignore_errors=True)
        pytest.skip("File-backed SQLite is unavailable in this sandbox")
    assert completed.returncode == 0, completed.stderr

    output_lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    payload = json.loads(output_lines[-1])
    assert payload["success"] is True
    assert payload["recovery"]["status_before_abort"] == "running"
    assert payload["recovery"]["status_after_restart"] == "failed"
    assert payload["recovery"]["error_type_after_restart"] == "ProcessAbortRecovery"
    assert payload["recovery"]["readiness_ready"] is True
    shutil.rmtree(workspace, ignore_errors=True)


def test_80_migration_rehearsal_supports_optional_xlarge_scenario():
    workspace = _local_test_dir("pytest-migration-rehearsal-xlarge")
    project_root = Path(__file__).resolve().parents[1]
    command = [
        sys.executable,
        str(project_root / "scripts" / "migration-rehearsal.py"),
        "--workspace",
        str(workspace),
        "--label",
        "pytest-xlarge",
        "--small-runs",
        "80",
        "--medium-runs",
        "160",
        "--large-runs",
        "240",
        "--xlarge-runs",
        "320",
        "--payload-bytes",
        "128",
    ]
    completed = subprocess.run(
        command,
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0 and "disk i/o error" in completed.stderr.lower():
        shutil.rmtree(workspace, ignore_errors=True)
        pytest.skip("File-backed SQLite is unavailable in this sandbox")
    assert completed.returncode == 0, completed.stderr

    output_lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    payload = json.loads(output_lines[-1])
    scenario_names = [scenario["scenario"] for scenario in payload["scenarios"]]
    assert payload["success"] is True
    assert scenario_names == ["small", "medium", "large", "xlarge"]
    shutil.rmtree(workspace, ignore_errors=True)


def test_81_workflow_lock_resilience_drill_reports_success():
    project_root = Path(__file__).resolve().parents[1]
    command = [
        sys.executable,
        str(project_root / "scripts" / "workflow-lock-resilience-drill.py"),
        "--lock-failures",
        "5",
        "--timeout-seconds",
        "12",
    ]
    completed = subprocess.run(
        command,
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr

    output_lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    payload = json.loads(output_lines[-1])
    assert payload["success"] is True
    assert payload["run"]["status"] == "succeeded"
    assert payload["lock_conflict_metric"] >= payload["requested_lock_failures"]
    assert payload["worker_status"]["is_running"] is True


def test_82_workflow_soak_drill_reports_success():
    project_root = Path(__file__).resolve().parents[1]
    command = [
        sys.executable,
        str(project_root / "scripts" / "workflow-soak-drill.py"),
        "--run-count",
        "30",
        "--timeout-seconds",
        "25",
    ]
    completed = subprocess.run(
        command,
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr

    output_lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    payload = json.loads(output_lines[-1])
    assert payload["success"] is True
    assert payload["run_count"] == 30
    assert payload["status_counts"]["succeeded"] == 30
    assert payload["readiness"]["ready"] is True
    assert payload["slo_status"] == "ok"


def test_83_start_workflow_restarts_worker_if_stopped(client):
    catalog = client.app.state.services.workflow_catalog
    catalog.stop_worker()
    assert catalog.worker_status()["is_running"] is False

    started = client.post(
        "/workflows/maintenance.retention_cleanup/start",
        json={"requested_by": "workflow-test", "payload": {"source": "worker-restart"}},
    )
    assert started.status_code == 201
    run_id = started.json()["run"]["run_id"]

    final_run = _wait_for_run_in_states(
        client,
        run_id,
        {"succeeded", "failed", "timed_out", "cancelled"},
        timeout_seconds=3.0,
    )
    assert final_run["status"] == "succeeded"
    assert catalog.worker_status()["is_running"] is True


def test_84_system_readiness_reports_not_ready_on_startup_recovery_error(client):
    catalog = client.app.state.services.workflow_catalog
    catalog.ensure_worker_running()
    catalog._startup_recovery_state = {
        "executed": True,
        "recovered_count": 0,
        "run_ids": [],
        "error": "startup recovery failed in test",
        "at_utc": now_utc(),
        "max_age_seconds": 0,
    }

    response = client.get("/system/readiness")
    assert response.status_code == 200
    payload = response.json()
    worker = payload["checks"]["workflow_worker"]

    assert payload["ready"] is False
    assert worker["is_running"] is True
    assert worker["ok"] is False
    assert worker["startup_recovery_ok"] is False
    assert worker["startup_recovery_error"] == "startup recovery failed in test"


def test_85_workflow_worker_restart_drill_reports_success():
    project_root = Path(__file__).resolve().parents[1]
    command = [
        sys.executable,
        str(project_root / "scripts" / "workflow-worker-restart-drill.py"),
        "--timeout-seconds",
        "12",
    ]
    completed = subprocess.run(
        command,
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr

    output_lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    payload = json.loads(output_lines[-1])
    assert payload["success"] is True
    assert payload["before"]["ready"] is False
    assert payload["before"]["worker_running"] is False
    assert payload["after"]["ready"] is True
    assert payload["after"]["worker_running"] is True
    assert payload["run"]["status"] == "succeeded"


def test_86_event_consumer_recovery_chaos_drill_reports_success():
    project_root = Path(__file__).resolve().parents[1]
    command = [
        sys.executable,
        str(project_root / "scripts" / "event-consumer-recovery-chaos-drill.py"),
        "--goal-count",
        "20",
        "--stale-processing-count",
        "8",
        "--drain-batch-size",
        "80",
        "--timeout-seconds",
        "12",
    ]
    completed = subprocess.run(
        command,
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr

    output_lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    payload = json.loads(output_lines[-1])
    assert payload["success"] is True
    assert payload["reclaimed_count"] >= payload["stale_processing_target"]
    assert payload["status_counts"].get("processing", 0) == 0
    assert payload["status_counts"].get("failed", 0) == 0
    assert payload["readiness_ready"] is True
    assert payload["slo_status"] == "ok"


def test_87_invariant_burst_drill_reports_success():
    project_root = Path(__file__).resolve().parents[1]
    command = [
        sys.executable,
        str(project_root / "scripts" / "invariant-burst-drill.py"),
        "--goal-count",
        "24",
    ]
    completed = subprocess.run(
        command,
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr

    output_lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    payload = json.loads(output_lines[-1])
    assert payload["success"] is True
    assert payload["goal_count"] == 24
    assert payload["invariant_violations"]["direct"] == []
    assert payload["invariant_violations"]["health"] == []
    assert payload["readiness_ready"] is True


def test_88_long_soak_budget_drill_reports_success():
    project_root = Path(__file__).resolve().parents[1]
    command = [
        sys.executable,
        str(project_root / "scripts" / "long-soak-budget-drill.py"),
        "--duration-seconds",
        "6",
        "--max-p95-latency-ms",
        "500",
        "--max-p99-latency-ms",
        "800",
        "--max-max-latency-ms",
        "5000",
        "--max-http-429-rate-percent",
        "1.0",
        "--max-error-rate-percent",
        "1.0",
        "--min-requests",
        "120",
        "--drain-batch-size",
        "120",
        "--workflow-start-every-cycles",
        "0",
    ]
    completed = subprocess.run(
        command,
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr

    output_lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    payload = json.loads(output_lines[-1])
    assert payload["success"] is True
    assert payload["requests_total"] >= 120
    assert payload["latency_ms"]["p95"] <= 500
    assert payload["latency_ms"]["p99"] <= 800
    assert payload["latency_ms"]["max"] <= 5000
    assert payload["observed_rates_percent"]["http_429_rate"] <= 1.0
    assert payload["observed_rates_percent"]["error_rate"] <= 1.0
    assert payload["readiness_ready"] is True
    assert payload["slo_status"] == "ok"


def test_89_release_freeze_policy_drill_reports_success():
    workspace = _local_test_dir("pytest-release-freeze-policy")
    project_root = Path(__file__).resolve().parents[1]
    manifest_path = workspace / "desktop-rings.json"
    command = [
        sys.executable,
        str(project_root / "scripts" / "release-freeze-policy.py"),
        "--workspace",
        str(workspace),
        "--label",
        "pytest-drill",
        "--manifest-path",
        str(manifest_path),
        "--ring",
        "stable",
        "--mock-slo-statuses",
        "degraded,critical,critical,critical",
        "--mock-error-budget-burn-rates",
        "0.5,1.0,2.5,2.5",
        "--non-ok-window-seconds",
        "2",
        "--poll-interval-seconds",
        "1",
        "--max-observation-seconds",
        "8",
        "--max-error-budget-burn-rate-percent",
        "2.0",
        "--seed-previous-version",
        "0.0.1",
        "--seed-incident-version",
        "0.0.2",
        "--promotion-test-version",
        "0.0.3",
    ]
    completed = subprocess.run(
        command,
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0 and "powershell executable not found" in completed.stderr.lower():
        shutil.rmtree(workspace, ignore_errors=True)
        pytest.skip("PowerShell is unavailable in this environment")
    assert completed.returncode == 0, completed.stderr

    output_lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    payload = json.loads(output_lines[-1])
    assert payload["success"] is True
    assert payload["observation"]["triggered"] is True
    assert payload["freeze"]["executed"] is True
    assert payload["promotion_block_verification"]["blocked"] is True
    shutil.rmtree(workspace, ignore_errors=True)


def test_90_db_safe_mode_watchdog_drill_reports_success():
    project_root = Path(__file__).resolve().parents[1]
    command = [
        sys.executable,
        str(project_root / "scripts" / "db-safe-mode-watchdog-drill.py"),
        "--lock-error-injections",
        "4",
    ]
    completed = subprocess.run(
        command,
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr

    output_lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    payload = json.loads(output_lines[-1])
    assert payload["success"] is True
    assert payload["safe_mode_active_after_injection"] is True
    assert payload["blocked_status_code"] == 503
    assert payload["allowed_reclaim_status_code"] == 200
    assert payload["safe_mode_active_after_disable"] is False
    assert payload["post_disable_goal_create_status_code"] == 201


def test_91_invariant_monitor_watchdog_drill_reports_success():
    project_root = Path(__file__).resolve().parents[1]
    command = [
        sys.executable,
        str(project_root / "scripts" / "invariant-monitor-watchdog-drill.py"),
        "--timeout-seconds",
        "8",
    ]
    completed = subprocess.run(
        command,
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr

    output_lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    payload = json.loads(output_lines[-1])
    assert payload["success"] is True
    assert payload["detected_violation_count"] > 0
    assert payload["safe_mode_active_after_detection"] is True
    assert payload["blocked_status_code"] == 503


def test_92_mutation_idempotency_replays_goal_create_response(client):
    headers = {"Idempotency-Key": "goal-create-idem-1"}
    payload = {
        "title": "Idempotent Goal",
        "description": "ensure replay",
        "urgency": 0.5,
        "value": 0.5,
        "deadline_score": 0.2,
    }

    first = client.post("/goals", json=payload, headers=headers)
    second = client.post("/goals", json=payload, headers=headers)
    assert first.status_code == 201
    assert second.status_code == 201
    assert second.headers.get("x-idempotency-replay") == "true"

    first_goal = first.json()
    second_goal = second.json()
    assert first_goal["goal_id"] == second_goal["goal_id"]

    rows = client.app.state.services.db.fetch_all("SELECT goal_id FROM goals WHERE title = ?", "Idempotent Goal")
    assert len(rows) == 1


def test_93_mutation_idempotency_rejects_payload_mismatch(client):
    headers = {"Idempotency-Key": "goal-create-idem-2"}
    first = client.post(
        "/goals",
        json={
            "title": "Idempotent A",
            "description": "first",
            "urgency": 0.4,
            "value": 0.5,
            "deadline_score": 0.1,
        },
        headers=headers,
    )
    second = client.post(
        "/goals",
        json={
            "title": "Idempotent B",
            "description": "second",
            "urgency": 0.7,
            "value": 0.6,
            "deadline_score": 0.3,
        },
        headers=headers,
    )
    assert first.status_code == 201
    assert second.status_code == 409
    assert "different payload" in second.json()["detail"].lower()


def test_94_stability_canary_reports_success_with_short_soak():
    workspace = _local_test_dir("pytest-stability-canary")
    project_root = Path(__file__).resolve().parents[1]
    baseline_file = workspace / "baseline.json"
    report_file = workspace / "report.json"
    baseline_file.write_text(
        json.dumps(
            {
                "max_duration_regression_percent": 10_000.0,
                "drills": {
                    "release_freeze_policy": {"baseline_duration_seconds": 0.1},
                    "db_corruption_quarantine": {"baseline_duration_seconds": 0.1},
                    "power_loss_durability": {"baseline_duration_seconds": 0.1},
                    "upgrade_downgrade_compatibility": {"baseline_duration_seconds": 0.1},
                    "db_safe_mode_watchdog": {"baseline_duration_seconds": 0.1},
                    "invariant_monitor_watchdog": {"baseline_duration_seconds": 0.1},
                    "event_consumer_recovery_chaos": {"baseline_duration_seconds": 0.1},
                    "invariant_burst": {"baseline_duration_seconds": 0.1},
                    "long_soak_budget": {
                        "baseline_duration_seconds": 0.1,
                        "max_http_429_rate_percent": 1.0,
                        "max_error_rate_percent": 1.0,
                    },
                },
            },
            ensure_ascii=True,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    command = [
        sys.executable,
        str(project_root / "scripts" / "stability-canary.py"),
        "--baseline-file",
        str(baseline_file),
        "--output-file",
        str(report_file),
        "--long-soak-duration-seconds",
        "6",
    ]
    completed = subprocess.run(
        command,
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0 and "powershell executable not found" in completed.stderr.lower():
        shutil.rmtree(workspace, ignore_errors=True)
        pytest.skip("PowerShell is unavailable in this environment")
    assert completed.returncode == 0, completed.stderr

    output_lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    payload = json.loads(output_lines[-1])
    assert payload["success"] is True
    assert report_file.exists()
    shutil.rmtree(workspace, ignore_errors=True)


def test_95_startup_db_corruption_quarantine_activates_safe_mode():
    workspace = _local_test_dir("pytest-db-corruption-startup-recovery")
    db_path = workspace / "corrupted.db"
    quarantine_dir = workspace / "quarantine"
    db_path.write_bytes(b"this is not a sqlite database file")

    app = create_app(
        Settings(
            database_url=str(db_path),
            db_quarantine_dir=str(quarantine_dir),
            db_startup_corruption_recovery_enabled=True,
            workflow_worker_poll_interval_seconds=0.05,
        )
    )
    with TestClient(app) as local_client:
        readiness_before = local_client.get("/system/readiness")
        assert readiness_before.status_code == 200
        readiness_before_payload = readiness_before.json()

        startup_recovery = readiness_before_payload["checks"]["database"]["startup_recovery"]
        assert readiness_before_payload["ready"] is False
        assert startup_recovery["triggered"] is True
        assert startup_recovery["recovered"] is True
        assert startup_recovery["quarantined_exists"] is True
        assert startup_recovery["quarantined_path"]

        safe_mode = local_client.get("/system/safe-mode").json()
        assert safe_mode["active"] is True
        assert safe_mode["source"] == "db_startup_recovery"

        blocked = local_client.post(
            "/goals",
            json={
                "title": "Blocked by startup recovery safe mode",
                "description": "mutation should be blocked",
                "urgency": 0.4,
                "value": 0.5,
                "deadline_score": 0.2,
            },
        )
        assert blocked.status_code == 503

        disable = local_client.post(
            "/system/safe-mode/disable",
            json={"reason": "Startup corruption recovered and validated in test."},
        )
        assert disable.status_code == 200

        created = local_client.post(
            "/goals",
            json={
                "title": "Allowed after startup recovery",
                "description": "safe mode disabled",
                "urgency": 0.6,
                "value": 0.7,
                "deadline_score": 0.3,
            },
        )
        assert created.status_code == 201

        readiness_after = local_client.get("/system/readiness")
        assert readiness_after.status_code == 200
        assert readiness_after.json()["ready"] is True

        integrity = local_client.get("/system/database/integrity?mode=quick")
        assert integrity.status_code == 200
        startup_recovery_integrity = integrity.json()["startup_recovery"]
        assert startup_recovery_integrity["triggered"] is True
        assert startup_recovery_integrity["recovered"] is True
        assert startup_recovery_integrity["quarantined_exists"] is True

    shutil.rmtree(workspace, ignore_errors=True)


def test_96_db_corruption_quarantine_drill_reports_success():
    workspace = _local_test_dir("pytest-db-corruption-quarantine-drill")
    project_root = Path(__file__).resolve().parents[1]
    command = [
        sys.executable,
        str(project_root / "scripts" / "db-corruption-quarantine-drill.py"),
        "--workspace",
        str(workspace),
        "--label",
        "pytest-drill",
        "--corruption-bytes",
        "192",
    ]
    completed = subprocess.run(
        command,
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0 and "disk i/o error" in completed.stderr.lower():
        shutil.rmtree(workspace, ignore_errors=True)
        pytest.skip("File-backed SQLite is unavailable in this sandbox")
    assert completed.returncode == 0, completed.stderr

    output_lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    payload = json.loads(output_lines[-1])
    assert payload["success"] is True
    assert payload["startup_recovery"]["triggered"] is True
    assert payload["startup_recovery"]["recovered"] is True
    assert payload["blocked_status_code"] == 503
    assert payload["post_disable_goal_create_status_code"] == 201
    shutil.rmtree(workspace, ignore_errors=True)


def test_97_upgrade_downgrade_compatibility_drill_reports_success():
    workspace = _local_test_dir("pytest-upgrade-downgrade-compatibility-drill")
    project_root = Path(__file__).resolve().parents[1]
    command = [
        sys.executable,
        str(project_root / "scripts" / "upgrade-downgrade-compatibility-drill.py"),
        "--workspace",
        str(workspace),
        "--label",
        "pytest-drill",
        "--n-minus-1-runs",
        "120",
        "--payload-bytes",
        "128",
        "--max-upgrade-ms",
        "15000",
        "--max-rollback-restore-ms",
        "15000",
        "--max-reupgrade-ms",
        "15000",
    ]
    completed = subprocess.run(
        command,
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0 and "disk i/o error" in completed.stderr.lower():
        shutil.rmtree(workspace, ignore_errors=True)
        pytest.skip("File-backed SQLite is unavailable in this sandbox")
    assert completed.returncode == 0, completed.stderr

    output_lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    payload = json.loads(output_lines[-1])
    assert payload["success"] is True
    assert payload["probes"]["upgrade"]["readiness_ready"] is True
    assert payload["probes"]["upgrade"]["slo_status"] == "ok"
    assert payload["probes"]["reupgrade"]["readiness_ready"] is True
    assert payload["probes"]["reupgrade"]["slo_status"] == "ok"
    assert payload["snapshots"]["n_minus_1"]["schema"]["has_idempotency_key"] is False
    assert payload["snapshots"]["upgrade"]["schema"]["has_idempotency_key"] is True
    assert payload["snapshots"]["rollback"]["schema"]["has_idempotency_key"] is False
    shutil.rmtree(workspace, ignore_errors=True)


def test_98_power_loss_durability_drill_reports_success():
    workspace = _local_test_dir("pytest-power-loss-durability-drill")
    project_root = Path(__file__).resolve().parents[1]
    command = [
        sys.executable,
        str(project_root / "scripts" / "power-loss-durability-drill.py"),
        "--workspace",
        str(workspace),
        "--label",
        "pytest-drill",
        "--transaction-rows",
        "60",
        "--payload-bytes",
        "96",
        "--startup-timeout-seconds",
        "15",
    ]
    completed = subprocess.run(
        command,
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0 and "disk i/o error" in completed.stderr.lower():
        shutil.rmtree(workspace, ignore_errors=True)
        pytest.skip("File-backed SQLite is unavailable in this sandbox")
    assert completed.returncode == 0, completed.stderr

    output_lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    payload = json.loads(output_lines[-1])
    assert payload["success"] is True
    assert payload["scenarios"]["abort_before_commit"]["observed_rows"] == 0
    assert payload["scenarios"]["abort_after_commit"]["observed_rows"] == 60
    assert payload["app_probe"]["readiness_ready"] is True
    assert payload["app_probe"]["slo_status"] == "ok"
    assert payload["app_probe"]["integrity_ok"] is True
    assert payload["post_recovery_write_rows"] == 1
    shutil.rmtree(workspace, ignore_errors=True)


def test_99_disk_pressure_fault_injection_drill_reports_success():
    workspace = _local_test_dir("pytest-disk-pressure-fault-injection-drill")
    project_root = Path(__file__).resolve().parents[1]
    command = [
        sys.executable,
        str(project_root / "scripts" / "disk-pressure-fault-injection-drill.py"),
        "--workspace",
        str(workspace),
        "--label",
        "pytest-drill",
        "--fault-injections",
        "2",
    ]
    completed = subprocess.run(
        command,
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0 and "disk i/o error" in completed.stderr.lower():
        shutil.rmtree(workspace, ignore_errors=True)
        pytest.skip("File-backed SQLite is unavailable in this sandbox")
    assert completed.returncode == 0, completed.stderr

    output_lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    payload = json.loads(output_lines[-1])
    assert payload["success"] is True
    case_names = [case["name"] for case in payload["cases"]]
    assert case_names == ["sqlite_full", "sqlite_ioerr", "readonly_permission_flip"]
    for case in payload["cases"]:
        assert case["success"] is True
        assert case["safe_mode"]["active_after_faults"] is True
        assert case["safe_mode"]["active_after_disable"] is False
        assert case["status_codes"]["blocked_mutation"] == 503
        assert case["status_codes"]["post_recovery_goal_create"] == 201
        assert case["readiness"]["during_fault"] is False
        assert case["readiness"]["after_recovery"] is True
        assert case["slo"]["during_fault"] == "critical"
        assert case["slo"]["after_recovery"] == "ok"
        assert case["integrity"]["during_fault_quick_ok"] is True
        assert case["integrity"]["during_fault_full_ok"] is True
        assert case["integrity"]["after_recovery_quick_ok"] is True
        assert case["integrity"]["after_recovery_full_ok"] is True
        assert case["workflow_runs"]["running_during_fault"] == []
        assert case["workflow_runs"]["running_after_recovery"] == []
    shutil.rmtree(workspace, ignore_errors=True)


def test_100_sqlite_real_full_drill_reports_success():
    workspace = _local_test_dir("pytest-sqlite-real-full-drill")
    project_root = Path(__file__).resolve().parents[1]
    command = [
        sys.executable,
        str(project_root / "scripts" / "sqlite-real-full-drill.py"),
        "--workspace",
        str(workspace),
        "--label",
        "pytest-drill",
        "--payload-bytes",
        "4096",
        "--max-write-attempts",
        "160",
        "--max-page-growth",
        "16",
        "--recovery-page-growth",
        "120",
    ]
    completed = subprocess.run(
        command,
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0 and "disk i/o error" in completed.stderr.lower():
        shutil.rmtree(workspace, ignore_errors=True)
        pytest.skip("File-backed SQLite is unavailable in this sandbox")
    assert completed.returncode == 0, completed.stderr

    output_lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    payload = json.loads(output_lines[-1])
    assert payload["success"] is True
    assert payload["fill"]["first_failure_status"] == 500
    assert payload["safe_mode"]["after_full_trigger"]["active"] is True
    assert payload["status_codes"]["blocked_mutation_during_safe_mode"] == 503
    assert payload["status_codes"]["post_recovery_goal_create"] == 201
    assert payload["readiness"]["during_fault"] is False
    assert payload["readiness"]["after_recovery"] is True
    assert payload["slo"]["during_fault"] == "critical"
    assert payload["slo"]["after_recovery"] == "ok"
    assert payload["integrity"]["during_fault_quick_ok"] is True
    assert payload["integrity"]["during_fault_full_ok"] is True
    assert payload["integrity"]["after_recovery_quick_ok"] is True
    assert payload["integrity"]["after_recovery_full_ok"] is True
    assert payload["runtime_metrics"]["io_error_count"] >= 1
    assert payload["workflow_runs"]["running_during_fault"] == []
    assert payload["workflow_runs"]["running_after_recovery"] == []
    shutil.rmtree(workspace, ignore_errors=True)


def test_101_wal_checkpoint_crash_drill_reports_success():
    workspace = _local_test_dir("pytest-wal-checkpoint-crash-drill")
    project_root = Path(__file__).resolve().parents[1]
    command = [
        sys.executable,
        str(project_root / "scripts" / "wal-checkpoint-crash-drill.py"),
        "--workspace",
        str(workspace),
        "--label",
        "pytest-drill",
        "--rows",
        "60",
        "--payload-bytes",
        "128",
        "--startup-timeout-seconds",
        "15",
        "--sleep-before-checkpoint-seconds",
        "30",
        "--checkpoint-mode",
        "TRUNCATE",
    ]
    completed = subprocess.run(
        command,
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0 and "disk i/o error" in completed.stderr.lower():
        shutil.rmtree(workspace, ignore_errors=True)
        pytest.skip("File-backed SQLite is unavailable in this sandbox")
    assert completed.returncode == 0, completed.stderr

    output_lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    payload = json.loads(output_lines[-1])
    assert payload["success"] is True
    assert payload["scenario"]["rows_persisted_before_crash"] == 60
    assert payload["scenario"]["rows_observed_after_crash"] == 60
    assert payload["checkpoint_recovery"]["busy"] == 0
    assert payload["integrity"]["after_crash"]["quick_ok"] is True
    assert payload["integrity"]["after_crash"]["full_ok"] is True
    assert payload["integrity"]["after_recovery_checkpoint"]["quick_ok"] is True
    assert payload["integrity"]["after_recovery_checkpoint"]["full_ok"] is True
    assert payload["app_probe"]["readiness_ready"] is True
    assert payload["app_probe"]["slo_status"] == "ok"
    assert payload["app_probe"]["safe_mode_active"] is False
    assert payload["app_probe"]["post_recovery_goal_status_code"] == 201
    assert payload["app_probe"]["running_run_ids"] == []
    shutil.rmtree(workspace, ignore_errors=True)


def test_102_recovery_idempotence_drill_reports_success():
    workspace = _local_test_dir("pytest-recovery-idempotence-drill")
    project_root = Path(__file__).resolve().parents[1]
    command = [
        sys.executable,
        str(project_root / "scripts" / "recovery-idempotence-drill.py"),
        "--workspace",
        str(workspace),
        "--label",
        "pytest-drill",
        "--recovery-cycles",
        "3",
        "--startup-timeout-seconds",
        "15",
    ]
    completed = subprocess.run(
        command,
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0 and "disk i/o error" in completed.stderr.lower():
        shutil.rmtree(workspace, ignore_errors=True)
        pytest.skip("File-backed SQLite is unavailable in this sandbox")
    assert completed.returncode == 0, completed.stderr

    output_lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    payload = json.loads(output_lines[-1])
    assert payload["success"] is True
    assert payload["status_before_abort"] == "running"
    assert payload["recovery_cycles"] == 3
    assert len(payload["cycles"]) == 3
    assert payload["cycles"][0]["startup_recovery"]["recovered_count"] >= 1
    assert payload["cycles"][0]["recovered_event_count"] == 1
    assert payload["cycles"][1]["startup_recovery"]["recovered_count"] == 0
    assert payload["cycles"][1]["recovered_event_count"] == 1
    assert payload["cycles"][2]["startup_recovery"]["recovered_count"] == 0
    assert payload["cycles"][2]["recovered_event_count"] == 1
    for cycle in payload["cycles"]:
        assert cycle["run_status"] == "failed"
        assert cycle["run_error_type"] == "ProcessAbortRecovery"
        assert cycle["readiness_ready"] is True
        assert cycle["slo_status"] == "ok"
        assert cycle["goal_create_status_code"] == 201
        assert cycle["running_run_ids"] == []
    shutil.rmtree(workspace, ignore_errors=True)


def test_103_fsync_io_stall_drill_reports_success():
    workspace = _local_test_dir("pytest-fsync-io-stall-drill")
    project_root = Path(__file__).resolve().parents[1]
    command = [
        sys.executable,
        str(project_root / "scripts" / "fsync-io-stall-drill.py"),
        "--workspace",
        str(workspace),
        "--label",
        "pytest-drill",
        "--fault-injections",
        "2",
        "--stall-seconds",
        "0.15",
        "--max-stall-request-seconds",
        "2.0",
    ]
    completed = subprocess.run(
        command,
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0 and "disk i/o error" in completed.stderr.lower():
        shutil.rmtree(workspace, ignore_errors=True)
        pytest.skip("File-backed SQLite is unavailable in this sandbox")
    assert completed.returncode == 0, completed.stderr

    output_lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    payload = json.loads(output_lines[-1])
    assert payload["success"] is True
    assert payload["safe_mode"]["active_after_faults"] is True
    assert payload["safe_mode"]["active_after_disable"] is False
    assert payload["status_codes"]["blocked_mutation"] == 503
    assert payload["status_codes"]["post_recovery_goal_create"] == 201
    assert payload["readiness"]["during_fault"] is False
    assert payload["readiness"]["after_recovery"] is True
    assert payload["slo"]["during_fault"] == "critical"
    assert payload["slo"]["after_recovery"] == "ok"
    assert payload["integrity"]["during_fault_quick_ok"] is True
    assert payload["integrity"]["during_fault_full_ok"] is True
    assert payload["integrity"]["after_recovery_quick_ok"] is True
    assert payload["integrity"]["after_recovery_full_ok"] is True
    assert payload["runtime_metrics"]["io_error_count"] >= 2
    assert payload["workflow_runs"]["running_during_fault"] == []
    assert payload["workflow_runs"]["running_after_recovery"] == []
    shutil.rmtree(workspace, ignore_errors=True)


def test_104_critical_drill_flake_gate_reports_success():
    project_root = Path(__file__).resolve().parents[1]
    command = [
        sys.executable,
        str(project_root / "scripts" / "critical-drill-flake-gate.py"),
        "--repeats",
        "2",
        "--max-failed-iterations",
        "0",
        "--target-file",
        str(project_root / "tests" / "test_goal_ops.py"),
        "--keyword-expression",
        "test_100_sqlite_real_full_drill_reports_success or test_101_wal_checkpoint_crash_drill_reports_success",
        "--timeout-seconds",
        "600",
    ]
    completed = subprocess.run(
        command,
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0 and "disk i/o error" in completed.stderr.lower():
        pytest.skip("File-backed SQLite is unavailable in this sandbox")
    assert completed.returncode == 0, completed.stderr

    output_lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    payload = json.loads(output_lines[-1])
    assert payload["success"] is True
    assert payload["config"]["repeats"] == 2
    assert payload["summary"]["failed_iterations"] == 0
    assert payload["summary"]["passed_iterations"] == 2
    assert len(payload["iterations"]) == 2
    for iteration in payload["iterations"]:
        assert iteration["success"] is True
        assert iteration["return_code"] == 0


def test_105_storage_corruption_hardening_drill_reports_success():
    workspace = _local_test_dir("pytest-storage-corruption-hardening-drill")
    project_root = Path(__file__).resolve().parents[1]
    command = [
        sys.executable,
        str(project_root / "scripts" / "storage-corruption-hardening-drill.py"),
        "--workspace",
        str(workspace),
        "--label",
        "pytest-drill",
        "--corruption-bytes",
        "128",
        "--rows",
        "48",
        "--payload-bytes",
        "96",
    ]
    completed = subprocess.run(
        command,
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0 and "disk i/o error" in completed.stderr.lower():
        shutil.rmtree(workspace, ignore_errors=True)
        pytest.skip("File-backed SQLite is unavailable in this sandbox")
    assert completed.returncode == 0, completed.stderr

    output_lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    payload = json.loads(output_lines[-1])
    assert payload["success"] is True
    assert len(payload["cases"]) == 2
    case_names = [case["name"] for case in payload["cases"]]
    assert case_names == ["wal_file_anomaly", "rollback_journal_anomaly"]
    for case in payload["cases"]:
        assert case["success"] is True
        assert case["recovery"]["startup_recovery"]["triggered"] is True
        assert case["recovery"]["startup_recovery"]["recovered"] is True
        assert case["recovery"]["blocked_status_code"] == 503
        assert case["recovery"]["post_disable_goal_create_status_code"] == 201
        assert case["recovery"]["readiness_before"]["ready"] is False
        assert case["recovery"]["readiness_after"]["ready"] is True
    shutil.rmtree(workspace, ignore_errors=True)


def test_106_backup_restore_stress_drill_reports_success():
    workspace = _local_test_dir("pytest-backup-restore-stress-drill")
    project_root = Path(__file__).resolve().parents[1]
    command = [
        sys.executable,
        str(project_root / "scripts" / "backup-restore-stress-drill.py"),
        "--workspace",
        str(workspace),
        "--label",
        "pytest-drill",
        "--rounds",
        "2",
        "--goals-per-round",
        "30",
        "--tasks-per-goal",
        "2",
        "--workflow-runs-per-round",
        "8",
    ]
    completed = subprocess.run(
        command,
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0 and "disk i/o error" in completed.stderr.lower():
        shutil.rmtree(workspace, ignore_errors=True)
        pytest.skip("File-backed SQLite is unavailable in this sandbox")
    assert completed.returncode == 0, completed.stderr

    output_lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    payload = json.loads(output_lines[-1])
    assert payload["success"] is True
    assert payload["config"]["rounds"] == 2
    assert len(payload["rounds"]) == 2
    for round_report in payload["rounds"]:
        assert round_report["restore_matches_source"] is True
        assert round_report["restore_idempotent"] is True
        assert round_report["source"]["integrity"]["quick_ok"] is True
        assert round_report["source"]["integrity"]["full_ok"] is True
        assert round_report["restored_a"]["integrity"]["quick_ok"] is True
        assert round_report["restored_a"]["integrity"]["full_ok"] is True
        assert round_report["restored_b"]["integrity"]["quick_ok"] is True
        assert round_report["restored_b"]["integrity"]["full_ok"] is True
        assert round_report["app_probe"]["readiness_ready"] is True
        assert round_report["app_probe"]["slo_status"] == "ok"
        assert round_report["app_probe"]["post_restore_goal_status_code"] == 201
        assert round_report["app_probe"]["running_run_ids"] == []
    shutil.rmtree(workspace, ignore_errors=True)


def test_107_snapshot_restore_crash_consistency_drill_reports_success():
    workspace = _local_test_dir("pytest-snapshot-restore-crash-consistency-drill")
    project_root = Path(__file__).resolve().parents[1]
    command = [
        sys.executable,
        str(project_root / "scripts" / "snapshot-restore-crash-consistency-drill.py"),
        "--workspace",
        str(workspace),
        "--label",
        "pytest-drill",
        "--seed-rows",
        "48",
        "--payload-bytes",
        "96",
    ]
    completed = subprocess.run(
        command,
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0 and "disk i/o error" in completed.stderr.lower():
        shutil.rmtree(workspace, ignore_errors=True)
        pytest.skip("File-backed SQLite is unavailable in this sandbox")
    assert completed.returncode == 0, completed.stderr

    output_lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    payload = json.loads(output_lines[-1])
    assert payload["success"] is True
    assert payload["config"]["fault_matrix_cases"] == 4
    assert len(payload["cases"]) == 4

    case_names = [case["name"] for case in payload["cases"]]
    assert case_names == [
        "missing_manifest_after_snapshot_abort",
        "tampered_snapshot_checksum_mismatch",
        "restore_abort_then_recover",
        "happy_path_restore",
    ]
    for case in payload["cases"]:
        assert case["success"] is True

    restore_abort_case = payload["cases"][2]
    assert restore_abort_case["aborted_return_code"] != 0
    assert restore_abort_case["app_probe"]["readiness_ready"] is True
    assert restore_abort_case["app_probe"]["slo_status"] == "ok"
    assert restore_abort_case["app_probe"]["goal_create_status_code"] == 201
    assert restore_abort_case["app_probe"]["running_run_ids"] == []

    happy_case = payload["cases"][3]
    assert happy_case["app_probe"]["readiness_ready"] is True
    assert happy_case["app_probe"]["slo_status"] == "ok"
    assert happy_case["app_probe"]["goal_create_status_code"] == 201
    assert happy_case["app_probe"]["running_run_ids"] == []
    shutil.rmtree(workspace, ignore_errors=True)


def test_108_multi_db_atomic_switch_drill_reports_success():
    workspace = _local_test_dir("pytest-multi-db-atomic-switch-drill")
    project_root = Path(__file__).resolve().parents[1]
    command = [
        sys.executable,
        str(project_root / "scripts" / "multi-db-atomic-switch-drill.py"),
        "--workspace",
        str(workspace),
        "--label",
        "pytest-drill",
        "--seed-rows",
        "48",
        "--payload-bytes",
        "96",
    ]
    completed = subprocess.run(
        command,
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0 and "disk i/o error" in completed.stderr.lower():
        shutil.rmtree(workspace, ignore_errors=True)
        pytest.skip("File-backed SQLite is unavailable in this sandbox")
    assert completed.returncode == 0, completed.stderr

    output_lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    payload = json.loads(output_lines[-1])
    assert payload["success"] is True
    assert payload["config"]["cases"] == 4
    assert len(payload["cases"]) == 4

    case_names = [case["name"] for case in payload["cases"]]
    assert case_names == [
        "abort_before_pointer_replace",
        "candidate_integrity_reject",
        "switch_to_candidate_success",
        "switch_back_to_primary_success",
    ]
    for case in payload["cases"]:
        assert case["success"] is True

    abort_case = payload["cases"][0]
    assert abort_case["aborted_return_code"] != 0
    assert abort_case["pointer_active_after"] == "primary"
    assert abort_case["app_probe"]["readiness_ready"] is True
    assert abort_case["app_probe"]["slo_status"] == "ok"
    assert abort_case["app_probe"]["running_run_ids"] == []

    reject_case = payload["cases"][1]
    assert reject_case["failure_reason"] in {"target_snapshot_failed", "target_integrity_failed"}
    assert reject_case["pointer_active_before"] == "primary"
    assert reject_case["pointer_active_after"] == "primary"

    candidate_case = payload["cases"][2]
    assert candidate_case["pointer_active_after"] == "candidate"
    assert candidate_case["app_probe"]["readiness_ready"] is True
    assert candidate_case["app_probe"]["slo_status"] == "ok"
    assert candidate_case["app_probe"]["goal_create_status_code"] == 201
    assert candidate_case["app_probe"]["running_run_ids"] == []

    rollback_case = payload["cases"][3]
    assert rollback_case["pointer_active_after"] == "primary"
    assert rollback_case["app_probe"]["readiness_ready"] is True
    assert rollback_case["app_probe"]["slo_status"] == "ok"
    assert rollback_case["app_probe"]["goal_create_status_code"] == 201
    assert rollback_case["app_probe"]["running_run_ids"] == []
    shutil.rmtree(workspace, ignore_errors=True)


def test_109_release_gate_runtime_stability_drill_reports_success():
    project_root = Path(__file__).resolve().parents[1]
    command = [
        sys.executable,
        str(project_root / "scripts" / "release-gate-runtime-stability-drill.py"),
        "--label",
        "pytest-drill",
        "--samples",
        "2",
        "--repeats-per-sample",
        "1",
        "--target-file",
        str(project_root / "tests" / "test_goal_ops.py"),
        "--keyword-expression",
        (
            "test_105_storage_corruption_hardening_drill_reports_success or "
            "test_106_backup_restore_stress_drill_reports_success or "
            "test_107_snapshot_restore_crash_consistency_drill_reports_success or "
            "test_108_multi_db_atomic_switch_drill_reports_success"
        ),
        "--timeout-seconds",
        "900",
        "--max-mean-duration-ms",
        "300000",
        "--max-stddev-ms",
        "180000",
        "--max-iteration-duration-ms",
        "480000",
    ]
    completed = subprocess.run(
        command,
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0 and "disk i/o error" in completed.stderr.lower():
        pytest.skip("File-backed SQLite is unavailable in this sandbox")
    assert completed.returncode == 0, completed.stderr

    output_lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    payload = json.loads(output_lines[-1])
    assert payload["success"] is True
    assert payload["config"]["samples"] == 2
    assert payload["config"]["repeats_per_sample"] == 1
    assert payload["metrics"]["iterations_total"] >= 2
    assert payload["metrics"]["failed_iterations"] == 0
    assert payload["metrics"]["mean_duration_ms"] > 0
    assert payload["metrics"]["max_duration_ms"] > 0
    assert len(payload["samples"]) == 2
    for sample in payload["samples"]:
        assert sample["success"] is True
        assert sample["return_code"] == 0


def test_110_p0_burnin_consecutive_green_reports_success_after_recovery_window():
    workspace = _local_test_dir("pytest-p0-burnin-consecutive-green")
    project_root = Path(__file__).resolve().parents[1]
    fixtures_dir = workspace / "fixtures"
    jobs_dir = fixtures_dir / "jobs"
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    jobs_dir.mkdir(parents=True, exist_ok=True)

    runs_payload = {
        "workflow_runs": [
            {
                "id": 9003,
                "name": "CI",
                "status": "completed",
                "conclusion": "success",
                "head_sha": "sha-9003",
                "updated_at": "2026-04-16T10:00:03Z",
            },
            {
                "id": 9002,
                "name": "CI",
                "status": "completed",
                "conclusion": "success",
                "head_sha": "sha-9002",
                "updated_at": "2026-04-16T10:00:02Z",
            },
            {
                "id": 9001,
                "name": "CI",
                "status": "completed",
                "conclusion": "success",
                "head_sha": "sha-9001",
                "updated_at": "2026-04-16T10:00:01Z",
            },
            {
                "id": 9000,
                "name": "CI",
                "status": "completed",
                "conclusion": "failure",
                "head_sha": "sha-9000",
                "updated_at": "2026-04-16T10:00:00Z",
            },
        ]
    }
    # Older failure after a sequence of fresh green runs models recovery after a transient incident.
    runs_file = fixtures_dir / "runs.json"
    runs_file.write_text(json.dumps(runs_payload, ensure_ascii=True, sort_keys=True), encoding="utf-8")

    required_jobs = [
        "Release Gate (Windows)",
        "Pytest (Python 3.11)",
        "Pytest (Python 3.12)",
        "Desktop Smoke (Windows)",
    ]
    for run_id in (9003, 9002, 9001, 9000):
        conclusion = "success" if run_id != 9000 else "failure"
        jobs_payload = {
            "jobs": [
                {"name": required_jobs[0], "conclusion": conclusion},
                {"name": required_jobs[1], "conclusion": conclusion},
                {"name": required_jobs[2], "conclusion": conclusion},
                {"name": required_jobs[3], "conclusion": conclusion},
                {"name": "Auxiliary Check", "conclusion": "success"},
            ]
        }
        (jobs_dir / f"{run_id}.json").write_text(
            json.dumps(jobs_payload, ensure_ascii=True, sort_keys=True),
            encoding="utf-8",
        )

    command = [
        sys.executable,
        str(project_root / "scripts" / "p0-burnin-consecutive-green.py"),
        "--label",
        "pytest-drill",
        "--repo",
        "donatomaurizio99-collab/GOC",
        "--branch",
        "master",
        "--workflow-name",
        "CI",
        "--required-jobs",
        ",".join(required_jobs),
        "--required-consecutive",
        "3",
        "--per-page",
        "10",
        "--runs-file",
        str(runs_file),
        "--jobs-dir",
        str(jobs_dir),
    ]
    completed = subprocess.run(
        command,
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr

    output_lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    payload = json.loads(output_lines[-1])
    assert payload["success"] is True
    assert payload["config"]["required_consecutive"] == 3
    assert payload["metrics"]["consecutive_green"] == 3
    assert payload["metrics"]["evaluated_runs"] == 3
    assert payload["first_non_green"] is None
    assert len(payload["evaluations"]) == 3
    for evaluation in payload["evaluations"]:
        assert evaluation["is_green"] is True
        assert evaluation["missing_jobs"] == []
        assert evaluation["failing_jobs"] == []

    shutil.rmtree(workspace, ignore_errors=True)


def test_111_p0_burnin_consecutive_green_reports_failure_for_latest_non_green_run():
    workspace = _local_test_dir("pytest-p0-burnin-consecutive-green-failure")
    project_root = Path(__file__).resolve().parents[1]
    fixtures_dir = workspace / "fixtures"
    jobs_dir = fixtures_dir / "jobs"
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    jobs_dir.mkdir(parents=True, exist_ok=True)

    required_jobs = [
        "Release Gate (Windows)",
        "Pytest (Python 3.11)",
        "Pytest (Python 3.12)",
        "Desktop Smoke (Windows)",
    ]
    runs_payload = {
        "workflow_runs": [
            {
                "id": 9102,
                "name": "CI",
                "status": "completed",
                "conclusion": "failure",
                "head_sha": "sha-9102",
                "updated_at": "2026-04-16T11:00:02Z",
            },
            {
                "id": 9101,
                "name": "CI",
                "status": "completed",
                "conclusion": "success",
                "head_sha": "sha-9101",
                "updated_at": "2026-04-16T11:00:01Z",
            },
        ]
    }
    runs_file = fixtures_dir / "runs.json"
    runs_file.write_text(json.dumps(runs_payload, ensure_ascii=True, sort_keys=True), encoding="utf-8")

    jobs_payload_failure = {
        "jobs": [
            {"name": required_jobs[0], "conclusion": "failure"},
            {"name": required_jobs[1], "conclusion": "success"},
            {"name": required_jobs[2], "conclusion": "success"},
            {"name": required_jobs[3], "conclusion": "success"},
        ]
    }
    (jobs_dir / "9102.json").write_text(
        json.dumps(jobs_payload_failure, ensure_ascii=True, sort_keys=True),
        encoding="utf-8",
    )
    jobs_payload_success = {
        "jobs": [{"name": job_name, "conclusion": "success"} for job_name in required_jobs]
    }
    (jobs_dir / "9101.json").write_text(
        json.dumps(jobs_payload_success, ensure_ascii=True, sort_keys=True),
        encoding="utf-8",
    )

    command = [
        sys.executable,
        str(project_root / "scripts" / "p0-burnin-consecutive-green.py"),
        "--label",
        "pytest-drill",
        "--repo",
        "donatomaurizio99-collab/GOC",
        "--branch",
        "master",
        "--workflow-name",
        "CI",
        "--required-jobs",
        ",".join(required_jobs),
        "--required-consecutive",
        "2",
        "--per-page",
        "10",
        "--runs-file",
        str(runs_file),
        "--jobs-dir",
        str(jobs_dir),
    ]
    completed = subprocess.run(
        command,
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    assert completed.returncode != 0
    marker = "P0 burn-in consecutive-green check not met: "
    assert marker in completed.stderr

    report = json.loads(completed.stderr.split(marker, 1)[1].strip())
    assert report["success"] is False
    assert report["metrics"]["consecutive_green"] == 0
    assert report["metrics"]["evaluated_runs"] == 1
    assert report["first_non_green"]["run_id"] == 9102
    assert report["first_non_green"]["is_green"] is False

    shutil.rmtree(workspace, ignore_errors=True)


def test_112_p0_runbook_contract_check_reports_success():
    workspace = _local_test_dir("pytest-p0-runbook-contract-check")
    project_root = Path(__file__).resolve().parents[1]
    output_file = workspace / "runbook-contract-report.json"

    command = [
        sys.executable,
        str(project_root / "scripts" / "p0-runbook-contract-check.py"),
        "--label",
        "pytest-drill",
        "--project-root",
        str(project_root),
        "--output-file",
        str(output_file),
    ]
    completed = subprocess.run(
        command,
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr

    output_lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    payload = json.loads(output_lines[-1])
    assert payload["success"] is True
    assert payload["checks"]["missing_strict_flags_in_ci_workflow"] == []
    assert payload["checks"]["missing_strict_flags_in_runbook"] == []
    assert payload["checks"]["missing_required_runbook_scripts"] == []
    assert payload["checks"]["missing_script_files_for_runbook_references"] == []
    assert output_file.exists()

    report_file_payload = json.loads(output_file.read_text(encoding="utf-8"))
    assert report_file_payload["success"] is True

    shutil.rmtree(workspace, ignore_errors=True)


def test_113_p0_release_evidence_bundle_reports_success_with_required_files():
    workspace = _local_test_dir("pytest-p0-release-evidence-bundle-success").resolve()
    project_root = Path(__file__).resolve().parents[1]
    artifacts_dir = workspace / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    burnin_report = artifacts_dir / "p0-burnin-consecutive-green-release-gate.json"
    runbook_report = artifacts_dir / "p0-runbook-contract-check-release-gate.json"
    burnin_report.write_text(
        json.dumps({"label": "burnin", "success": True}, ensure_ascii=True, sort_keys=True),
        encoding="utf-8",
    )
    runbook_report.write_text(
        json.dumps({"label": "runbook", "success": True}, ensure_ascii=True, sort_keys=True),
        encoding="utf-8",
    )
    output_file = artifacts_dir / "p0-release-evidence-bundle-release-gate.json"
    bundle_dir = artifacts_dir / "p0-release-evidence-files-release-gate"

    command = [
        sys.executable,
        str(project_root / "scripts" / "p0-release-evidence-bundle.py"),
        "--label",
        "pytest-drill",
        "--project-root",
        str(workspace.resolve()),
        "--artifacts-dir",
        str(artifacts_dir.resolve()),
        "--required-files",
        ",".join([str(burnin_report.resolve()), str(runbook_report.resolve())]),
        "--output-file",
        str(output_file.resolve()),
        "--bundle-dir",
        str(bundle_dir.resolve()),
    ]
    completed = subprocess.run(
        command,
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr

    output_lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    payload = json.loads(output_lines[-1])
    assert payload["success"] is True
    assert payload["metrics"]["required_missing_reports"] == 0
    assert payload["metrics"]["failed_reports"] == 0
    assert payload["metrics"]["success_reports"] == 2
    assert output_file.exists()
    assert (bundle_dir / burnin_report.name).exists()
    assert (bundle_dir / runbook_report.name).exists()

    shutil.rmtree(workspace, ignore_errors=True)


def test_114_p0_release_evidence_bundle_fails_when_required_file_missing():
    workspace = _local_test_dir("pytest-p0-release-evidence-bundle-missing").resolve()
    project_root = Path(__file__).resolve().parents[1]
    artifacts_dir = workspace / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    existing_report = artifacts_dir / "p0-burnin-consecutive-green-release-gate.json"
    missing_report = artifacts_dir / "p0-runbook-contract-check-release-gate.json"
    existing_report.write_text(
        json.dumps({"label": "burnin", "success": True}, ensure_ascii=True, sort_keys=True),
        encoding="utf-8",
    )
    output_file = artifacts_dir / "p0-release-evidence-bundle-release-gate.json"

    command = [
        sys.executable,
        str(project_root / "scripts" / "p0-release-evidence-bundle.py"),
        "--label",
        "pytest-drill",
        "--project-root",
        str(workspace.resolve()),
        "--artifacts-dir",
        str(artifacts_dir.resolve()),
        "--required-files",
        ",".join([str(existing_report.resolve()), str(missing_report.resolve())]),
        "--output-file",
        str(output_file.resolve()),
    ]
    completed = subprocess.run(
        command,
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    assert completed.returncode != 0
    marker = "P0 release evidence bundle check failed: "
    assert marker in completed.stderr
    report = json.loads(completed.stderr.split(marker, 1)[1].strip())
    assert report["success"] is False
    assert report["metrics"]["required_missing_reports"] == 1
    assert str(missing_report) in report["required_missing"]

    shutil.rmtree(workspace, ignore_errors=True)


def test_115_p0_closure_report_reports_success_when_all_criteria_pass():
    workspace = _local_test_dir("pytest-p0-closure-report-success").resolve()
    project_root = Path(__file__).resolve().parents[1]
    artifacts_dir = workspace / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    evidence_file = artifacts_dir / "p0-release-evidence-bundle-release-gate.json"
    burnin_file = artifacts_dir / "p0-burnin-consecutive-green-release-gate.json"
    runbook_file = artifacts_dir / "p0-runbook-contract-check-release-gate.json"
    output_file = artifacts_dir / "p0-closure-report-release-gate.json"

    evidence_file.write_text(
        json.dumps({"label": "evidence", "success": True}, ensure_ascii=True, sort_keys=True),
        encoding="utf-8",
    )
    burnin_file.write_text(
        json.dumps(
            {"label": "burnin", "success": True, "metrics": {"consecutive_green": 12}},
            ensure_ascii=True,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    runbook_file.write_text(
        json.dumps({"label": "runbook", "success": True}, ensure_ascii=True, sort_keys=True),
        encoding="utf-8",
    )

    command = [
        sys.executable,
        str(project_root / "scripts" / "p0-closure-report.py"),
        "--label",
        "pytest-drill",
        "--project-root",
        str(workspace.resolve()),
        "--required-consecutive",
        "10",
        "--evidence-bundle-file",
        str(evidence_file.resolve()),
        "--burnin-file",
        str(burnin_file.resolve()),
        "--runbook-contract-file",
        str(runbook_file.resolve()),
        "--output-file",
        str(output_file.resolve()),
    ]
    completed = subprocess.run(
        command,
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads([line.strip() for line in completed.stdout.splitlines() if line.strip()][-1])
    assert payload["success"] is True
    assert payload["metrics"]["criteria_failed"] == 0
    assert output_file.exists()

    shutil.rmtree(workspace, ignore_errors=True)


def test_116_p0_closure_report_fails_when_burnin_threshold_not_met():
    workspace = _local_test_dir("pytest-p0-closure-report-failure").resolve()
    project_root = Path(__file__).resolve().parents[1]
    artifacts_dir = workspace / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    evidence_file = artifacts_dir / "p0-release-evidence-bundle-release-gate.json"
    burnin_file = artifacts_dir / "p0-burnin-consecutive-green-release-gate.json"
    runbook_file = artifacts_dir / "p0-runbook-contract-check-release-gate.json"
    output_file = artifacts_dir / "p0-closure-report-release-gate.json"

    evidence_file.write_text(
        json.dumps({"label": "evidence", "success": True}, ensure_ascii=True, sort_keys=True),
        encoding="utf-8",
    )
    burnin_file.write_text(
        json.dumps(
            {"label": "burnin", "success": True, "metrics": {"consecutive_green": 3}},
            ensure_ascii=True,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    runbook_file.write_text(
        json.dumps({"label": "runbook", "success": True}, ensure_ascii=True, sort_keys=True),
        encoding="utf-8",
    )

    command = [
        sys.executable,
        str(project_root / "scripts" / "p0-closure-report.py"),
        "--label",
        "pytest-drill",
        "--project-root",
        str(workspace.resolve()),
        "--required-consecutive",
        "10",
        "--evidence-bundle-file",
        str(evidence_file.resolve()),
        "--burnin-file",
        str(burnin_file.resolve()),
        "--runbook-contract-file",
        str(runbook_file.resolve()),
        "--output-file",
        str(output_file.resolve()),
    ]
    completed = subprocess.run(
        command,
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    assert completed.returncode != 0
    marker = "P0 closure report is not green: "
    assert marker in completed.stderr
    payload = json.loads(completed.stderr.split(marker, 1)[1].strip())
    assert payload["success"] is False
    assert payload["metrics"]["criteria_failed"] >= 1

    shutil.rmtree(workspace, ignore_errors=True)


def test_117_operator_auth_blocks_system_mutations_without_valid_token():
    app = create_app(
        Settings(
            database_url=":memory:",
            operator_auth_required=True,
            operator_auth_token="operator-token-0123456789",
            operator_auth_token_min_length=16,
        )
    )
    with TestClient(app) as local_client:
        health = local_client.get("/system/health")
        blocked = local_client.post("/system/scheduler/age")
        wrong = local_client.post(
            "/system/scheduler/age",
            headers={"Authorization": "Bearer wrong-token"},
        )
        allowed = local_client.post(
            "/system/scheduler/age",
            headers={"Authorization": "Bearer operator-token-0123456789"},
        )

    assert health.status_code == 200
    assert blocked.status_code == 401
    assert blocked.headers.get("WWW-Authenticate") == "Bearer"
    assert "Operator authentication required" in blocked.json()["detail"]
    assert wrong.status_code == 401
    assert allowed.status_code == 200


def test_118_create_app_rejects_weak_operator_token_when_auth_required():
    with pytest.raises(ValueError) as exc_info:
        create_app(
            Settings(
                database_url=":memory:",
                operator_auth_required=True,
                operator_auth_token="short",
                operator_auth_token_min_length=16,
            )
        )
    assert "Operator auth is required" in str(exc_info.value)


def test_119_security_config_hardening_check_reports_success():
    project_root = Path(__file__).resolve().parents[1]
    command = [
        sys.executable,
        str(project_root / "scripts" / "security-config-hardening-check.py"),
        "--label",
        "pytest-drill",
        "--deployment-profile",
        "production",
        "--operator-auth-required",
        "--operator-auth-token",
        "security-token-0123456789",
        "--min-operator-token-length",
        "16",
        "--database-url",
        "goal_ops.db",
        "--startup-corruption-recovery-enabled",
    ]
    completed = subprocess.run(
        command,
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads([line.strip() for line in completed.stdout.splitlines() if line.strip()][-1])
    assert payload["success"] is True
    assert payload["metrics"]["criteria_failed"] == 0


def test_120_security_config_hardening_check_reports_failure_without_auth_requirement():
    project_root = Path(__file__).resolve().parents[1]
    command = [
        sys.executable,
        str(project_root / "scripts" / "security-config-hardening-check.py"),
        "--label",
        "pytest-drill",
        "--deployment-profile",
        "production",
        "--operator-auth-token",
        "security-token-0123456789",
        "--min-operator-token-length",
        "16",
        "--database-url",
        "goal_ops.db",
        "--startup-corruption-recovery-enabled",
    ]
    completed = subprocess.run(
        command,
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    assert completed.returncode != 0
    marker = "[security-config-hardening-check] ERROR: "
    assert marker in completed.stderr
    payload = json.loads(completed.stderr.split(marker, 1)[1].strip())
    assert payload["success"] is False
    assert payload["metrics"]["criteria_failed"] >= 1


def test_121_audit_integrity_endpoint_detects_tampering(client):
    created = client.post(
        "/goals",
        json={
            "title": "Audit integrity goal",
            "description": "tamper detection",
            "urgency": 0.6,
            "value": 0.7,
            "deadline_score": 0.1,
        },
    )
    assert created.status_code == 201

    baseline = client.get("/system/audit/integrity?verify_limit=500")
    assert baseline.status_code == 200
    baseline_payload = baseline.json()
    assert baseline_payload["ok"] is True
    assert baseline_payload["metrics"]["missing_integrity_rows"] == 0

    services = client.app.state.services
    latest_audit_id = services.db.fetch_scalar(
        "SELECT audit_id FROM audit_log ORDER BY created_at DESC, audit_id DESC LIMIT 1"
    )
    assert latest_audit_id is not None
    services.db.execute(
        "UPDATE audit_log_integrity SET entry_hash = ? WHERE audit_id = ?",
        "0" * 64,
        latest_audit_id,
    )

    tampered = client.get("/system/audit/integrity?verify_limit=500")
    assert tampered.status_code == 200
    tampered_payload = tampered.json()
    assert tampered_payload["ok"] is False
    assert tampered_payload["metrics"]["hash_mismatch_count"] >= 1


def test_122_readiness_reports_not_ready_when_audit_integrity_is_tampered():
    app = create_app(Settings(database_url=":memory:"))
    with TestClient(app) as local_client:
        created = local_client.post(
            "/goals",
            json={
                "title": "Readiness tamper goal",
                "description": "check",
                "urgency": 0.5,
                "value": 0.4,
                "deadline_score": 0.1,
            },
        )
        assert created.status_code == 201

        services = local_client.app.state.services
        latest_audit_id = services.db.fetch_scalar(
            "SELECT audit_id FROM audit_log ORDER BY created_at DESC, audit_id DESC LIMIT 1"
        )
        assert latest_audit_id is not None
        services.db.execute(
            "UPDATE audit_log_integrity SET entry_hash = ? WHERE audit_id = ?",
            "0" * 64,
            latest_audit_id,
        )

        readiness = local_client.get("/system/readiness")
        assert readiness.status_code == 200
        payload = readiness.json()
        assert payload["ready"] is False
        assert payload["checks"]["audit_integrity"]["ok"] is False
        assert payload["checks"]["audit_integrity"]["metrics"]["hash_mismatch_count"] >= 1


def test_123_audit_trail_hardening_check_reports_success():
    workspace = _local_test_dir("pytest-audit-trail-hardening-check-success").resolve()
    project_root = Path(__file__).resolve().parents[1]
    output_file = workspace / "audit-trail-hardening-check-report.json"
    command = [
        sys.executable,
        str(project_root / "scripts" / "audit-trail-hardening-check.py"),
        "--label",
        "pytest-drill",
        "--deployment-profile",
        "production",
        "--audit-retention-days",
        "365",
        "--min-audit-retention-days",
        "90",
        "--seed-entries",
        "8",
        "--workspace",
        str(workspace),
        "--output-file",
        str(output_file),
    ]
    completed = subprocess.run(
        command,
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads([line.strip() for line in completed.stdout.splitlines() if line.strip()][-1])
    assert payload["success"] is True
    assert payload["metrics"]["criteria_failed"] == 0
    assert output_file.exists()

    shutil.rmtree(workspace, ignore_errors=True)


def test_124_audit_trail_hardening_check_fails_with_low_retention_policy():
    workspace = _local_test_dir("pytest-audit-trail-hardening-check-failure").resolve()
    project_root = Path(__file__).resolve().parents[1]
    output_file = workspace / "audit-trail-hardening-check-report.json"
    command = [
        sys.executable,
        str(project_root / "scripts" / "audit-trail-hardening-check.py"),
        "--label",
        "pytest-drill",
        "--deployment-profile",
        "production",
        "--audit-retention-days",
        "14",
        "--min-audit-retention-days",
        "90",
        "--seed-entries",
        "8",
        "--workspace",
        str(workspace),
        "--output-file",
        str(output_file),
    ]
    completed = subprocess.run(
        command,
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    assert completed.returncode != 0
    marker = "[audit-trail-hardening-check] ERROR: "
    assert marker in completed.stderr
    payload = json.loads(completed.stderr.split(marker, 1)[1].strip())
    assert payload["success"] is False
    assert payload["metrics"]["criteria_failed"] >= 1

    shutil.rmtree(workspace, ignore_errors=True)


def test_125_service_startup_backfills_legacy_audit_integrity_rows():
    workspace = _local_test_dir("pytest-audit-integrity-backfill")
    db_path = workspace / "legacy-audit.db"
    db = Database(str(db_path))
    db.initialize()

    legacy_audit_id = new_id()
    db.execute(
        """INSERT INTO audit_log
           (audit_id, action, actor, status, entity_type, entity_id, correlation_id, details, created_at)
           VALUES (?, 'legacy.audit', 'test', 'success', 'legacy', 'entry-1', NULL, NULL, ?)""",
        legacy_audit_id,
        now_utc(),
    )

    app = create_app(Settings(database_url=str(db_path)))
    with TestClient(app) as local_client:
        integrity = local_client.get("/system/audit/integrity?verify_limit=500")
        assert integrity.status_code == 200
        payload = integrity.json()
        assert payload["ok"] is True
        assert payload["metrics"]["missing_integrity_rows"] == 0
        assert payload["metrics"]["chain_entries"] >= 1
        assert payload["metrics"]["total_audit_entries"] >= 1

    shutil.rmtree(workspace, ignore_errors=True)


def test_126_security_ci_lane_check_reports_success_with_fixture_inputs():
    workspace = _local_test_dir("pytest-security-ci-lane-check-success").resolve()
    project_root = Path(__file__).resolve().parents[1]
    artifacts_dir = workspace / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    dependency_file = artifacts_dir / "dependency-audit.json"
    sast_file = artifacts_dir / "sast-bandit.json"
    sbom_file = artifacts_dir / "security-sbom.json"
    output_file = artifacts_dir / "security-ci-lane-report.json"

    dependency_file.write_text(json.dumps([], ensure_ascii=True), encoding="utf-8")
    sast_file.write_text(
        json.dumps({"results": [], "metrics": {}}, ensure_ascii=True, sort_keys=True),
        encoding="utf-8",
    )

    command = [
        sys.executable,
        str(project_root / "scripts" / "security-ci-lane-check.py"),
        "--label",
        "pytest-drill",
        "--deployment-profile",
        "production",
        "--scan-path",
        "goal_ops_console",
        "--max-dependency-vulnerabilities",
        "0",
        "--max-sast-high",
        "0",
        "--max-sast-medium",
        "0",
        "--dependency-audit-json-file",
        str(dependency_file.resolve()),
        "--sast-json-file",
        str(sast_file.resolve()),
        "--sbom-output-file",
        str(sbom_file.resolve()),
        "--output-file",
        str(output_file.resolve()),
    ]
    completed = subprocess.run(
        command,
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads([line.strip() for line in completed.stdout.splitlines() if line.strip()][-1])
    assert payload["success"] is True
    assert payload["metrics"]["criteria_failed"] == 0
    assert sbom_file.exists()
    assert output_file.exists()

    sbom_payload = json.loads(sbom_file.read_text(encoding="utf-8"))
    assert sbom_payload["component_count"] >= 1

    shutil.rmtree(workspace, ignore_errors=True)


def test_127_security_ci_lane_check_fails_when_dependency_budget_exceeded():
    workspace = _local_test_dir("pytest-security-ci-lane-check-failure").resolve()
    project_root = Path(__file__).resolve().parents[1]
    artifacts_dir = workspace / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    dependency_file = artifacts_dir / "dependency-audit.json"
    sast_file = artifacts_dir / "sast-bandit.json"
    output_file = artifacts_dir / "security-ci-lane-report.json"

    dependency_file.write_text(
        json.dumps(
            [
                {
                    "name": "example-package",
                    "version": "1.2.3",
                    "vulns": [{"id": "PYSEC-2026-0001", "fix_versions": ["1.2.4"]}],
                }
            ],
            ensure_ascii=True,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    sast_file.write_text(
        json.dumps({"results": [], "metrics": {}}, ensure_ascii=True, sort_keys=True),
        encoding="utf-8",
    )

    command = [
        sys.executable,
        str(project_root / "scripts" / "security-ci-lane-check.py"),
        "--label",
        "pytest-drill",
        "--deployment-profile",
        "production",
        "--scan-path",
        "goal_ops_console",
        "--max-dependency-vulnerabilities",
        "0",
        "--max-sast-high",
        "0",
        "--max-sast-medium",
        "0",
        "--dependency-audit-json-file",
        str(dependency_file.resolve()),
        "--sast-json-file",
        str(sast_file.resolve()),
        "--sbom-output-file",
        str((artifacts_dir / "security-sbom.json").resolve()),
        "--output-file",
        str(output_file.resolve()),
    ]
    completed = subprocess.run(
        command,
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    assert completed.returncode != 0
    marker = "[security-ci-lane-check] ERROR: "
    assert marker in completed.stderr
    payload = json.loads(completed.stderr.split(marker, 1)[1].strip())
    assert payload["success"] is False
    assert payload["metrics"]["criteria_failed"] >= 1
    assert payload["metrics"]["dependency_vulnerability_count"] >= 1

    shutil.rmtree(workspace, ignore_errors=True)


def test_128_alert_routing_oncall_check_reports_success_with_mock_critical():
    workspace = _local_test_dir("pytest-alert-routing-oncall-check-success").resolve()
    project_root = Path(__file__).resolve().parents[1]
    output_file = workspace / "alert-routing-oncall-report.json"
    command = [
        sys.executable,
        str(project_root / "scripts" / "alert-routing-oncall-check.py"),
        "--label",
        "pytest-drill",
        "--deployment-profile",
        "production",
        "--mock-slo-status",
        "critical",
        "--mock-alert-count",
        "2",
        "--routing-policy-file",
        str((project_root / "docs" / "oncall-alert-routing-policy.json").resolve()),
        "--runbook-file",
        str((project_root / "docs" / "production-runbook.md").resolve()),
        "--output-file",
        str(output_file.resolve()),
    ]
    completed = subprocess.run(
        command,
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads([line.strip() for line in completed.stdout.splitlines() if line.strip()][-1])
    assert payload["success"] is True
    assert payload["metrics"]["criteria_failed"] == 0
    assert payload["metrics"]["critical_alert_count"] >= 2
    assert payload["metrics"]["action_count"] >= 4
    assert output_file.exists()

    shutil.rmtree(workspace, ignore_errors=True)


def test_129_alert_routing_oncall_check_fails_when_critical_route_missing():
    workspace = _local_test_dir("pytest-alert-routing-oncall-check-failure").resolve()
    project_root = Path(__file__).resolve().parents[1]
    policy_file = workspace / "broken-policy.json"
    output_file = workspace / "alert-routing-oncall-report.json"

    policy_file.write_text(
        json.dumps(
            {
                "routes": {
                    "warning": {
                        "channel": "ops-slack-warning",
                        "primary": "ops-duty-manager",
                        "backup": "ops-secondary",
                        "max_ack_minutes": 60,
                        "runbook_section": "### 3.28 On-call warning alert routing",
                    }
                },
                "escalation": {
                    "critical_page_within_minutes": 5,
                    "critical_backup_after_minutes": 10,
                    "warning_notify_within_minutes": 15,
                    "warning_ticket_within_minutes": 30,
                },
            },
            ensure_ascii=True,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    command = [
        sys.executable,
        str(project_root / "scripts" / "alert-routing-oncall-check.py"),
        "--label",
        "pytest-drill",
        "--deployment-profile",
        "production",
        "--mock-slo-status",
        "critical",
        "--mock-alert-count",
        "1",
        "--routing-policy-file",
        str(policy_file.resolve()),
        "--runbook-file",
        str((project_root / "docs" / "production-runbook.md").resolve()),
        "--output-file",
        str(output_file.resolve()),
    ]
    completed = subprocess.run(
        command,
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    assert completed.returncode != 0
    marker = "[alert-routing-oncall-check] ERROR: "
    assert marker in completed.stderr
    payload = json.loads(completed.stderr.split(marker, 1)[1].strip())
    assert payload["success"] is False
    assert payload["metrics"]["criteria_failed"] >= 1

    shutil.rmtree(workspace, ignore_errors=True)


def test_130_incident_drill_automation_check_reports_success_with_mock_data():
    workspace = _local_test_dir("pytest-incident-drill-automation-check-success").resolve()
    project_root = Path(__file__).resolve().parents[1]
    output_file = workspace / "incident-drill-automation-report.json"
    command = [
        sys.executable,
        str(project_root / "scripts" / "incident-drill-automation-check.py"),
        "--label",
        "pytest-drill",
        "--deployment-profile",
        "production",
        "--mock-report",
        "--mock-days-since-tabletop",
        "7",
        "--mock-days-since-technical",
        "3",
        "--mock-tabletop-status",
        "completed",
        "--mock-technical-status",
        "completed",
        "--mock-open-followups",
        "0",
        "--policy-file",
        str((project_root / "docs" / "incident-drill-automation-policy.json").resolve()),
        "--runbook-file",
        str((project_root / "docs" / "production-runbook.md").resolve()),
        "--output-file",
        str(output_file.resolve()),
    ]
    completed = subprocess.run(
        command,
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads([line.strip() for line in completed.stdout.splitlines() if line.strip()][-1])
    assert payload["success"] is True
    assert payload["metrics"]["criteria_failed"] == 0
    assert payload["metrics"]["required_scenario_count"] >= 2
    assert output_file.exists()

    shutil.rmtree(workspace, ignore_errors=True)


def test_131_incident_drill_automation_check_fails_when_technical_drill_is_stale():
    workspace = _local_test_dir("pytest-incident-drill-automation-check-failure").resolve()
    project_root = Path(__file__).resolve().parents[1]
    output_file = workspace / "incident-drill-automation-report.json"
    command = [
        sys.executable,
        str(project_root / "scripts" / "incident-drill-automation-check.py"),
        "--label",
        "pytest-drill",
        "--deployment-profile",
        "production",
        "--mock-report",
        "--mock-days-since-tabletop",
        "5",
        "--mock-days-since-technical",
        "45",
        "--mock-tabletop-status",
        "completed",
        "--mock-technical-status",
        "completed",
        "--mock-open-followups",
        "0",
        "--max-technical-age-days",
        "14",
        "--policy-file",
        str((project_root / "docs" / "incident-drill-automation-policy.json").resolve()),
        "--runbook-file",
        str((project_root / "docs" / "production-runbook.md").resolve()),
        "--output-file",
        str(output_file.resolve()),
    ]
    completed = subprocess.run(
        command,
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    assert completed.returncode != 0
    marker = "[incident-drill-automation-check] ERROR: "
    assert marker in completed.stderr
    payload = json.loads(completed.stderr.split(marker, 1)[1].strip())
    assert payload["success"] is False
    assert payload["metrics"]["criteria_failed"] >= 1
    assert any(
        str(item.get("name") or "") == "technical.incident-rollback.recency_budget"
        for item in payload.get("failed_criteria", [])
        if isinstance(item, dict)
    )

    shutil.rmtree(workspace, ignore_errors=True)


def test_132_load_profile_framework_check_reports_success_with_fixture_profile():
    workspace = _local_test_dir("pytest-load-profile-framework-check-success").resolve()
    project_root = Path(__file__).resolve().parents[1]
    profile_file = workspace / "load-profile-catalog.json"
    output_file = workspace / "load-profile-framework-report.json"

    profile_file.write_text(
        json.dumps(
            {
                "catalog_version": "pytest.stage-c.1",
                "profiles": [
                    {
                        "name": "pytest_smoke",
                        "version": "1.0.0",
                        "description": "pytest profile",
                        "stages": [
                            {
                                "name": "steady",
                                "cycles": 4,
                                "workflow_start_every_cycles": 0,
                                "drain_batch_size": 50,
                                "readiness_check_every_cycles": 2,
                            },
                            {
                                "name": "burst",
                                "cycles": 6,
                                "workflow_start_every_cycles": 3,
                                "drain_batch_size": 80,
                                "readiness_check_every_cycles": 2,
                            },
                        ],
                        "budgets": {
                            "max_p95_latency_ms": 5000.0,
                            "max_p99_latency_ms": 6000.0,
                            "max_max_latency_ms": 12000.0,
                            "max_http_429_rate_percent": 50.0,
                            "max_error_rate_percent": 10.0,
                            "min_total_requests": 40,
                        },
                    }
                ],
            },
            ensure_ascii=True,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    command = [
        sys.executable,
        str(project_root / "scripts" / "load-profile-framework-check.py"),
        "--label",
        "pytest-drill",
        "--deployment-profile",
        "production",
        "--profile-file",
        str(profile_file.resolve()),
        "--profile-name",
        "pytest_smoke",
        "--profile-version",
        "1.0.0",
        "--output-file",
        str(output_file.resolve()),
    ]
    completed = subprocess.run(
        command,
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads([line.strip() for line in completed.stdout.splitlines() if line.strip()][-1])
    assert payload["success"] is True
    assert payload["metrics"]["criteria_failed"] == 0
    assert payload["metrics"]["requests_total"] >= 40
    assert payload["profile"]["name"] == "pytest_smoke"
    assert output_file.exists()

    shutil.rmtree(workspace, ignore_errors=True)


def test_133_load_profile_framework_check_fails_when_min_requests_budget_is_unmet():
    workspace = _local_test_dir("pytest-load-profile-framework-check-failure").resolve()
    project_root = Path(__file__).resolve().parents[1]
    profile_file = workspace / "load-profile-catalog.json"
    output_file = workspace / "load-profile-framework-report.json"

    profile_file.write_text(
        json.dumps(
            {
                "catalog_version": "pytest.stage-c.1",
                "profiles": [
                    {
                        "name": "pytest_strict",
                        "version": "1.0.0",
                        "description": "pytest strict profile",
                        "stages": [
                            {
                                "name": "steady",
                                "cycles": 3,
                                "workflow_start_every_cycles": 0,
                                "drain_batch_size": 50,
                                "readiness_check_every_cycles": 0,
                            }
                        ],
                        "budgets": {
                            "max_p95_latency_ms": 5000.0,
                            "max_p99_latency_ms": 6000.0,
                            "max_max_latency_ms": 12000.0,
                            "max_http_429_rate_percent": 50.0,
                            "max_error_rate_percent": 10.0,
                            "min_total_requests": 2000,
                        },
                    }
                ],
            },
            ensure_ascii=True,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    command = [
        sys.executable,
        str(project_root / "scripts" / "load-profile-framework-check.py"),
        "--label",
        "pytest-drill",
        "--deployment-profile",
        "production",
        "--profile-file",
        str(profile_file.resolve()),
        "--profile-name",
        "pytest_strict",
        "--profile-version",
        "1.0.0",
        "--output-file",
        str(output_file.resolve()),
    ]
    completed = subprocess.run(
        command,
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    assert completed.returncode != 0
    marker = "[load-profile-framework-check] ERROR: "
    assert marker in completed.stderr
    payload = json.loads(completed.stderr.split(marker, 1)[1].strip())
    assert payload["success"] is False
    assert payload["metrics"]["criteria_failed"] >= 1
    assert any(
        str(item.get("name") or "") == "min_total_requests"
        for item in payload.get("failed_criteria", [])
        if isinstance(item, dict)
    )

    shutil.rmtree(workspace, ignore_errors=True)


def test_134_rto_rpo_assertion_suite_reports_success_with_fixture_policy():
    workspace = _local_test_dir("pytest-rto-rpo-assertion-suite-success").resolve()
    project_root = Path(__file__).resolve().parents[1]
    policy_file = workspace / "rto-rpo-policy.json"
    output_file = workspace / "rto-rpo-report.json"

    policy_file.write_text(
        json.dumps(
            {
                "version": "pytest.1.0.0",
                "max_rto_seconds": 30.0,
                "max_rpo_rows_lost": 400,
                "scenarios": [
                    {
                        "id": "restore.zero_loss",
                        "runbook_section": "### 3.33 RTO zero-loss restore assertion",
                    },
                    {
                        "id": "restore.bounded_loss",
                        "runbook_section": "### 3.34 RPO bounded-loss assertion",
                    },
                ],
            },
            ensure_ascii=True,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    command = [
        sys.executable,
        str(project_root / "scripts" / "rto-rpo-assertion-suite.py"),
        "--label",
        "pytest-drill",
        "--deployment-profile",
        "production",
        "--workspace",
        str((workspace / "suite-workspace").resolve()),
        "--policy-file",
        str(policy_file.resolve()),
        "--runbook-file",
        str((project_root / "docs" / "production-runbook.md").resolve()),
        "--seed-rows",
        "18",
        "--tail-write-rows",
        "6",
        "--max-rto-seconds",
        "30",
        "--max-rpo-rows-lost",
        "400",
        "--output-file",
        str(output_file.resolve()),
    ]
    completed = subprocess.run(
        command,
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads([line.strip() for line in completed.stdout.splitlines() if line.strip()][-1])
    assert payload["success"] is True
    assert payload["metrics"]["criteria_failed"] == 0
    assert payload["metrics"]["max_restore_duration_ms"] >= 0
    assert payload["metrics"]["bounded_rows_lost"] <= 400
    assert output_file.exists()

    shutil.rmtree(workspace, ignore_errors=True)


def test_135_rto_rpo_assertion_suite_fails_when_rpo_budget_is_exceeded():
    workspace = _local_test_dir("pytest-rto-rpo-assertion-suite-failure").resolve()
    project_root = Path(__file__).resolve().parents[1]
    policy_file = workspace / "rto-rpo-policy.json"
    output_file = workspace / "rto-rpo-report.json"

    policy_file.write_text(
        json.dumps(
            {
                "version": "pytest.1.0.0",
                "max_rto_seconds": 30.0,
                "max_rpo_rows_lost": 0,
                "scenarios": [
                    {
                        "id": "restore.zero_loss",
                        "runbook_section": "### 3.33 RTO zero-loss restore assertion",
                    },
                    {
                        "id": "restore.bounded_loss",
                        "runbook_section": "### 3.34 RPO bounded-loss assertion",
                    },
                ],
            },
            ensure_ascii=True,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    command = [
        sys.executable,
        str(project_root / "scripts" / "rto-rpo-assertion-suite.py"),
        "--label",
        "pytest-drill",
        "--deployment-profile",
        "production",
        "--workspace",
        str((workspace / "suite-workspace").resolve()),
        "--policy-file",
        str(policy_file.resolve()),
        "--runbook-file",
        str((project_root / "docs" / "production-runbook.md").resolve()),
        "--seed-rows",
        "18",
        "--tail-write-rows",
        "6",
        "--max-rto-seconds",
        "30",
        "--max-rpo-rows-lost",
        "0",
        "--output-file",
        str(output_file.resolve()),
    ]
    completed = subprocess.run(
        command,
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    assert completed.returncode != 0
    marker = "[rto-rpo-assertion-suite] ERROR: "
    assert marker in completed.stderr
    payload = json.loads(completed.stderr.split(marker, 1)[1].strip())
    assert payload["success"] is False
    assert payload["metrics"]["criteria_failed"] >= 1
    assert any(
        str(item.get("name") or "") == "bounded_loss_rpo_budget"
        for item in payload.get("failed_criteria", [])
        if isinstance(item, dict)
    )

    shutil.rmtree(workspace, ignore_errors=True)
