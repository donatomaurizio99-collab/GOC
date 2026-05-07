from fastapi import APIRouter, Depends

from goal_ops_console.models import (
    DomainError,
    GoalCreateRequest,
    PlannerPreviewResponse,
    PlannerTaskCreateRequest,
    PlannerTaskCreateResponse,
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


@router.post("/{goal_id}/plan", response_model=PlannerPreviewResponse)
def preview_goal_plan(goal_id: str, services: AppServices = Depends(get_services)) -> dict:
    goal = services.state_manager.get_goal(goal_id)
    return services.planner.create_plan(goal)


@router.post("/{goal_id}/plan/tasks", status_code=201, response_model=PlannerTaskCreateResponse)
def create_task_from_plan_suggestion(
    goal_id: str,
    request: PlannerTaskCreateRequest,
    services: AppServices = Depends(get_services),
) -> dict:
    goal = services.state_manager.get_goal(goal_id)
    plan = services.planner.create_plan(goal)
    suggestions = plan["suggestions"]
    if request.suggestion_index >= len(suggestions):
        raise DomainError(
            f"Planner suggestion index {request.suggestion_index} not found for goal {goal_id}"
        )
    suggestion = suggestions[request.suggestion_index]
    task = services.execution_layer.create_task(goal_id=goal_id, title=suggestion["title"])
    return {
        "goal_id": goal_id,
        "suggestion_index": request.suggestion_index,
        "suggestion": suggestion,
        "task": task,
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
