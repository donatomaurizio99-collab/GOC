from __future__ import annotations

import threading

import pytest

from goal_ops_console.database import new_id, now_utc
from goal_ops_console.failure_intelligence import compute_error_hash
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
    lock = threading.Lock()

    def handler(event):
        with lock:
            handled.append(event["event_id"])

    def worker():
        barrier.wait()
        services.event_bus.process_event(event_id, "race-consumer", handler)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

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
