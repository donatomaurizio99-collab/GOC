import hashlib

from goal_ops_console.config import (
    ERROR_HASH_ESCALATION_WINDOW_MINUTES,
    SYSTEMIC_FAILURE_THRESHOLD,
    SYSTEMIC_FAILURE_WINDOW_SECONDS,
)
from goal_ops_console.database import Database, Transaction, new_id, now_utc
from goal_ops_console.models import OptimisticLockError


def compute_error_hash(error_type: str, error_message: str) -> str:
    normalized = f"{error_type}:{error_message.strip().lower()[:200]}"
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


class FailureIntelligence:
    def __init__(self, db: Database):
        self.db = db

    def log_failure(
        self,
        tx: Transaction,
        *,
        task_id: str,
        goal_id: str,
        correlation_id: str,
        failure_type: str,
        fingerprint: str,
        retry_count: int,
        error_message: str,
    ) -> str:
        failure_id = new_id()
        timestamp = now_utc()
        tx.execute(
            "INSERT INTO failure_log "
            "(id, task_id, goal_id, correlation_id, failure_type, fingerprint, retry_count, "
            " last_error, status, version, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'recorded', 1, ?, ?)",
            failure_id,
            task_id,
            goal_id,
            correlation_id,
            failure_type,
            fingerprint,
            retry_count,
            error_message,
            timestamp,
            timestamp,
        )
        return failure_id

    def count_errors_in_window(
        self,
        error_hash: str,
        *,
        window_minutes: int = ERROR_HASH_ESCALATION_WINDOW_MINUTES,
        tx: Transaction | None = None,
    ) -> int:
        runner = tx or self.db
        value = runner.fetch_scalar(
            """SELECT COUNT(*) FROM failure_log
               WHERE fingerprint = ?
               AND created_at > datetime('now', ? || ' minutes')""",
            error_hash,
            f"-{window_minutes}",
        )
        return int(value or 0)

    def systemic_external_failure_count(self, *, tx: Transaction | None = None) -> int:
        runner = tx or self.db
        value = runner.fetch_scalar(
            """SELECT COUNT(*) FROM failure_log
               WHERE failure_type = 'ExternalFailure'
               AND created_at > datetime('now', ? || ' seconds')""",
            f"-{SYSTEMIC_FAILURE_WINDOW_SECONDS}",
        )
        return int(value or 0)

    def is_systemic_external_failure(self, *, tx: Transaction | None = None) -> bool:
        return self.systemic_external_failure_count(tx=tx) >= SYSTEMIC_FAILURE_THRESHOLD

    def should_escalate(
        self,
        *,
        failure_type: str,
        error_hash: str,
        retry_count: int,
        max_retries: int,
        tx: Transaction,
    ) -> bool:
        if failure_type == "ExternalFailure":
            return self.is_systemic_external_failure(tx=tx)
        if retry_count < max_retries:
            return False
        return self.count_errors_in_window(error_hash, tx=tx) >= 2

    def get_fault_by_id(
        self,
        failure_id: str,
        *,
        tx: Transaction | None = None,
    ) -> dict | None:
        runner = tx or self.db
        row = runner.fetch_one(
            """SELECT fl.id AS failure_id,
                      fl.version AS failure_version,
                      fl.created_at,
                      fl.failure_type,
                      fl.fingerprint AS error_hash,
                      fl.retry_count,
                      fl.last_error,
                      fl.status AS failure_status,
                      fl.task_id,
                      t.title AS task_title,
                      ts.status AS task_status,
                      ts.correlation_id AS task_correlation_id,
                      fl.goal_id,
                      g.title AS goal_title,
                      g.state AS goal_state,
                      fl.correlation_id
               FROM failure_log fl
               LEFT JOIN task_state ts ON ts.task_id = fl.task_id
               LEFT JOIN tasks t ON t.task_id = fl.task_id
               LEFT JOIN goals g ON g.goal_id = fl.goal_id
               WHERE fl.id = ?""",
            failure_id,
        )
        return dict(row) if row else None

    def update_failure_status(
        self,
        tx: Transaction,
        *,
        failure_id: str,
        expected_version: int,
        status: str,
    ) -> None:
        updated = tx.execute(
            """UPDATE failure_log
               SET status = ?, updated_at = ?, version = version + 1
               WHERE id = ? AND version = ?""",
            status,
            now_utc(),
            failure_id,
            expected_version,
        )
        if updated == 0:
            raise OptimisticLockError(f"Failure {failure_id} version conflict")

    def list_faults(
        self,
        *,
        limit: int = 200,
        failure_type: str | None = None,
        failure_status: str | None = None,
        task_status: str | None = None,
        goal_id: str | None = None,
        error_hash: str | None = None,
        dead_letter_only: bool = False,
    ) -> list[dict]:
        clauses, params = self._fault_filters(
            failure_type=failure_type,
            failure_status=failure_status,
            task_status=task_status,
            goal_id=goal_id,
            error_hash=error_hash,
            dead_letter_only=dead_letter_only,
        )
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.db.fetch_all(
            f"""SELECT fl.id AS failure_id,
                       fl.created_at,
                       fl.failure_type,
                       fl.fingerprint AS error_hash,
                       fl.retry_count,
                       fl.last_error,
                       fl.status AS failure_status,
                       fl.task_id,
                       t.title AS task_title,
                       ts.status AS task_status,
                       fl.goal_id,
                       g.title AS goal_title,
                       g.state AS goal_state,
                       fl.correlation_id
                FROM failure_log fl
                LEFT JOIN task_state ts ON ts.task_id = fl.task_id
                LEFT JOIN tasks t ON t.task_id = fl.task_id
                LEFT JOIN goals g ON g.goal_id = fl.goal_id
                {where}
                ORDER BY fl.created_at DESC
                LIMIT ?""",
            *params,
            limit,
        )
        return [dict(row) for row in rows]

    def fault_summary(
        self,
        *,
        limit: int = 20,
        failure_type: str | None = None,
        failure_status: str | None = None,
        task_status: str | None = None,
        goal_id: str | None = None,
        error_hash: str | None = None,
        dead_letter_only: bool = False,
    ) -> dict:
        clauses, params = self._fault_filters(
            failure_type=failure_type,
            failure_status=failure_status,
            task_status=task_status,
            goal_id=goal_id,
            error_hash=error_hash,
            dead_letter_only=dead_letter_only,
        )
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        top_rows = self.db.fetch_all(
            f"""SELECT fl.failure_type,
                       fl.fingerprint AS error_hash,
                       COUNT(*) AS count,
                       COUNT(DISTINCT fl.task_id) AS task_count,
                       MAX(fl.created_at) AS latest_at
                FROM failure_log fl
                LEFT JOIN task_state ts ON ts.task_id = fl.task_id
                {where}
                GROUP BY fl.failure_type, fl.fingerprint
                ORDER BY count DESC, latest_at DESC
                LIMIT ?""",
            *params,
            limit,
        )
        total_failures = self.db.fetch_scalar(
            f"""SELECT COUNT(*)
                FROM failure_log fl
                LEFT JOIN task_state ts ON ts.task_id = fl.task_id
                {where}""",
            *params,
        ) or 0
        poison_tasks = self.db.fetch_scalar("SELECT COUNT(*) FROM task_state WHERE status = 'poison'") or 0
        exhausted_tasks = (
            self.db.fetch_scalar("SELECT COUNT(*) FROM task_state WHERE status = 'exhausted'") or 0
        )
        dead_letter_tasks = int(poison_tasks) + int(exhausted_tasks)
        return {
            "total_failures": int(total_failures),
            "dead_letter_tasks": dead_letter_tasks,
            "poison_tasks": int(poison_tasks),
            "exhausted_tasks": int(exhausted_tasks),
            "top_error_hashes": [dict(row) for row in top_rows],
        }

    def _fault_filters(
        self,
        *,
        failure_type: str | None,
        failure_status: str | None,
        task_status: str | None,
        goal_id: str | None,
        error_hash: str | None,
        dead_letter_only: bool,
    ) -> tuple[list[str], list[object]]:
        clauses: list[str] = []
        params: list[object] = []
        if failure_type:
            clauses.append("fl.failure_type = ?")
            params.append(failure_type)
        if failure_status:
            clauses.append("fl.status = ?")
            params.append(failure_status)
        if task_status:
            clauses.append("ts.status = ?")
            params.append(task_status)
        if goal_id:
            clauses.append("fl.goal_id = ?")
            params.append(goal_id)
        if error_hash:
            clauses.append("fl.fingerprint = ?")
            params.append(error_hash)
        if dead_letter_only:
            clauses.append("ts.status IN ('poison', 'exhausted')")
        return clauses, params
