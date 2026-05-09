def _create_goal(client, title: str) -> dict:
    response = client.post(
        "/goals",
        json={"title": title, "urgency": 0.6, "value": 0.7, "deadline_score": 0.2},
    )
    assert response.status_code == 201
    return response.json()


def _planner_suggestion_count(client, goal_id: str) -> int:
    response = client.post(f"/goals/{goal_id}/plan")
    assert response.status_code == 200
    return len(response.json()["suggestions"])


def test_planner_global_handoffs_empty_state(client):
    response = client.get("/goals/planner/handoffs")

    assert response.status_code == 200
    assert response.json() == {
        "summary": {
            "total_goals": 0,
            "goals_needing_attention": 0,
            "pending": 0,
            "deferred": 0,
            "rejected": 0,
            "created": 0,
        },
        "items": [],
    }


def test_planner_global_handoffs_mixed_goals_rollup(client):
    pending_goal = _create_goal(client, "Alpha pending handoff")
    deferred_goal = _create_goal(client, "Beta deferred handoff")
    created_goal = _create_goal(client, "Gamma created handoff")
    rejected_goal = _create_goal(client, "Delta rejected handoff")

    pending_total = _planner_suggestion_count(client, pending_goal["goal_id"])
    deferred_total = _planner_suggestion_count(client, deferred_goal["goal_id"])
    created_total = _planner_suggestion_count(client, created_goal["goal_id"])
    rejected_total = _planner_suggestion_count(client, rejected_goal["goal_id"])

    client.post(
        f"/goals/{deferred_goal['goal_id']}/plan/reviews/bulk",
        json={"suggestion_indexes": list(range(deferred_total)), "decision": "deferred", "comment": "Needs owner."},
    )
    client.post(
        f"/goals/{created_goal['goal_id']}/plan/tasks/bulk",
        json={"suggestion_indexes": list(range(created_total)), "overrides": {}},
    )
    client.post(
        f"/goals/{rejected_goal['goal_id']}/plan/reviews/bulk",
        json={"suggestion_indexes": list(range(rejected_total)), "decision": "rejected", "comment": "Out of scope."},
    )

    response = client.get("/goals/planner/handoffs")
    payload = response.json()
    items_by_goal = {item["goal_id"]: item for item in payload["items"]}

    assert response.status_code == 200
    assert payload["summary"] == {
        "total_goals": 4,
        "goals_needing_attention": 3,
        "pending": pending_total,
        "deferred": deferred_total,
        "rejected": rejected_total,
        "created": created_total,
    }
    assert items_by_goal[pending_goal["goal_id"]]["needs_operator_attention"] is True
    assert items_by_goal[pending_goal["goal_id"]]["attention_reason"] == "pending_review"
    assert items_by_goal[pending_goal["goal_id"]]["state"] == pending_goal["state"]
    assert items_by_goal[pending_goal["goal_id"]]["last_reviewed_at"] is None
    assert items_by_goal[pending_goal["goal_id"]]["pending"] == pending_total
    assert items_by_goal[pending_goal["goal_id"]]["next_pending_suggestion"]["suggestion_index"] == 0
    assert items_by_goal[pending_goal["goal_id"]]["follow_up_actions"][0]["id"] == "review_pending_suggestion"
    assert items_by_goal[pending_goal["goal_id"]]["follow_up_actions"][0]["action_type"] == "open_plan_preview"
    assert items_by_goal[pending_goal["goal_id"]]["follow_up_actions"][0]["target"] == {
        "goal_id": pending_goal["goal_id"],
        "suggestion_index": 0,
        "focus_next_pending": True,
    }
    assert items_by_goal[deferred_goal["goal_id"]]["needs_operator_attention"] is True
    assert items_by_goal[deferred_goal["goal_id"]]["attention_reason"] == "deferred_followup"
    assert items_by_goal[deferred_goal["goal_id"]]["deferred"] == deferred_total
    assert items_by_goal[deferred_goal["goal_id"]]["latest_deferred_suggestion"]["comment"] == "Needs owner."
    assert items_by_goal[deferred_goal["goal_id"]]["last_reviewed_at"] is not None
    assert items_by_goal[deferred_goal["goal_id"]]["follow_up_actions"][0]["id"] == "resolve_deferred_followup"
    assert (
        items_by_goal[deferred_goal["goal_id"]]["follow_up_actions"][0]["target"]["suggestion_index"]
        == items_by_goal[deferred_goal["goal_id"]]["latest_deferred_suggestion"]["suggestion_index"]
    )
    assert items_by_goal[created_goal["goal_id"]]["needs_operator_attention"] is True
    assert items_by_goal[created_goal["goal_id"]]["attention_reason"] == "created_task_not_terminal"
    assert items_by_goal[created_goal["goal_id"]]["created_task_statuses"] == {"pending": created_total}
    assert items_by_goal[created_goal["goal_id"]]["follow_up_actions"][0]["id"] == "monitor_created_tasks"
    assert items_by_goal[created_goal["goal_id"]]["follow_up_actions"][0]["action_type"] == "select_goal_tasks"
    assert items_by_goal[created_goal["goal_id"]]["follow_up_actions"][0]["target"]["status_counts"] == {
        "pending": created_total,
    }
    assert items_by_goal[rejected_goal["goal_id"]]["needs_operator_attention"] is False
    assert items_by_goal[rejected_goal["goal_id"]]["attention_reason"] == "ready"
    assert items_by_goal[rejected_goal["goal_id"]]["rejected"] == rejected_total
    assert items_by_goal[rejected_goal["goal_id"]]["follow_up_actions"] == [
        {
            "id": "no_action_required",
            "label": "No immediate action",
            "description": (
                "This planner handoff is clear; continue monitoring or open the preview if context is needed."
            ),
            "action_type": "none",
            "target": {"goal_id": rejected_goal["goal_id"]},
            "mutates": False,
        }
    ]
    assert all(
        action["mutates"] is False
        for item in items_by_goal.values()
        for action in item["follow_up_actions"]
    )


def test_planner_global_handoffs_read_only(client):
    goal = _create_goal(client, "Read only global handoff")
    total = _planner_suggestion_count(client, goal["goal_id"])
    services = client.app.state.services
    before_reviews = services.db.fetch_scalar("SELECT COUNT(*) FROM planner_suggestion_reviews")
    before_tasks = client.get(f"/tasks?goal_id={goal['goal_id']}").json()
    before_events = services.db.fetch_scalar("SELECT COUNT(*) FROM events")

    response = client.get("/goals/planner/handoffs")

    after_reviews = services.db.fetch_scalar("SELECT COUNT(*) FROM planner_suggestion_reviews")
    after_tasks = client.get(f"/tasks?goal_id={goal['goal_id']}").json()
    after_events = services.db.fetch_scalar("SELECT COUNT(*) FROM events")

    assert response.status_code == 200
    assert response.json()["summary"]["pending"] == total
    assert before_reviews == after_reviews == 0
    assert before_tasks == after_tasks == []
    assert before_events == after_events


def test_planner_global_handoffs_sorting_and_attention(client):
    created_goal = _create_goal(client, "A created only")
    deferred_goal = _create_goal(client, "B deferred only")
    pending_goal = _create_goal(client, "C pending only")
    rejected_goal = _create_goal(client, "D rejected only")

    created_total = _planner_suggestion_count(client, created_goal["goal_id"])
    deferred_total = _planner_suggestion_count(client, deferred_goal["goal_id"])
    rejected_total = _planner_suggestion_count(client, rejected_goal["goal_id"])

    client.post(
        f"/goals/{created_goal['goal_id']}/plan/tasks/bulk",
        json={"suggestion_indexes": list(range(created_total)), "overrides": {}},
    )
    client.post(
        f"/goals/{deferred_goal['goal_id']}/plan/reviews/bulk",
        json={"suggestion_indexes": list(range(deferred_total)), "decision": "deferred"},
    )
    client.post(
        f"/goals/{rejected_goal['goal_id']}/plan/reviews/bulk",
        json={"suggestion_indexes": list(range(rejected_total)), "decision": "rejected"},
    )

    payload = client.get("/goals/planner/handoffs").json()

    assert [item["goal_id"] for item in payload["items"]] == [
        pending_goal["goal_id"],
        deferred_goal["goal_id"],
        created_goal["goal_id"],
        rejected_goal["goal_id"],
    ]
    assert [item["needs_operator_attention"] for item in payload["items"]] == [True, True, True, False]
    assert [item["attention_reason"] for item in payload["items"]] == [
        "pending_review",
        "deferred_followup",
        "created_task_not_terminal",
        "ready",
    ]


def test_planner_global_handoffs_filters_and_sorts(client):
    pending_goal = _create_goal(client, "Zulu pending handoff")
    deferred_goal = _create_goal(client, "Bravo deferred handoff")
    created_goal = _create_goal(client, "Charlie created handoff")
    ready_goal = _create_goal(client, "Alpha ready handoff")

    deferred_total = _planner_suggestion_count(client, deferred_goal["goal_id"])
    created_total = _planner_suggestion_count(client, created_goal["goal_id"])
    ready_total = _planner_suggestion_count(client, ready_goal["goal_id"])
    _planner_suggestion_count(client, pending_goal["goal_id"])

    client.post(
        f"/goals/{deferred_goal['goal_id']}/plan/reviews/bulk",
        json={"suggestion_indexes": list(range(deferred_total)), "decision": "deferred"},
    )
    created_payload = client.post(
        f"/goals/{created_goal['goal_id']}/plan/tasks/bulk",
        json={"suggestion_indexes": list(range(created_total)), "overrides": {}},
    ).json()
    for created_item in created_payload["created"]:
        client.post(f"/tasks/{created_item['task']['task_id']}/success")
    client.post(
        f"/goals/{ready_goal['goal_id']}/plan/reviews/bulk",
        json={"suggestion_indexes": list(range(ready_total)), "decision": "rejected"},
    )

    services = client.app.state.services
    services.db.execute(
        "UPDATE planner_suggestion_reviews SET updated_at = ? WHERE goal_id = ?",
        "2026-04-01 08:00:00",
        deferred_goal["goal_id"],
    )
    services.db.execute(
        "UPDATE planner_suggestion_reviews SET updated_at = ? WHERE goal_id = ?",
        "2026-03-31 07:00:00",
        created_goal["goal_id"],
    )
    services.db.execute(
        "UPDATE planner_suggestion_reviews SET updated_at = ? WHERE goal_id = ?",
        "2026-04-02 09:00:00",
        ready_goal["goal_id"],
    )

    needs_attention = client.get("/goals/planner/handoffs?status=needs_attention").json()
    ready = client.get("/goals/planner/handoffs?status=ready").json()
    deferred_only = client.get("/goals/planner/handoffs?reason=deferred_followup").json()
    by_title = client.get("/goals/planner/handoffs?sort=goal_title").json()
    by_reviewed = client.get("/goals/planner/handoffs?sort=last_reviewed_at").json()

    assert [item["goal_id"] for item in needs_attention["items"]] == [
        pending_goal["goal_id"],
        deferred_goal["goal_id"],
    ]
    assert needs_attention["summary"]["total_goals"] == 2
    assert [item["goal_id"] for item in ready["items"]] == [
        ready_goal["goal_id"],
        created_goal["goal_id"],
    ]
    assert ready["summary"]["goals_needing_attention"] == 0
    assert [item["goal_id"] for item in deferred_only["items"]] == [deferred_goal["goal_id"]]
    assert [item["goal_title"] for item in by_title["items"]] == [
        "Alpha ready handoff",
        "Bravo deferred handoff",
        "Charlie created handoff",
        "Zulu pending handoff",
    ]
    assert [item["goal_id"] for item in by_reviewed["items"][:2]] == [
        ready_goal["goal_id"],
        deferred_goal["goal_id"],
    ]


def test_planner_global_handoffs_invalid_query_returns_422(client):
    for query in [
        "status=blocked",
        "reason=unknown",
        "sort=created",
    ]:
        response = client.get(f"/goals/planner/handoffs?{query}")

        assert response.status_code == 422
