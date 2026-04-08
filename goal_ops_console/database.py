import contextlib
import sqlite3
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path


def now_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


def new_id() -> str:
    return str(uuid.uuid4())


SCHEMA = """
CREATE TABLE IF NOT EXISTS goals (
  goal_id            TEXT PRIMARY KEY,
  title              TEXT NOT NULL,
  description        TEXT,
  state              TEXT NOT NULL,
  blocked_reason     TEXT,
  escalation_reason  TEXT,
  version            INTEGER NOT NULL DEFAULT 1,
  created_at         TEXT NOT NULL,
  updated_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_goals_state ON goals(state);

CREATE TABLE IF NOT EXISTS tasks (
  task_id        TEXT PRIMARY KEY,
  goal_id        TEXT NOT NULL,
  title          TEXT NOT NULL,
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL,
  FOREIGN KEY(goal_id) REFERENCES goals(goal_id)
);
CREATE INDEX IF NOT EXISTS idx_tasks_goal_id ON tasks(goal_id);

CREATE TABLE IF NOT EXISTS skill_metrics (
  skill_id      TEXT PRIMARY KEY,
  run_count     INTEGER DEFAULT 0,
  success_count INTEGER DEFAULT 0,
  last_used_at  TEXT,
  version       INTEGER NOT NULL DEFAULT 1,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS goal_queue (
  goal_id        TEXT PRIMARY KEY,
  urgency        REAL NOT NULL DEFAULT 0.0,
  value          REAL NOT NULL DEFAULT 0.0,
  deadline_score REAL NOT NULL DEFAULT 0.0,
  base_priority  REAL NOT NULL DEFAULT 0.0,
  priority       REAL NOT NULL DEFAULT 0.0,
  wait_cycles    INTEGER NOT NULL DEFAULT 0,
  force_promoted INTEGER NOT NULL DEFAULT 0,
  status         TEXT NOT NULL,
  version        INTEGER NOT NULL DEFAULT 1,
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL,
  FOREIGN KEY(goal_id) REFERENCES goals(goal_id)
);

CREATE TABLE IF NOT EXISTS task_state (
  task_id        TEXT PRIMARY KEY,
  goal_id        TEXT NOT NULL,
  correlation_id TEXT NOT NULL,
  status         TEXT NOT NULL,
  retry_count    INTEGER DEFAULT 0,
  failure_type   TEXT,
  error_hash     TEXT,
  version        INTEGER NOT NULL DEFAULT 1,
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL,
  FOREIGN KEY(task_id) REFERENCES tasks(task_id),
  FOREIGN KEY(goal_id) REFERENCES goals(goal_id)
);

CREATE TABLE IF NOT EXISTS failure_log (
  id             TEXT PRIMARY KEY,
  task_id        TEXT NOT NULL,
  goal_id        TEXT NOT NULL,
  correlation_id TEXT NOT NULL,
  failure_type   TEXT NOT NULL,
  fingerprint    TEXT,
  retry_count    INTEGER DEFAULT 0,
  last_error     TEXT,
  status         TEXT NOT NULL,
  version        INTEGER NOT NULL DEFAULT 1,
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
  seq            INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id       TEXT NOT NULL UNIQUE,
  event_type     TEXT NOT NULL,
  entity_id      TEXT NOT NULL,
  correlation_id TEXT NOT NULL,
  payload        TEXT,
  emitted_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_entity_seq ON events(entity_id, seq);
CREATE INDEX IF NOT EXISTS idx_events_correlation ON events(correlation_id, seq);

CREATE TABLE IF NOT EXISTS event_processing (
  event_id              TEXT NOT NULL,
  consumer_id           TEXT NOT NULL,
  status                TEXT NOT NULL DEFAULT 'pending',
  processing_started_at TEXT,
  processed_at          TEXT,
  version               INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY (event_id, consumer_id)
);

CREATE TABLE IF NOT EXISTS ephemeral_state (
  key        TEXT PRIMARY KEY,
  value      TEXT NOT NULL,
  expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS permission_state (
  category   TEXT NOT NULL,
  key        TEXT NOT NULL,
  value      TEXT NOT NULL,
  granted_by TEXT NOT NULL,
  version    INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (category, key)
);

CREATE TABLE IF NOT EXISTS plans (
  plan_id          TEXT PRIMARY KEY,
  goal_id          TEXT NOT NULL,
  content          TEXT NOT NULL,
  similarity_score REAL,
  reuse_candidate  INTEGER DEFAULT 0,
  version          INTEGER NOT NULL DEFAULT 1,
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS learnings (
  learning_id        TEXT PRIMARY KEY,
  version            INTEGER NOT NULL DEFAULT 1,
  parent_learning_id TEXT,
  tier               INTEGER NOT NULL,
  status             TEXT NOT NULL,
  content            TEXT NOT NULL,
  source_goal        TEXT,
  expires_at         TEXT,
  created_at         TEXT NOT NULL,
  updated_at         TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_learning_active_version
ON learnings(parent_learning_id)
WHERE status = 'promoted';

CREATE TABLE IF NOT EXISTS audit_log (
  audit_id        TEXT PRIMARY KEY,
  action          TEXT NOT NULL,
  actor           TEXT NOT NULL,
  status          TEXT NOT NULL,
  entity_type     TEXT,
  entity_id       TEXT,
  correlation_id  TEXT,
  details         TEXT,
  created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_log_created_at ON audit_log(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_log_action_created_at ON audit_log(action, created_at DESC);

CREATE TABLE IF NOT EXISTS metrics_counters (
  metric_name TEXT PRIMARY KEY,
  value       INTEGER NOT NULL DEFAULT 0,
  updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_metrics_updated_at ON metrics_counters(updated_at DESC);
"""


class Transaction:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def execute(self, sql: str, *params: object) -> int:
        cursor = self.conn.execute(sql, params)
        return cursor.rowcount

    def fetch_one(self, sql: str, *params: object) -> sqlite3.Row | None:
        return self.conn.execute(sql, params).fetchone()

    def fetch_all(self, sql: str, *params: object) -> list[sqlite3.Row]:
        return list(self.conn.execute(sql, params).fetchall())

    def fetch_scalar(self, sql: str, *params: object) -> object | None:
        row = self.conn.execute(sql, params).fetchone()
        if row is None:
            return None
        return row[0]


class Database:
    def __init__(self, database_url: str):
        self.original_url = database_url
        self.database_url = self._normalize_database_url(database_url)
        self._uri = self.database_url.startswith("file:")
        self._keeper = None
        if "mode=memory" in self.database_url:
            self._keeper = self._connect()

    def _normalize_database_url(self, database_url: str) -> str:
        if database_url == ":memory:":
            return f"file:goal_ops_{uuid.uuid4().hex}?mode=memory&cache=shared"
        return str(Path(database_url)) if not database_url.startswith("file:") else database_url

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self.database_url,
            uri=self._uri,
            check_same_thread=False,
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def initialize(self) -> None:
        conn = self._keeper or self._connect()
        try:
            conn.executescript(SCHEMA)
        finally:
            if conn is not self._keeper:
                conn.close()

    def execute(self, sql: str, *params: object) -> int:
        conn = self._connect()
        try:
            cursor = conn.execute(sql, params)
            return cursor.rowcount
        finally:
            conn.close()

    def fetch_one(self, sql: str, *params: object) -> sqlite3.Row | None:
        conn = self._connect()
        try:
            return conn.execute(sql, params).fetchone()
        finally:
            conn.close()

    def fetch_all(self, sql: str, *params: object) -> list[sqlite3.Row]:
        conn = self._connect()
        try:
            return list(conn.execute(sql, params).fetchall())
        finally:
            conn.close()

    def fetch_scalar(self, sql: str, *params: object) -> object | None:
        row = self.fetch_one(sql, *params)
        if row is None:
            return None
        return row[0]

    @contextlib.contextmanager
    def transaction(self) -> Iterator[Transaction]:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            tx = Transaction(conn)
            yield tx
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
