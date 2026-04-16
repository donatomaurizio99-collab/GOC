import json
from collections.abc import Callable
from typing import TYPE_CHECKING
from typing import Any

from goal_ops_console.config import CONSUMER_BATCH_SIZE, PROCESSING_TIMEOUT_SECONDS
from goal_ops_console.database import Database, Transaction, new_id, now_utc
from goal_ops_console.models import BackpressureError

if TYPE_CHECKING:
    from goal_ops_console.observability import ObservabilityService


def make_payload(data: dict[str, Any] | None = None) -> str:
    return json.dumps({"schema_version": 1, "data": data or {}})


class EventBus:
    def __init__(
        self,
        db: Database,
        processing_timeout_seconds: int = PROCESSING_TIMEOUT_SECONDS,
        *,
        default_consumer_id: str,
        max_pending_events: int,
        backpressure_retry_after_seconds: int,
        events_retention_days: int,
        event_processing_retention_days: int,
        failure_log_retention_days: int,
        audit_log_retention_days: int,
        idempotency_retention_days: int,
        observability: "ObservabilityService | None" = None,
    ):
        self.db = db
        self.processing_timeout_seconds = processing_timeout_seconds
        self.default_consumer_id = default_consumer_id
        self.max_pending_events = max_pending_events
        self.backpressure_retry_after_seconds = backpressure_retry_after_seconds
        self.events_retention_days = events_retention_days
        self.event_processing_retention_days = event_processing_retention_days
        self.failure_log_retention_days = failure_log_retention_days
        self.audit_log_retention_days = audit_log_retention_days
        self.idempotency_retention_days = idempotency_retention_days
        self.observability = observability

    def record_event(
        self,
        event_type: str,
        entity_id: str,
        correlation_id: str,
        payload: dict[str, Any] | None = None,
        *,
        event_id: str | None = None,
        tx: Transaction | None = None,
    ) -> str:
        self.ensure_within_backpressure(tx=tx)
        identifier = event_id or new_id()
        runner = tx or self.db
        inserted = runner.execute(
            "INSERT OR IGNORE INTO events "
            "(event_id, event_type, entity_id, correlation_id, payload, emitted_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            identifier,
            event_type,
            entity_id,
            correlation_id,
            make_payload(payload),
            now_utc(),
        )
        if inserted:
            self._metric("events.emitted", tx=tx)
        return identifier

    def pending_backlog_count(
        self,
        consumer_id: str | None = None,
        *,
        tx: Transaction | None = None,
    ) -> int:
        consumer = consumer_id or self.default_consumer_id
        runner = tx or self.db
        value = runner.fetch_scalar(
            """SELECT COUNT(*)
               FROM events e
               LEFT JOIN event_processing ep
                 ON e.event_id = ep.event_id AND ep.consumer_id = ?
               WHERE ep.event_id IS NULL
                  OR ep.status IN ('pending', 'failed', 'processing')""",
            consumer,
        )
        return int(value or 0)

    def backpressure_snapshot(self, consumer_id: str | None = None) -> dict[str, Any]:
        consumer = consumer_id or self.default_consumer_id
        pending = self.pending_backlog_count(consumer)
        return {
            "consumer_id": consumer,
            "pending_events": pending,
            "max_pending_events": self.max_pending_events,
            "is_throttled": pending >= self.max_pending_events,
            "retry_after_seconds": self.backpressure_retry_after_seconds,
        }

    def ensure_within_backpressure(
        self,
        consumer_id: str | None = None,
        *,
        tx: Transaction | None = None,
    ) -> int:
        consumer = consumer_id or self.default_consumer_id
        pending = self.pending_backlog_count(consumer, tx=tx)
        if pending >= self.max_pending_events:
            self._metric("backpressure.throttled", tx=tx)
            raise BackpressureError(
                (
                    f"Event backlog limit reached for consumer '{consumer}' "
                    f"({pending}/{self.max_pending_events}). Drain events and retry."
                ),
                retry_after_seconds=self.backpressure_retry_after_seconds,
            )
        return pending

    def run_retention_cleanup(self) -> dict[str, int]:
        with self.db.transaction() as tx:
            event_processing_deleted = tx.execute(
                """DELETE FROM event_processing
                   WHERE status = 'processed'
                   AND processed_at IS NOT NULL
                   AND processed_at < datetime('now', ? || ' days')""",
                f"-{self.event_processing_retention_days}",
            )
            events_deleted = tx.execute(
                """DELETE FROM events
                   WHERE emitted_at < datetime('now', ? || ' days')
                   AND NOT EXISTS (
                       SELECT 1 FROM event_processing ep
                       WHERE ep.event_id = events.event_id
                   )""",
                f"-{self.events_retention_days}",
            )
            failures_deleted = tx.execute(
                """DELETE FROM failure_log
                   WHERE created_at < datetime('now', ? || ' days')""",
                f"-{self.failure_log_retention_days}",
            )
            audit_integrity_deleted = tx.execute(
                """DELETE FROM audit_log_integrity
                   WHERE created_at < datetime('now', ? || ' days')
                      OR audit_id IN (
                          SELECT audit_id
                          FROM audit_log
                          WHERE created_at < datetime('now', ? || ' days')
                      )""",
                f"-{self.audit_log_retention_days}",
                f"-{self.audit_log_retention_days}",
            )
            audit_log_deleted = tx.execute(
                """DELETE FROM audit_log
                   WHERE created_at < datetime('now', ? || ' days')""",
                f"-{self.audit_log_retention_days}",
            )
            idempotency_deleted = tx.execute(
                """DELETE FROM idempotency_keys
                   WHERE updated_at < datetime('now', ? || ' days')""",
                f"-{self.idempotency_retention_days}",
            )
            if events_deleted:
                self._metric("maintenance.retention.events_deleted", events_deleted, tx=tx)
            if event_processing_deleted:
                self._metric(
                    "maintenance.retention.event_processing_deleted",
                    event_processing_deleted,
                    tx=tx,
                )
            if failures_deleted:
                self._metric("maintenance.retention.failure_log_deleted", failures_deleted, tx=tx)
            if audit_integrity_deleted:
                self._metric(
                    "maintenance.retention.audit_integrity_deleted",
                    audit_integrity_deleted,
                    tx=tx,
                )
            if audit_log_deleted:
                self._metric("maintenance.retention.audit_log_deleted", audit_log_deleted, tx=tx)
            if idempotency_deleted:
                self._metric("maintenance.retention.idempotency_deleted", idempotency_deleted, tx=tx)
        return {
            "events_deleted": events_deleted,
            "event_processing_deleted": event_processing_deleted,
            "failure_log_deleted": failures_deleted,
            "audit_integrity_deleted": audit_integrity_deleted,
            "audit_log_deleted": audit_log_deleted,
            "idempotency_deleted": idempotency_deleted,
        }

    def list_events(
        self,
        *,
        correlation_id: str | None = None,
        entity_id: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if correlation_id:
            clauses.append("correlation_id LIKE ?")
            params.append(f"{correlation_id}%")
        if entity_id:
            clauses.append("entity_id = ?")
            params.append(entity_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.db.fetch_all(
            f"SELECT seq, event_id, event_type, entity_id, correlation_id, payload, emitted_at "
            f"FROM events {where} ORDER BY seq ASC LIMIT ?",
            *params,
            limit,
        )
        return [self._row_to_event(row) for row in rows]

    def flow_trace(self, goal_id: str, *, limit: int = 500) -> dict[str, Any]:
        events = self.list_events(correlation_id=goal_id, limit=limit)
        prefix = f"{goal_id}:"
        goal_level_events: list[dict[str, Any]] = []
        attempt_buckets: dict[tuple[str, int], list[dict[str, Any]]] = {}

        for event in events:
            correlation = event["correlation_id"]
            if correlation == goal_id:
                goal_level_events.append(event)
                continue
            if not correlation.startswith(prefix):
                continue

            suffix = correlation[len(prefix):]
            parts = suffix.split(":")
            if len(parts) < 2:
                continue
            task_id = parts[0]
            try:
                attempt = int(parts[1])
            except ValueError:
                continue

            attempt_buckets.setdefault((task_id, attempt), []).append(event)

        attempts: list[dict[str, Any]] = []
        for (task_id, attempt), grouped_events in attempt_buckets.items():
            seqs = [item["seq"] for item in grouped_events]
            attempts.append(
                {
                    "task_id": task_id,
                    "attempt": attempt,
                    "event_count": len(grouped_events),
                    "first_seq": min(seqs),
                    "last_seq": max(seqs),
                    "event_types": [item["event_type"] for item in grouped_events],
                }
            )

        attempts.sort(key=lambda item: (item["first_seq"], item["task_id"], item["attempt"]))
        return {
            "goal_id": goal_id,
            "event_count": len(events),
            "goal_level_count": len(goal_level_events),
            "attempt_count": len(attempts),
            "goal_level_events": goal_level_events,
            "attempts": attempts,
            "events": events,
        }

    def reclaim_stuck_processing(self, consumer_id: str) -> int:
        reclaimed = self.db.execute(
            """UPDATE event_processing
               SET    status = 'pending',
                      version = version + 1
               WHERE  consumer_id = ?
               AND    status = 'processing'
               AND    processing_started_at < datetime('now', ? || ' seconds')""",
            consumer_id,
            f"-{self.processing_timeout_seconds}",
        )
        if reclaimed:
            self._metric("event_processing.reclaimed", reclaimed)
        return reclaimed

    def process_event(
        self,
        event_id: str,
        consumer_id: str,
        handler: Callable[[dict[str, Any]], None],
    ) -> bool:
        inserted = self.db.execute(
            "INSERT OR IGNORE INTO event_processing "
            "(event_id, consumer_id, status, version) VALUES (?, ?, 'pending', 1)",
            event_id,
            consumer_id,
        )

        if not inserted:
            row = self.db.fetch_one(
                "SELECT status FROM event_processing WHERE event_id = ? AND consumer_id = ?",
                event_id,
                consumer_id,
            )
            if row and row["status"] == "processed":
                return False

        claimed = self.db.execute(
            """UPDATE event_processing
               SET    status = 'processing',
                      processing_started_at = ?,
                      version = version + 1
               WHERE  event_id = ?
               AND    consumer_id = ?
               AND   (
                        status IN ('pending', 'failed')
                     OR (
                            status = 'processing'
                        AND processing_started_at < datetime('now', ? || ' seconds')
                     )
               )""",
            now_utc(),
            event_id,
            consumer_id,
            f"-{self.processing_timeout_seconds}",
        )
        if not claimed:
            return False

        event_row = self.db.fetch_one(
            "SELECT seq, event_id, event_type, entity_id, correlation_id, payload, emitted_at "
            "FROM events WHERE event_id = ?",
            event_id,
        )
        if event_row is None:
            self.db.execute(
                "UPDATE event_processing SET status = 'failed', version = version + 1 "
                "WHERE event_id = ? AND consumer_id = ?",
                event_id,
                consumer_id,
            )
            return False

        event = self._row_to_event(event_row)
        try:
            handler(event)
            self.db.execute(
                "UPDATE event_processing "
                "SET status = 'processed', processed_at = ?, version = version + 1 "
                "WHERE event_id = ? AND consumer_id = ?",
                now_utc(),
                event_id,
                consumer_id,
            )
            self._metric("events.processed")
            return True
        except Exception:
            self.db.execute(
                "UPDATE event_processing SET status = 'failed', version = version + 1 "
                "WHERE event_id = ? AND consumer_id = ?",
                event_id,
                consumer_id,
            )
            self._metric("events.failed")
            raise

    def consume_batch(
        self,
        consumer_id: str,
        handler: Callable[[dict[str, Any]], None],
        batch_size: int = CONSUMER_BATCH_SIZE,
    ) -> int:
        self.reclaim_stuck_processing(consumer_id)
        rows = self.db.fetch_all(
            """SELECT e.event_id, e.entity_id, e.seq
               FROM events e
               LEFT JOIN event_processing ep
                 ON e.event_id = ep.event_id AND ep.consumer_id = ?
               WHERE ep.event_id IS NULL OR ep.status IN ('pending', 'failed')
               ORDER BY e.seq ASC
               LIMIT ?""",
            consumer_id,
            batch_size,
        )
        processed = 0
        for row in rows:
            if self.process_event(row["event_id"], consumer_id, handler):
                processed += 1
        if processed:
            self._metric("event_batches.processed")
        return processed

    def consumer_stats(self) -> list[dict[str, Any]]:
        rows = self.db.fetch_all(
            """SELECT consumer_id,
                      status,
                      COUNT(*) AS count
               FROM event_processing
               GROUP BY consumer_id, status
               ORDER BY consumer_id ASC, status ASC"""
        )
        return [dict(row) for row in rows]

    def stuck_events(self) -> list[dict[str, Any]]:
        rows = self.db.fetch_all(
            """SELECT ep.consumer_id,
                      ep.event_id,
                      ep.processing_started_at,
                      e.event_type,
                      e.entity_id,
                      e.correlation_id
               FROM event_processing ep
               JOIN events e ON e.event_id = ep.event_id
               WHERE ep.status = 'processing'
               AND ep.processing_started_at < datetime('now', ? || ' seconds')
               ORDER BY e.seq ASC""",
            f"-{self.processing_timeout_seconds}",
        )
        return [dict(row) for row in rows]

    def _row_to_event(self, row: Any) -> dict[str, Any]:
        payload = row["payload"]
        return {
            "seq": row["seq"],
            "event_id": row["event_id"],
            "event_type": row["event_type"],
            "entity_id": row["entity_id"],
            "correlation_id": row["correlation_id"],
            "payload": json.loads(payload) if payload else None,
            "emitted_at": row["emitted_at"],
        }

    def _metric(self, name: str, delta: int = 1, *, tx: Transaction | None = None) -> None:
        if self.observability is None:
            return
        self.observability.increment_metric(name, delta=delta, tx=tx)
