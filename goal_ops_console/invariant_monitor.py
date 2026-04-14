from __future__ import annotations

import threading
from typing import TYPE_CHECKING
from typing import Any

from goal_ops_console.database import now_utc

if TYPE_CHECKING:
    from goal_ops_console.observability import ObservabilityService
    from goal_ops_console.runtime_guard import RuntimeGuard
    from goal_ops_console.state_manager import StateManager


class InvariantMonitor:
    def __init__(
        self,
        state_manager: "StateManager",
        *,
        scan_interval_seconds: int,
        auto_safe_mode: bool,
        runtime_guard: "RuntimeGuard | None" = None,
        observability: "ObservabilityService | None" = None,
    ):
        self.state_manager = state_manager
        self.scan_interval_seconds = max(1, int(scan_interval_seconds))
        self.auto_safe_mode = bool(auto_safe_mode)
        self.runtime_guard = runtime_guard
        self.observability = observability

        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._state: dict[str, Any] = {
            "is_running": False,
            "scan_interval_seconds": self.scan_interval_seconds,
            "auto_safe_mode": self.auto_safe_mode,
            "last_scan_at_utc": None,
            "last_error": None,
            "violation_count": 0,
            "violations": [],
        }

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._loop,
                daemon=True,
                name="goal-ops-invariant-monitor",
            )
            self._thread.start()

    def stop(self, timeout_seconds: float = 5.0) -> None:
        with self._lock:
            thread = self._thread
            if thread is None:
                return
            self._stop.set()
        thread.join(timeout=timeout_seconds)
        with self._lock:
            if self._thread is thread:
                self._thread = None
                self._state["is_running"] = False

    def status(self) -> dict[str, Any]:
        with self._lock:
            thread = self._thread
            self._state["is_running"] = bool(thread is not None and thread.is_alive())
            self._state["scan_interval_seconds"] = self.scan_interval_seconds
            self._state["auto_safe_mode"] = self.auto_safe_mode
            return dict(self._state)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                violations = self.state_manager.find_invariant_violations()
                with self._lock:
                    self._state["last_scan_at_utc"] = now_utc()
                    self._state["last_error"] = None
                    self._state["violation_count"] = len(violations)
                    self._state["violations"] = list(violations)
                    self._state["is_running"] = True

                if violations:
                    self._metric("invariants.violations.detected", delta=len(violations))
                    self._audit(
                        action="system.invariants.detected",
                        status="error",
                        details={
                            "violation_count": len(violations),
                            "violations": list(violations),
                        },
                    )
                    if self.auto_safe_mode and self.runtime_guard is not None:
                        self.runtime_guard.activate_safe_mode(
                            reason=(
                                "Invariant monitor detected queue/state inconsistencies; "
                                "safe mode activated."
                            ),
                            source="invariant_monitor",
                            auto=True,
                        )
                else:
                    self._metric("invariants.scan.ok")
            except Exception as exc:
                with self._lock:
                    self._state["last_scan_at_utc"] = now_utc()
                    self._state["last_error"] = str(exc)
                    self._state["is_running"] = True
                self._metric("invariants.scan.errors")
            self._stop.wait(self.scan_interval_seconds)

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
            entity_type="invariant_monitor",
            entity_id="goal_ops_console",
            correlation_id=None,
            details=details,
        )
