import hashlib
import json
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from time import perf_counter

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from goal_ops_console.config import Settings
from goal_ops_console.database import now_utc
from goal_ops_console.models import DomainError
from goal_ops_console.routers import events, goals, system, tasks, workflows
from goal_ops_console.services import build_services


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.services.workflow_catalog.start_worker()
        app.state.services.invariant_monitor.start()
        try:
            yield
        finally:
            app.state.services.invariant_monitor.stop()
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

    def allow_idempotency_storage(status_code: int) -> bool:
        return 200 <= int(status_code) < 500

    @app.middleware("http")
    async def stability_idempotency_middleware(request: Request, call_next):
        services = request.app.state.services
        method = request.method.upper()
        path = request.url.path

        if services.runtime_guard.should_block_mutation(method=method, path=path):
            safe_mode = services.runtime_guard.safe_mode_snapshot()
            return JSONResponse(
                status_code=503,
                content={
                    "detail": "Runtime safe mode active: mutating operations are temporarily blocked.",
                    "safe_mode": safe_mode,
                },
            )

        if method not in {"POST", "PUT", "PATCH", "DELETE"} or path.startswith("/static"):
            return await call_next(request)
        if path.startswith("/workflows/") and path.endswith("/start"):
            # Workflow start has richer domain-level idempotency metadata that we keep intact.
            return await call_next(request)

        idempotency_key_raw = request.headers.get("Idempotency-Key")
        if idempotency_key_raw is None:
            return await call_next(request)

        idempotency_key = idempotency_key_raw.strip()
        if not idempotency_key:
            return await call_next(request)
        if len(idempotency_key) > 120:
            return JSONResponse(
                status_code=400,
                content={"detail": "Idempotency-Key must be 120 characters or fewer."},
            )

        request_body = await request.body()
        request_hash = hashlib.sha256(request_body).hexdigest()

        existing = services.db.fetch_one(
            """SELECT request_hash, response_status, response_body
               FROM idempotency_keys
               WHERE idempotency_key = ? AND method = ? AND path = ?""",
            idempotency_key,
            method,
            path,
        )
        if existing is not None:
            if str(existing["request_hash"]) != request_hash:
                return JSONResponse(
                    status_code=409,
                    content={
                        "detail": (
                            "Idempotency-Key was already used for a different payload on this endpoint."
                        )
                    },
                )
            try:
                replay_payload = json.loads(str(existing["response_body"]))
            except json.JSONDecodeError:
                return JSONResponse(
                    status_code=500,
                    content={"detail": "Stored idempotency response payload is invalid."},
                )
            replay = JSONResponse(
                status_code=int(existing["response_status"]),
                content=replay_payload,
            )
            replay.headers["X-Idempotency-Replay"] = "true"
            return replay

        consumed = False

        async def receive() -> dict[str, object]:
            nonlocal consumed
            if consumed:
                return {"type": "http.request", "body": b"", "more_body": False}
            consumed = True
            return {"type": "http.request", "body": request_body, "more_body": False}

        replay_request = Request(request.scope, receive)
        response = await call_next(replay_request)

        content_type = str(response.headers.get("content-type") or "").lower()
        if "application/json" not in content_type or not allow_idempotency_storage(response.status_code):
            return response

        body_bytes = getattr(response, "body", None)
        if body_bytes is None:
            chunks = [chunk async for chunk in response.body_iterator]
            body_bytes = b"".join(chunks)
            response = Response(
                content=body_bytes,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type=response.media_type,
            )

        try:
            payload = json.loads(body_bytes.decode("utf-8"))
        except json.JSONDecodeError:
            return response

        timestamp = now_utc()
        try:
            services.db.execute(
                """INSERT INTO idempotency_keys
                   (idempotency_key, method, path, request_hash, response_status,
                    response_body, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                idempotency_key,
                method,
                path,
                request_hash,
                int(response.status_code),
                json.dumps(payload, ensure_ascii=True, sort_keys=True),
                timestamp,
                timestamp,
            )
        except sqlite3.IntegrityError:
            # Race-safe behavior: another request stored the same key in parallel.
            pass
        return response

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
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        services = request.app.state.services
        services.observability.increment_metric("errors.unhandled")
        if isinstance(exc, sqlite3.OperationalError):
            services.runtime_guard.record_database_error(
                message=str(exc),
                source="unhandled_exception",
            )
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    return app


app = create_app()
