from typing import TYPE_CHECKING
from typing import Any

from goal_ops_console.config import BACKPRESSURE_RETRY_AFTER_SECONDS, MAX_GOAL_QUEUE_ENTRIES, SPEC_VERSION
from goal_ops_console.database import Database, Transaction, new_id, now_utc
from goal_ops_console.event_bus import EventBus
from goal_ops_console.models import BackpressureError, ConflictError, NotFoundError, OptimisticLockError
from goal_ops_console.scheduler import base_priority
from goal_ops_console.transition_rules import queue_status_for_goal_state, validate_transition

if TYPE_CHECKING:
    from goal_ops_console.observability import ObservabilityService


class StateManager:
    def __init__(
        self,
        db: Database,
        event_bus: EventBus,
        *,
        observability: "ObservabilityService | None" = None,
        max_goal_queue_entries: int = MAX_GOAL_QUEUE_ENTRIES,
        backpressure_retry_after_seconds: int = BACKPRESSURE_RETRY_AFTER_SECONDS,
    ):
        self.db = db
        self.event_bus = event_bus
        self.observability = observability
        self.max_goal_queue_entries = max_goal_queue_entries
        self.backpressure_retry_after_seconds = backpressure_retry_after_seconds

    def create_goal(
        self,
        *,
        title: str,
        description: str | None,
        urgency: float,
        value: float,
        deadline_score: float,
    ) -> dict[str, Any]:
        queue_size = int(self.db.fetch_scalar("SELECT COUNT(*) FROM goal_queue") or 0)
        if queue_size >= self.max_goal_queue_entries:
            raise BackpressureError(
                (
                    f"Goal queue limit reached ({queue_size}/{self.max_goal_queue_entries}). "
                    "Archive or complete existing goals and retry."
                ),
                retry_after_seconds=self.backpressure_retry_after_seconds,
            )
        goal_id = new_id()
        timestamp = now_utc()
        priority = base_priority(urgency, value, deadline_score)
        with self.db.transaction() as tx:
            tx.execute(
                "INSERT INTO goals "
                "(goal_id, title, description, state, version, created_at, updated_at) "
                "VALUES (?, ?, ?, 'draft', 1, ?, ?)",
                goal_id,
                title,
                description,
                timestamp,
                timestamp,
            )
            tx.execute(
                "INSERT INTO goal_queue "
                "(goal_id, urgency, value, deadline_score, base_priority, priority, wait_cycles, "
                " force_promoted, status, version, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 0, 0, 'queued', 1, ?, ?)",
                goal_id,
                urgency,
                value,
                deadline_score,
                priority,
                priority,
                timestamp,
                timestamp,
            )
            self.event_bus.record_event(
                "goal.created",
                goal_id,
                goal_id,
                {
                    "spec_version": SPEC_VERSION,
                    "input_snapshot": {
                        "title": title,
                        "urgency": urgency,
                        "value": value,
                        "deadline_score": deadline_score,
                    },
                },
                tx=tx,
            )
            self._metric("goals.created", tx=tx)
        return self.get_goal(goal_id)

    def list_goals(self) -> list[dict[str, Any]]:
        rows = self.db.fetch_all(
            """SELECT g.goal_id,
                      g.title,
                      g.description,
                      g.state,
                      g.blocked_reason,
                      g.escalation_reason,
                      g.version,
                      g.created_at,
                      g.updated_at,
                      q.urgency,
                      q.value,
                      q.deadline_score,
                      q.base_priority,
                      q.priority,
                      q.wait_cycles,
                      q.force_promoted,
                      q.status AS queue_status,
                      q.version AS queue_version,
                      COALESCE(task_counts.task_count, 0) AS task_count
               FROM goals g
               LEFT JOIN goal_queue q ON q.goal_id = g.goal_id
               LEFT JOIN (
                   SELECT goal_id, COUNT(*) AS task_count
                   FROM tasks
                   GROUP BY goal_id
               ) task_counts ON task_counts.goal_id = g.goal_id
               ORDER BY g.created_at ASC"""
        )
        return [dict(row) for row in rows]

    def get_goal(self, goal_id: str) -> dict[str, Any]:
        row = self.db.fetch_one(
            """SELECT g.goal_id,
                      g.title,
                      g.description,
                      g.state,
                      g.blocked_reason,
                      g.escalation_reason,
                      g.version,
                      g.created_at,
                      g.updated_at,
                      q.urgency,
                      q.value,
                      q.deadline_score,
                      q.base_priority,
                      q.priority,
                      q.wait_cycles,
                      q.force_promoted,
                      q.status AS queue_status,
                      q.version AS queue_version
               FROM goals g
               LEFT JOIN goal_queue q ON q.goal_id = g.goal_id
               WHERE g.goal_id = ?""",
            goal_id,
        )
        if row is None:
            raise NotFoundError(f"Goal {goal_id} not found")
        return dict(row)

    def transition_goal(
        self,
        goal_id: str,
        *,
        to_state: str,
        owner: str,
        event_type: str,
        correlation_id: str,
        reason: str | None = None,
        payload: dict[str, Any] | None = None,
        tx: Transaction | None = None,
    ) -> dict[str, Any]:
        goal = (tx or self.db).fetch_one("SELECT * FROM goals WHERE goal_id = ?", goal_id)
        if goal is None:
            raise NotFoundError(f"Goal {goal_id} not found")

        valid, reason_text = validate_transition("goal", goal["state"], to_state, owner)
        if not valid:
            self._metric("transition.rejected")
            self.event_bus.record_event(
                "transition.rejected",
                goal_id,
                correlation_id,
                {
                    "entity_type": "goal",
                    "from_state": goal["state"],
                    "to_state": to_state,
                    "owner": owner,
                    "reason": reason_text,
                },
            )
            raise ConflictError(reason_text)

        if tx is not None:
            return self._transition_goal_in_tx(
                tx, dict(goal), to_state, event_type, correlation_id, reason, payload
            )

        with self.db.transaction() as inner_tx:
            latest_goal = inner_tx.fetch_one("SELECT * FROM goals WHERE goal_id = ?", goal_id)
            if latest_goal is None:
                raise NotFoundError(f"Goal {goal_id} not found")
            return self._transition_goal_in_tx(
                inner_tx,
                dict(latest_goal),
                to_state,
                event_type,
                correlation_id,
                reason,
                payload,
            )

    def transition_task(
        self,
        task_id: str,
        *,
        to_state: str,
        owner: str,
        event_type: str,
        correlation_id: str,
        extra_fields: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        tx: Transaction,
    ) -> dict[str, Any]:
        task = tx.fetch_one("SELECT * FROM task_state WHERE task_id = ?", task_id)
        if task is None:
            raise NotFoundError(f"Task {task_id} not found")
        valid, reason_text = validate_transition("task", task["status"], to_state, owner)
        if not valid:
            self._metric("transition.rejected", tx=tx)
            self.event_bus.record_event(
                "transition.rejected",
                task_id,
                correlation_id,
                {
                    "entity_type": "task",
                    "from_state": task["status"],
                    "to_state": to_state,
                    "owner": owner,
                    "reason": reason_text,
                },
            )
            raise ConflictError(reason_text)

        updates = {"status": to_state, **(extra_fields or {})}
        assignments = ", ".join(f"{column} = ?" for column in updates)
        params = [*updates.values(), now_utc(), task_id, task["version"]]
        rows = tx.execute(
            f"UPDATE task_state SET {assignments}, updated_at = ?, version = version + 1 "
            "WHERE task_id = ? AND version = ?",
            *params,
        )
        if rows == 0:
            raise OptimisticLockError(f"Task {task_id} version conflict")

        self.event_bus.record_event(
            event_type,
            task_id,
            correlation_id,
            payload or {"from_state": task["status"], "to_state": to_state},
            tx=tx,
        )
        self._metric(f"tasks.transition.{to_state}", tx=tx)
        updated = tx.fetch_one(
            """SELECT ts.*, t.title
               FROM task_state ts
               JOIN tasks t ON t.task_id = ts.task_id
               WHERE ts.task_id = ?""",
            task_id,
        )
        return dict(updated) if updated else {}

    def find_invariant_violations(self) -> list[str]:
        violations: list[str] = []
        rows = self.db.fetch_all(
            """SELECT g.goal_id, g.state, q.status AS queue_status
               FROM goals g
               LEFT JOIN goal_queue q ON q.goal_id = g.goal_id"""
        )
        for row in rows:
            if row["state"] in {"archived", "cancelled"} and row["queue_status"] is not None:
                violations.append(
                    f"Goal {row['goal_id']} is {row['state']} but still exists in the queue"
                )
            expected = queue_status_for_goal_state(row["state"])
            if expected and row["queue_status"] != expected:
                violations.append(
                    f"Goal {row['goal_id']} expects queue status '{expected}' but found '{row['queue_status']}'"
                )
        return violations

    def _transition_goal_in_tx(
        self,
        tx: Transaction,
        goal: dict[str, Any],
        to_state: str,
        event_type: str,
        correlation_id: str,
        reason: str | None,
        payload: dict[str, Any] | None,
    ) -> dict[str, Any]:
        blocked_reason = goal["blocked_reason"]
        escalation_reason = goal["escalation_reason"]
        if to_state == "blocked":
            blocked_reason = reason or blocked_reason
        if to_state == "escalation_pending":
            escalation_reason = reason or escalation_reason
        rows = tx.execute(
            """UPDATE goals
               SET state = ?, blocked_reason = ?, escalation_reason = ?,
                   updated_at = ?, version = version + 1
               WHERE goal_id = ? AND version = ?""",
            to_state,
            blocked_reason,
            escalation_reason,
            now_utc(),
            goal["goal_id"],
            goal["version"],
        )
        if rows == 0:
            raise OptimisticLockError(f"Goal {goal['goal_id']} version conflict")

        queue = tx.fetch_one("SELECT * FROM goal_queue WHERE goal_id = ?", goal["goal_id"])
        queue_status = queue_status_for_goal_state(to_state)
        if queue_status is None:
            if queue is not None:
                deleted = tx.execute(
                    "DELETE FROM goal_queue WHERE goal_id = ? AND version = ?",
                    goal["goal_id"],
                    queue["version"],
                )
                if deleted == 0:
                    raise OptimisticLockError(f"Goal queue {goal['goal_id']} delete conflict")
        else:
            if queue is None:
                raise ConflictError(f"Goal queue missing for goal {goal['goal_id']}")
            wait_cycles = 0 if to_state == "active" else queue["wait_cycles"]
            priority = queue["base_priority"] if to_state == "active" else queue["priority"]
            updated = tx.execute(
                """UPDATE goal_queue
                   SET status = ?, wait_cycles = ?, priority = ?, updated_at = ?, version = version + 1
                   WHERE goal_id = ? AND version = ?""",
                queue_status,
                wait_cycles,
                priority,
                now_utc(),
                goal["goal_id"],
                queue["version"],
            )
            if updated == 0:
                raise OptimisticLockError(f"Goal queue {goal['goal_id']} version conflict")

        self.event_bus.record_event(
            event_type,
            goal["goal_id"],
            correlation_id,
            payload or {"from_state": goal["state"], "to_state": to_state, "reason": reason},
            tx=tx,
        )
        self._metric(f"goals.transition.{to_state}", tx=tx)
        updated_goal = tx.fetch_one(
            """SELECT g.goal_id,
                      g.title,
                      g.description,
                      g.state,
                      g.blocked_reason,
                      g.escalation_reason,
                      g.version,
                      g.created_at,
                      g.updated_at,
                      q.urgency,
                      q.value,
                      q.deadline_score,
                      q.base_priority,
                      q.priority,
                      q.wait_cycles,
                      q.force_promoted,
                      q.status AS queue_status,
                      q.version AS queue_version
               FROM goals g
               LEFT JOIN goal_queue q ON q.goal_id = g.goal_id
               WHERE g.goal_id = ?""",
            goal["goal_id"],
        )
        return dict(updated_goal) if updated_goal else {}

    def _metric(self, name: str, delta: int = 1, *, tx: Transaction | None = None) -> None:
        if self.observability is None:
            return
        self.observability.increment_metric(name, delta=delta, tx=tx)
