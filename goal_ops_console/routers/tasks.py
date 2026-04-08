from fastapi import APIRouter, Depends

from goal_ops_console.models import TaskCreateRequest, TaskFailureRequest
from goal_ops_console.services import AppServices, get_services

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("")
def list_tasks(goal_id: str | None = None, services: AppServices = Depends(get_services)) -> list[dict]:
    return services.execution_layer.list_tasks(goal_id=goal_id)


@router.post("", status_code=201)
def create_task(
    request: TaskCreateRequest,
    services: AppServices = Depends(get_services),
) -> dict:
    return services.execution_layer.create_task(goal_id=request.goal_id, title=request.title)


@router.get("/{task_id}")
def get_task(task_id: str, services: AppServices = Depends(get_services)) -> dict:
    return services.execution_layer.get_task(task_id)


@router.post("/{task_id}/success")
def succeed_task(task_id: str, services: AppServices = Depends(get_services)) -> dict:
    return services.execution_layer.simulate_success(task_id)


@router.post("/{task_id}/fail")
def fail_task(
    task_id: str,
    request: TaskFailureRequest,
    services: AppServices = Depends(get_services),
) -> dict:
    return services.execution_layer.simulate_failure(
        task_id,
        failure_type=request.failure_type.value,
        error_message=request.error_message,
    )
