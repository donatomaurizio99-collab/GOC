from typing import TYPE_CHECKING
from typing import Any

from goal_ops_console.config import RETRY_STRATEGY
from goal_ops_console.database import Database, Transaction, new_id, now_utc
from goal_ops_console.event_bus import EventBus
from goal_ops_console.failure_intelligence import FailureIntelligence, compute_error_hash
from goal_ops_console.models import ConflictError, NotFoundError
from goal_ops_console.state_manager import StateManager

if TYPE_CHECKING:
    from goal_ops_console.observability import ObservabilityService


class ExecutionLayer:
    def __init__(
        self,
        db: Database,
        state_manager: StateManager,
        event_bus: EventBus,
        failure_intelligence: FailureIntelligence,
        *,
        observability: "ObservabilityService | None" = None,
    ):
        self.db = db
        self.state_manager = state_manager
        self.event_bus = event_bus
        self.failure_intelligence = failure_intelligence
        self.observability = observability

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
            self._metric("tasks.created", tx=tx)
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
            self._metric("tasks.succeeded", tx=tx)
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
            self._metric(f"tasks.failed.{failure_type}", tx=tx)

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
                self._metric("tasks.poison", tx=tx)
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
                self._metric("tasks.exhausted", tx=tx)

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

    def retry_fault(
        self,
        *,
        failure_id: str,
        reason: str,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        context = self.failure_intelligence.get_fault_by_id(failure_id)
        if context is None:
            raise NotFoundError(f"Failure {failure_id} not found")

        plan = self._retry_plan(context)
        if dry_run:
            return {
                "dry_run": True,
                "failure_id": failure_id,
                **plan,
            }
        if not plan["allowed"]:
            raise ConflictError("; ".join(plan["blockers"]))

        with self.db.transaction() as tx:
            current = self.failure_intelligence.get_fault_by_id(failure_id, tx=tx)
            if current is None:
                raise NotFoundError(f"Failure {failure_id} not found")

            plan = self._retry_plan(current)
            if not plan["allowed"]:
                raise ConflictError("; ".join(plan["blockers"]))

            correlation_id = self._remediation_correlation_id(current)
            if plan["will_requeue_goal"]:
                self.state_manager.transition_goal(
                    current["goal_id"],
                    to_state="active",
                    owner="state_manager",
                    event_type="goal.remediation_requeued",
                    correlation_id=correlation_id,
                    reason=reason,
                    payload={
                        "failure_id": failure_id,
                        "failure_type": current["failure_type"],
                        "error_hash": current["error_hash"],
                        "reason": reason,
                    },
                    tx=tx,
                )
                self._metric("faults.remediation.goal_requeued", tx=tx)

            retry_task_id = new_id()
            retry_correlation_id = f"{current['goal_id']}:{retry_task_id}:0"
            retry_title = current["task_title"] or f"Retry for {current['task_id']}"
            timestamp = now_utc()

            tx.execute(
                "INSERT INTO tasks (task_id, goal_id, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                retry_task_id,
                current["goal_id"],
                retry_title,
                timestamp,
                timestamp,
            )
            tx.execute(
                "INSERT INTO task_state "
                "(task_id, goal_id, correlation_id, status, retry_count, version, created_at, updated_at) "
                "VALUES (?, ?, ?, 'pending', 0, 1, ?, ?)",
                retry_task_id,
                current["goal_id"],
                retry_correlation_id,
                timestamp,
                timestamp,
            )
            self.event_bus.record_event(
                "task.remediation_retry_queued",
                retry_task_id,
                retry_correlation_id,
                {
                    "failure_id": failure_id,
                    "source_task_id": current["task_id"],
                    "source_error_hash": current["error_hash"],
                    "reason": reason,
                },
                tx=tx,
            )
            self.failure_intelligence.update_failure_status(
                tx,
                failure_id=failure_id,
                expected_version=int(current["failure_version"]),
                status="retry_queued",
            )
            self._metric("faults.remediation.retry", tx=tx)
            self._metric("tasks.created", tx=tx)
            if self.observability is not None:
                self.observability.record_audit(
                    action="fault.remediation.retry",
                    actor="supervisor",
                    status="success",
                    entity_type="failure",
                    entity_id=failure_id,
                    correlation_id=retry_correlation_id,
                    details={
                        "goal_id": current["goal_id"],
                        "source_task_id": current["task_id"],
                        "retry_task_id": retry_task_id,
                        "goal_requeued": plan["will_requeue_goal"],
                        "reason": reason,
                    },
                    tx=tx,
                )

        return {
            "dry_run": False,
            "failure_id": failure_id,
            "goal_id": context["goal_id"],
            "source_task_id": context["task_id"],
            "retry_task": self.get_task(retry_task_id),
            "goal_requeued": plan["will_requeue_goal"],
        }

    def requeue_goal_from_fault(
        self,
        *,
        failure_id: str,
        reason: str,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        context = self.failure_intelligence.get_fault_by_id(failure_id)
        if context is None:
            raise NotFoundError(f"Failure {failure_id} not found")

        plan = self._requeue_goal_plan(context)
        if dry_run:
            return {
                "dry_run": True,
                "failure_id": failure_id,
                **plan,
            }
        if not plan["allowed"]:
            raise ConflictError("; ".join(plan["blockers"]))

        with self.db.transaction() as tx:
            current = self.failure_intelligence.get_fault_by_id(failure_id, tx=tx)
            if current is None:
                raise NotFoundError(f"Failure {failure_id} not found")
            plan = self._requeue_goal_plan(current)
            if not plan["allowed"]:
                raise ConflictError("; ".join(plan["blockers"]))

            correlation_id = self._remediation_correlation_id(current)
            goal = self.state_manager.transition_goal(
                current["goal_id"],
                to_state="active",
                owner="state_manager",
                event_type="goal.remediation_requeued",
                correlation_id=correlation_id,
                reason=reason,
                payload={
                    "failure_id": failure_id,
                    "failure_type": current["failure_type"],
                    "error_hash": current["error_hash"],
                    "reason": reason,
                },
                tx=tx,
            )
            self.failure_intelligence.update_failure_status(
                tx,
                failure_id=failure_id,
                expected_version=int(current["failure_version"]),
                status="goal_requeued",
            )
            self._metric("faults.remediation.goal_requeued", tx=tx)
            if self.observability is not None:
                self.observability.record_audit(
                    action="fault.remediation.requeue_goal",
                    actor="supervisor",
                    status="success",
                    entity_type="failure",
                    entity_id=failure_id,
                    correlation_id=correlation_id,
                    details={
                        "goal_id": current["goal_id"],
                        "source_task_id": current["task_id"],
                        "reason": reason,
                    },
                    tx=tx,
                )

        return {
            "dry_run": False,
            "failure_id": failure_id,
            "goal": goal,
        }

    def _retry_plan(self, fault: dict[str, Any]) -> dict[str, Any]:
        blockers: list[str] = []
        task_status = fault.get("task_status")
        goal_state = fault.get("goal_state")
        failure_status = fault.get("failure_status")

        if task_status not in {"failed", "exhausted", "poison"}:
            blockers.append(
                f"Task {fault.get('task_id')} has status '{task_status}' and cannot be retried."
            )
        if goal_state not in {"active", "blocked", "escalation_pending"}:
            blockers.append(
                f"Goal {fault.get('goal_id')} is '{goal_state}' and cannot receive remediated retries."
            )
        if failure_status == "retry_queued":
            blockers.append(f"Failure {fault.get('failure_id')} already has a queued retry task.")

        return {
            "allowed": len(blockers) == 0,
            "blockers": blockers,
            "will_requeue_goal": goal_state in {"blocked", "escalation_pending"},
            "create_retry_task": True,
            "task_status": task_status,
            "goal_state": goal_state,
            "failure_status": failure_status,
        }

    def _requeue_goal_plan(self, fault: dict[str, Any]) -> dict[str, Any]:
        blockers: list[str] = []
        goal_state = fault.get("goal_state")
        if goal_state not in {"blocked", "escalation_pending"}:
            blockers.append(
                f"Goal {fault.get('goal_id')} is '{goal_state}' and cannot be requeued to active."
            )
        return {
            "allowed": len(blockers) == 0,
            "blockers": blockers,
            "goal_state": goal_state,
            "will_requeue_goal": True,
        }

    def _remediation_correlation_id(self, fault: dict[str, Any]) -> str:
        goal_id = fault.get("goal_id")
        task_id = fault.get("task_id")
        retry_count = fault.get("retry_count")
        correlation_id = fault.get("correlation_id") or fault.get("task_correlation_id")
        if isinstance(correlation_id, str) and correlation_id.startswith(f"{goal_id}:"):
            return correlation_id
        if goal_id and task_id is not None and retry_count is not None:
            return f"{goal_id}:{task_id}:{retry_count}"
        return str(goal_id or "")

    def _get_task_state(self, tx: Transaction, task_id: str) -> dict[str, Any]:
        row = tx.fetch_one("SELECT * FROM task_state WHERE task_id = ?", task_id)
        if row is None:
            raise NotFoundError(f"Task {task_id} not found")
        return dict(row)

    def _metric(self, name: str, delta: int = 1, *, tx: Transaction | None = None) -> None:
        if self.observability is None:
            return
        self.observability.increment_metric(name, delta=delta, tx=tx)
