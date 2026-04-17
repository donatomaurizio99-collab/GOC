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


def _ensure_probe_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS durability_probe (
               marker      TEXT NOT NULL,
               seq         INTEGER NOT NULL,
               payload     TEXT NOT NULL,
               phase       TEXT NOT NULL,
               created_at  TEXT NOT NULL DEFAULT (datetime('now')),
               PRIMARY KEY (marker, seq)
           )"""
    )


def _write_state(state_file: Path, payload: dict[str, Any]) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(
        json.dumps(payload, ensure_ascii=True, sort_keys=True),
        encoding="utf-8",
    )


def _insert_rows(
    connection: sqlite3.Connection,
    *,
    marker: str,
    rows: int,
    payload_bytes: int,
) -> None:
    payload_seed = "x" * max(16, int(payload_bytes))
    for sequence in range(max(1, int(rows))):
        payload = json.dumps(
            {
                "marker": marker,
                "sequence": sequence,
                "payload": payload_seed,
            },
            ensure_ascii=True,
            sort_keys=True,
        )
        connection.execute(
            """INSERT INTO durability_probe (marker, seq, payload, phase)
               VALUES (?, ?, ?, 'power-loss-target')""",
            (marker, sequence, payload),
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Internal helper for power-loss durability drill. Starts a transaction and "
            "waits for parent process to hard-abort this process before or after commit."
        )
    )
    parser.add_argument("--database-path", required=True)
    parser.add_argument("--state-file", required=True)
    parser.add_argument(
        "--mode",
        required=True,
        choices=("abort-before-commit", "abort-after-commit"),
    )
    parser.add_argument("--transaction-label", required=True)
    parser.add_argument("--rows", type=int, default=120)
    parser.add_argument("--payload-bytes", type=int, default=256)
    args = parser.parse_args(argv)

    if int(args.rows) <= 0:
        print("[power-loss-durability-target] ERROR: --rows must be > 0.", file=sys.stderr)
        return 2
    if int(args.payload_bytes) <= 0:
        print("[power-loss-durability-target] ERROR: --payload-bytes must be > 0.", file=sys.stderr)
        return 2

    database_path = Path(str(args.database_path)).expanduser()
    state_file = Path(str(args.state_file)).expanduser()
    label = str(args.transaction_label)
    mode = str(args.mode)

    connection: sqlite3.Connection | None = None
    try:
        connection = _connect(database_path)
        _ensure_probe_table(connection)
        connection.execute("BEGIN IMMEDIATE")
        _insert_rows(
            connection,
            marker=label,
            rows=int(args.rows),
            payload_bytes=int(args.payload_bytes),
        )

        if mode == "abort-before-commit":
            _write_state(
                state_file,
                {
                    "status": "pending_commit",
                    "mode": mode,
                    "label": label,
                    "rows_requested": int(args.rows),
                    "pid": os.getpid(),
                    "timestamp_utc": _utc_iso(),
                },
            )
            while True:
                time.sleep(1.0)

        connection.commit()
        persisted_rows = int(
            connection.execute(
                "SELECT COUNT(*) FROM durability_probe WHERE marker = ?",
                (label,),
            ).fetchone()[0]
        )
        _write_state(
            state_file,
            {
                "status": "committed",
                "mode": mode,
                "label": label,
                "rows_requested": int(args.rows),
                "persisted_rows": persisted_rows,
                "pid": os.getpid(),
                "timestamp_utc": _utc_iso(),
            },
        )
        while True:
            time.sleep(1.0)
    except Exception as exc:
        try:
            _write_state(
                state_file,
                {
                    "status": "error",
                    "mode": mode,
                    "label": label,
                    "error": str(exc),
                    "pid": os.getpid(),
                    "timestamp_utc": _utc_iso(),
                },
            )
        except Exception:
            pass
        print(f"[power-loss-durability-target] ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        if connection is not None:
            connection.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
