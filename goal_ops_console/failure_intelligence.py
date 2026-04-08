import hashlib

from goal_ops_console.config import (
    ERROR_HASH_ESCALATION_WINDOW_MINUTES,
    SYSTEMIC_FAILURE_THRESHOLD,
    SYSTEMIC_FAILURE_WINDOW_SECONDS,
)
from goal_ops_console.database import Database, Transaction, new_id, now_utc


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
