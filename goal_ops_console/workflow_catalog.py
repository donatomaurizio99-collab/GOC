from __future__ import annotations

import json
import sqlite3
import threading
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

TERMINAL_RUN_STATES = {"succeeded", "failed", "timed_out", "cancelled"}


class WorkflowCatalog:
    def __init__(
        self,
        db: Database,
        event_bus: EventBus,
        scheduler: SchedulerService,
        *,
        run_timeout_seconds: int = 300,
        reaper_batch_size: int = 200,
        worker_poll_interval_seconds: float = 0.5,
        startup_recovery_max_age_seconds: int = 0,
        observability: "ObservabilityService | None" = None,
    ):
        self.db = db
        self.event_bus = event_bus
        self.scheduler = scheduler
        self.run_timeout_seconds = max(0, int(run_timeout_seconds))
        self.reaper_batch_size = max(1, int(reaper_batch_size))
        self.worker_poll_interval_seconds = max(0.05, float(worker_poll_interval_seconds))
        self.startup_recovery_max_age_seconds = max(0, int(startup_recovery_max_age_seconds))
        self.observability = observability
        self.handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "scheduler.age_queue": self._run_scheduler_age_queue,
            "scheduler.pick_next_goal": self._run_scheduler_pick_next_goal,
            "maintenance.retention_cleanup": self._run_retention_cleanup,
        }

        self._worker_stop = threading.Event()
        self._worker_wakeup = threading.Event()
        self._worker_lock = threading.Lock()
        self._worker_thread: threading.Thread | None = None
        self._startup_recovery_state: dict[str, Any] = {
            "executed": False,
            "recovered_count": 0,
            "run_ids": [],
            "error": None,
            "at_utc": None,
            "max_age_seconds": self.startup_recovery_max_age_seconds,
        }

        self._seed_default_workflows()

    def start_worker(self) -> None:
        with self._worker_lock:
            if self._worker_thread is not None and self._worker_thread.is_alive():
                return
            startup_state: dict[str, Any] = {
                "executed": True,
                "recovered_count": 0,
                "run_ids": [],
                "error": None,
                "at_utc": now_utc(),
                "max_age_seconds": self.startup_recovery_max_age_seconds,
            }
            try:
                recovered = self.recover_interrupted_runs(
                    max_age_seconds=self.startup_recovery_max_age_seconds,
                    limit=self.reaper_batch_size,
                )
                startup_state["recovered_count"] = int(recovered["recovered_count"])
                startup_state["run_ids"] = list(recovered["run_ids"])
            except Exception as exc:
                startup_state["error"] = str(exc)
                self._metric("workflows.startup_recovery.errors")
            self._startup_recovery_state = startup_state

            self._worker_stop.clear()
            self._worker_wakeup.set()
            self._worker_thread = threading.Thread(
                target=self._worker_loop,
                daemon=True,
                name="goal-ops-workflow-worker",
            )
            self._worker_thread.start()

    def stop_worker(self, timeout_seconds: float = 5.0) -> None:
        with self._worker_lock:
            thread = self._worker_thread
            if thread is None:
                return
            self._worker_stop.set()
            self._worker_wakeup.set()
        thread.join(timeout=timeout_seconds)
        with self._worker_lock:
            if self._worker_thread is thread:
                self._worker_thread = None

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

    def worker_status(self) -> dict[str, Any]:
        with self._worker_lock:
            thread = self._worker_thread
            is_running = bool(thread is not None and thread.is_alive())
        queued_runs = int(
            self.db.fetch_scalar("SELECT COUNT(*) FROM workflow_runs WHERE status = 'queued'") or 0
        )
        running_runs = int(
            self.db.fetch_scalar("SELECT COUNT(*) FROM workflow_runs WHERE status = 'running'") or 0
        )
        return {
            "is_running": is_running,
            "stop_requested": self._worker_stop.is_set(),
            "queued_runs": queued_runs,
            "running_runs": running_runs,
            "startup_recovery": dict(self._startup_recovery_state),
        }

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
            max(1, min(500, int(limit))),
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
        created_at = now_utc()
        input_payload = payload or {}
        try:
            self._insert_queued_run(
                run_id=run_id,
                workflow_id=workflow_id,
                requested_by=requested_by,
                correlation_id=correlation_id,
                idempotency_key=normalized_idempotency_key,
                input_payload=input_payload,
                created_at=created_at,
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

        self._worker_wakeup.set()
        queued = self.get_run(run_id)
        queued["idempotency_replay"] = False
        queued["stale_runs_reaped"] = stale["reaped_count"]
        return queued

    def cancel_run(
        self,
        run_id: str,
        *,
        requested_by: str = "operator",
        reason: str | None = None,
    ) -> dict[str, Any]:
        with self.db.transaction() as tx:
            run = tx.fetch_one(
                "SELECT run_id, workflow_id, status, correlation_id FROM workflow_runs WHERE run_id = ?",
                run_id,
            )
            if run is None:
                raise NotFoundError(f"Workflow run {run_id} not found")

            current_status = str(run["status"])
            if current_status in {"succeeded", "failed", "timed_out"}:
                raise ConflictError(
                    f"Workflow run {run_id} is already terminal with status '{current_status}'"
                )
            if current_status == "cancelled":
                return self.get_run(run_id)

            timestamp = now_utc()
            updated = tx.execute(
                """UPDATE workflow_runs
                   SET status = 'cancelled',
                       finished_at = COALESCE(finished_at, ?),
                       updated_at = ?
                   WHERE run_id = ? AND status IN ('queued', 'running')""",
                timestamp,
                timestamp,
                run_id,
            )
            if updated == 0:
                return self.get_run(run_id)

            self._safe_record_event(
                "workflow.run.cancelled",
                run_id,
                str(run["correlation_id"]),
                {
                    "workflow_id": run["workflow_id"],
                    "requested_by": requested_by,
                    "reason": reason,
                    "from_status": current_status,
                },
                tx=tx,
            )
            self._metric("workflows.runs.cancelled", tx=tx)
            self._record_audit(
                tx,
                action="workflow.cancel",
                actor=requested_by,
                status="success",
                workflow_id=str(run["workflow_id"]),
                correlation_id=str(run["correlation_id"]),
                details={
                    "run_id": run_id,
                    "from_status": current_status,
                    "reason": reason,
                },
            )

        self._worker_wakeup.set()
        return self.get_run(run_id)

    def reap_stuck_runs(self, *, timeout_seconds: int, limit: int) -> dict[str, Any]:
        safe_limit = max(1, min(500, int(limit)))
        rows = self.db.fetch_all(
            """SELECT run_id, workflow_id, requested_by, correlation_id, started_at
               FROM workflow_runs
               WHERE status = 'running'
               AND started_at < datetime('now', ? || ' seconds')
               ORDER BY started_at ASC
               LIMIT ?""",
            f"-{max(0, int(timeout_seconds))}",
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
                self._safe_record_event(
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

    def recover_interrupted_runs(self, *, max_age_seconds: int, limit: int) -> dict[str, Any]:
        safe_limit = max(1, min(500, int(limit)))
        safe_max_age = max(0, int(max_age_seconds))
        rows = self.db.fetch_all(
            """SELECT run_id, workflow_id, requested_by, correlation_id, started_at
               FROM workflow_runs
               WHERE status = 'running'
               AND (
                   started_at IS NULL
                   OR started_at <= datetime('now', ? || ' seconds')
               )
               ORDER BY COALESCE(started_at, created_at) ASC
               LIMIT ?""",
            f"-{safe_max_age}",
            safe_limit,
        )
        if not rows:
            return {"recovered_count": 0, "run_ids": []}

        run_ids: list[str] = []
        with self.db.transaction() as tx:
            for row in rows:
                run_id = str(row["run_id"])
                finished_at = now_utc()
                updated = tx.execute(
                    """UPDATE workflow_runs
                       SET status = 'failed',
                           result_payload = ?,
                           finished_at = ?,
                           updated_at = ?
                       WHERE run_id = ? AND status = 'running'""",
                    self._json_dump(
                        {
                            "error_type": "ProcessAbortRecovery",
                            "error": (
                                "Recovered interrupted workflow run during startup "
                                "after previous process termination."
                            ),
                            "max_age_seconds": safe_max_age,
                        }
                    ),
                    finished_at,
                    finished_at,
                    run_id,
                )
                if updated == 0:
                    continue
                run_ids.append(run_id)
                self._safe_record_event(
                    "workflow.run.recovered_after_abort",
                    run_id,
                    str(row["correlation_id"]),
                    {
                        "workflow_id": row["workflow_id"],
                        "requested_by": row["requested_by"],
                        "started_at": row["started_at"],
                        "max_age_seconds": safe_max_age,
                    },
                    tx=tx,
                )
                self._record_audit(
                    tx,
                    action="workflow.startup_recovery",
                    actor="system",
                    status="error",
                    workflow_id=str(row["workflow_id"]),
                    correlation_id=str(row["correlation_id"]),
                    details={
                        "run_id": run_id,
                        "max_age_seconds": safe_max_age,
                        "started_at": row["started_at"],
                    },
                )

            if run_ids:
                self._metric("workflows.runs.recovered_after_abort", delta=len(run_ids), tx=tx)
        return {"recovered_count": len(run_ids), "run_ids": run_ids}

    def _worker_loop(self) -> None:
        while not self._worker_stop.is_set():
            try:
                self.reap_stuck_runs(
                    timeout_seconds=self.run_timeout_seconds,
                    limit=self.reaper_batch_size,
                )
            except Exception:
                self._metric("workflows.worker.errors")

            processed_any = False
            while not self._worker_stop.is_set():
                try:
                    claimed = self._claim_next_queued_run()
                except sqlite3.OperationalError:
                    # SQLite lock contention is transient in concurrent test/desktop scenarios.
                    # Keep worker alive and retry on the next wake cycle.
                    self._metric("workflows.worker.lock_conflicts")
                    break
                except Exception:
                    self._metric("workflows.worker.errors")
                    break
                if claimed is None:
                    break
                processed_any = True
                self._execute_claimed_run(claimed)

            if processed_any:
                continue

            self._worker_wakeup.wait(self.worker_poll_interval_seconds)
            self._worker_wakeup.clear()

    def _claim_next_queued_run(self) -> dict[str, Any] | None:
        with self.db.transaction() as tx:
            row = tx.fetch_one(
                """SELECT run_id
                   FROM workflow_runs
                   WHERE status = 'queued'
                   ORDER BY created_at ASC
                   LIMIT 1"""
            )
            if row is None:
                return None

            run_id = str(row["run_id"])
            started_at = now_utc()
            updated = tx.execute(
                """UPDATE workflow_runs
                   SET status = 'running', started_at = ?, updated_at = ?
                   WHERE run_id = ? AND status = 'queued'""",
                started_at,
                started_at,
                run_id,
            )
            if updated == 0:
                return None

            claimed = tx.fetch_one(
                """SELECT run_id, workflow_id, requested_by, correlation_id, input_payload
                   FROM workflow_runs WHERE run_id = ?""",
                run_id,
            )
            if claimed is None:
                return None

            self._safe_record_event(
                "workflow.run.started",
                run_id,
                str(claimed["correlation_id"]),
                {
                    "workflow_id": claimed["workflow_id"],
                    "requested_by": claimed["requested_by"],
                },
                tx=tx,
            )
            self._metric("workflows.runs.started", tx=tx)
            return dict(claimed)

    def _execute_claimed_run(self, claimed_run: dict[str, Any]) -> None:
        run_id = str(claimed_run["run_id"])
        workflow_id = str(claimed_run["workflow_id"])
        requested_by = str(claimed_run["requested_by"])
        correlation_id = str(claimed_run["correlation_id"])

        try:
            definition = self.get_workflow(workflow_id, include_disabled=True)
            handler = self.handlers.get(str(definition["entrypoint"]))
            if handler is None:
                self._mark_run_failed(
                    run_id=run_id,
                    workflow_id=workflow_id,
                    requested_by=requested_by,
                    correlation_id=correlation_id,
                    error=ConflictError(
                        f"Workflow {workflow_id} has unknown entrypoint '{definition['entrypoint']}'"
                    ),
                    status="failed",
                    expected_status="running",
                )
                return

            payload = self._json_load(claimed_run.get("input_payload")) or {}
            started_monotonic = perf_counter()
            try:
                result_payload = handler(payload)
            except Exception as error:
                self._mark_run_failed(
                    run_id=run_id,
                    workflow_id=workflow_id,
                    requested_by=requested_by,
                    correlation_id=correlation_id,
                    error=error,
                    status="failed",
                    expected_status="running",
                )
                return

            duration_ms = int((perf_counter() - started_monotonic) * 1000)
            if duration_ms > self.run_timeout_seconds * 1000:
                self._mark_run_failed(
                    run_id=run_id,
                    workflow_id=workflow_id,
                    requested_by=requested_by,
                    correlation_id=correlation_id,
                    error=TimeoutError(
                        (
                            f"Workflow {workflow_id} exceeded timeout "
                            f"({duration_ms}ms > {self.run_timeout_seconds * 1000}ms)"
                        )
                    ),
                    status="timed_out",
                    expected_status="running",
                    details={"duration_ms": duration_ms},
                )
                return

            self._mark_run_succeeded(
                run_id=run_id,
                workflow_id=workflow_id,
                requested_by=requested_by,
                correlation_id=correlation_id,
                result_payload={**result_payload, "duration_ms": duration_ms},
                expected_status="running",
            )
        except Exception:
            self._metric("workflows.worker.errors")

    def _insert_queued_run(
        self,
        *,
        run_id: str,
        workflow_id: str,
        requested_by: str,
        correlation_id: str,
        idempotency_key: str | None,
        input_payload: dict[str, Any],
        created_at: str,
        entrypoint: str,
    ) -> None:
        with self.db.transaction() as tx:
            tx.execute(
                """INSERT INTO workflow_runs
                   (run_id, workflow_id, status, requested_by, correlation_id, idempotency_key,
                    input_payload, result_payload, started_at, finished_at, created_at, updated_at)
                   VALUES (?, ?, 'queued', ?, ?, ?, ?, NULL, ?, NULL, ?, ?)""",
                run_id,
                workflow_id,
                requested_by,
                correlation_id,
                idempotency_key,
                self._json_dump(input_payload),
                created_at,
                created_at,
                created_at,
            )
            self._safe_record_event(
                "workflow.run.queued",
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
            self._metric("workflows.runs.queued", tx=tx)
            self._record_audit(
                tx,
                action="workflow.enqueue",
                actor=requested_by,
                status="success",
                workflow_id=workflow_id,
                correlation_id=correlation_id,
                details={
                    "run_id": run_id,
                    "idempotency_key": idempotency_key,
                },
            )

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
        expected_status: str,
    ) -> dict[str, Any]:
        finished_at = now_utc()
        applied = False
        with self.db.transaction() as tx:
            rows = tx.execute(
                """UPDATE workflow_runs
                   SET status = 'succeeded',
                       result_payload = ?,
                       finished_at = ?,
                       updated_at = ?
                   WHERE run_id = ? AND status = ?""",
                self._json_dump(result_payload),
                finished_at,
                finished_at,
                run_id,
                expected_status,
            )
            if rows > 0:
                applied = True
                self._safe_record_event(
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

        run = self.get_run(run_id)
        if not applied and run["status"] not in TERMINAL_RUN_STATES:
            raise ConflictError(
                f"Workflow run {run_id} could not transition from {expected_status} to succeeded"
            )
        return run

    def _mark_run_failed(
        self,
        *,
        run_id: str,
        workflow_id: str,
        requested_by: str,
        correlation_id: str,
        error: Exception,
        status: str,
        expected_status: str,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        finished_at = now_utc()
        error_payload = {
            "error_type": error.__class__.__name__,
            "error": str(error),
            **(details or {}),
        }
        event_type = "workflow.run.timed_out" if status == "timed_out" else "workflow.run.failed"
        metric_name = "workflows.runs.timed_out" if status == "timed_out" else "workflows.runs.failed"

        applied = False
        with self.db.transaction() as tx:
            rows = tx.execute(
                """UPDATE workflow_runs
                   SET status = ?,
                       result_payload = ?,
                       finished_at = ?,
                       updated_at = ?
                   WHERE run_id = ? AND status = ?""",
                status,
                self._json_dump(error_payload),
                finished_at,
                finished_at,
                run_id,
                expected_status,
            )
            if rows > 0:
                applied = True
                self._safe_record_event(
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

        run = self.get_run(run_id)
        if not applied and run["status"] not in TERMINAL_RUN_STATES:
            raise ConflictError(
                f"Workflow run {run_id} could not transition from {expected_status} to {status}"
            )
        return run

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

    def _safe_record_event(
        self,
        event_type: str,
        entity_id: str,
        correlation_id: str,
        payload: dict[str, Any],
        *,
        tx: Transaction | None = None,
    ) -> None:
        try:
            self.event_bus.record_event(
                event_type,
                entity_id,
                correlation_id,
                payload,
                tx=tx,
            )
        except Exception:
            self._metric("workflows.events.dropped", tx=tx)

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
