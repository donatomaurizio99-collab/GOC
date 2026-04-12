from fastapi import APIRouter, Depends

from goal_ops_console.services import AppServices, get_services

router = APIRouter(prefix="/events", tags=["events"])


@router.get("")
def list_events(
    correlation_id: str | None = None,
    entity_id: str | None = None,
    limit: int = 200,
    services: AppServices = Depends(get_services),
) -> list[dict]:
    return services.event_bus.list_events(
        correlation_id=correlation_id,
        entity_id=entity_id,
        limit=limit,
    )


@router.get("/trace/{goal_id}")
def flow_trace(
    goal_id: str,
    limit: int = 500,
    services: AppServices = Depends(get_services),
) -> dict:
    return services.event_bus.flow_trace(goal_id, limit=limit)
