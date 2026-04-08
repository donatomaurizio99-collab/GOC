from dataclasses import dataclass

from fastapi import Request

from goal_ops_console.config import Settings
from goal_ops_console.database import Database
from goal_ops_console.event_bus import EventBus
from goal_ops_console.execution_layer import ExecutionLayer
from goal_ops_console.failure_intelligence import FailureIntelligence
from goal_ops_console.scheduler import SchedulerService
from goal_ops_console.state_manager import StateManager
from goal_ops_console.stubs import PermissionManager, Planner, QdrantClientStub


@dataclass(slots=True)
class AppServices:
    settings: Settings
    db: Database
    event_bus: EventBus
    state_manager: StateManager
    execution_layer: ExecutionLayer
    failure_intelligence: FailureIntelligence
    scheduler: SchedulerService
    qdrant: QdrantClientStub
    planner: Planner
    permission_manager: PermissionManager


def build_services(settings: Settings | None = None) -> AppServices:
    app_settings = settings or Settings()
    db = Database(app_settings.database_url)
    db.initialize()
    event_bus = EventBus(db)
    state_manager = StateManager(db, event_bus)
    failure_intelligence = FailureIntelligence(db)
    execution_layer = ExecutionLayer(db, state_manager, event_bus, failure_intelligence)
    scheduler = SchedulerService(db, state_manager)
    return AppServices(
        settings=app_settings,
        db=db,
        event_bus=event_bus,
        state_manager=state_manager,
        execution_layer=execution_layer,
        failure_intelligence=failure_intelligence,
        scheduler=scheduler,
        qdrant=QdrantClientStub(),
        planner=Planner(),
        permission_manager=PermissionManager(),
    )


def get_services(request: Request) -> AppServices:
    return request.app.state.services
