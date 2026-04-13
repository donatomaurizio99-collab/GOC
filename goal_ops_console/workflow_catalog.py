from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from time import perf_counter
from typing import TYPE_CHECKING, Any

from goal_ops_console.database import Database, Transaction, new_id, now_utc
from goal_ops_console.event_bus import EventBus
from goal_ops_console.models import ConflictError, NotFoundError
from goal_ops_console.scheduler import SchedulerService

if TYPE_CHECKING:
    from goal_ops_console.observability import ObservabilityService


DEFAULT_WORKFLOW_DEFINITIONS: tuple[dict[str, str], ...] = (
    {
        "workflow_id": "scheduler.age_queue",
        "name": "Age Goal Queue",
        "description": "Increase wait cycles and effective priority for queued goals.",
        "entrypoint": "scheduler.age_queue",
    },
    {
        "workflow_id": "scheduler.pick_next_goal",
        "name": "Pick Next Goal",
        "description": "Activate the highest-priority queued goal via the scheduler.",
        "entrypoint": "scheduler.pick_next_goal",
    },
    {
        "workflow_id": "maintenance.retention_cleanup",
        "name": "Retention Cleanup",
        "description": "Delete old events, processing rows, and failure logs by retention policy.",
        "entrypoint": "maintenance.retention_cleanup",
    },
)


class WorkflowCatalog:
    def __init__(
        self,
        db: Database,
        event_bus: EventBus,
        scheduler: SchedulerService,
        *,
        run_timeout_seconds: int = 300,
        reaper_batch_size: int = 200,
        observability: "ObservabilityService | None" = None,
    ):
        self.db = db
        self.event_bus = event_bus
        self.scheduler = scheduler
        self.run_timeout_seconds = run_timeout_seconds
        self.reaper_batch_size = reaper_batch_size
        self.observability = observability
        self.handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "scheduler.age_queue": self._run_scheduler_age_queue,
            "scheduler.pick_next_goal": self._run_scheduler_pick_next_goal,
            "maintenance.retention_cleanup": self._run_retention_cleanup,
        }
        self._seed_default_workflows()

    def list_workflows(self, *, include_disabled: bool = False) -> list[dict[str, Any]]:
        where = "" if include_disabled else "WHERE wd.is_enabled = 1"
        rows = self.db.fetch_all(
            f"""SELECT wd.workflow_id,
                       wd.name,
                       wd.description,
                       wd.entrypoint,
                       wd.is_enabled,
                       wd.version,
                       wd.created_at,
                       wd.updated_at,
                       COALESCE(run_stats.run_count, 0) AS run_count,
                       run_stats.last_run_at
                FROM workflow_definitions wd
                LEFT JOIN (
                    SELECT workflow_id,
                           COUNT(*) AS run_count,
                           MAX(created_at) AS last_run_at
                    FROM workflow_runs
                    GROUP BY workflow_id
                ) run_stats ON run_stats.workflow_id = wd.workflow_id
                {where}
                ORDER BY wd.name ASC"""
        )
        return [self._definition_to_dict(row) for row in rows]

    def get_workflow(self, workflow_id: str, *, include_disabled: bool = False) -> dict[str, Any]:
        clause = "" if include_disabled else "AND wd.is_enabled = 1"
        row = self.db.fetch_one(
            f"""SELECT wd.workflow_id,
                       wd.name,
                       wd.description,
                       wd.entrypoint,
                       wd.is_enabled,
                       wd.version,
                       wd.created_at,
                       wd.updated_at
                FROM workflow_definitions wd
                WHERE wd.workflow_id = ?
                {clause}""",
            workflow_id,
        )
        if row is None:
            raise NotFoundError(f"Workflow {workflow_id} not found")
        return self._definition_to_dict(row)

    def list_runs(self, *, limit: int = 100, workflow_id: str | None = None) -> list[dict[str, Any]]:
        params: list[Any] = []
        where = ""
        if workflow_id:
            where = "WHERE wr.workflow_id = ?"
            params.append(workflow_id)
        rows = self.db.fetch_all(
            f"""SELECT wr.run_id,
                       wr.workflow_id,
                       wd.name AS workflow_name,
                       wr.status,
                       wr.requested_by,
                       wr.correlation_id,
                       wr.idempotency_key,
                       wr.input_payload,
                       wr.result_payload,
                       wr.started_at,
                       wr.finished_at,
                       wr.created_at,
                       wr.updated_at
                FROM workflow_runs wr
                JOIN workflow_definitions wd ON wd.workflow_id = wr.workflow_id
                {where}
                ORDER BY wr.created_at DESC
                LIMIT ?""",
            *params,
            limit,
        )
        return [self._run_to_dict(row) for row in rows]

    def get_run(self, run_id: str) -> dict[str, Any]:
        row = self.db.fetch_one(
            """SELECT wr.run_id,
                      wr.workflow_id,
                      wd.name AS workflow_name,
                      wr.status,
                      wr.requested_by,
                      wr.correlation_id,
                      wr.idempotency_key,
                      wr.input_payload,
                      wr.result_payload,
                      wr.started_at,
                      wr.finished_at,
                      wr.created_at,
                      wr.updated_at
               FROM workflow_runs wr
               JOIN workflow_definitions wd ON wd.workflow_id = wr.workflow_id
               WHERE wr.run_id = ?""",
            run_id,
        )
        if row is None:
            raise NotFoundError(f"Workflow run {run_id} not found")
        return self._run_to_dict(row)

    def start_workflow(
        self,
        workflow_id: str,
        *,
        requested_by: str = "operator",
        payload: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        definition = self.get_workflow(workflow_id, include_disabled=True)
        if not definition["is_enabled"]:
            raise ConflictError(f"Workflow {workflow_id} is disabled")

        handler = self.handlers.get(str(definition["entrypoint"]))
        if handler is None:
            raise ConflictError(
                f"Workflow {workflow_id} has unknown entrypoint '{definition['entrypoint']}'"
            )

        stale = self.reap_stuck_runs(
            timeout_seconds=self.run_timeout_seconds,
            limit=self.reaper_batch_size,
        )
        self.event_bus.ensure_within_backpressure()

        normalized_idempotency_key = self._normalize_idempotency_key(idempotency_key)
        if normalized_idempotency_key:
            existing = self._find_run_by_idempotency(workflow_id, normalized_idempotency_key)
            if existing is not None:
                replayed = self.get_run(existing["run_id"])
                replayed["idempotency_replay"] = True
                replayed["stale_runs_reaped"] = stale["reaped_count"]
                return replayed

        run_id = new_id()
        correlation_id = f"workflow:{workflow_id}:{run_id[:8]}"
        started_at = now_utc()
        input_payload = payload or {}
        try:
            self._insert_run(
                run_id=run_id,
                workflow_id=workflow_id,
                requested_by=requested_by,
                correlation_id=correlation_id,
                idempotency_key=normalized_idempotency_key,
                input_payload=input_payload,
                started_at=started_at,
                entrypoint=str(definition["entrypoint"]),
            )
        except sqlite3.IntegrityError as error:
            existing = self._find_run_by_idempotency(workflow_id, normalized_idempotency_key)
            if normalized_idempotency_key and existing is not None:
                replayed = self.get_run(existing["run_id"])
                replayed["idempotency_replay"] = True
                replayed["stale_runs_reaped"] = stale["reaped_count"]
                return replayed
            raise ConflictError(f"Workflow run insert failed: {error}") from error

        started_monotonic = perf_counter()
        try:
            result_payload = handler(input_payload)
        except Exception as error:
            failed = self._mark_run_failed(
                run_id=run_id,
                workflow_id=workflow_id,
                requested_by=requested_by,
                correlation_id=correlation_id,
                error=error,
                status="failed",
            )
            failed["idempotency_replay"] = False
            failed["stale_runs_reaped"] = stale["reaped_count"]
            return failed

        duration_ms = int((perf_counter() - started_monotonic) * 1000)
        if duration_ms > self.run_timeout_seconds * 1000:
            timeout_error = TimeoutError(
                (
                    f"Workflow {workflow_id} exceeded timeout "
                    f"({duration_ms}ms > {self.run_timeout_seconds * 1000}ms)"
                )
            )
            timed_out = self._mark_run_failed(
                run_id=run_id,
                workflow_id=workflow_id,
                requested_by=requested_by,
                correlation_id=correlation_id,
                error=timeout_error,
                status="timed_out",
                details={"duration_ms": duration_ms},
            )
            timed_out["idempotency_replay"] = False
            timed_out["stale_runs_reaped"] = stale["reaped_count"]
            return timed_out

        result = self._mark_run_succeeded(
            run_id=run_id,
            workflow_id=workflow_id,
            requested_by=requested_by,
            correlation_id=correlation_id,
            result_payload={**result_payload, "duration_ms": duration_ms},
        )
        result["idempotency_replay"] = False
        result["stale_runs_reaped"] = stale["reaped_count"]
        return result

    def reap_stuck_runs(self, *, timeout_seconds: int, limit: int) -> dict[str, Any]:
        safe_limit = max(1, min(500, int(limit)))
        rows = self.db.fetch_all(
            """SELECT run_id, workflow_id, requested_by, correlation_id, started_at
               FROM workflow_runs
               WHERE status = 'running'
               AND started_at < datetime('now', ? || ' seconds')
               ORDER BY started_at ASC
               LIMIT ?""",
            f"-{timeout_seconds}",
            safe_limit,
        )
        if not rows:
            return {"reaped_count": 0, "run_ids": []}

        run_ids: list[str] = []
        with self.db.transaction() as tx:
            for row in rows:
                run_id = str(row["run_id"])
                finished_at = now_utc()
                updated = tx.execute(
                    """UPDATE workflow_runs
                       SET status = 'timed_out',
                           result_payload = ?,
                           finished_at = ?,
                           updated_at = ?
                       WHERE run_id = ? AND status = 'running'""",
                    self._json_dump(
                        {
                            "error_type": "TimeoutError",
                            "error": (
                                f"Reaper timed out run after {timeout_seconds}s without completion"
                            ),
                            "timeout_seconds": timeout_seconds,
                        }
                    ),
                    finished_at,
                    finished_at,
                    run_id,
                )
                if updated == 0:
                    continue
                run_ids.append(run_id)
                self.event_bus.record_event(
                    "workflow.run.timed_out",
                    run_id,
                    str(row["correlation_id"]),
                    {
                        "workflow_id": row["workflow_id"],
                        "requested_by": row["requested_by"],
                        "timeout_seconds": timeout_seconds,
                        "started_at": row["started_at"],
                    },
                    tx=tx,
                )
                self._record_audit(
                    tx,
                    action="workflow.reaper",
                    actor="system",
                    status="error",
                    workflow_id=str(row["workflow_id"]),
                    correlation_id=str(row["correlation_id"]),
                    details={
                        "run_id": run_id,
                        "timeout_seconds": timeout_seconds,
                    },
                )

            if run_ids:
                self._metric("workflows.runs.timed_out", delta=len(run_ids), tx=tx)
        return {"reaped_count": len(run_ids), "run_ids": run_ids}

    def _insert_run(
        self,
        *,
        run_id: str,
        workflow_id: str,
        requested_by: str,
        correlation_id: str,
        idempotency_key: str | None,
        input_payload: dict[str, Any],
        started_at: str,
        entrypoint: str,
    ) -> None:
        with self.db.transaction() as tx:
            tx.execute(
                """INSERT INTO workflow_runs
                   (run_id, workflow_id, status, requested_by, correlation_id, idempotency_key,
                    input_payload, result_payload, started_at, finished_at, created_at, updated_at)
                   VALUES (?, ?, 'running', ?, ?, ?, ?, NULL, ?, NULL, ?, ?)""",
                run_id,
                workflow_id,
                requested_by,
                correlation_id,
                idempotency_key,
                self._json_dump(input_payload),
                started_at,
                started_at,
                started_at,
            )
            self.event_bus.record_event(
                "workflow.run.started",
                run_id,
                correlation_id,
                {
                    "workflow_id": workflow_id,
                    "requested_by": requested_by,
                    "entrypoint": entrypoint,
                    "idempotency_key": idempotency_key,
                },
                tx=tx,
            )
            self._metric("workflows.runs.started", tx=tx)

    def _find_run_by_idempotency(
        self,
        workflow_id: str,
        idempotency_key: str | None,
    ) -> dict[str, Any] | None:
        if not idempotency_key:
            return None
        return self.db.fetch_one(
            """SELECT run_id
               FROM workflow_runs
               WHERE workflow_id = ? AND idempotency_key = ?""",
            workflow_id,
            idempotency_key,
        )

    def _normalize_idempotency_key(self, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    def _mark_run_succeeded(
        self,
        *,
        run_id: str,
        workflow_id: str,
        requested_by: str,
        correlation_id: str,
        result_payload: dict[str, Any],
    ) -> dict[str, Any]:
        finished_at = now_utc()
        with self.db.transaction() as tx:
            tx.execute(
                """UPDATE workflow_runs
                   SET status = 'succeeded',
                       result_payload = ?,
                       finished_at = ?,
                       updated_at = ?
                   WHERE run_id = ?""",
                self._json_dump(result_payload),
                finished_at,
                finished_at,
                run_id,
            )
            self.event_bus.record_event(
                "workflow.run.succeeded",
                run_id,
                correlation_id,
                {
                    "workflow_id": workflow_id,
                    "requested_by": requested_by,
                    "result": result_payload,
                },
                tx=tx,
            )
            self._metric("workflows.runs.succeeded", tx=tx)
            self._record_audit(
                tx,
                action="workflow.run",
                actor=requested_by,
                status="success",
                workflow_id=workflow_id,
                correlation_id=correlation_id,
                details={"run_id": run_id, "result": result_payload},
            )
        return self.get_run(run_id)

    def _mark_run_failed(
        self,
        *,
        run_id: str,
        workflow_id: str,
        requested_by: str,
        correlation_id: str,
        error: Exception,
        status: str = "failed",
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        finished_at = now_utc()
        error_payload = {
            "error_type": error.__class__.__name__,
            "error": str(error),
            **(details or {}),
        }
        event_type = "workflow.run.timed_out" if status == "timed_out" else "workflow.run.failed"
        with self.db.transaction() as tx:
            tx.execute(
                """UPDATE workflow_runs
                   SET status = ?,
                       result_payload = ?,
                       finished_at = ?,
                       updated_at = ?
                   WHERE run_id = ?""",
                status,
                self._json_dump(error_payload),
                finished_at,
                finished_at,
                run_id,
            )
            self.event_bus.record_event(
                event_type,
                run_id,
                correlation_id,
                {
                    "workflow_id": workflow_id,
                    "requested_by": requested_by,
                    "error_type": error.__class__.__name__,
                    "error": str(error),
                },
                tx=tx,
            )
            metric_name = "workflows.runs.timed_out" if status == "timed_out" else "workflows.runs.failed"
            self._metric(metric_name, tx=tx)
            self._record_audit(
                tx,
                action="workflow.run",
                actor=requested_by,
                status="error",
                workflow_id=workflow_id,
                correlation_id=correlation_id,
                details={"run_id": run_id, "status": status, "error": str(error)},
            )
        return self.get_run(run_id)

    def _run_scheduler_age_queue(self, _: dict[str, Any]) -> dict[str, Any]:
        self.event_bus.ensure_within_backpressure()
        aged = [goal for goal in self.scheduler.age_queue() if goal]
        return {"aged_count": len(aged), "goals": aged}

    def _run_scheduler_pick_next_goal(self, _: dict[str, Any]) -> dict[str, Any]:
        self.event_bus.ensure_within_backpressure()
        return {"picked_goal": self.scheduler.pick_next_goal()}

    def _run_retention_cleanup(self, _: dict[str, Any]) -> dict[str, Any]:
        return self.event_bus.run_retention_cleanup()

    def _seed_default_workflows(self) -> None:
        timestamp = now_utc()
        with self.db.transaction() as tx:
            for definition in DEFAULT_WORKFLOW_DEFINITIONS:
                tx.execute(
                    """INSERT OR IGNORE INTO workflow_definitions
                       (workflow_id, name, description, entrypoint, is_enabled, version, created_at, updated_at)
                       VALUES (?, ?, ?, ?, 1, 1, ?, ?)""",
                    definition["workflow_id"],
                    definition["name"],
                    definition["description"],
                    definition["entrypoint"],
                    timestamp,
                    timestamp,
                )

    def _definition_to_dict(self, row: Any) -> dict[str, Any]:
        data = dict(row)
        data["is_enabled"] = bool(data.get("is_enabled"))
        return data

    def _run_to_dict(self, row: Any) -> dict[str, Any]:
        data = dict(row)
        data["input_payload"] = self._json_load(data.get("input_payload"))
        data["result_payload"] = self._json_load(data.get("result_payload"))
        data["idempotency_key"] = data.get("idempotency_key")
        return data

    def _json_dump(self, payload: dict[str, Any]) -> str:
        return json.dumps(payload, sort_keys=True)

    def _json_load(self, payload: Any) -> dict[str, Any] | None:
        if payload is None:
            return None
        if isinstance(payload, str):
            try:
                loaded = json.loads(payload)
            except json.JSONDecodeError:
                return {"raw": payload}
            if isinstance(loaded, dict):
                return loaded
            return {"value": loaded}
        if isinstance(payload, dict):
            return payload
        return {"value": payload}

    def _record_audit(
        self,
        tx: Transaction,
        *,
        action: str,
        actor: str,
        status: str,
        workflow_id: str,
        correlation_id: str,
        details: dict[str, Any],
    ) -> None:
        if self.observability is None:
            return
        self.observability.record_audit(
            action=action,
            actor=actor,
            status=status,
            entity_type="workflow",
            entity_id=workflow_id,
            correlation_id=correlation_id,
            details=details,
            tx=tx,
        )

    def _metric(self, name: str, delta: int = 1, *, tx: Transaction | None = None) -> None:
        if self.observability is None:
            return
        self.observability.increment_metric(name, delta=delta, tx=tx)