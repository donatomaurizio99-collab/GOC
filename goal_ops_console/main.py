from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from goal_ops_console.config import Settings
from goal_ops_console.models import DomainError
from goal_ops_console.routers import events, goals, system, tasks
from goal_ops_console.services import build_services


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="Goal Ops Console", version="0.1.0")
    app.state.services = build_services(settings)

    template_dir = Path(__file__).parent / "templates"
    static_dir = Path(__file__).parent / "static"
    app.state.templates = Jinja2Templates(directory=str(template_dir))
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    app.include_router(system.router)
    app.include_router(goals.router)
    app.include_router(tasks.router)
    app.include_router(events.router)

    @app.exception_handler(DomainError)
    async def handle_domain_error(_: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})

    return app


app = create_app()
