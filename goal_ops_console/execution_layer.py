from typing import Any

from goal_ops_console.config import RETRY_STRATEGY
from goal_ops_console.database import Database, Transaction, new_id, now_utc
from goal_ops_console.event_bus import EventBus
from goal_ops_console.failure_intelligence import FailureIntelligence, compute_error_hash
from goal_ops_console.models import ConflictError, NotFoundError
from goal_ops_console.state_manager import StateManager


class ExecutionLayer:
    def __init__(
        self,
        db: Database,
        state_manager: StateManager,
        event_bus: EventBus,
        failure_intelligence: FailureIntelligence,
    ):
        self.db = db
        self.state_manager = state_manager
        self.event_bus = event_bus
        self.failure_intelligence = failure_intelligence

    def create_task(self, *, goal_id: str, title: str) -> dict[str, Any]:
        goal = self.state_manager.get_goal(goal_id)
        if goal["state"] in {"archived", "cancelled"}:
            raise ConflictError(f"Goal {goal_id} cannot accept new tasks in state {goal['state']}")

        task_id = new_id()
        timestamp = now_utc()
        correlation_id = f"{goal_id}:{task_id}:0"
        with self.db.transaction() as tx:
            tx.execute(
                "INSERT INTO tasks (task_id, goal_id, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                task_id,
                goal_id,
                title,
                timestamp,
                timestamp,
            )
            tx.execute(
                "INSERT INTO task_state "
                "(task_id, goal_id, correlation_id, status, retry_count, version, created_at, updated_at) "
                "VALUES (?, ?, ?, 'pending', 0, 1, ?, ?)",
                task_id,
                goal_id,
                correlation_id,
                timestamp,
                timestamp,
            )
            self.event_bus.record_event(
                "task.created",
                task_id,
                correlation_id,
                {"goal_id": goal_id, "title": title},
                tx=tx,
            )
        return self.get_task(task_id)

    def list_tasks(self, goal_id: str | None = None) -> list[dict[str, Any]]:
        params: list[Any] = []
        where = ""
        if goal_id:
            where = "WHERE ts.goal_id = ?"
            params.append(goal_id)
        rows = self.db.fetch_all(
            f"""SELECT ts.task_id,
                       ts.goal_id,
                       t.title,
                       ts.correlation_id,
                       ts.status,
                       ts.retry_count,
                       ts.failure_type,
                       ts.error_hash,
                       ts.version,
                       ts.created_at,
                       ts.updated_at
                FROM task_state ts
                JOIN tasks t ON t.task_id = ts.task_id
                {where}
                ORDER BY ts.created_at ASC""",
            *params,
        )
        return [dict(row) for row in rows]

    def get_task(self, task_id: str) -> dict[str, Any]:
        row = self.db.fetch_one(
            """SELECT ts.task_id,
                      ts.goal_id,
                      t.title,
                      ts.correlation_id,
                      ts.status,
                      ts.retry_count,
                      ts.failure_type,
                      ts.error_hash,
                      ts.version,
                      ts.created_at,
                      ts.updated_at
               FROM task_state ts
               JOIN tasks t ON t.task_id = ts.task_id
               WHERE ts.task_id = ?""",
            task_id,
        )
        if row is None:
            raise NotFoundError(f"Task {task_id} not found")
        return dict(row)

    def simulate_success(self, task_id: str) -> dict[str, Any]:
        with self.db.transaction() as tx:
            task = self._get_task_state(tx, task_id)
            correlation_id = f"{task['goal_id']}:{task_id}:{task['retry_count']}"
            if task["status"] == "pending":
                self.state_manager.transition_task(
                    task_id,
                    to_state="running",
                    owner="execution_layer",
                    event_type="task.started",
                    correlation_id=correlation_id,
                    extra_fields={"correlation_id": correlation_id},
                    payload={"to_state": "running"},
                    tx=tx,
                )
            elif task["status"] == "failed":
                self.state_manager.transition_task(
                    task_id,
                    to_state="running",
                    owner="execution_layer",
                    event_type="task.retried",
                    correlation_id=correlation_id,
                    extra_fields={"correlation_id": correlation_id},
                    payload={"to_state": "running"},
                    tx=tx,
                )
            elif task["status"] not in {"running"}:
                raise ConflictError(f"Task {task_id} cannot succeed from state {task['status']}")

            self.state_manager.transition_task(
                task_id,
                to_state="succeeded",
                owner="execution_layer",
                event_type="task.succeeded",
                correlation_id=correlation_id,
                payload={"to_state": "succeeded"},
                tx=tx,
            )
        return self.get_task(task_id)

    def simulate_failure(
        self,
        task_id: str,
        *,
        failure_type: str,
        error_message: str,
    ) -> dict[str, Any]:
        with self.db.transaction() as tx:
            task = self._get_task_state(tx, task_id)
            goal = tx.fetch_one("SELECT * FROM goals WHERE goal_id = ?", task["goal_id"])
            if goal is None:
                raise NotFoundError(f"Goal {task['goal_id']} not found")
            attempt = task["retry_count"]
            correlation_id = f"{task['goal_id']}:{task_id}:{attempt}"

            if task["status"] == "pending":
                self.state_manager.transition_task(
                    task_id,
                    to_state="running",
                    owner="execution_layer",
                    event_type="task.started",
                    correlation_id=correlation_id,
                    extra_fields={"correlation_id": correlation_id},
                    payload={"to_state": "running", "attempt": attempt},
                    tx=tx,
                )
            elif task["status"] == "failed":
                self.state_manager.transition_task(
                    task_id,
                    to_state="running",
                    owner="execution_layer",
                    event_type="task.retried",
                    correlation_id=correlation_id,
                    extra_fields={"correlation_id": correlation_id},
                    payload={"to_state": "running", "attempt": attempt},
                    tx=tx,
                )
            elif task["status"] not in {"running"}:
                raise ConflictError(f"Task {task_id} cannot fail from state {task['status']}")

            error_hash = compute_error_hash(failure_type, error_message)
            next_retry_count = attempt + 1
            self.failure_intelligence.log_failure(
                tx,
                task_id=task_id,
                goal_id=task["goal_id"],
                correlation_id=correlation_id,
                failure_type=failure_type,
                fingerprint=error_hash,
                retry_count=next_retry_count,
                error_message=error_message,
            )

            max_retries = RETRY_STRATEGY[failure_type]["max"]
            should_escalate = self.failure_intelligence.should_escalate(
                failure_type=failure_type,
                error_hash=error_hash,
                retry_count=next_retry_count,
                max_retries=max_retries,
                tx=tx,
            )

            # Terminal outcomes still pass through `failed` first so the event
            # stream preserves the semantic attempt failure before escalation.
            self.state_manager.transition_task(
                task_id,
                to_state="failed",
                owner="execution_layer",
                event_type="task.failed",
                correlation_id=correlation_id,
                extra_fields={
                    "retry_count": next_retry_count,
                    "failure_type": failure_type,
                    "error_hash": error_hash,
                    "correlation_id": (
                        correlation_id
                        if should_escalate or next_retry_count >= max_retries
                        else f"{task['goal_id']}:{task_id}:{next_retry_count}"
                    ),
                },
                payload={
                    "failure_type": failure_type,
                    "error_hash": error_hash,
                    "error_message": error_message,
                    "attempt": attempt,
                },
                tx=tx,
            )

            final_state = "failed"
            if should_escalate:
                final_state = "poison"
                self.state_manager.transition_task(
                    task_id,
                    to_state="poison",
                    owner="execution_layer",
                    event_type="task.poison.detected",
                    correlation_id=correlation_id,
                    extra_fields={"correlation_id": correlation_id},
                    payload={
                        "failure_type": failure_type,
                        "error_hash": error_hash,
                        "task_id": task_id,
                    },
                    tx=tx,
                )
            elif next_retry_count >= max_retries:
                final_state = "exhausted"
                self.state_manager.transition_task(
                    task_id,
                    to_state="exhausted",
                    owner="execution_layer",
                    event_type="task.exhausted",
                    correlation_id=correlation_id,
                    extra_fields={"correlation_id": correlation_id},
                    payload={
                        "failure_type": failure_type,
                        "error_hash": error_hash,
                        "task_id": task_id,
                    },
                    tx=tx,
                )

            if final_state == "poison" and goal["state"] == "active":
                self.state_manager.transition_goal(
                    task["goal_id"],
                    to_state="escalation_pending",
                    owner="state_manager",
                    event_type="goal.escalation_pending",
                    correlation_id=correlation_id,
                    reason=f"{failure_type}:{error_hash}",
                    payload={
                        "failure_type": failure_type,
                        "error_hash": error_hash,
                        "task_id": task_id,
                    },
                    tx=tx,
                )
            elif final_state == "exhausted" and goal["state"] == "active":
                self.state_manager.transition_goal(
                    task["goal_id"],
                    to_state="blocked",
                    owner="state_manager",
                    event_type="goal.blocked",
                    correlation_id=correlation_id,
                    reason=f"{failure_type}:{error_hash}",
                    payload={
                        "failure_type": failure_type,
                        "error_hash": error_hash,
                        "task_id": task_id,
                    },
                    tx=tx,
                )

        return self.get_task(task_id)

    def _get_task_state(self, tx: Transaction, task_id: str) -> dict[str, Any]:
        row = tx.fetch_one("SELECT * FROM task_state WHERE task_id = ?", task_id)
        if row is None:
            raise NotFoundError(f"Task {task_id} not found")
        return dict(row)
