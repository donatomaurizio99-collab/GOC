from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from goal_ops_console.config import MAX_TOTAL_RETRIES_PER_CYCLE, SPEC_VERSION
from goal_ops_console.services import AppServices, get_services

router = APIRouter(tags=["system"])


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"spec_version": SPEC_VERSION},
    )


@router.get("/system/health")
def system_health(services: AppServices = Depends(get_services)) -> dict:
    total_events = services.db.fetch_scalar("SELECT COUNT(*) FROM events") or 0
    total_goals = services.db.fetch_scalar("SELECT COUNT(*) FROM goals") or 0
    total_tasks = services.db.fetch_scalar("SELECT COUNT(*) FROM task_state") or 0
    return {
        "spec_version": SPEC_VERSION,
        "totals": {
            "events": int(total_events),
            "goals": int(total_goals),
            "tasks": int(total_tasks),
        },
        "retry_budget_per_cycle": MAX_TOTAL_RETRIES_PER_CYCLE,
        "consumer_stats": services.event_bus.consumer_stats(),
        "stuck_events": services.event_bus.stuck_events(),
        "invariant_violations": services.state_manager.find_invariant_violations(),
    }
