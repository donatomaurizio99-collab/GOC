import json
from collections.abc import Callable
from typing import Any

from goal_ops_console.config import CONSUMER_BATCH_SIZE, PROCESSING_TIMEOUT_SECONDS
from goal_ops_console.database import Database, Transaction, new_id, now_utc


def make_payload(data: dict[str, Any] | None = None) -> str:
    return json.dumps({"schema_version": 1, "data": data or {}})


class EventBus:
    def __init__(self, db: Database, processing_timeout_seconds: int = PROCESSING_TIMEOUT_SECONDS):
        self.db = db
        self.processing_timeout_seconds = processing_timeout_seconds

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
        identifier = event_id or new_id()
        runner = tx or self.db
        runner.execute(
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
        return identifier

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

    def reclaim_stuck_processing(self, consumer_id: str) -> int:
        return self.db.execute(
            """UPDATE event_processing
               SET    status = 'pending',
                      version = version + 1
               WHERE  consumer_id = ?
               AND    status = 'processing'
               AND    processing_started_at < datetime('now', ? || ' seconds')""",
            consumer_id,
            f"-{self.processing_timeout_seconds}",
        )

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
            return True
        except Exception:
            self.db.execute(
                "UPDATE event_processing SET status = 'failed', version = version + 1 "
                "WHERE event_id = ? AND consumer_id = ?",
                event_id,
                consumer_id,
            )
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
