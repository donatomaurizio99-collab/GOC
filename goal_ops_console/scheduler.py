import random
import time
from collections.abc import Callable
from typing import Any

from goal_ops_console.config import (
    AGING_FACTOR_PER_CYCLE,
    AGING_MAX_MULTIPLIER,
    MAX_TOTAL_RETRIES_PER_CYCLE,
    MAX_WAIT_CYCLES,
    OL_BASE_BACKOFF_MS,
    OL_MAX_RETRIES,
)
from goal_ops_console.database import Database, now_utc
from goal_ops_console.models import OptimisticLockError, RetryBudgetExceeded


def base_priority(urgency: float, value: float, deadline_score: float) -> float:
    return 0.4 * urgency + 0.4 * value + 0.2 * deadline_score


def effective_priority(base: float, wait_cycles: int) -> float:
    boost = min(AGING_FACTOR_PER_CYCLE * wait_cycles, AGING_MAX_MULTIPLIER)
    return min(base * (1 + boost), 1.0)


class RetryBudget:
    def __init__(self, max_retries: int = MAX_TOTAL_RETRIES_PER_CYCLE):
        self.remaining = max_retries

    def consume(self) -> None:
        if self.remaining <= 0:
            raise RetryBudgetExceeded("Retry budget exhausted for this cycle")
        self.remaining -= 1


def write_with_retry(
    write_fn: Callable[[], Any],
    load_fn: Callable[[], Any],
    budget: RetryBudget,
) -> Any:
    for attempt in range(OL_MAX_RETRIES + 1):
        try:
            return write_fn()
        except OptimisticLockError:
            if attempt == OL_MAX_RETRIES:
                raise
            budget.consume()
            delay = OL_BASE_BACKOFF_MS * (2**attempt)
            jitter = random.uniform(0.8, 1.2)
            time.sleep(delay * jitter / 1000)
            load_fn()
    raise RetryBudgetExceeded("Retry loop exited unexpectedly")


class SchedulerService:
    def __init__(self, db: Database, state_manager: Any):
        self.db = db
        self.state_manager = state_manager

    def age_queue(self, budget: RetryBudget | None = None) -> list[dict[str, Any]]:
        cycle_budget = budget or RetryBudget()
        rows = self.db.fetch_all(
            "SELECT goal_id FROM goal_queue WHERE status IN ('queued', 'active') ORDER BY created_at ASC"
        )
        aged: list[dict[str, Any]] = []
        for row in rows:
            aged.append(self._age_single_goal(row["goal_id"], cycle_budget))
        return aged

    def pick_next_goal(self, budget: RetryBudget | None = None) -> dict[str, Any] | None:
        cycle_budget = budget or RetryBudget()
        self.age_queue(cycle_budget)
        row = self.db.fetch_one(
            "SELECT goal_id FROM goal_queue WHERE status = 'queued' ORDER BY priority DESC, created_at ASC LIMIT 1"
        )
        if row is None:
            return None
        return self.state_manager.transition_goal(
            row["goal_id"],
            to_state="active",
            owner="scheduler",
            event_type="goal.activated",
            correlation_id=row["goal_id"],
        )

    def _age_single_goal(self, goal_id: str, budget: RetryBudget) -> dict[str, Any]:
        current = self.db.fetch_one("SELECT * FROM goal_queue WHERE goal_id = ?", goal_id)
        if current is None:
            return {}

        def load_fn() -> None:
            nonlocal current
            current = self.db.fetch_one("SELECT * FROM goal_queue WHERE goal_id = ?", goal_id)

        def write_fn() -> dict[str, Any]:
            nonlocal current
            if current is None:
                return {}
            next_wait = min(current["wait_cycles"] + 1, MAX_WAIT_CYCLES)
            next_priority = effective_priority(current["base_priority"], next_wait)
            rows = self.db.execute(
                """UPDATE goal_queue
                   SET wait_cycles = ?, priority = ?, updated_at = ?, version = version + 1
                   WHERE goal_id = ? AND version = ?""",
                next_wait,
                next_priority,
                now_utc(),
                goal_id,
                current["version"],
            )
            if rows == 0:
                raise OptimisticLockError(f"goal_queue conflict for {goal_id}")
            current = self.db.fetch_one("SELECT * FROM goal_queue WHERE goal_id = ?", goal_id)
            return dict(current) if current else {}

        return write_with_retry(write_fn, load_fn, budget)
