import os
from dataclasses import dataclass

SPEC_VERSION = "1.4.4"

# Skill Selection
MIN_RUNS = 5
DEFAULT_BASELINE_SCORE = 0.60
RECENCY_DECAY_HALFLIFE_DAYS = 30.0

# Scheduling
MAX_WAIT_CYCLES = 10
AGING_FACTOR_PER_CYCLE = 0.05
AGING_MAX_MULTIPLIER = 0.5

# Retry / Backoff
BACKOFF_BASE_SECONDS = 2
BACKOFF_MAX_SECONDS = 60
RETRY_STRATEGY = {
    "SkillFailure": {"max": 2, "action": "switch_skill"},
    "ExecutionFailure": {"max": 3, "action": "retry_same"},
    "ExternalFailure": {"max": 5, "action": "backoff_retry"},
    "PlanFailure": {"max": 1, "action": "replan_hitl"},
}

# Optimistic Locking
OL_MAX_RETRIES = 3
OL_BASE_BACKOFF_MS = 10
MAX_TOTAL_RETRIES_PER_CYCLE = 100

# Backpressure
MAX_PENDING_EVENTS = 1_000
MAX_GOAL_QUEUE_ENTRIES = 500
MAX_CONSUMER_DRAIN_BATCH_SIZE = 500
BACKPRESSURE_RETRY_AFTER_SECONDS = 10

# Event Processing
PROCESSING_TIMEOUT_SECONDS = 30
CONSUMER_BATCH_SIZE = 50
CONSUMER_POLL_INTERVAL_SECONDS = 1

# Systemic Failure Detection
SYSTEMIC_FAILURE_WINDOW_SECONDS = 60
SYSTEMIC_FAILURE_THRESHOLD = 20
ERROR_HASH_ESCALATION_WINDOW_MINUTES = 30

# Maintenance
EPHEMERAL_CLEANUP_INTERVAL_SECONDS = 300
EPHEMERAL_MAX_ROWS = 10_000
EVENTS_RETENTION_DAYS = 30
EVENT_PROCESSING_RETENTION_DAYS = 30
FAILURE_LOG_RETENTION_DAYS = 90
WORKFLOW_RUN_TIMEOUT_SECONDS = 300
WORKFLOW_REAPER_BATCH_SIZE = 200

# The sandbox in this workspace rejects file-backed SQLite locks, so the
# persistent `goal_ops.db` path should be supplied via GOAL_OPS_DATABASE_URL.
DEFAULT_DATABASE_URL = os.getenv("GOAL_OPS_DATABASE_URL", ":memory:")
DEFAULT_CONSUMER_ID = "goal_ops_console"

GOAL_QUEUE_STATUS_MAP = {
    "draft": "queued",
    "active": "active",
    "blocked": "blocked",
    "escalation_pending": "blocked",
    "completed": "done",
}


@dataclass(slots=True)
class Settings:
    database_url: str = DEFAULT_DATABASE_URL
    consumer_id: str = DEFAULT_CONSUMER_ID
    max_pending_events: int = MAX_PENDING_EVENTS
    max_goal_queue_entries: int = MAX_GOAL_QUEUE_ENTRIES
    max_consumer_drain_batch_size: int = MAX_CONSUMER_DRAIN_BATCH_SIZE
    backpressure_retry_after_seconds: int = BACKPRESSURE_RETRY_AFTER_SECONDS
    events_retention_days: int = EVENTS_RETENTION_DAYS
    event_processing_retention_days: int = EVENT_PROCESSING_RETENTION_DAYS
    failure_log_retention_days: int = FAILURE_LOG_RETENTION_DAYS
    workflow_run_timeout_seconds: int = WORKFLOW_RUN_TIMEOUT_SECONDS
    workflow_reaper_batch_size: int = WORKFLOW_REAPER_BATCH_SIZE
