from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from goal_ops_console.config import Settings
from goal_ops_console.main import create_app


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _pending_event_ids(client: TestClient, consumer_id: str) -> list[str]:
    rows = client.app.state.services.db.fetch_all(
        """SELECT e.event_id
           FROM events e
           LEFT JOIN event_processing ep
             ON e.event_id = ep.event_id AND ep.consumer_id = ?
           WHERE ep.event_id IS NULL OR ep.status IN ('pending', 'failed', 'processing')
           ORDER BY e.seq ASC""",
        consumer_id,
    )
    return [str(row["event_id"]) for row in rows]


def _stale_processing_count(client: TestClient, consumer_id: str) -> int:
    rows = client.app.state.services.event_bus.stuck_events()
    return sum(1 for row in rows if str(row.get("consumer_id")) == consumer_id)


def run_drill(
    *,
    goal_count: int,
    stale_processing_count: int,
    consumer_id: str,
    drain_batch_size: int,
    timeout_seconds: float,
) -> dict:
    app = create_app(Settings(database_url=":memory:"))

    with TestClient(app) as client:
        baseline_seq = int(
            client.app.state.services.db.fetch_scalar("SELECT COALESCE(MAX(seq), 0) FROM events") or 0
        )
        goal_ids: list[str] = []
        for index in range(int(goal_count)):
            created = client.post(
                "/goals",
                json={
                    "title": f"Consumer chaos goal {index}",
                    "description": "event consumer recovery drill",
                    "urgency": 0.4,
                    "value": 0.7,
                    "deadline_score": 0.2,
                },
            )
            _expect(created.status_code == 201, f"Goal creation failed at index={index}: {created.text}")
            goal_id = str(created.json()["goal_id"])
            goal_ids.append(goal_id)

            activated = client.post(f"/goals/{goal_id}/activate")
            _expect(activated.status_code == 200, f"Goal activation failed for {goal_id}: {activated.text}")

            task = client.post(
                "/tasks",
                json={"goal_id": goal_id, "title": f"Consumer chaos task {index}"},
            )
            _expect(task.status_code == 201, f"Task creation failed for {goal_id}: {task.text}")

        generated_rows = client.app.state.services.db.fetch_all(
            "SELECT event_id FROM events WHERE seq > ? ORDER BY seq ASC",
            baseline_seq,
        )
        generated_event_ids = [str(row["event_id"]) for row in generated_rows]
        _expect(generated_event_ids, "Drill generated no events.")

        stale_target = min(max(1, int(stale_processing_count)), len(generated_event_ids))
        stale_ids = generated_event_ids[:stale_target]
        for event_id in stale_ids:
            client.app.state.services.db.execute(
                """INSERT INTO event_processing
                   (event_id, consumer_id, status, processing_started_at, processed_at, version)
                   VALUES (?, ?, 'processing', datetime('now', '-120 seconds'), NULL, 1)
                   ON CONFLICT(event_id, consumer_id) DO UPDATE
                     SET status = 'processing',
                         processing_started_at = datetime('now', '-120 seconds'),
                         processed_at = NULL,
                         version = event_processing.version + 1""",
                event_id,
                consumer_id,
            )

        stale_before = _stale_processing_count(client, consumer_id)
        _expect(
            stale_before >= stale_target,
            (
                "Expected stale processing rows before recovery. "
                f"stale_before={stale_before} stale_target={stale_target}"
            ),
        )

        reclaim = client.post(f"/system/consumers/{consumer_id}/reclaim")
        _expect(reclaim.status_code == 200, f"Consumer reclaim failed: {reclaim.text}")
        reclaimed_count = int(reclaim.json()["reclaimed_count"])
        _expect(
            reclaimed_count >= stale_target,
            (
                "Reclaim did not recover expected stale rows. "
                f"reclaimed_count={reclaimed_count} stale_target={stale_target}"
            ),
        )

        processed_total = 0
        deadline = time.time() + max(1.0, float(timeout_seconds))
        while time.time() < deadline:
            pending = _pending_event_ids(client, consumer_id)
            if not pending:
                break
            drained = client.post(
                f"/system/consumers/{consumer_id}/drain",
                params={"batch_size": int(drain_batch_size)},
            )
            _expect(drained.status_code == 200, f"Consumer drain failed: {drained.text}")
            processed_total += int(drained.json()["processed_count"])
            time.sleep(0.02)

        pending_after = _pending_event_ids(client, consumer_id)
        _expect(
            not pending_after,
            (
                "Pending events remain after recovery drill. "
                f"pending_count={len(pending_after)} pending_sample={pending_after[:10]}"
            ),
        )

        status_rows = client.app.state.services.db.fetch_all(
            """SELECT status, COUNT(*) AS count
               FROM event_processing
               WHERE consumer_id = ?
               GROUP BY status
               ORDER BY status ASC""",
            consumer_id,
        )
        status_counts = {str(row["status"]): int(row["count"]) for row in status_rows}
        _expect(status_counts.get("processing", 0) == 0, f"Processing rows still stuck: {status_counts}")
        _expect(status_counts.get("failed", 0) == 0, f"Failed rows remain after recovery: {status_counts}")
        _expect(
            status_counts.get("processed", 0) >= len(generated_event_ids),
            (
                "Not all generated events reached processed state. "
                f"processed={status_counts.get('processed', 0)} generated={len(generated_event_ids)}"
            ),
        )

        readiness = client.get("/system/readiness").json()
        slo = client.get("/system/slo").json()
        _expect(bool(readiness["ready"]), f"Readiness is false after drill: {json.dumps(readiness, sort_keys=True)}")
        _expect(str(slo["status"]) == "ok", f"SLO is not ok after drill: {json.dumps(slo, sort_keys=True)}")

        return {
            "success": True,
            "goal_count": int(goal_count),
            "generated_events": len(generated_event_ids),
            "consumer_id": consumer_id,
            "stale_processing_target": stale_target,
            "stale_processing_before_reclaim": stale_before,
            "reclaimed_count": reclaimed_count,
            "drain_batch_size": int(drain_batch_size),
            "processed_total": processed_total,
            "status_counts": status_counts,
            "readiness_ready": bool(readiness["ready"]),
            "slo_status": str(slo["status"]),
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Chaos drill for event consumer recovery: seed stale processing rows, reclaim, drain backlog, "
            "and verify clean processed state."
        )
    )
    parser.add_argument("--goal-count", type=int, default=40)
    parser.add_argument("--stale-processing-count", type=int, default=15)
    parser.add_argument("--consumer-id", default="chaos-recovery")
    parser.add_argument("--drain-batch-size", type=int, default=100)
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    args = parser.parse_args(argv)

    if int(args.goal_count) <= 0:
        print("[event-consumer-recovery-chaos-drill] ERROR: --goal-count must be > 0.", file=sys.stderr)
        return 2
    if int(args.stale_processing_count) <= 0:
        print(
            "[event-consumer-recovery-chaos-drill] ERROR: --stale-processing-count must be > 0.",
            file=sys.stderr,
        )
        return 2
    if int(args.drain_batch_size) <= 0:
        print("[event-consumer-recovery-chaos-drill] ERROR: --drain-batch-size must be > 0.", file=sys.stderr)
        return 2
    if float(args.timeout_seconds) <= 0:
        print("[event-consumer-recovery-chaos-drill] ERROR: --timeout-seconds must be > 0.", file=sys.stderr)
        return 2

    try:
        report = run_drill(
            goal_count=int(args.goal_count),
            stale_processing_count=int(args.stale_processing_count),
            consumer_id=str(args.consumer_id),
            drain_batch_size=int(args.drain_batch_size),
            timeout_seconds=float(args.timeout_seconds),
        )
    except Exception as exc:
        print(f"[event-consumer-recovery-chaos-drill] ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
