import json
import platform
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from goal_ops_console.config import CONSUMER_BATCH_SIZE, MAX_TOTAL_RETRIES_PER_CYCLE, SPEC_VERSION
from goal_ops_console.models import (
    BackpressureError,
    FaultBulkResolveRequest,
    FaultRemediationRequest,
    SafeModeToggleRequest,
)
from goal_ops_console.services import AppServices, get_services

router = APIRouter(tags=["system"])


def _utc_iso() -> str:
    return datetime.now(UTC).isoformat()


def _build_health_payload(services: AppServices) -> dict[str, Any]:
    total_events = services.db.fetch_scalar("SELECT COUNT(*) FROM events") or 0
    total_goals = services.db.fetch_scalar("SELECT COUNT(*) FROM goals") or 0
    total_tasks = services.db.fetch_scalar("SELECT COUNT(*) FROM task_state") or 0
    backpressure = services.event_bus.backpressure_snapshot()
    faults = services.failure_intelligence.fault_summary(limit=5, dead_letter_only=True)
    faults["systemic_external_failures_last_window"] = (
        services.failure_intelligence.systemic_external_failure_count()
    )
    audit_integrity = services.observability.audit_integrity_status(verify_limit=200)
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
            "audit_log_days": services.settings.audit_log_retention_days,
            "idempotency_keys_days": services.settings.idempotency_retention_days,
        },
        "metrics": services.observability.metrics_summary(),
        "audit": {
            "entries_last_24h": services.observability.recent_audit_count(hours=24),
            "integrity": audit_integrity,
        },
        "faults": faults,
        "retry_budget_per_cycle": MAX_TOTAL_RETRIES_PER_CYCLE,
        "consumer_stats": services.event_bus.consumer_stats(),
        "stuck_events": services.event_bus.stuck_events(),
        "invariant_violations": services.state_manager.find_invariant_violations(),
        "invariant_monitor": services.invariant_monitor.status(),
        "safe_mode": services.runtime_guard.safe_mode_snapshot(),
        "database_startup_recovery": services.db.startup_recovery_status(),
        "security": {
            "operator_auth_required": bool(services.settings.operator_auth_required),
            "operator_auth_token_configured": bool(
                str(services.settings.operator_auth_token or "").strip()
            ),
            "operator_auth_token_min_length": int(
                services.settings.operator_auth_token_min_length
            ),
        },
    }


def _build_readiness_payload(services: AppServices) -> dict[str, Any]:
    db_ok = True
    db_error: str | None = None
    db_startup_recovery = services.db.startup_recovery_status()
    try:
        services.db.fetch_scalar("SELECT 1")
    except Exception as exc:
        db_ok = False
        db_error = str(exc)

    worker_error: str | None = None
    worker_status: dict[str, Any] = {
        "is_running": False,
        "stop_requested": False,
        "queued_runs": 0,
        "running_runs": 0,
        "startup_recovery": {
            "executed": False,
            "recovered_count": 0,
            "run_ids": [],
            "error": None,
            "at_utc": None,
            "max_age_seconds": 0,
        },
    }
    try:
        worker_status = services.workflow_catalog.worker_status()
    except Exception as exc:
        worker_error = str(exc)

    startup_recovery = worker_status.get("startup_recovery")
    startup_recovery_error: str | None = None
    if isinstance(startup_recovery, dict):
        raw_error = startup_recovery.get("error")
        if raw_error is not None:
            startup_recovery_error = str(raw_error)
    startup_recovery_ok = startup_recovery_error is None

    worker_ok = (
        bool(worker_status.get("is_running"))
        and worker_error is None
        and startup_recovery_ok
    )

    safe_mode_error: str | None = None
    safe_mode: dict[str, Any] = {
        "active": False,
        "reason": None,
        "source": None,
        "auto": False,
        "activated_at_utc": None,
        "error_counters": {
            "lock_errors_in_window": 0,
            "lock_error_window_seconds": 0,
            "io_errors_in_window": 0,
            "io_error_window_seconds": 0,
        },
        "thresholds": {
            "lock_error_threshold": 0,
            "io_error_threshold": 0,
            "auto_disable_after_seconds": 0,
        },
    }
    try:
        safe_mode = services.runtime_guard.safe_mode_snapshot()
    except Exception as exc:
        safe_mode_error = str(exc)
    safe_mode_ok = safe_mode_error is None and not bool(safe_mode.get("active"))

    audit_integrity_error: str | None = None
    audit_integrity: dict[str, Any] = {
        "ok": False,
        "metrics": {
            "total_audit_entries": 0,
            "chain_entries": 0,
            "missing_integrity_rows": 0,
            "coverage_percent": 100.0,
            "sampled_rows": 0,
            "hash_mismatch_count": 0,
            "previous_link_mismatch_count": 0,
            "chain_gap_count": 0,
            "verify_limit": 500,
        },
        "violations": [],
    }
    try:
        audit_integrity = services.observability.audit_integrity_status(verify_limit=500)
    except Exception as exc:
        audit_integrity_error = str(exc)
    audit_integrity_ok = audit_integrity_error is None and bool(audit_integrity.get("ok"))

    invariant_monitor_error: str | None = None
    invariant_monitor: dict[str, Any] = {
        "is_running": False,
        "scan_interval_seconds": int(services.settings.invariant_monitor_interval_seconds),
        "auto_safe_mode": bool(services.settings.invariant_monitor_auto_safe_mode),
        "last_scan_at_utc": None,
        "last_error": None,
        "violation_count": 0,
        "violations": [],
    }
    try:
        invariant_monitor = services.invariant_monitor.status()
    except Exception as exc:
        invariant_monitor_error = str(exc)
    invariant_monitor_ok = (
        invariant_monitor_error is None
        and bool(invariant_monitor.get("is_running"))
        and int(invariant_monitor.get("violation_count") or 0) == 0
        and not invariant_monitor.get("last_error")
    )

    operator_token = str(services.settings.operator_auth_token or "").strip()
    operator_token_min_length = max(1, int(services.settings.operator_auth_token_min_length))
    operator_auth_required = bool(services.settings.operator_auth_required)
    operator_auth_ok = (not operator_auth_required) or (len(operator_token) >= operator_token_min_length)

    return {
        "ready": bool(
            db_ok
            and worker_ok
            and safe_mode_ok
            and audit_integrity_ok
            and invariant_monitor_ok
            and operator_auth_ok
        ),
        "spec_version": SPEC_VERSION,
        "timestamp_utc": _utc_iso(),
        "checks": {
            "database": {
                "ok": db_ok,
                "error": db_error,
                "startup_recovery": db_startup_recovery,
            },
            "workflow_worker": {
                **worker_status,
                "ok": worker_ok,
                "error": worker_error,
                "startup_recovery_ok": startup_recovery_ok,
                "startup_recovery_error": startup_recovery_error,
            },
            "safe_mode": {
                **safe_mode,
                "ok": safe_mode_ok,
                "error": safe_mode_error,
            },
            "audit_integrity": {
                **audit_integrity,
                "ok": audit_integrity_ok,
                "error": audit_integrity_error,
            },
            "invariant_monitor": {
                **invariant_monitor,
                "ok": invariant_monitor_ok,
                "error": invariant_monitor_error,
            },
            "operator_auth": {
                "ok": operator_auth_ok,
                "required": operator_auth_required,
                "token_configured": bool(operator_token),
                "token_length": len(operator_token),
                "token_min_length": operator_token_min_length,
            },
        },
    }


def _metric_counter_value(services: AppServices, metric_name: str) -> int:
    value = services.db.fetch_scalar(
        "SELECT value FROM metrics_counters WHERE metric_name = ?",
        metric_name,
    )
    return int(value or 0)


def _status_from_alerts(alerts: list[dict[str, Any]]) -> str:
    if any(alert["severity"] == "critical" for alert in alerts):
        return "critical"
    if any(alert["severity"] == "warning" for alert in alerts):
        return "degraded"
    return "ok"


def _build_slo_payload(services: AppServices) -> dict[str, Any]:
    readiness = _build_readiness_payload(services)
    database_check = readiness["checks"].get("database", {})
    db_startup_recovery = database_check.get("startup_recovery", {})
    safe_mode = readiness["checks"].get("safe_mode", {})
    invariant_monitor = readiness["checks"].get("invariant_monitor", {})
    db_integrity = services.db.integrity_check(mode="quick")
    backpressure = services.event_bus.backpressure_snapshot()
    stuck_events_count = len(services.event_bus.stuck_events())

    http_total = _metric_counter_value(services, "http.requests.total")
    http_429 = _metric_counter_value(services, "http.requests.status.429")

    status_rows = services.observability.list_metrics(prefix="http.requests.status.", limit=1000)
    http_5xx = 0
    for item in status_rows:
        metric_name = str(item.get("metric_name") or "")
        suffix = metric_name.rsplit(".", 1)[-1]
        if not suffix.isdigit():
            continue
        status_code = int(suffix)
        if 500 <= status_code <= 599:
            http_5xx += int(item.get("value") or 0)

    events_processed = _metric_counter_value(services, "events.processed")
    events_failed = _metric_counter_value(services, "events.failed")
    event_attempts = events_processed + events_failed

    max_pending_events = max(1, int(backpressure.get("max_pending_events") or 1))
    pending_events = int(backpressure.get("pending_events") or 0)
    backlog_utilization_percent = (pending_events / max_pending_events) * 100.0

    http_success_rate_percent: float | None = None
    http_429_rate_percent: float | None = None
    if http_total > 0:
        http_success_rate_percent = ((http_total - http_5xx) / http_total) * 100.0
        http_429_rate_percent = (http_429 / http_total) * 100.0

    event_failure_rate_percent: float | None = None
    if event_attempts > 0:
        event_failure_rate_percent = (events_failed / event_attempts) * 100.0

    thresholds = {
        "min_http_request_sample": int(services.settings.slo_min_http_request_sample),
        "min_http_success_rate_percent": float(services.settings.slo_min_http_success_rate_percent),
        "max_http_429_rate_percent": float(services.settings.slo_max_http_429_rate_percent),
        "min_event_attempt_sample": int(services.settings.slo_min_event_attempt_sample),
        "max_event_failure_rate_percent": float(services.settings.slo_max_event_failure_rate_percent),
        "max_backlog_utilization_percent": float(services.settings.slo_max_backlog_utilization_percent),
        "max_stuck_events": int(services.settings.slo_max_stuck_events),
    }

    alerts: list[dict[str, Any]] = []

    def add_alert(
        code: str,
        severity: Literal["warning", "critical"],
        message: str,
        *,
        observed: Any = None,
        threshold: Any = None,
    ) -> None:
        alerts.append(
            {
                "code": code,
                "severity": severity,
                "message": message,
                "observed": observed,
                "threshold": threshold,
            }
        )

    if not readiness["ready"]:
        add_alert(
            "readiness.not_ready",
            "critical",
            "System readiness is false.",
            observed=readiness,
            threshold=True,
        )

    if not bool(db_integrity.get("ok")):
        add_alert(
            "database.integrity.failed",
            "critical",
            "Database quick integrity check failed.",
            observed=db_integrity.get("result"),
            threshold="ok",
        )

    if bool(db_startup_recovery.get("triggered")):
        add_alert(
            "database.startup_recovery.triggered",
            "critical",
            "Startup corruption recovery quarantined the database and requires operator validation.",
            observed=db_startup_recovery,
            threshold=False,
        )

    if bool(safe_mode.get("active")):
        add_alert(
            "runtime.safe_mode_active",
            "critical",
            "Runtime safe mode is active and mutating API operations are restricted.",
            observed=safe_mode,
            threshold=False,
        )

    invariant_violation_count = int(invariant_monitor.get("violation_count") or 0)
    if invariant_violation_count > 0:
        add_alert(
            "invariants.violations_detected",
            "critical",
            "Invariant monitor detected queue/state consistency violations.",
            observed=invariant_violation_count,
            threshold=0,
        )

    if backlog_utilization_percent >= thresholds["max_backlog_utilization_percent"]:
        severity: Literal["warning", "critical"] = (
            "critical" if bool(backpressure.get("is_throttled")) else "warning"
        )
        add_alert(
            "backpressure.utilization_high",
            severity,
            "Event backlog utilization exceeds configured threshold.",
            observed=round(backlog_utilization_percent, 3),
            threshold=thresholds["max_backlog_utilization_percent"],
        )

    if stuck_events_count > thresholds["max_stuck_events"]:
        add_alert(
            "events.stuck_processing",
            "warning",
            "Stuck processing events exceed configured threshold.",
            observed=stuck_events_count,
            threshold=thresholds["max_stuck_events"],
        )

    if http_total >= thresholds["min_http_request_sample"]:
        if (
            http_success_rate_percent is not None
            and http_success_rate_percent < thresholds["min_http_success_rate_percent"]
        ):
            add_alert(
                "http.success_rate_low",
                "critical",
                "HTTP success rate is below SLO.",
                observed=round(http_success_rate_percent, 3),
                threshold=thresholds["min_http_success_rate_percent"],
            )
        if (
            http_429_rate_percent is not None
            and http_429_rate_percent > thresholds["max_http_429_rate_percent"]
        ):
            add_alert(
                "http.429_rate_high",
                "warning",
                "HTTP 429 rate exceeds configured threshold.",
                observed=round(http_429_rate_percent, 3),
                threshold=thresholds["max_http_429_rate_percent"],
            )

    if event_attempts >= thresholds["min_event_attempt_sample"]:
        if (
            event_failure_rate_percent is not None
            and event_failure_rate_percent > thresholds["max_event_failure_rate_percent"]
        ):
            add_alert(
                "events.failure_rate_high",
                "warning",
                "Event processing failure rate exceeds configured threshold.",
                observed=round(event_failure_rate_percent, 3),
                threshold=thresholds["max_event_failure_rate_percent"],
            )

    status = _status_from_alerts(alerts)
    indicators = {
        "http_total_requests": http_total,
        "http_5xx_requests": http_5xx,
        "http_429_requests": http_429,
        "http_success_rate_percent": http_success_rate_percent,
        "http_429_rate_percent": http_429_rate_percent,
        "event_attempts": event_attempts,
        "event_failed": events_failed,
        "event_failure_rate_percent": event_failure_rate_percent,
        "backlog_utilization_percent": backlog_utilization_percent,
        "stuck_events_count": stuck_events_count,
        "database_startup_recovery_triggered": bool(db_startup_recovery.get("triggered")),
        "safe_mode_active": bool(safe_mode.get("active")),
        "invariant_violation_count": invariant_violation_count,
    }
    return {
        "timestamp_utc": _utc_iso(),
        "spec_version": SPEC_VERSION,
        "status": status,
        "alert_count": len(alerts),
        "alerts": alerts,
        "thresholds": thresholds,
        "indicators": indicators,
        "checks": {
            "readiness": readiness,
            "database_integrity": db_integrity,
            "backpressure": backpressure,
            "safe_mode": safe_mode,
            "invariant_monitor": invariant_monitor,
        },
    }


def _resolve_diagnostics_dir(configured_path: str) -> Path:
    normalized = configured_path.strip()
    if normalized:
        return Path(normalized).expanduser()
    return Path.home() / ".goal_ops_console" / "diagnostics"


def _queue_snapshot(services: AppServices, *, limit: int = 200) -> list[dict[str, Any]]:
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
           ORDER BY q.priority DESC, q.created_at ASC
           LIMIT ?""",
        max(1, min(500, int(limit))),
    )
    return [dict(row) for row in rows]


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
    return _build_health_payload(services)


@router.get("/system/readiness")
def system_readiness(services: AppServices = Depends(get_services)) -> dict:
    return _build_readiness_payload(services)


@router.get("/system/slo")
def system_slo(services: AppServices = Depends(get_services)) -> dict:
    return _build_slo_payload(services)


@router.get("/system/database/integrity")
def database_integrity(
    mode: Literal["quick", "full"] = "quick",
    services: AppServices = Depends(get_services),
) -> dict:
    check = services.db.integrity_check(mode=mode)
    return {
        "timestamp_utc": _utc_iso(),
        "integrity": check,
        "file": services.db.database_file_info(),
        "startup_recovery": services.db.startup_recovery_status(),
        "migrations": services.db.migration_status(),
    }


@router.post("/system/diagnostics")
def export_system_diagnostics(services: AppServices = Depends(get_services)) -> dict:
    diagnostics_dir = _resolve_diagnostics_dir(services.settings.diagnostics_dir)
    diagnostics_dir.mkdir(parents=True, exist_ok=True)

    generated_at = _utc_iso()
    snapshot = {
        "generated_at_utc": generated_at,
        "spec_version": SPEC_VERSION,
        "runtime": {
            "python_version": platform.python_version(),
            "platform": platform.platform(),
        },
        "database": {
            "original_url": services.db.original_url,
            "normalized_url": services.db.database_url,
            "file": services.db.database_file_info(),
            "integrity": services.db.integrity_check(mode="quick"),
            "startup_recovery": services.db.startup_recovery_status(),
            "migrations": services.db.migration_status(),
        },
        "readiness": _build_readiness_payload(services),
        "slo": _build_slo_payload(services),
        "health": _build_health_payload(services),
        "queue": _queue_snapshot(services, limit=200),
        "recent_workflow_runs": services.workflow_catalog.list_runs(limit=50),
        "recent_faults": services.failure_intelligence.list_faults(limit=50, dead_letter_only=False),
        "recent_audit_entries": services.observability.list_audit(limit=50),
        "consumer_stats": services.event_bus.consumer_stats(),
        "backpressure": services.event_bus.backpressure_snapshot(),
    }

    file_name = f"system-diagnostics-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    target_file = diagnostics_dir / file_name
    target_file.write_text(
        json.dumps(snapshot, ensure_ascii=True, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return {
        "file_path": str(target_file),
        "generated_at_utc": generated_at,
        "ready": bool(snapshot["readiness"]["ready"]),
    }


@router.get("/system/queue")
def queue_snapshot(services: AppServices = Depends(get_services)) -> list[dict]:
    return _queue_snapshot(services)


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


@router.get("/system/safe-mode")
def safe_mode_status(services: AppServices = Depends(get_services)) -> dict:
    return services.runtime_guard.safe_mode_snapshot()


@router.post("/system/safe-mode/enable")
def enable_safe_mode(
    request: SafeModeToggleRequest,
    services: AppServices = Depends(get_services),
) -> dict:
    return services.runtime_guard.activate_safe_mode(
        reason=request.reason,
        source="operator",
        auto=False,
    )


@router.post("/system/safe-mode/disable")
def disable_safe_mode(
    request: SafeModeToggleRequest,
    services: AppServices = Depends(get_services),
) -> dict:
    return services.runtime_guard.deactivate_safe_mode(
        reason=request.reason,
        source="operator",
    )


@router.get("/system/invariants")
def invariants_status(services: AppServices = Depends(get_services)) -> dict:
    monitor = services.invariant_monitor.status()
    return {
        "timestamp_utc": _utc_iso(),
        "monitor": monitor,
        "violations": services.state_manager.find_invariant_violations(),
    }


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


@router.get("/system/audit/integrity")
def audit_integrity(
    verify_limit: int = 500,
    services: AppServices = Depends(get_services),
) -> dict:
    return {
        "timestamp_utc": _utc_iso(),
        **services.observability.audit_integrity_status(verify_limit=verify_limit),
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
            "audit_log": services.settings.audit_log_retention_days,
            "idempotency_keys": services.settings.idempotency_retention_days,
        },
    }
