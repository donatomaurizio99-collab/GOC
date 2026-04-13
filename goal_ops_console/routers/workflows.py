from fastapi import APIRouter, Depends, Header

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


@router.post("/runs/reap")
def reap_workflow_runs(
    timeout_seconds: int | None = None,
    limit: int | None = None,
    services: AppServices = Depends(get_services),
) -> dict:
    return services.workflow_catalog.reap_stuck_runs(
        timeout_seconds=timeout_seconds or services.settings.workflow_run_timeout_seconds,
        limit=limit or services.settings.workflow_reaper_batch_size,
    )


@router.post("/{workflow_id}/start", status_code=201)
def start_workflow(
    workflow_id: str,
    request: WorkflowStartRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=120),
    services: AppServices = Depends(get_services),
) -> dict:
    run = services.workflow_catalog.start_workflow(
        workflow_id,
        requested_by=request.requested_by,
        payload=request.payload,
        idempotency_key=idempotency_key,
    )
    return {"run": run}
