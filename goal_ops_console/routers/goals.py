import json
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query

from goal_ops_console.database import now_utc
from goal_ops_console.models import (
    ConflictError,
    DomainError,
    GoalCreateRequest,
    NotFoundError,
    PlannerBulkReviewDecisionRequest,
    PlannerBulkReviewDecisionResponse,
    PlannerBulkTaskCreateRequest,
    PlannerBulkTaskCreateResponse,
    PlannerDeferredFollowupResponse,
    PlannerPreviewResponse,
    PlannerReviewAuditResponse,
    PlannerReviewDecisionRequest,
    PlannerReviewDecisionResponse,
    PlannerReviewInboxResponse,
    PlannerReviewListResponse,
    PlannerReviewReopenResponse,
    PlannerTaskCreateRequest,
    PlannerTaskCreateResponse,
    PlannerTaskSuggestionOverride,
)
from goal_ops_console.services import AppServices, get_services

router = APIRouter(prefix="/goals", tags=["goals"])

_PLANNER_REVIEW_AUDIT_EVENT_TYPES = (
    "planner.suggestion_reviewed",
    "planner.suggestion_review_reopened",
)


@router.get("")
def list_goals(services: AppServices = Depends(get_services)) -> list[dict]:
    return services.state_manager.list_goals()


@router.post("", status_code=201)
def create_goal(
    request: GoalCreateRequest,
    services: AppServices = Depends(get_services),
) -> dict:
    return services.state_manager.create_goal(
        title=request.title,
        description=request.description,
        urgency=request.urgency,
        value=request.value,
        deadline_score=request.deadline_score,
    )


@router.get("/{goal_id}")
def get_goal(goal_id: str, services: AppServices = Depends(get_services)) -> dict:
    return services.state_manager.get_goal(goal_id)


def _preview_goal_plan(goal_id: str, services: AppServices) -> dict:
    goal = services.state_manager.get_goal(goal_id)
    plan = services.planner.create_plan(goal)
    existing_by_title = _existing_tasks_by_title(goal_id, services)
    existing_by_index = _existing_tasks_by_suggestion_index(goal_id, services)
    reviews_by_index = _planner_reviews_by_index(goal_id, services)

    for suggestion_index, suggestion in enumerate(plan["suggestions"]):
        existing = existing_by_index.get(suggestion_index) or existing_by_title.get(suggestion["title"])
        review = reviews_by_index.get(suggestion_index)
        suggestion["task_exists"] = existing is not None
        suggestion["existing_task_id"] = existing["task_id"] if existing is not None else None
        suggestion["review_decision"] = _suggestion_review_decision(review, existing)
        suggestion["review_comment"] = review["comment"] if review is not None else None
        suggestion["review_task_id"] = (
            review["task_id"]
            if review is not None
            else suggestion["existing_task_id"]
        )
        suggestion["reviewed_at"] = review["updated_at"] if review is not None else None
    return plan


def _existing_tasks_by_title(goal_id: str, services: AppServices) -> dict[str, dict]:
    existing_by_title = {}
    for task in services.execution_layer.list_tasks(goal_id=goal_id):
        existing_by_title.setdefault(task["title"], task)
    return existing_by_title


def _existing_tasks_by_suggestion_index(goal_id: str, services: AppServices) -> dict[int, dict]:
    existing_by_index = {}
    for task in services.execution_layer.list_tasks(goal_id=goal_id):
        suggestion_index = task.get("planner_suggestion_index")
        if isinstance(suggestion_index, int):
            existing_by_index.setdefault(suggestion_index, task)
    return existing_by_index


def _get_plan_suggestion(plan: dict, suggestion_index: int, goal_id: str) -> dict:
    suggestions = plan["suggestions"]
    if suggestion_index < 0 or suggestion_index >= len(suggestions):
        raise DomainError(f"Planner suggestion index {suggestion_index} not found for goal {goal_id}")
    return suggestions[suggestion_index]


def _override_to_dict(override: PlannerTaskSuggestionOverride | None) -> dict | None:
    if override is None:
        return None
    values = override.model_dump(exclude_none=True)
    return values or None


def _apply_suggestion_override(suggestion: dict, override: PlannerTaskSuggestionOverride | None) -> tuple[dict, dict | None]:
    override_values = _override_to_dict(override)
    if override_values is None:
        return dict(suggestion), None
    applied_suggestion = {**suggestion, **override_values}
    if "title" in override_values:
        applied_suggestion["task_exists"] = False
        applied_suggestion["existing_task_id"] = None
    return applied_suggestion, override_values


def _create_task_from_suggestion(
    goal_id: str,
    suggestion_index: int,
    original_suggestion: dict,
    applied_suggestion: dict,
    services: AppServices,
    operator_override: dict | None = None,
) -> dict:
    return services.execution_layer.create_task(
        goal_id=goal_id,
        title=applied_suggestion["title"],
        planner_source=original_suggestion["source"],
        planner_suggestion_index=suggestion_index,
        planner_priority_hint=original_suggestion["priority_hint"],
        planner_suggestion_description=original_suggestion["description"],
        planner_suggestion_rationale=original_suggestion["rationale"],
        planner_operator_overrides=operator_override,
    )


def _planner_review_from_row(row: Any) -> dict:
    review = dict(row)
    raw_override = review.get("operator_override")
    if isinstance(raw_override, str) and raw_override:
        review["operator_override"] = json.loads(raw_override)
    else:
        review["operator_override"] = None
    return review


def _planner_reviews_by_index(goal_id: str, services: AppServices) -> dict[int, dict]:
    rows = services.db.fetch_all(
        """SELECT goal_id,
                  suggestion_index,
                  decision,
                  comment,
                  task_id,
                  planner_source,
                  suggestion_title,
                  suggestion_description,
                  suggestion_rationale,
                  suggestion_priority_hint,
                  operator_override,
                  created_at,
                  updated_at
           FROM planner_suggestion_reviews
           WHERE goal_id = ?""",
        goal_id,
    )
    return {int(row["suggestion_index"]): _planner_review_from_row(row) for row in rows}


def _planner_review_summary(plan: dict) -> dict:
    summary = {
        "total_suggestions": len(plan["suggestions"]),
        "pending": 0,
        "created": 0,
        "deferred": 0,
        "rejected": 0,
    }
    for suggestion in plan["suggestions"]:
        decision = suggestion["review_decision"]
        summary[decision] += 1
    return summary


def _planner_review_list(goal_id: str, services: AppServices) -> dict:
    plan = _preview_goal_plan(goal_id, services)
    reviews = sorted(
        _planner_reviews_by_index(goal_id, services).values(),
        key=lambda review: review["suggestion_index"],
    )
    return {
        "goal_id": plan["goal_id"],
        "goal_title": plan["goal_title"],
        "source": plan["source"],
        "summary": _planner_review_summary(plan),
        "reviews": reviews,
    }


def _planner_review_event_payload(row: Any) -> dict:
    raw_payload = row["payload"]
    payload = json.loads(raw_payload) if raw_payload else {}
    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    return data if isinstance(data, dict) else {}


def _planner_review_audit_entry(row: Any, goal_id: str, plan: dict) -> dict | None:
    data = _planner_review_event_payload(row)
    raw_suggestion_index = data.get("suggestion_index")
    if not isinstance(raw_suggestion_index, int) or raw_suggestion_index < 0:
        return None

    suggestion = plan["suggestions"][raw_suggestion_index] if raw_suggestion_index < len(plan["suggestions"]) else {}
    event_type = row["event_type"]
    is_reopen = event_type == "planner.suggestion_review_reopened"
    decision = None if is_reopen else data.get("decision")
    cleared_decision = data.get("cleared_decision") if is_reopen else None
    comment = None if is_reopen else data.get("comment")
    cleared_comment = data.get("cleared_comment") if is_reopen else None
    return {
        "seq": row["seq"],
        "event_id": row["event_id"],
        "event_type": event_type,
        "action": "reopened" if is_reopen else "reviewed",
        "goal_id": data.get("goal_id") or goal_id,
        "suggestion_index": raw_suggestion_index,
        "suggestion_title": suggestion.get("title") or f"Suggestion #{raw_suggestion_index + 1}",
        "decision": decision,
        "cleared_decision": cleared_decision,
        "comment": comment,
        "cleared_comment": cleared_comment,
        "task_id": None if is_reopen else data.get("task_id"),
        "source": data.get("source") or suggestion.get("source") or plan["source"],
        "emitted_at": row["emitted_at"],
    }


def _planner_review_audit(goal_id: str, services: AppServices, *, limit: int = 100) -> dict:
    plan = _preview_goal_plan(goal_id, services)
    placeholders = ", ".join("?" for _ in _PLANNER_REVIEW_AUDIT_EVENT_TYPES)
    rows = services.db.fetch_all(
        f"""SELECT seq, event_id, event_type, entity_id, correlation_id, payload, emitted_at
            FROM events
            WHERE entity_id = ?
              AND event_type IN ({placeholders})
            ORDER BY seq DESC
            LIMIT ?""",
        goal_id,
        *_PLANNER_REVIEW_AUDIT_EVENT_TYPES,
        limit,
    )
    entries = [
        entry
        for entry in (_planner_review_audit_entry(row, goal_id, plan) for row in rows)
        if entry is not None
    ]
    return {
        "goal_id": plan["goal_id"],
        "goal_title": plan["goal_title"],
        "source": plan["source"],
        "summary": _planner_review_summary(plan),
        "entries": entries,
    }


def _planner_review_inbox_next_suggestion(plan: dict) -> dict | None:
    for suggestion_index, suggestion in enumerate(plan["suggestions"]):
        if suggestion["review_decision"] == "pending":
            return {
                "suggestion_index": suggestion_index,
                "title": suggestion["title"],
                "description": suggestion["description"],
                "rationale": suggestion["rationale"],
                "priority_hint": suggestion["priority_hint"],
                "source": suggestion["source"],
            }
    return None


def _planner_review_inbox_item(goal: dict, services: AppServices) -> dict:
    plan = _preview_goal_plan(goal["goal_id"], services)
    reviews = sorted(
        _planner_reviews_by_index(goal["goal_id"], services).values(),
        key=lambda review: review["suggestion_index"],
    )
    summary = _planner_review_summary(plan)
    return {
        "goal_id": plan["goal_id"],
        "goal_title": plan["goal_title"],
        "state": goal["state"],
        "source": plan["source"],
        "summary": summary,
        "last_reviewed_at": max((review["updated_at"] for review in reviews), default=None),
        "needs_review": summary["pending"] > 0,
        "next_suggestion": _planner_review_inbox_next_suggestion(plan),
    }


def _filter_planner_review_inbox_items(items: list[dict], status: str) -> list[dict]:
    if status == "needs_review":
        return [item for item in items if item["needs_review"]]
    if status == "reviewed":
        return [item for item in items if not item["needs_review"]]
    return items


def _sort_planner_review_inbox_items(items: list[dict], sort: str) -> list[dict]:
    if sort == "goal_title":
        return sorted(items, key=lambda item: item["goal_title"].lower())
    if sort == "last_reviewed_at":
        sorted_items = sorted(items, key=lambda item: item["goal_title"].lower())
        sorted_items.sort(key=lambda item: item["last_reviewed_at"] or "", reverse=True)
        return sorted_items
    return sorted(
        items,
        key=lambda item: (
            0 if item["needs_review"] else 1,
            item["last_reviewed_at"] or "",
            item["goal_title"].lower(),
        ),
    )


def _planner_review_inbox(services: AppServices, status: str = "all", sort: str = "needs_review") -> dict:
    items = [_planner_review_inbox_item(goal, services) for goal in services.state_manager.list_goals()]
    items = _filter_planner_review_inbox_items(items, status)
    items = _sort_planner_review_inbox_items(items, sort)
    return {
        "summary": {
            "total_goals": len(items),
            "goals_needing_review": sum(1 for item in items if item["needs_review"]),
            "pending_suggestions": sum(item["summary"]["pending"] for item in items),
            "created": sum(item["summary"]["created"] for item in items),
            "deferred": sum(item["summary"]["deferred"] for item in items),
            "rejected": sum(item["summary"]["rejected"] for item in items),
        },
        "items": items,
    }


def _planner_deferred_followup_item(goal: dict, review: dict) -> dict:
    return {
        "goal_id": goal["goal_id"],
        "goal_title": goal["title"],
        "state": goal["state"],
        "source": review["planner_source"],
        "suggestion_index": review["suggestion_index"],
        "suggestion_title": review["suggestion_title"],
        "suggestion_description": review["suggestion_description"],
        "suggestion_rationale": review["suggestion_rationale"],
        "priority_hint": review["suggestion_priority_hint"],
        "comment": review["comment"],
        "deferred_at": review["updated_at"],
    }


def _sort_planner_deferred_followups(items: list[dict]) -> list[dict]:
    sorted_items = sorted(
        items,
        key=lambda item: (item["goal_title"].lower(), item["suggestion_index"]),
    )
    sorted_items.sort(key=lambda item: item["deferred_at"], reverse=True)
    return sorted_items


def _planner_deferred_followups(services: AppServices) -> dict:
    items = []
    for goal in services.state_manager.list_goals():
        reviews = _planner_reviews_by_index(goal["goal_id"], services).values()
        items.extend(
            _planner_deferred_followup_item(goal, review)
            for review in reviews
            if review["decision"] == "deferred"
        )
    items = _sort_planner_deferred_followups(items)
    return {
        "summary": {
            "total_followups": len(items),
            "goals_with_followups": len({item["goal_id"] for item in items}),
        },
        "items": items,
    }


def _get_planner_review(goal_id: str, suggestion_index: int, services: AppServices) -> dict | None:
    row = services.db.fetch_one(
        """SELECT goal_id,
                  suggestion_index,
                  decision,
                  comment,
                  task_id,
                  planner_source,
                  suggestion_title,
                  suggestion_description,
                  suggestion_rationale,
                  suggestion_priority_hint,
                  operator_override,
                  created_at,
                  updated_at
           FROM planner_suggestion_reviews
           WHERE goal_id = ? AND suggestion_index = ?""",
        goal_id,
        suggestion_index,
    )
    return _planner_review_from_row(row) if row is not None else None


def _suggestion_review_decision(review: dict | None, existing_task: dict | None) -> str:
    if review is not None:
        return review["decision"]
    if existing_task is not None:
        return "created"
    return "pending"


def _normalized_review_comment(comment: str | None) -> str | None:
    cleaned = (comment or "").strip()
    return cleaned or None


def _ensure_unique_suggestion_indexes(goal_id: str, suggestion_indexes: list[int]) -> None:
    if len(set(suggestion_indexes)) != len(suggestion_indexes):
        raise DomainError(f"Planner suggestion indexes must be unique for goal {goal_id}")


def _create_planner_review(
    *,
    goal_id: str,
    suggestion_index: int,
    suggestion: dict,
    services: AppServices,
    decision: str,
    comment: str | None = None,
    task_id: str | None = None,
    operator_override: dict | None = None,
) -> dict:
    existing_review = _get_planner_review(goal_id, suggestion_index, services)
    if existing_review is not None:
        raise ConflictError(
            f"Planner suggestion {suggestion_index} already has review decision "
            f"{existing_review['decision']} for goal {goal_id}"
        )
    timestamp = now_utc()
    normalized_comment = _normalized_review_comment(comment)
    operator_override_json = (
        json.dumps(operator_override, sort_keys=True)
        if operator_override is not None
        else None
    )
    with services.db.transaction() as tx:
        tx.execute(
            """INSERT INTO planner_suggestion_reviews
               (goal_id, suggestion_index, decision, comment, task_id, planner_source,
                suggestion_title, suggestion_description, suggestion_rationale,
                suggestion_priority_hint, operator_override, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            goal_id,
            suggestion_index,
            decision,
            normalized_comment,
            task_id,
            suggestion["source"],
            suggestion["title"],
            suggestion["description"],
            suggestion["rationale"],
            suggestion["priority_hint"],
            operator_override_json,
            timestamp,
            timestamp,
        )
        services.event_bus.record_event(
            "planner.suggestion_reviewed",
            goal_id,
            f"{goal_id}:planner:{suggestion_index}",
            {
                "goal_id": goal_id,
                "suggestion_index": suggestion_index,
                "decision": decision,
                "comment": normalized_comment,
                "task_id": task_id,
                "source": suggestion["source"],
            },
            tx=tx,
        )
    review = _get_planner_review(goal_id, suggestion_index, services)
    assert review is not None
    return review


def _create_planner_reviews(
    *,
    goal_id: str,
    resolved_suggestions: list[tuple[int, dict]],
    services: AppServices,
    decision: str,
    comment: str | None = None,
) -> list[dict]:
    timestamp = now_utc()
    normalized_comment = _normalized_review_comment(comment)
    with services.db.transaction() as tx:
        for suggestion_index, suggestion in resolved_suggestions:
            tx.execute(
                """INSERT INTO planner_suggestion_reviews
                   (goal_id, suggestion_index, decision, comment, task_id, planner_source,
                    suggestion_title, suggestion_description, suggestion_rationale,
                    suggestion_priority_hint, operator_override, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                goal_id,
                suggestion_index,
                decision,
                normalized_comment,
                None,
                suggestion["source"],
                suggestion["title"],
                suggestion["description"],
                suggestion["rationale"],
                suggestion["priority_hint"],
                None,
                timestamp,
                timestamp,
            )
            services.event_bus.record_event(
                "planner.suggestion_reviewed",
                goal_id,
                f"{goal_id}:planner:{suggestion_index}",
                {
                    "goal_id": goal_id,
                    "suggestion_index": suggestion_index,
                    "decision": decision,
                    "comment": normalized_comment,
                    "task_id": None,
                    "source": suggestion["source"],
                },
                tx=tx,
            )
    reviews = [_get_planner_review(goal_id, suggestion_index, services) for suggestion_index, _ in resolved_suggestions]
    assert all(review is not None for review in reviews)
    return [review for review in reviews if review is not None]


def _ensure_suggestion_can_be_created(goal_id: str, suggestion_index: int, services: AppServices) -> None:
    existing_review = _get_planner_review(goal_id, suggestion_index, services)
    if existing_review is not None:
        if existing_review["decision"] == "created" and existing_review.get("task_id"):
            raise ConflictError(
                f"Planner suggestion already exists as task {existing_review['task_id']} for goal {goal_id}"
            )
        raise ConflictError(
            f"Planner suggestion {suggestion_index} already has review decision "
            f"{existing_review['decision']} for goal {goal_id}"
        )


def _reopen_planner_review(goal_id: str, suggestion_index: int, services: AppServices) -> dict:
    existing_review = _get_planner_review(goal_id, suggestion_index, services)
    if existing_review is None:
        raise NotFoundError(f"Planner suggestion {suggestion_index} has no review decision for goal {goal_id}")
    if existing_review["decision"] == "created":
        raise ConflictError(
            f"Planner suggestion already exists as task {existing_review['task_id']} for goal {goal_id}"
        )
    timestamp = now_utc()
    with services.db.transaction() as tx:
        tx.execute(
            "DELETE FROM planner_suggestion_reviews WHERE goal_id = ? AND suggestion_index = ?",
            goal_id,
            suggestion_index,
        )
        services.event_bus.record_event(
            "planner.suggestion_review_reopened",
            goal_id,
            f"{goal_id}:planner:{suggestion_index}",
            {
                "goal_id": goal_id,
                "suggestion_index": suggestion_index,
                "cleared_decision": existing_review["decision"],
                "cleared_comment": existing_review["comment"],
                "source": existing_review["planner_source"],
                "reopened_at": timestamp,
            },
            tx=tx,
        )
    return existing_review


@router.post("/{goal_id}/plan", response_model=PlannerPreviewResponse)
def preview_goal_plan(goal_id: str, services: AppServices = Depends(get_services)) -> dict:
    return _preview_goal_plan(goal_id, services)


@router.get("/{goal_id}/plan/reviews", response_model=PlannerReviewListResponse)
def list_plan_suggestion_reviews(
    goal_id: str,
    services: AppServices = Depends(get_services),
) -> dict:
    return _planner_review_list(goal_id, services)


@router.get("/{goal_id}/plan/reviews/audit", response_model=PlannerReviewAuditResponse)
def list_plan_suggestion_review_audit(
    goal_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    services: AppServices = Depends(get_services),
) -> dict:
    return _planner_review_audit(goal_id, services, limit=limit)


@router.get("/planner/reviews", response_model=PlannerReviewInboxResponse)
def list_planner_review_inbox(
    status: Literal["all", "needs_review", "reviewed"] = "all",
    sort: Literal["needs_review", "last_reviewed_at", "goal_title"] = "needs_review",
    services: AppServices = Depends(get_services),
) -> dict:
    return _planner_review_inbox(services, status=status, sort=sort)


@router.get("/planner/reviews/followups", response_model=PlannerDeferredFollowupResponse)
def list_planner_deferred_followups(
    services: AppServices = Depends(get_services),
) -> dict:
    return _planner_deferred_followups(services)


@router.post("/{goal_id}/plan/reviews", status_code=201, response_model=PlannerReviewDecisionResponse)
def review_plan_suggestion(
    goal_id: str,
    request: PlannerReviewDecisionRequest,
    services: AppServices = Depends(get_services),
) -> dict:
    plan = _preview_goal_plan(goal_id, services)
    suggestion = _get_plan_suggestion(plan, request.suggestion_index, goal_id)
    if suggestion["task_exists"]:
        raise ConflictError(
            f"Planner suggestion already exists as task {suggestion['existing_task_id']} for goal {goal_id}"
        )
    review = _create_planner_review(
        goal_id=goal_id,
        suggestion_index=request.suggestion_index,
        suggestion=suggestion,
        services=services,
        decision=request.decision,
        comment=request.comment,
    )
    refreshed_plan = _preview_goal_plan(goal_id, services)
    refreshed_suggestion = _get_plan_suggestion(refreshed_plan, request.suggestion_index, goal_id)
    return {
        "goal_id": goal_id,
        "suggestion_index": request.suggestion_index,
        "suggestion": refreshed_suggestion,
        "review": review,
    }


@router.post("/{goal_id}/plan/reviews/bulk", status_code=201, response_model=PlannerBulkReviewDecisionResponse)
def review_plan_suggestions_bulk(
    goal_id: str,
    request: PlannerBulkReviewDecisionRequest,
    services: AppServices = Depends(get_services),
) -> dict:
    plan = _preview_goal_plan(goal_id, services)
    _ensure_unique_suggestion_indexes(goal_id, request.suggestion_indexes)
    existing_reviews_by_index = _planner_reviews_by_index(goal_id, services)
    resolved_suggestions = []
    for suggestion_index in request.suggestion_indexes:
        suggestion = _get_plan_suggestion(plan, suggestion_index, goal_id)
        if suggestion["task_exists"]:
            raise ConflictError(
                f"Planner suggestion already exists as task {suggestion['existing_task_id']} for goal {goal_id}"
            )
        existing_review = existing_reviews_by_index.get(suggestion_index)
        if existing_review is not None:
            raise ConflictError(
                f"Planner suggestion {suggestion_index} already has review decision "
                f"{existing_review['decision']} for goal {goal_id}"
            )
        resolved_suggestions.append((suggestion_index, suggestion))

    reviews = _create_planner_reviews(
        goal_id=goal_id,
        resolved_suggestions=resolved_suggestions,
        services=services,
        decision=request.decision,
        comment=request.comment,
    )
    refreshed_plan = _preview_goal_plan(goal_id, services)
    refreshed_suggestions = [
        _get_plan_suggestion(refreshed_plan, suggestion_index, goal_id)
        for suggestion_index in request.suggestion_indexes
    ]
    return {
        "goal_id": goal_id,
        "requested_suggestion_indexes": request.suggestion_indexes,
        "decision": request.decision,
        "suggestions": refreshed_suggestions,
        "reviews": reviews,
    }


@router.delete("/{goal_id}/plan/reviews/{suggestion_index}", response_model=PlannerReviewReopenResponse)
def reopen_plan_suggestion_review(
    goal_id: str,
    suggestion_index: int,
    services: AppServices = Depends(get_services),
) -> dict:
    plan = _preview_goal_plan(goal_id, services)
    _get_plan_suggestion(plan, suggestion_index, goal_id)
    cleared_review = _reopen_planner_review(goal_id, suggestion_index, services)
    refreshed_plan = _preview_goal_plan(goal_id, services)
    refreshed_suggestion = _get_plan_suggestion(refreshed_plan, suggestion_index, goal_id)
    return {
        "goal_id": goal_id,
        "suggestion_index": suggestion_index,
        "suggestion": refreshed_suggestion,
        "cleared_review": cleared_review,
    }


@router.post("/{goal_id}/plan/tasks", status_code=201, response_model=PlannerTaskCreateResponse)
def create_task_from_plan_suggestion(
    goal_id: str,
    request: PlannerTaskCreateRequest,
    services: AppServices = Depends(get_services),
) -> dict:
    plan = _preview_goal_plan(goal_id, services)
    suggestion = _get_plan_suggestion(plan, request.suggestion_index, goal_id)
    _ensure_suggestion_can_be_created(goal_id, request.suggestion_index, services)
    if suggestion["task_exists"]:
        raise ConflictError(
            f"Planner suggestion already exists as task {suggestion['existing_task_id']} for goal {goal_id}"
        )
    applied_suggestion, operator_override = _apply_suggestion_override(suggestion, request.override)
    existing_by_title = _existing_tasks_by_title(goal_id, services)
    existing = existing_by_title.get(applied_suggestion["title"])
    if existing is not None:
        raise ConflictError(
            f"Planner suggestion already exists as task {existing['task_id']} for goal {goal_id}"
        )
    task = _create_task_from_suggestion(
        goal_id,
        request.suggestion_index,
        suggestion,
        applied_suggestion,
        services,
        operator_override,
    )
    review = _create_planner_review(
        goal_id=goal_id,
        suggestion_index=request.suggestion_index,
        suggestion=suggestion,
        services=services,
        decision="created",
        task_id=task["task_id"],
        operator_override=operator_override,
    )
    return {
        "goal_id": goal_id,
        "suggestion_index": request.suggestion_index,
        "suggestion": suggestion,
        "applied_suggestion": applied_suggestion,
        "operator_override": operator_override,
        "review": review,
        "task": task,
    }


@router.post("/{goal_id}/plan/tasks/bulk", status_code=201, response_model=PlannerBulkTaskCreateResponse)
def create_tasks_from_plan_suggestions(
    goal_id: str,
    request: PlannerBulkTaskCreateRequest,
    services: AppServices = Depends(get_services),
) -> dict:
    plan = _preview_goal_plan(goal_id, services)
    for suggestion_index in request.suggestion_indexes:
        _get_plan_suggestion(plan, suggestion_index, goal_id)
    requested_indexes = set(request.suggestion_indexes)
    for override_index in request.overrides:
        if override_index not in requested_indexes:
            raise DomainError(f"Planner override index {override_index} was not requested for goal {goal_id}")

    resolved_suggestions = []
    for suggestion_index in request.suggestion_indexes:
        suggestion = _get_plan_suggestion(plan, suggestion_index, goal_id)
        applied_suggestion, operator_override = _apply_suggestion_override(
            suggestion,
            request.overrides.get(suggestion_index),
        )
        resolved_suggestions.append(
            {
                "suggestion_index": suggestion_index,
                "suggestion": suggestion,
                "applied_suggestion": applied_suggestion,
                "operator_override": operator_override,
            }
        )

    existing_by_title = _existing_tasks_by_title(goal_id, services)
    existing_by_index = _existing_tasks_by_suggestion_index(goal_id, services)
    existing_reviews_by_index = _planner_reviews_by_index(goal_id, services)
    created_by_title: dict[str, dict] = {}
    created: list[dict] = []
    skipped_duplicates: list[dict] = []

    for item in resolved_suggestions:
        suggestion_index = item["suggestion_index"]
        suggestion = item["suggestion"]
        applied_suggestion = item["applied_suggestion"]
        operator_override = item["operator_override"]
        existing_review = existing_reviews_by_index.get(suggestion_index)
        if existing_review is not None:
            reason = (
                "already_exists"
                if existing_review["decision"] == "created"
                else f"review_{existing_review['decision']}"
            )
            skipped_duplicates.append(
                {
                    "suggestion_index": suggestion_index,
                    "suggestion": suggestion,
                    "applied_suggestion": applied_suggestion,
                    "operator_override": operator_override,
                    "existing_task_id": existing_review["task_id"],
                    "review": existing_review,
                    "reason": reason,
                }
            )
            continue
        existing = existing_by_index.get(suggestion_index) or existing_by_title.get(applied_suggestion["title"])
        in_request_duplicate = created_by_title.get(applied_suggestion["title"])
        existing_task_id = (existing["task_id"] if existing is not None else None) or (
            in_request_duplicate["task"]["task_id"] if in_request_duplicate is not None else None
        )
        if existing_task_id is not None:
            skipped_duplicates.append(
                {
                    "suggestion_index": suggestion_index,
                    "suggestion": suggestion,
                    "applied_suggestion": {
                        **applied_suggestion,
                        "task_exists": True,
                        "existing_task_id": existing_task_id,
                    },
                    "operator_override": operator_override,
                    "existing_task_id": existing_task_id,
                    "reason": "already_exists",
                }
            )
            continue

        task = _create_task_from_suggestion(
            goal_id,
            suggestion_index,
            suggestion,
            applied_suggestion,
            services,
            operator_override,
        )
        review = _create_planner_review(
            goal_id=goal_id,
            suggestion_index=suggestion_index,
            suggestion=suggestion,
            services=services,
            decision="created",
            task_id=task["task_id"],
            operator_override=operator_override,
        )
        created_item = {
            "suggestion_index": suggestion_index,
            "suggestion": suggestion,
            "applied_suggestion": applied_suggestion,
            "operator_override": operator_override,
            "review": review,
            "task": task,
        }
        created.append(created_item)
        created_by_title[applied_suggestion["title"]] = created_item

    return {
        "goal_id": goal_id,
        "requested_suggestion_indexes": request.suggestion_indexes,
        "created": created,
        "skipped_duplicates": skipped_duplicates,
    }


@router.post("/{goal_id}/activate")
def activate_goal(goal_id: str, services: AppServices = Depends(get_services)) -> dict:
    return services.state_manager.transition_goal(
        goal_id,
        to_state="active",
        owner="scheduler",
        event_type="goal.activated",
        correlation_id=goal_id,
    )


@router.post("/{goal_id}/block")
def block_goal(goal_id: str, services: AppServices = Depends(get_services)) -> dict:
    return services.state_manager.transition_goal(
        goal_id,
        to_state="blocked",
        owner="state_manager",
        event_type="goal.blocked",
        correlation_id=goal_id,
        reason="Manual block from dashboard",
    )


@router.post("/{goal_id}/archive")
def archive_goal(goal_id: str, services: AppServices = Depends(get_services)) -> dict:
    return services.state_manager.transition_goal(
        goal_id,
        to_state="archived",
        owner="state_manager",
        event_type="goal.archived",
        correlation_id=goal_id,
    )


@router.post("/{goal_id}/hitl_approve")
def hitl_approve(goal_id: str, services: AppServices = Depends(get_services)) -> dict:
    return services.state_manager.transition_goal(
        goal_id,
        to_state="active",
        owner="state_manager",
        event_type="goal.hitl_approved",
        correlation_id=goal_id,
        reason="HITL approval",
    )
