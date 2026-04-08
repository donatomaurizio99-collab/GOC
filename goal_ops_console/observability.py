import json
from typing import Any

from goal_ops_console.database import Database, Transaction, new_id, now_utc


class ObservabilityService:
    def __init__(self, db: Database):
        self.db = db

    def increment_metric(
        self,
        metric_name: str,
        delta: int = 1,
        *,
        tx: Transaction | None = None,
    ) -> None:
        if delta == 0:
            return
        timestamp = now_utc()
        runner = tx or self.db
        runner.execute(
            """INSERT INTO metrics_counters (metric_name, value, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(metric_name) DO UPDATE
                 SET value = metrics_counters.value + excluded.value,
                     updated_at = excluded.updated_at""",
            metric_name,
            delta,
            timestamp,
        )

    def list_metrics(self, *, prefix: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if prefix:
            clauses.append("metric_name LIKE ?")
            params.append(f"{prefix}%")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.db.fetch_all(
            f"""SELECT metric_name, value, updated_at
                FROM metrics_counters
                {where}
                ORDER BY value DESC, metric_name ASC
                LIMIT ?""",
            *params,
            limit,
        )
        return [dict(row) for row in rows]

    def metrics_summary(self) -> dict[str, int]:
        watched = (
            "http.requests.total",
            "http.requests.status.429",
            "events.emitted",
            "events.processed",
            "events.failed",
            "goals.created",
            "tasks.created",
            "transition.rejected",
            "backpressure.throttled",
        )
        summary: dict[str, int] = {}
        for metric in watched:
            value = self.db.fetch_scalar(
                "SELECT value FROM metrics_counters WHERE metric_name = ?",
                metric,
            )
            summary[metric] = int(value or 0)
        return summary

    def record_audit(
        self,
        *,
        action: str,
        actor: str,
        status: str,
        entity_type: str | None = None,
        entity_id: str | None = None,
        correlation_id: str | None = None,
        details: dict[str, Any] | None = None,
        tx: Transaction | None = None,
    ) -> str:
        audit_id = new_id()
        runner = tx or self.db
        runner.execute(
            """INSERT INTO audit_log
               (audit_id, action, actor, status, entity_type, entity_id,
                correlation_id, details, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            audit_id,
            action,
            actor,
            status,
            entity_type,
            entity_id,
            correlation_id,
            json.dumps(details) if details else None,
            now_utc(),
        )
        return audit_id

    def list_audit(
        self,
        *,
        limit: int = 200,
        action: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if action:
            clauses.append("action = ?")
            params.append(action)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.db.fetch_all(
            f"""SELECT audit_id, action, actor, status, entity_type, entity_id,
                       correlation_id, details, created_at
                FROM audit_log
                {where}
                ORDER BY created_at DESC
                LIMIT ?""",
            *params,
            limit,
        )
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            details_raw = item.get("details")
            item["details"] = json.loads(details_raw) if details_raw else None
            result.append(item)
        return result

    def recent_audit_count(self, *, hours: int = 24) -> int:
        count = self.db.fetch_scalar(
            """SELECT COUNT(*) FROM audit_log
               WHERE created_at >= datetime('now', ? || ' hours')""",
            f"-{hours}",
        )
        return int(count or 0)
