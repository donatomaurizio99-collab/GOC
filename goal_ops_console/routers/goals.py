from fastapi import APIRouter, Depends

from goal_ops_console.models import (
    ConflictError,
    DomainError,
    GoalCreateRequest,
    PlannerBulkTaskCreateRequest,
    PlannerBulkTaskCreateResponse,
    PlannerPreviewResponse,
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

    for suggestion in plan["suggestions"]:
        existing = existing_by_title.get(suggestion["title"])
        suggestion["task_exists"] = existing is not None
        suggestion["existing_task_id"] = existing["task_id"] if existing is not None else None
    return plan


def _existing_tasks_by_title(goal_id: str, services: AppServices) -> dict[str, dict]:
    existing_by_title = {}
    for task in services.execution_layer.list_tasks(goal_id=goal_id):
        existing_by_title.setdefault(task["title"], task)
    return existing_by_title


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
        planner_operator_overrides=operator_override,
    )


@router.post("/{goal_id}/plan", response_model=PlannerPreviewResponse)
def preview_goal_plan(goal_id: str, services: AppServices = Depends(get_services)) -> dict:
    return _preview_goal_plan(goal_id, services)


@router.post("/{goal_id}/plan/tasks", status_code=201, response_model=PlannerTaskCreateResponse)
def create_task_from_plan_suggestion(
    goal_id: str,
    request: PlannerTaskCreateRequest,
    services: AppServices = Depends(get_services),
) -> dict:
    plan = _preview_goal_plan(goal_id, services)
    suggestion = _get_plan_suggestion(plan, request.suggestion_index, goal_id)
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
    return {
        "goal_id": goal_id,
        "suggestion_index": request.suggestion_index,
        "suggestion": suggestion,
        "applied_suggestion": applied_suggestion,
        "operator_override": operator_override,
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
    created_by_title: dict[str, dict] = {}
    created: list[dict] = []
    skipped_duplicates: list[dict] = []

    for item in resolved_suggestions:
        suggestion_index = item["suggestion_index"]
        suggestion = item["suggestion"]
        applied_suggestion = item["applied_suggestion"]
        operator_override = item["operator_override"]
        existing = existing_by_title.get(applied_suggestion["title"])
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
        created_item = {
            "suggestion_index": suggestion_index,
            "suggestion": suggestion,
            "applied_suggestion": applied_suggestion,
            "operator_override": operator_override,
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
