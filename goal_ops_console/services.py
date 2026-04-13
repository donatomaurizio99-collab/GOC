from dataclasses import dataclass

from fastapi import Request

from goal_ops_console.config import Settings
from goal_ops_console.database import Database
from goal_ops_console.event_bus import EventBus
from goal_ops_console.execution_layer import ExecutionLayer
from goal_ops_console.failure_intelligence import FailureIntelligence
from goal_ops_console.observability import ObservabilityService
from goal_ops_console.scheduler import SchedulerService
from goal_ops_console.state_manager import StateManager
from goal_ops_console.stubs import PermissionManager, Planner, QdrantClientStub
from goal_ops_console.workflow_catalog import WorkflowCatalog


@dataclass(slots=True)
class AppServices:
    settings: Settings
    db: Database
    observability: ObservabilityService
    event_bus: EventBus
    state_manager: StateManager
    execution_layer: ExecutionLayer
    failure_intelligence: FailureIntelligence
    scheduler: SchedulerService
    workflow_catalog: WorkflowCatalog
    qdrant: QdrantClientStub
    planner: Planner
    permission_manager: PermissionManager


def build_services(settings: Settings | None = None) -> AppServices:
    app_settings = settings or Settings()
    db = Database(
        app_settings.database_url,
        migration_backup_dir=app_settings.db_migration_backup_dir,
    )
    db.initialize()
    observability = ObservabilityService(db)
    event_bus = EventBus(
        db,
        default_consumer_id=app_settings.consumer_id,
        max_pending_events=app_settings.max_pending_events,
        backpressure_retry_after_seconds=app_settings.backpressure_retry_after_seconds,
        events_retention_days=app_settings.events_retention_days,
        event_processing_retention_days=app_settings.event_processing_retention_days,
        failure_log_retention_days=app_settings.failure_log_retention_days,
        observability=observability,
    )
    state_manager = StateManager(
        db,
        event_bus,
        observability=observability,
        max_goal_queue_entries=app_settings.max_goal_queue_entries,
        backpressure_retry_after_seconds=app_settings.backpressure_retry_after_seconds,
    )
    failure_intelligence = FailureIntelligence(db)
    execution_layer = ExecutionLayer(
        db,
        state_manager,
        event_bus,
        failure_intelligence,
        observability=observability,
    )
    scheduler = SchedulerService(db, state_manager)
    workflow_catalog = WorkflowCatalog(
        db,
        event_bus,
        scheduler,
        run_timeout_seconds=app_settings.workflow_run_timeout_seconds,
        reaper_batch_size=app_settings.workflow_reaper_batch_size,
        worker_poll_interval_seconds=app_settings.workflow_worker_poll_interval_seconds,
        startup_recovery_max_age_seconds=app_settings.workflow_startup_recovery_max_age_seconds,
        observability=observability,
    )
    return AppServices(
        settings=app_settings,
        db=db,
        observability=observability,
        event_bus=event_bus,
        state_manager=state_manager,
        execution_layer=execution_layer,
        failure_intelligence=failure_intelligence,
        scheduler=scheduler,
        workflow_catalog=workflow_catalog,
        qdrant=QdrantClientStub(),
        planner=Planner(),
        permission_manager=PermissionManager(),
    )


def get_services(request: Request) -> AppServices:
    return request.app.state.services
