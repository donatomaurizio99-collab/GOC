from __future__ import annotations

import sqlite3
import threading
import time

import pytest
from fastapi.testclient import TestClient

from goal_ops_console.config import Settings
from goal_ops_console.database import new_id, now_utc
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
    response = client.post("/system/maintenance/retention")
    assert response.status_code == 200
    payload = response.json()
    assert payload["event_processing_deleted"] == 1
    assert payload["events_deleted"] == 1
    assert payload["failure_log_deleted"] == 1
    assert services.db.fetch_scalar("SELECT COUNT(*) FROM events WHERE event_id = ?", old_event_id) == 0
    assert (
        services.db.fetch_scalar("SELECT COUNT(*) FROM event_processing WHERE event_id = ?", old_event_id)
        == 0
    )
    assert services.db.fetch_scalar("SELECT COUNT(*) FROM failure_log WHERE id = ?", old_failure_id) == 0


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


def test_49_workflow_start_creates_succeeded_run_and_emits_events(client):
    response = client.post(
        "/workflows/maintenance.retention_cleanup/start",
        json={"requested_by": "workflow-test", "payload": {"source": "test"}},
    )
    assert response.status_code == 201
    run = response.json()["run"]
    assert run["workflow_id"] == "maintenance.retention_cleanup"
    assert run["status"] == "succeeded"
    assert run["requested_by"] == "workflow-test"
    assert run["result_payload"]["events_deleted"] >= 0

    runs = client.get("/workflows/runs").json()["runs"]
    assert any(item["run_id"] == run["run_id"] for item in runs)

    events = client.get(f"/events?entity_id={run['run_id']}").json()
    event_types = {item["event_type"] for item in events}
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
    assert payload["reaped_count"] >= 1
    assert run_id in payload["run_ids"]

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
        run = response.json()["run"]
        assert run["status"] == "timed_out"
        assert run["result_payload"]["error_type"] == "TimeoutError"


def test_58_workflow_runs_indexes_include_hardening_indexes(services):
    indexes = services.db.fetch_all("PRAGMA index_list('workflow_runs')")
    names = {row["name"] for row in indexes}
    assert "idx_workflow_runs_status_created_at" in names
    assert "idx_workflow_runs_correlation_id" in names
    assert "idx_workflow_runs_idempotency" in names
