from __future__ import annotations

import time
from collections import deque
from threading import Lock
from typing import TYPE_CHECKING
from typing import Any

from goal_ops_console.database import now_utc

if TYPE_CHECKING:
    from goal_ops_console.observability import ObservabilityService

MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class RuntimeGuard:
    def __init__(
        self,
        *,
        lock_error_threshold: int,
        lock_error_window_seconds: int,
        io_error_threshold: int,
        io_error_window_seconds: int,
        auto_disable_after_seconds: int,
        observability: "ObservabilityService | None" = None,
    ):
        self.lock_error_threshold = max(1, int(lock_error_threshold))
        self.lock_error_window_seconds = max(1, int(lock_error_window_seconds))
        self.io_error_threshold = max(1, int(io_error_threshold))
        self.io_error_window_seconds = max(1, int(io_error_window_seconds))
        self.auto_disable_after_seconds = max(0, int(auto_disable_after_seconds))
        self.observability = observability

        self._lock = Lock()
        self._lock_errors = deque()
        self._io_errors = deque()
        self._safe_mode_active = False
        self._safe_mode_reason: str | None = None
        self._safe_mode_source: str | None = None
        self._safe_mode_activated_at_utc: str | None = None
        self._safe_mode_activated_monotonic: float | None = None
        self._safe_mode_auto = False

    def _prune_locked(self, now_monotonic: float) -> None:
        lock_cutoff = now_monotonic - float(self.lock_error_window_seconds)
        io_cutoff = now_monotonic - float(self.io_error_window_seconds)
        while self._lock_errors and self._lock_errors[0] < lock_cutoff:
            self._lock_errors.popleft()
        while self._io_errors and self._io_errors[0] < io_cutoff:
            self._io_errors.popleft()

    def _auto_disable_if_due_locked(self) -> None:
        if (
            not self._safe_mode_active
            or self.auto_disable_after_seconds <= 0
            or self._safe_mode_activated_monotonic is None
        ):
            return
        elapsed = time.monotonic() - self._safe_mode_activated_monotonic
        if elapsed < float(self.auto_disable_after_seconds):
            return
        self._safe_mode_active = False
        self._safe_mode_reason = "Auto-disabled after safety cooldown window."
        self._safe_mode_source = "runtime_guard"
        self._safe_mode_auto = True
        self._safe_mode_activated_at_utc = None
        self._safe_mode_activated_monotonic = None
        self._metric("runtime.safe_mode.auto_disabled")
        self._audit(
            action="runtime.safe_mode.auto_disable",
            status="success",
            details={"auto_disable_after_seconds": self.auto_disable_after_seconds},
        )

    def safe_mode_snapshot(self) -> dict[str, Any]:
        with self._lock:
            self._auto_disable_if_due_locked()
            now_monotonic = time.monotonic()
            self._prune_locked(now_monotonic)
            return {
                "active": bool(self._safe_mode_active),
                "reason": self._safe_mode_reason,
                "source": self._safe_mode_source,
                "auto": bool(self._safe_mode_auto),
                "activated_at_utc": self._safe_mode_activated_at_utc,
                "error_counters": {
                    "lock_errors_in_window": len(self._lock_errors),
                    "lock_error_window_seconds": self.lock_error_window_seconds,
                    "io_errors_in_window": len(self._io_errors),
                    "io_error_window_seconds": self.io_error_window_seconds,
                },
                "thresholds": {
                    "lock_error_threshold": self.lock_error_threshold,
                    "io_error_threshold": self.io_error_threshold,
                    "auto_disable_after_seconds": self.auto_disable_after_seconds,
                },
            }

    def is_safe_mode_active(self) -> bool:
        return bool(self.safe_mode_snapshot()["active"])

    def activate_safe_mode(
        self,
        *,
        reason: str,
        source: str,
        auto: bool = True,
    ) -> dict[str, Any]:
        normalized_reason = reason.strip() or "Safe mode activated."
        normalized_source = source.strip() or "system"
        with self._lock:
            self._safe_mode_active = True
            self._safe_mode_reason = normalized_reason
            self._safe_mode_source = normalized_source
            self._safe_mode_auto = bool(auto)
            self._safe_mode_activated_at_utc = now_utc()
            self._safe_mode_activated_monotonic = time.monotonic()
        self._metric("runtime.safe_mode.activated")
        self._audit(
            action="runtime.safe_mode.activate",
            status="error",
            details={
                "reason": normalized_reason,
                "source": normalized_source,
                "auto": bool(auto),
            },
        )
        return self.safe_mode_snapshot()

    def deactivate_safe_mode(
        self,
        *,
        reason: str,
        source: str = "operator",
    ) -> dict[str, Any]:
        normalized_reason = reason.strip() or "Safe mode disabled."
        normalized_source = source.strip() or "operator"
        with self._lock:
            self._safe_mode_active = False
            self._safe_mode_reason = normalized_reason
            self._safe_mode_source = normalized_source
            self._safe_mode_auto = False
            self._safe_mode_activated_at_utc = None
            self._safe_mode_activated_monotonic = None
        self._metric("runtime.safe_mode.deactivated")
        self._audit(
            action="runtime.safe_mode.deactivate",
            status="success",
            details={"reason": normalized_reason, "source": normalized_source},
        )
        return self.safe_mode_snapshot()

    def should_block_mutation(self, *, method: str, path: str) -> bool:
        if method.upper() not in MUTATING_METHODS:
            return False
        if not self.is_safe_mode_active():
            return False
        normalized = path.strip()
        if normalized == "/system/diagnostics":
            return False
        if normalized.startswith("/system/safe-mode/"):
            return False
        if normalized.startswith("/system/consumers/") and (
            normalized.endswith("/drain") or normalized.endswith("/reclaim")
        ):
            return False
        return True

    def record_database_error(self, *, message: str, source: str = "api") -> dict[str, Any]:
        text = message.strip().lower()
        now_monotonic = time.monotonic()
        category = None
        with self._lock:
            self._auto_disable_if_due_locked()
            if (
                "database is locked" in text
                or "database table is locked" in text
                or "database schema is locked" in text
            ):
                self._lock_errors.append(now_monotonic)
                category = "lock"
            elif (
                "disk i/o error" in text
                or "database or disk is full" in text
                or "i/o error" in text
                or "readonly database" in text
                or "read-only file system" in text
            ):
                self._io_errors.append(now_monotonic)
                category = "io"

            self._prune_locked(now_monotonic)
            lock_count = len(self._lock_errors)
            io_count = len(self._io_errors)

        if category == "lock":
            self._metric("runtime.db_errors.lock")
        elif category == "io":
            self._metric("runtime.db_errors.io")
        else:
            self._metric("runtime.db_errors.other")
            return self.safe_mode_snapshot()

        should_activate = (
            (category == "lock" and lock_count >= self.lock_error_threshold)
            or (category == "io" and io_count >= self.io_error_threshold)
        )
        if should_activate and not self.is_safe_mode_active():
            threshold = (
                self.lock_error_threshold if category == "lock" else self.io_error_threshold
            )
            window_seconds = (
                self.lock_error_window_seconds if category == "lock" else self.io_error_window_seconds
            )
            self.activate_safe_mode(
                reason=(
                    f"Detected {category} database error burst "
                    f"({threshold}+ errors in {window_seconds}s window)."
                ),
                source=source,
                auto=True,
            )
        return self.safe_mode_snapshot()

    def _metric(self, metric_name: str, delta: int = 1) -> None:
        if self.observability is None:
            return
        self.observability.increment_metric(metric_name, delta=delta)

    def _audit(self, *, action: str, status: str, details: dict[str, Any]) -> None:
        if self.observability is None:
            return
        self.observability.record_audit(
            action=action,
            actor="system",
            status=status,
            entity_type="runtime_guard",
            entity_id="safe_mode",
            correlation_id=None,
            details=details,
        )
