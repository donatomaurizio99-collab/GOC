from fastapi import APIRouter, Depends

from goal_ops_console.models import WorkflowStartRequest
from goal_ops_console.services import AppServices, get_services

router = APIRouter(prefix="/workflows", tags=["workflows"])


@router.get("")
def list_workflows(
    include_disabled: bool = False,
    services: AppServices = Depends(get_services),
) -> dict:
    return {
        "workflows": services.workflow_catalog.list_workflows(include_disabled=include_disabled),
    }


@router.get("/runs")
def list_workflow_runs(
    workflow_id: str | None = None,
    limit: int = 100,
    services: AppServices = Depends(get_services),
) -> dict:
    return {
        "runs": services.workflow_catalog.list_runs(
            workflow_id=workflow_id,
            limit=limit,
        ),
    }


@router.post("/{workflow_id}/start", status_code=201)
def start_workflow(
    workflow_id: str,
    request: WorkflowStartRequest,
    services: AppServices = Depends(get_services),
) -> dict:
    run = services.workflow_catalog.start_workflow(
        workflow_id,
        requested_by=request.requested_by,
        payload=request.payload,
    )
    return {"run": run}
