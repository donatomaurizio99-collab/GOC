import json
from typing import Any

from fastapi import APIRouter, Depends

from goal_ops_console.database import now_utc
from goal_ops_console.models import (
    ConflictError,
    DomainError,
    GoalCreateRequest,
    NotFoundError,
    PlannerBulkTaskCreateRequest,
    PlannerBulkTaskCreateResponse,
    PlannerPreviewResponse,
    PlannerReviewDecisionRequest,
    PlannerReviewDecisionResponse,
    PlannerReviewReopenResponse,
    PlannerTaskCreateRequest,
    PlannerTaskCreateResponse,
    PlannerTaskSuggestionOverride,
)
from goal_ops_console.services import AppServices, get_services

router = APIRouter(prefix="/goals", tags=["goals"])


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
