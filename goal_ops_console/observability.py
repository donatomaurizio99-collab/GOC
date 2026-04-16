import json
import hashlib
from typing import Any

from goal_ops_console.database import Database, Transaction, new_id, now_utc


class ObservabilityService:
    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def _normalize_details(details: dict[str, Any] | None) -> str:
        if not details:
            return ""
        return json.dumps(details, ensure_ascii=True, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _compute_hash(payload: str) -> str:
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _canonical_audit_payload(
        *,
        audit_id: str,
        action: str,
        actor: str,
        status: str,
        entity_type: str | None,
        entity_id: str | None,
        correlation_id: str | None,
        details_json: str,
        created_at: str,
        previous_hash: str | None,
    ) -> str:
        payload = {
            "audit_id": audit_id,
            "action": action,
            "actor": actor,
            "status": status,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "correlation_id": correlation_id,
            "details": details_json,
            "created_at": created_at,
            "previous_hash": previous_hash,
        }
        return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))

    def _insert_audit_with_integrity(
        self,
        *,
        tx: Transaction,
        audit_id: str,
        action: str,
        actor: str,
        status: str,
        entity_type: str | None,
        entity_id: str | None,
        correlation_id: str | None,
        details_json: str,
        created_at: str,
    ) -> None:
        tx.execute(
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
            details_json if details_json else None,
            created_at,
        )
        previous_hash_value = tx.fetch_scalar(
            "SELECT entry_hash FROM audit_log_integrity ORDER BY chain_index DESC LIMIT 1"
        )
        previous_hash = str(previous_hash_value) if previous_hash_value is not None else None
        canonical_payload = self._canonical_audit_payload(
            audit_id=audit_id,
            action=action,
            actor=actor,
            status=status,
            entity_type=entity_type,
            entity_id=entity_id,
            correlation_id=correlation_id,
            details_json=details_json,
            created_at=created_at,
            previous_hash=previous_hash,
        )
        entry_hash = self._compute_hash(canonical_payload)
        tx.execute(
            """INSERT INTO audit_log_integrity
               (audit_id, previous_hash, entry_hash, canonical_payload, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            audit_id,
            previous_hash,
            entry_hash,
            canonical_payload,
            created_at,
        )

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
            "runtime.safe_mode.activated",
            "runtime.db_errors.lock",
            "runtime.db_errors.io",
            "runtime.db_startup_recovery.quarantined",
            "invariants.violations.detected",
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
        created_at = now_utc()
        details_json = self._normalize_details(details)
        if tx is not None:
            self._insert_audit_with_integrity(
                tx=tx,
                audit_id=audit_id,
                action=action,
                actor=actor,
                status=status,
                entity_type=entity_type,
                entity_id=entity_id,
                correlation_id=correlation_id,
                details_json=details_json,
                created_at=created_at,
            )
            return audit_id

        with self.db.transaction() as nested_tx:
            self._insert_audit_with_integrity(
                tx=nested_tx,
                audit_id=audit_id,
                action=action,
                actor=actor,
                status=status,
                entity_type=entity_type,
                entity_id=entity_id,
                correlation_id=correlation_id,
                details_json=details_json,
                created_at=created_at,
            )
        return audit_id

    def ensure_audit_integrity_backfill(self, *, batch_size: int = 1000) -> dict[str, int]:
        normalized_batch_size = max(1, int(batch_size))
        inserted_total = 0
        remaining = 0

        while True:
            with self.db.transaction() as tx:
                previous_hash_value = tx.fetch_scalar(
                    "SELECT entry_hash FROM audit_log_integrity ORDER BY chain_index DESC LIMIT 1"
                )
                previous_hash = str(previous_hash_value) if previous_hash_value is not None else None
                rows = tx.fetch_all(
                    """SELECT a.audit_id,
                              a.action,
                              a.actor,
                              a.status,
                              a.entity_type,
                              a.entity_id,
                              a.correlation_id,
                              a.details,
                              a.created_at
                       FROM audit_log a
                       LEFT JOIN audit_log_integrity ai ON ai.audit_id = a.audit_id
                       WHERE ai.audit_id IS NULL
                       ORDER BY a.created_at ASC, a.audit_id ASC
                       LIMIT ?""",
                    normalized_batch_size,
                )
                inserted_batch = 0
                for row in rows:
                    details_json = str(row["details"]) if row["details"] else ""
                    canonical_payload = self._canonical_audit_payload(
                        audit_id=str(row["audit_id"]),
                        action=str(row["action"]),
                        actor=str(row["actor"]),
                        status=str(row["status"]),
                        entity_type=str(row["entity_type"]) if row["entity_type"] is not None else None,
                        entity_id=str(row["entity_id"]) if row["entity_id"] is not None else None,
                        correlation_id=(
                            str(row["correlation_id"])
                            if row["correlation_id"] is not None
                            else None
                        ),
                        details_json=details_json,
                        created_at=str(row["created_at"]),
                        previous_hash=previous_hash,
                    )
                    entry_hash = self._compute_hash(canonical_payload)
                    tx.execute(
                        """INSERT INTO audit_log_integrity
                           (audit_id, previous_hash, entry_hash, canonical_payload, created_at)
                           VALUES (?, ?, ?, ?, ?)""",
                        str(row["audit_id"]),
                        previous_hash,
                        entry_hash,
                        canonical_payload,
                        str(row["created_at"]),
                    )
                    previous_hash = entry_hash
                    inserted_batch += 1

                remaining_value = tx.fetch_scalar(
                    """SELECT COUNT(*)
                       FROM audit_log a
                       LEFT JOIN audit_log_integrity ai ON ai.audit_id = a.audit_id
                       WHERE ai.audit_id IS NULL"""
                )
                remaining = int(remaining_value or 0)
                inserted_total += inserted_batch
                if inserted_batch:
                    self.increment_metric(
                        "audit.integrity.backfilled",
                        delta=inserted_batch,
                        tx=tx,
                    )
            if remaining == 0 or inserted_batch == 0:
                break

        return {"inserted": inserted_total, "remaining": remaining}

    def audit_integrity_status(self, *, verify_limit: int = 200) -> dict[str, Any]:
        normalized_limit = max(1, int(verify_limit))
        total_entries = int(self.db.fetch_scalar("SELECT COUNT(*) FROM audit_log") or 0)
        chain_entries = int(self.db.fetch_scalar("SELECT COUNT(*) FROM audit_log_integrity") or 0)
        missing_rows = int(
            self.db.fetch_scalar(
                """SELECT COUNT(*)
                   FROM audit_log a
                   LEFT JOIN audit_log_integrity ai ON ai.audit_id = a.audit_id
                   WHERE ai.audit_id IS NULL"""
            )
            or 0
        )

        if chain_entries == 0:
            coverage = 100.0 if total_entries == 0 else 0.0
            return {
                "ok": bool(total_entries == 0),
                "metrics": {
                    "total_audit_entries": total_entries,
                    "chain_entries": chain_entries,
                    "missing_integrity_rows": missing_rows,
                    "coverage_percent": coverage,
                    "sampled_rows": 0,
                    "hash_mismatch_count": 0,
                    "previous_link_mismatch_count": 0,
                    "chain_gap_count": 0,
                    "verify_limit": normalized_limit,
                },
                "violations": (
                    []
                    if total_entries == 0
                    else [
                        {
                            "type": "missing_integrity_rows",
                            "message": "Audit rows exist without matching integrity chain rows.",
                            "count": missing_rows,
                        }
                    ]
                ),
            }

        max_chain_index = int(
            self.db.fetch_scalar("SELECT COALESCE(MAX(chain_index), 0) FROM audit_log_integrity") or 0
        )
        start_index = max(1, max_chain_index - normalized_limit + 1)
        predecessor_hash: str | None = None
        if start_index > 1:
            predecessor_value = self.db.fetch_scalar(
                "SELECT entry_hash FROM audit_log_integrity WHERE chain_index = ?",
                start_index - 1,
            )
            predecessor_hash = str(predecessor_value) if predecessor_value is not None else None

        rows = self.db.fetch_all(
            """SELECT chain_index, previous_hash, entry_hash, canonical_payload
               FROM audit_log_integrity
               WHERE chain_index >= ?
               ORDER BY chain_index ASC""",
            start_index,
        )

        hash_mismatch_count = 0
        previous_link_mismatch_count = 0
        chain_gap_count = 0
        expected_previous = predecessor_hash
        last_index = start_index - 1

        for row in rows:
            current_index = int(row["chain_index"])
            if current_index != last_index + 1:
                chain_gap_count += max(1, current_index - (last_index + 1))
            previous_hash = str(row["previous_hash"]) if row["previous_hash"] is not None else None
            entry_hash = str(row["entry_hash"])
            canonical_payload = str(row["canonical_payload"])
            if previous_hash != expected_previous:
                previous_link_mismatch_count += 1
            if self._compute_hash(canonical_payload) != entry_hash:
                hash_mismatch_count += 1
            expected_previous = entry_hash
            last_index = current_index

        coverage_percent = 100.0 if total_entries == 0 else (chain_entries / max(1, total_entries)) * 100.0
        violations: list[dict[str, Any]] = []
        if missing_rows:
            violations.append(
                {
                    "type": "missing_integrity_rows",
                    "message": "Audit rows exist without matching integrity chain rows.",
                    "count": missing_rows,
                }
            )
        if hash_mismatch_count:
            violations.append(
                {
                    "type": "hash_mismatch",
                    "message": "Stored entry hash does not match canonical payload hash.",
                    "count": hash_mismatch_count,
                }
            )
        if previous_link_mismatch_count:
            violations.append(
                {
                    "type": "link_mismatch",
                    "message": "Integrity chain previous_hash links do not match preceding entry hash.",
                    "count": previous_link_mismatch_count,
                }
            )
        if chain_gap_count:
            violations.append(
                {
                    "type": "chain_gap",
                    "message": "Detected non-contiguous chain_index gaps in audit integrity rows.",
                    "count": chain_gap_count,
                }
            )

        return {
            "ok": (
                missing_rows == 0
                and hash_mismatch_count == 0
                and previous_link_mismatch_count == 0
                and chain_gap_count == 0
            ),
            "metrics": {
                "total_audit_entries": total_entries,
                "chain_entries": chain_entries,
                "missing_integrity_rows": missing_rows,
                "coverage_percent": coverage_percent,
                "sampled_rows": len(rows),
                "hash_mismatch_count": hash_mismatch_count,
                "previous_link_mismatch_count": previous_link_mismatch_count,
                "chain_gap_count": chain_gap_count,
                "verify_limit": normalized_limit,
                "sample_start_chain_index": start_index,
                "sample_end_chain_index": max_chain_index,
            },
            "violations": violations,
        }

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
