from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _utc_iso() -> str:
    return datetime.now(UTC).isoformat()


def _connect(database_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        str(database_path),
        check_same_thread=False,
        isolation_level=None,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def _write_state(state_file: Path, payload: dict[str, Any]) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(
        json.dumps(payload, ensure_ascii=True, sort_keys=True),
        encoding="utf-8",
    )


def _wal_size(database_path: Path) -> int:
    wal_path = Path(str(database_path) + "-wal")
    if not wal_path.exists():
        return 0
    return int(wal_path.stat().st_size)


def _setup_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS wal_checkpoint_probe (
               marker      TEXT NOT NULL,
               seq         INTEGER NOT NULL,
               payload     TEXT NOT NULL,
               created_at  TEXT NOT NULL DEFAULT (datetime('now')),
               PRIMARY KEY (marker, seq)
           )"""
    )


def _insert_rows(
    connection: sqlite3.Connection,
    *,
    marker: str,
    rows: int,
    payload_bytes: int,
) -> None:
    blob = "x" * max(64, int(payload_bytes))
    for index in range(max(1, int(rows))):
        payload = json.dumps(
            {"marker": marker, "sequence": index, "payload": blob},
            ensure_ascii=True,
            sort_keys=True,
        )
        connection.execute(
            """INSERT INTO wal_checkpoint_probe (marker, seq, payload)
               VALUES (?, ?, ?)""",
            (marker, index, payload),
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Internal helper for WAL checkpoint crash drill: write committed WAL rows, "
            "pause before checkpoint, then optionally execute checkpoint."
        )
    )
    parser.add_argument("--database-path", required=True)
    parser.add_argument("--state-file", required=True)
    parser.add_argument("--marker", required=True)
    parser.add_argument("--rows", type=int, default=240)
    parser.add_argument("--payload-bytes", type=int, default=1024)
    parser.add_argument("--sleep-before-checkpoint-seconds", type=float, default=30.0)
    parser.add_argument("--checkpoint-mode", default="TRUNCATE", choices=("PASSIVE", "FULL", "TRUNCATE"))
    args = parser.parse_args(argv)

    if int(args.rows) <= 0:
        print("[wal-checkpoint-crash-target] ERROR: --rows must be > 0.", file=sys.stderr)
        return 2
    if int(args.payload_bytes) <= 0:
        print("[wal-checkpoint-crash-target] ERROR: --payload-bytes must be > 0.", file=sys.stderr)
        return 2
    if float(args.sleep_before_checkpoint_seconds) <= 0:
        print("[wal-checkpoint-crash-target] ERROR: --sleep-before-checkpoint-seconds must be > 0.", file=sys.stderr)
        return 2

    database_path = Path(str(args.database_path)).expanduser()
    state_file = Path(str(args.state_file)).expanduser()
    marker = str(args.marker)

    connection: sqlite3.Connection | None = None
    try:
        connection = _connect(database_path)
        journal_mode = str(connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]).lower()
        connection.execute("PRAGMA wal_autocheckpoint = 0")
        connection.execute("PRAGMA synchronous = FULL")
        _setup_schema(connection)

        connection.execute("BEGIN IMMEDIATE")
        _insert_rows(
            connection,
            marker=marker,
            rows=int(args.rows),
            payload_bytes=int(args.payload_bytes),
        )
        connection.commit()

        persisted_rows = int(
            connection.execute(
                "SELECT COUNT(*) FROM wal_checkpoint_probe WHERE marker = ?",
                (marker,),
            ).fetchone()[0]
        )
        _write_state(
            state_file,
            {
                "status": "checkpoint_pending",
                "marker": marker,
                "rows_requested": int(args.rows),
                "persisted_rows": persisted_rows,
                "journal_mode": journal_mode,
                "wal_size_bytes": _wal_size(database_path),
                "checkpoint_mode": str(args.checkpoint_mode),
                "pid": os.getpid(),
                "timestamp_utc": _utc_iso(),
            },
        )

        time.sleep(float(args.sleep_before_checkpoint_seconds))

        row = connection.execute(f"PRAGMA wal_checkpoint({str(args.checkpoint_mode)})").fetchone()
        checkpoint_result = {
            "busy": int(row[0]) if row is not None else -1,
            "log_frames": int(row[1]) if row is not None else -1,
            "checkpointed_frames": int(row[2]) if row is not None else -1,
        }
        _write_state(
            state_file,
            {
                "status": "checkpoint_completed",
                "marker": marker,
                "rows_requested": int(args.rows),
                "persisted_rows": persisted_rows,
                "journal_mode": journal_mode,
                "wal_size_bytes": _wal_size(database_path),
                "checkpoint_mode": str(args.checkpoint_mode),
                "checkpoint_result": checkpoint_result,
                "pid": os.getpid(),
                "timestamp_utc": _utc_iso(),
            },
        )
        return 0
    except Exception as exc:
        try:
            _write_state(
                state_file,
                {
                    "status": "error",
                    "marker": marker,
                    "error": str(exc),
                    "pid": os.getpid(),
                    "timestamp_utc": _utc_iso(),
                },
            )
        except Exception:
            pass
        print(f"[wal-checkpoint-crash-target] ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        if connection is not None:
            connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
