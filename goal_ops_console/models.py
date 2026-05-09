from enum import StrEnum
from typing import Any, Literal

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


class PlannerTaskSuggestion(BaseModel):
    title: str
    description: str
    rationale: str
    priority_hint: str
    source: str
    task_exists: bool = False
    existing_task_id: str | None = None
    review_decision: Literal["pending", "created", "deferred", "rejected"] = "pending"
    review_comment: str | None = None
    review_task_id: str | None = None
    reviewed_at: str | None = None


class PlannerPreviewResponse(BaseModel):
    goal_id: str
    goal_title: str
    source: str
    suggestions: list[PlannerTaskSuggestion]


class PlannerTaskSuggestionOverride(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1_000)
    priority_hint: Literal["low", "medium", "high"] | None = None


class PlannerTaskCreateRequest(BaseModel):
    suggestion_index: int = Field(ge=0)
    override: PlannerTaskSuggestionOverride | None = None


class PlannerBulkTaskCreateRequest(BaseModel):
    suggestion_indexes: list[int] = Field(min_length=1, max_length=5)
    overrides: dict[int, PlannerTaskSuggestionOverride] = Field(default_factory=dict)


class PlannerSuggestionReview(BaseModel):
    goal_id: str
    suggestion_index: int
    decision: Literal["created", "deferred", "rejected"]
    comment: str | None = None
    task_id: str | None = None
    planner_source: str
    suggestion_title: str
    suggestion_description: str
    suggestion_rationale: str
    suggestion_priority_hint: str
    operator_override: dict[str, Any] | None = None
    created_at: str
    updated_at: str


class PlannerReviewDecisionRequest(BaseModel):
    suggestion_index: int = Field(ge=0)
    decision: Literal["deferred", "rejected"]
    comment: str | None = Field(default=None, max_length=500)


class PlannerReviewDecisionResponse(BaseModel):
    goal_id: str
    suggestion_index: int
    suggestion: PlannerTaskSuggestion
    review: PlannerSuggestionReview


class PlannerBulkReviewDecisionRequest(BaseModel):
    suggestion_indexes: list[int] = Field(min_length=1, max_length=5)
    decision: Literal["deferred", "rejected"]
    comment: str | None = Field(default=None, max_length=500)


class PlannerBulkReviewDecisionResponse(BaseModel):
    goal_id: str
    requested_suggestion_indexes: list[int]
    decision: Literal["deferred", "rejected"]
    suggestions: list[PlannerTaskSuggestion]
    reviews: list[PlannerSuggestionReview]


class PlannerReviewReopenResponse(BaseModel):
    goal_id: str
    suggestion_index: int
    suggestion: PlannerTaskSuggestion
    cleared_review: PlannerSuggestionReview


class PlannerReviewSummary(BaseModel):
    total_suggestions: int
    pending: int
    created: int
    deferred: int
    rejected: int


class PlannerReviewListResponse(BaseModel):
    goal_id: str
    goal_title: str
    source: str
    summary: PlannerReviewSummary
    reviews: list[PlannerSuggestionReview]


class PlannerReviewAuditEntry(BaseModel):
    seq: int
    event_id: str
    event_type: Literal["planner.suggestion_reviewed", "planner.suggestion_review_reopened"]
    action: Literal["reviewed", "reopened"]
    goal_id: str
    suggestion_index: int
    suggestion_title: str
    decision: Literal["created", "deferred", "rejected"] | None = None
    cleared_decision: Literal["created", "deferred", "rejected"] | None = None
    comment: str | None = None
    cleared_comment: str | None = None
    task_id: str | None = None
    source: str
    emitted_at: str


class PlannerReviewAuditResponse(BaseModel):
    goal_id: str
    goal_title: str
    source: str
    summary: PlannerReviewSummary
    entries: list[PlannerReviewAuditEntry]


class PlannerHandoffSuggestionItem(BaseModel):
    suggestion_index: int
    title: str
    description: str
    rationale: str
    priority_hint: str
    source: str
    comment: str | None = None
    reviewed_at: str | None = None


class PlannerHandoffCreatedTaskItem(PlannerHandoffSuggestionItem):
    task_id: str
    task_title: str
    task_status: str
    operator_override: dict[str, Any] | None = None


class PlannerReviewHandoffResponse(BaseModel):
    goal_id: str
    goal_title: str
    source: str
    summary: PlannerReviewSummary
    next_operator_action: str
    created_tasks: list[PlannerHandoffCreatedTaskItem]
    deferred_suggestions: list[PlannerHandoffSuggestionItem]
    rejected_suggestions: list[PlannerHandoffSuggestionItem]
    pending_suggestions: list[PlannerHandoffSuggestionItem]


class PlannerGlobalHandoffFollowUpAction(BaseModel):
    id: Literal[
        "review_pending_suggestion",
        "resolve_deferred_followup",
        "monitor_created_tasks",
        "no_action_required",
    ]
    label: str
    description: str
    action_type: Literal["open_plan_preview", "select_goal_tasks", "none"]
    target: dict[str, Any] = Field(default_factory=dict)
    mutates: bool = False


class PlannerGlobalHandoffItem(BaseModel):
    goal_id: str
    goal_title: str
    state: str
    source: str
    next_operator_action: str
    needs_operator_attention: bool
    attention_reason: Literal[
        "pending_review",
        "deferred_followup",
        "created_task_not_terminal",
        "ready",
    ]
    summary: PlannerReviewSummary
    pending: int
    deferred: int
    rejected: int
    created: int
    last_reviewed_at: str | None = None
    next_pending_suggestion: PlannerHandoffSuggestionItem | None = None
    latest_deferred_suggestion: PlannerHandoffSuggestionItem | None = None
    created_task_statuses: dict[str, int] = Field(default_factory=dict)
    created_tasks_preview: list[PlannerHandoffCreatedTaskItem] = Field(default_factory=list)
    follow_up_actions: list[PlannerGlobalHandoffFollowUpAction] = Field(default_factory=list)


class PlannerGlobalHandoffSummary(BaseModel):
    total_goals: int
    goals_needing_attention: int
    pending: int
    deferred: int
    rejected: int
    created: int


class PlannerGlobalHandoffResponse(BaseModel):
    summary: PlannerGlobalHandoffSummary
    items: list[PlannerGlobalHandoffItem]


class PlannerReviewInboxNextSuggestion(BaseModel):
    suggestion_index: int
    title: str
    description: str
    rationale: str
    priority_hint: str
    source: str


class PlannerReviewInboxItem(BaseModel):
    goal_id: str
    goal_title: str
    state: str
    source: str
    summary: PlannerReviewSummary
    last_reviewed_at: str | None = None
    needs_review: bool
    next_suggestion: PlannerReviewInboxNextSuggestion | None = None


class PlannerReviewInboxSummary(BaseModel):
    total_goals: int
    goals_needing_review: int
    pending_suggestions: int
    created: int
    deferred: int
    rejected: int


class PlannerReviewInboxResponse(BaseModel):
    summary: PlannerReviewInboxSummary
    items: list[PlannerReviewInboxItem]


class PlannerDeferredFollowupItem(BaseModel):
    goal_id: str
    goal_title: str
    state: str
    source: str
    suggestion_index: int
    suggestion_title: str
    suggestion_description: str
    suggestion_rationale: str
    priority_hint: str
    comment: str | None = None
    deferred_at: str


class PlannerDeferredFollowupSummary(BaseModel):
    total_followups: int
    goals_with_followups: int


class PlannerDeferredFollowupResponse(BaseModel):
    summary: PlannerDeferredFollowupSummary
    items: list[PlannerDeferredFollowupItem]


class PlannerTaskCreateResponse(BaseModel):
    goal_id: str
    suggestion_index: int
    suggestion: PlannerTaskSuggestion
    applied_suggestion: PlannerTaskSuggestion
    operator_override: dict[str, Any] | None = None
    review: PlannerSuggestionReview | None = None
    task: dict[str, Any]


class PlannerBulkTaskCreateResponse(BaseModel):
    goal_id: str
    requested_suggestion_indexes: list[int]
    created: list[dict[str, Any]]
    skipped_duplicates: list[dict[str, Any]]


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
