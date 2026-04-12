from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from goal_ops_console.config import CONSUMER_BATCH_SIZE, MAX_TOTAL_RETRIES_PER_CYCLE, SPEC_VERSION
from goal_ops_console.models import BackpressureError, FaultBulkResolveRequest, FaultRemediationRequest
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
    backpressure = services.event_bus.backpressure_snapshot()
    faults = services.failure_intelligence.fault_summary(limit=5, dead_letter_only=True)
    faults["systemic_external_failures_last_window"] = (
        services.failure_intelligence.systemic_external_failure_count()
    )
    return {
        "spec_version": SPEC_VERSION,
        "default_consumer_id": services.settings.consumer_id,
        "totals": {
            "events": int(total_events),
            "goals": int(total_goals),
            "tasks": int(total_tasks),
        },
        "backpressure": backpressure,
        "retention": {
            "events_days": services.settings.events_retention_days,
            "event_processing_days": services.settings.event_processing_retention_days,
            "failure_log_days": services.settings.failure_log_retention_days,
        },
        "metrics": services.observability.metrics_summary(),
        "audit": {
            "entries_last_24h": services.observability.recent_audit_count(hours=24),
        },
        "faults": faults,
        "retry_budget_per_cycle": MAX_TOTAL_RETRIES_PER_CYCLE,
        "consumer_stats": services.event_bus.consumer_stats(),
        "stuck_events": services.event_bus.stuck_events(),
        "invariant_violations": services.state_manager.find_invariant_violations(),
    }


@router.get("/system/queue")
def queue_snapshot(services: AppServices = Depends(get_services)) -> list[dict]:
    rows = services.db.fetch_all(
        """SELECT g.goal_id,
                  g.title,
                  g.state,
                  q.status AS queue_status,
                  q.base_priority,
                  q.priority,
                  q.wait_cycles,
                  q.force_promoted,
                  q.created_at,
                  q.updated_at
           FROM goal_queue q
           JOIN goals g ON g.goal_id = q.goal_id
           ORDER BY q.priority DESC, q.created_at ASC"""
    )
    return [dict(row) for row in rows]


@router.post("/system/scheduler/age")
def age_scheduler_queue(services: AppServices = Depends(get_services)) -> dict:
    services.event_bus.ensure_within_backpressure()
    aged = [goal for goal in services.scheduler.age_queue() if goal]
    return {"aged_count": len(aged), "goals": aged}


@router.post("/system/scheduler/pick")
def pick_next_goal(services: AppServices = Depends(get_services)) -> dict:
    services.event_bus.ensure_within_backpressure()
    picked = services.scheduler.pick_next_goal()
    return {"picked_goal": picked}


@router.post("/system/consumers/{consumer_id}/drain")
def drain_consumer(
    consumer_id: str,
    batch_size: int = CONSUMER_BATCH_SIZE,
    services: AppServices = Depends(get_services),
) -> dict:
    if batch_size > services.settings.max_consumer_drain_batch_size:
        raise BackpressureError(
            (
                f"Requested batch_size {batch_size} exceeds safe limit "
                f"{services.settings.max_consumer_drain_batch_size}."
            ),
            retry_after_seconds=services.settings.backpressure_retry_after_seconds,
        )
    handled: list[str] = []
    processed = services.event_bus.consume_batch(
        consumer_id,
        lambda event: handled.append(event["event_id"]),
        batch_size=batch_size,
    )
    return {
        "consumer_id": consumer_id,
        "batch_size": batch_size,
        "processed_count": processed,
        "processed_event_ids": handled,
    }


@router.post("/system/consumers/{consumer_id}/reclaim")
def reclaim_consumer(consumer_id: str, services: AppServices = Depends(get_services)) -> dict:
    reclaimed = services.event_bus.reclaim_stuck_processing(consumer_id)
    return {"consumer_id": consumer_id, "reclaimed_count": reclaimed}


@router.get("/system/backpressure")
def backpressure_status(services: AppServices = Depends(get_services)) -> dict:
    return services.event_bus.backpressure_snapshot()


@router.get("/system/metrics")
def metrics_status(
    prefix: str | None = None,
    limit: int = 200,
    services: AppServices = Depends(get_services),
) -> dict:
    return {
        "metrics": services.observability.list_metrics(prefix=prefix, limit=limit),
        "summary": services.observability.metrics_summary(),
    }


@router.get("/system/audit")
def audit_log(
    limit: int = 200,
    action: str | None = None,
    status: str | None = None,
    services: AppServices = Depends(get_services),
) -> dict:
    return {
        "entries": services.observability.list_audit(
            limit=limit,
            action=action,
            status=status,
        )
    }


@router.get("/system/faults")
def fault_explorer(
    limit: int = 200,
    failure_type: str | None = None,
    failure_status: str | None = None,
    task_status: str | None = None,
    goal_id: str | None = None,
    error_hash: str | None = None,
    dead_letter_only: bool = False,
    services: AppServices = Depends(get_services),
) -> dict:
    return {
        "entries": services.failure_intelligence.list_faults(
            limit=limit,
            failure_type=failure_type,
            failure_status=failure_status,
            task_status=task_status,
            goal_id=goal_id,
            error_hash=error_hash,
            dead_letter_only=dead_letter_only,
        )
    }


@router.get("/system/faults/summary")
def fault_summary(
    limit: int = 20,
    failure_type: str | None = None,
    failure_status: str | None = None,
    task_status: str | None = None,
    goal_id: str | None = None,
    error_hash: str | None = None,
    dead_letter_only: bool = False,
    services: AppServices = Depends(get_services),
) -> dict:
    summary = services.failure_intelligence.fault_summary(
        limit=limit,
        failure_type=failure_type,
        failure_status=failure_status,
        task_status=task_status,
        goal_id=goal_id,
        error_hash=error_hash,
        dead_letter_only=dead_letter_only,
    )
    summary["systemic_external_failures_last_window"] = (
        services.failure_intelligence.systemic_external_failure_count()
    )
    return summary


@router.post("/system/faults/resolve_bulk")
def resolve_faults_bulk(
    request: FaultBulkResolveRequest,
    services: AppServices = Depends(get_services),
) -> dict:
    return services.execution_layer.resolve_faults_bulk(
        reason=request.reason,
        dry_run=request.dry_run,
        failure_type=request.failure_type,
        failure_status=request.failure_status,
        task_status=request.task_status,
        goal_id=request.goal_id,
        error_hash=request.error_hash,
        dead_letter_only=request.dead_letter_only,
        limit=request.limit,
    )


@router.post("/system/faults/{failure_id}/retry")
def retry_fault(
    failure_id: str,
    request: FaultRemediationRequest,
    services: AppServices = Depends(get_services),
) -> dict:
    return services.execution_layer.retry_fault(
        failure_id=failure_id,
        reason=request.reason,
        dry_run=request.dry_run,
    )


@router.post("/system/faults/{failure_id}/requeue_goal")
def requeue_fault_goal(
    failure_id: str,
    request: FaultRemediationRequest,
    services: AppServices = Depends(get_services),
) -> dict:
    return services.execution_layer.requeue_goal_from_fault(
        failure_id=failure_id,
        reason=request.reason,
        dry_run=request.dry_run,
    )


@router.post("/system/faults/{failure_id}/resolve")
def resolve_fault(
    failure_id: str,
    request: FaultRemediationRequest,
    services: AppServices = Depends(get_services),
) -> dict:
    return services.execution_layer.resolve_fault(
        failure_id=failure_id,
        reason=request.reason,
        dry_run=request.dry_run,
    )


@router.post("/system/maintenance/retention")
def run_retention_cleanup(services: AppServices = Depends(get_services)) -> dict:
    deleted = services.event_bus.run_retention_cleanup()
    return {
        **deleted,
        "retention_days": {
            "events": services.settings.events_retention_days,
            "event_processing": services.settings.event_processing_retention_days,
            "failure_log": services.settings.failure_log_retention_days,
        },
    }
