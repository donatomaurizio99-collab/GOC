import os
from dataclasses import dataclass

SPEC_VERSION = "1.4.6"


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default

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
AUDIT_LOG_RETENTION_DAYS = _env_int("GOAL_OPS_AUDIT_LOG_RETENTION_DAYS", 365)
WORKFLOW_RUN_TIMEOUT_SECONDS = 300
WORKFLOW_REAPER_BATCH_SIZE = 200
WORKFLOW_WORKER_POLL_INTERVAL_SECONDS = 0.5
WORKFLOW_STARTUP_RECOVERY_MAX_AGE_SECONDS = _env_int(
    "GOAL_OPS_WORKFLOW_STARTUP_RECOVERY_MAX_AGE_SECONDS",
    0,
)
DIAGNOSTICS_DIR = os.getenv("GOAL_OPS_DIAGNOSTICS_DIR", "")
DB_MIGRATION_BACKUP_DIR = os.getenv("GOAL_OPS_DB_MIGRATION_BACKUP_DIR", "")
DB_QUARANTINE_DIR = os.getenv("GOAL_OPS_DB_QUARANTINE_DIR", "")
DB_STARTUP_CORRUPTION_RECOVERY_ENABLED = _env_bool(
    "GOAL_OPS_DB_STARTUP_CORRUPTION_RECOVERY_ENABLED",
    True,
)

# SLO / Alerting
SLO_MIN_HTTP_REQUEST_SAMPLE = _env_int("GOAL_OPS_SLO_MIN_HTTP_REQUEST_SAMPLE", 20)
SLO_MIN_EVENT_ATTEMPT_SAMPLE = _env_int("GOAL_OPS_SLO_MIN_EVENT_ATTEMPT_SAMPLE", 20)
SLO_MIN_HTTP_SUCCESS_RATE_PERCENT = _env_float("GOAL_OPS_SLO_MIN_HTTP_SUCCESS_RATE_PERCENT", 99.0)
SLO_MAX_HTTP_429_RATE_PERCENT = _env_float("GOAL_OPS_SLO_MAX_HTTP_429_RATE_PERCENT", 5.0)
SLO_MAX_EVENT_FAILURE_RATE_PERCENT = _env_float("GOAL_OPS_SLO_MAX_EVENT_FAILURE_RATE_PERCENT", 5.0)
SLO_MAX_BACKLOG_UTILIZATION_PERCENT = _env_float(
    "GOAL_OPS_SLO_MAX_BACKLOG_UTILIZATION_PERCENT",
    90.0,
)
SLO_MAX_STUCK_EVENTS = _env_int("GOAL_OPS_SLO_MAX_STUCK_EVENTS", 0)

# Runtime Stability Guards
INVARIANT_MONITOR_INTERVAL_SECONDS = _env_int(
    "GOAL_OPS_INVARIANT_MONITOR_INTERVAL_SECONDS",
    30,
)
INVARIANT_MONITOR_AUTO_SAFE_MODE = _env_bool(
    "GOAL_OPS_INVARIANT_MONITOR_AUTO_SAFE_MODE",
    False,
)
SAFE_MODE_LOCK_ERROR_THRESHOLD = _env_int(
    "GOAL_OPS_SAFE_MODE_LOCK_ERROR_THRESHOLD",
    6,
)
SAFE_MODE_LOCK_ERROR_WINDOW_SECONDS = _env_int(
    "GOAL_OPS_SAFE_MODE_LOCK_ERROR_WINDOW_SECONDS",
    60,
)
SAFE_MODE_IO_ERROR_THRESHOLD = _env_int(
    "GOAL_OPS_SAFE_MODE_IO_ERROR_THRESHOLD",
    2,
)
SAFE_MODE_IO_ERROR_WINDOW_SECONDS = _env_int(
    "GOAL_OPS_SAFE_MODE_IO_ERROR_WINDOW_SECONDS",
    120,
)
SAFE_MODE_AUTO_DISABLE_AFTER_SECONDS = _env_int(
    "GOAL_OPS_SAFE_MODE_AUTO_DISABLE_AFTER_SECONDS",
    0,
)
IDEMPOTENCY_RETENTION_DAYS = _env_int(
    "GOAL_OPS_IDEMPOTENCY_RETENTION_DAYS",
    14,
)
OPERATOR_AUTH_REQUIRED = _env_bool(
    "GOAL_OPS_OPERATOR_AUTH_REQUIRED",
    False,
)
OPERATOR_AUTH_TOKEN = os.getenv("GOAL_OPS_OPERATOR_AUTH_TOKEN", "")
OPERATOR_AUTH_TOKEN_MIN_LENGTH = _env_int(
    "GOAL_OPS_OPERATOR_AUTH_TOKEN_MIN_LENGTH",
    16,
)

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
    audit_log_retention_days: int = AUDIT_LOG_RETENTION_DAYS
    workflow_run_timeout_seconds: int = WORKFLOW_RUN_TIMEOUT_SECONDS
    workflow_reaper_batch_size: int = WORKFLOW_REAPER_BATCH_SIZE
    workflow_worker_poll_interval_seconds: float = WORKFLOW_WORKER_POLL_INTERVAL_SECONDS
    workflow_startup_recovery_max_age_seconds: int = WORKFLOW_STARTUP_RECOVERY_MAX_AGE_SECONDS
    diagnostics_dir: str = DIAGNOSTICS_DIR
    db_migration_backup_dir: str = DB_MIGRATION_BACKUP_DIR
    db_quarantine_dir: str = DB_QUARANTINE_DIR
    db_startup_corruption_recovery_enabled: bool = DB_STARTUP_CORRUPTION_RECOVERY_ENABLED
    slo_min_http_request_sample: int = SLO_MIN_HTTP_REQUEST_SAMPLE
    slo_min_event_attempt_sample: int = SLO_MIN_EVENT_ATTEMPT_SAMPLE
    slo_min_http_success_rate_percent: float = SLO_MIN_HTTP_SUCCESS_RATE_PERCENT
    slo_max_http_429_rate_percent: float = SLO_MAX_HTTP_429_RATE_PERCENT
    slo_max_event_failure_rate_percent: float = SLO_MAX_EVENT_FAILURE_RATE_PERCENT
    slo_max_backlog_utilization_percent: float = SLO_MAX_BACKLOG_UTILIZATION_PERCENT
    slo_max_stuck_events: int = SLO_MAX_STUCK_EVENTS
    invariant_monitor_interval_seconds: int = INVARIANT_MONITOR_INTERVAL_SECONDS
    invariant_monitor_auto_safe_mode: bool = INVARIANT_MONITOR_AUTO_SAFE_MODE
    safe_mode_lock_error_threshold: int = SAFE_MODE_LOCK_ERROR_THRESHOLD
    safe_mode_lock_error_window_seconds: int = SAFE_MODE_LOCK_ERROR_WINDOW_SECONDS
    safe_mode_io_error_threshold: int = SAFE_MODE_IO_ERROR_THRESHOLD
    safe_mode_io_error_window_seconds: int = SAFE_MODE_IO_ERROR_WINDOW_SECONDS
    safe_mode_auto_disable_after_seconds: int = SAFE_MODE_AUTO_DISABLE_AFTER_SECONDS
    idempotency_retention_days: int = IDEMPOTENCY_RETENTION_DAYS
    operator_auth_required: bool = OPERATOR_AUTH_REQUIRED
    operator_auth_token: str = OPERATOR_AUTH_TOKEN
    operator_auth_token_min_length: int = OPERATOR_AUTH_TOKEN_MIN_LENGTH
