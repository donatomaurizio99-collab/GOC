from contextlib import asynccontextmanager
from pathlib import Path
from time import perf_counter

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from goal_ops_console.config import Settings
from goal_ops_console.models import DomainError
from goal_ops_console.routers import events, goals, system, tasks, workflows
from goal_ops_console.services import build_services


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.services.workflow_catalog.start_worker()
        try:
            yield
        finally:
            app.state.services.workflow_catalog.stop_worker()

    app = FastAPI(title="Goal Ops Console", version="0.1.0", lifespan=lifespan)
    app.state.services = build_services(settings)

    template_dir = Path(__file__).parent / "templates"
    static_dir = Path(__file__).parent / "static"
    app.state.templates = Jinja2Templates(directory=str(template_dir))
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    app.include_router(system.router)
    app.include_router(goals.router)
    app.include_router(tasks.router)
    app.include_router(events.router)
    app.include_router(workflows.router)

    def route_template(request: Request) -> str:
        route = request.scope.get("route")
        if route is not None and hasattr(route, "path"):
            return str(route.path)
        return request.url.path

    @app.middleware("http")
    async def observability_middleware(request: Request, call_next):
        status_code = 500
        started = perf_counter()
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            services = request.app.state.services
            route = route_template(request)
            method = request.method.upper()
            duration_ms = int((perf_counter() - started) * 1000)
            route_metric = (
                route.strip("/")
                .replace("/", ".")
                .replace("{", "")
                .replace("}", "")
                or "root"
            )
            services.observability.increment_metric("http.requests.total")
            services.observability.increment_metric(f"http.requests.method.{method}")
            services.observability.increment_metric(f"http.requests.status.{status_code}")
            services.observability.increment_metric(f"http.requests.route.{route_metric}")

            if method in {"POST", "PUT", "PATCH", "DELETE"} and not route.startswith("/static"):
                services.observability.record_audit(
                    action="http.mutation",
                    actor="api",
                    status="success" if status_code < 400 else "error",
                    entity_type="route",
                    entity_id=route,
                    correlation_id=request.headers.get("x-correlation-id"),
                    details={
                        "method": method,
                        "status_code": status_code,
                        "duration_ms": duration_ms,
                    },
                )

    @app.exception_handler(DomainError)
    async def handle_domain_error(request: Request, exc: DomainError) -> JSONResponse:
        request.app.state.services.observability.increment_metric(
            f"errors.domain.{exc.__class__.__name__}"
        )
        content = {"detail": exc.message}
        headers: dict[str, str] = {}
        retry_after = getattr(exc, "retry_after_seconds", None)
        if isinstance(retry_after, int) and retry_after > 0:
            content["retry_after_seconds"] = retry_after
            headers["Retry-After"] = str(retry_after)
        return JSONResponse(status_code=exc.status_code, content=content, headers=headers)

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, _: Exception) -> JSONResponse:
        request.app.state.services.observability.increment_metric("errors.unhandled")
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    return app


app = create_app()
