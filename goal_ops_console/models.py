from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class GoalState(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    BLOCKED = "blocked"
    ESCALATION_PENDING = "escalation_pending"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ARCHIVED = "archived"


class TaskState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    FAILED = "failed"
    SUCCEEDED = "succeeded"
    EXHAUSTED = "exhausted"
    POISON = "poison"


class FailureType(StrEnum):
    SKILL = "SkillFailure"
    EXECUTION = "ExecutionFailure"
    EXTERNAL = "ExternalFailure"
    PLAN = "PlanFailure"


class DomainError(Exception):
    status_code = 400

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class NotFoundError(DomainError):
    status_code = 404


class ConflictError(DomainError):
    status_code = 409


class OptimisticLockError(ConflictError):
    pass


class RetryBudgetExceeded(DomainError):
    status_code = 429


class BackpressureError(DomainError):
    status_code = 429

    def __init__(self, message: str, *, retry_after_seconds: int):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class GoalCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1_000)
    urgency: float = Field(default=0.5, ge=0.0, le=1.0)
    value: float = Field(default=0.5, ge=0.0, le=1.0)
    deadline_score: float = Field(default=0.0, ge=0.0, le=1.0)


class TaskCreateRequest(BaseModel):
    goal_id: str
    title: str = Field(min_length=1, max_length=200)


class TaskFailureRequest(BaseModel):
    failure_type: FailureType = FailureType.SKILL
    error_message: str = Field(default="Simulated failure", min_length=1, max_length=500)


class FaultRemediationRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)
    dry_run: bool = False


class FaultBulkResolveRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)
    dry_run: bool = False
    failure_type: FailureType | None = None
    failure_status: str | None = Field(default=None, max_length=64)
    task_status: TaskState | None = None
    goal_id: str | None = Field(default=None, max_length=200)
    error_hash: str | None = Field(default=None, max_length=128)
    dead_letter_only: bool = True
    limit: int = Field(default=50, ge=1, le=500)


class EventResponse(BaseModel):
    seq: int
    event_id: str
    event_type: str
    entity_id: str
    correlation_id: str
    payload: dict[str, Any] | None
    emitted_at: str


class WorkflowStartRequest(BaseModel):
    requested_by: str = Field(default="operator", min_length=1, max_length=120)
    payload: dict[str, Any] = Field(default_factory=dict)


class WorkflowCancelRequest(BaseModel):
    requested_by: str = Field(default="operator", min_length=1, max_length=120)
    reason: str | None = Field(default=None, max_length=500)


class SafeModeToggleRequest(BaseModel):
    reason: str = Field(default="Operator override", min_length=3, max_length=500)
