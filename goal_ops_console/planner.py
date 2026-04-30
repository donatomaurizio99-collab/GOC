from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class Planner:
    source = "deterministic_planner"

    def create_plan(self, goal: Mapping[str, Any]) -> dict[str, Any]:
        title = _clean_text(goal.get("title"), fallback="Untitled goal")
        description = _clean_text(goal.get("description"), fallback="")
        urgency = _score(goal.get("urgency"))
        value = _score(goal.get("value"))
        deadline_score = _score(goal.get("deadline_score"))
        priority_hint = _priority_hint(max(urgency, value, deadline_score))
        context = description or f"Goal context is currently limited to the title: {title}."

        suggestions = [
            _suggestion(
                title=f"Clarify success criteria for {title}",
                description=(
                    "Define the smallest observable outcome, owner expectation, and stop condition "
                    f"before execution starts. Context: {context}"
                ),
                priority_hint=priority_hint,
            ),
            _suggestion(
                title="Identify risks and dependencies",
                description=(
                    "List blockers, required inputs, and any approval points so the operator can keep "
                    "the plan supervised and reversible."
                ),
                priority_hint=_priority_hint(max(urgency, deadline_score)),
            ),
            _suggestion(
                title="Execute the first reversible task",
                description=(
                    "Choose the smallest implementation step that produces evidence without committing "
                    "the whole goal to an irreversible path."
                ),
                priority_hint=_priority_hint(max(value, urgency)),
            ),
            _suggestion(
                title="Validate impact and capture evidence",
                description=(
                    "Check the result against the success criteria, record evidence, and decide whether "
                    "the goal should continue, pause, or escalate."
                ),
                priority_hint=_priority_hint(max(value, deadline_score)),
            ),
        ]
        if urgency >= 0.75 or deadline_score >= 0.75:
            suggestions.insert(
                2,
                _suggestion(
                    title="Resolve time-critical constraints",
                    description=(
                        "Handle deadline-sensitive blockers first and confirm whether scope must be "
                        "reduced to protect the goal outcome."
                    ),
                    priority_hint="high",
                ),
            )

        return {
            "goal_id": _clean_text(goal.get("goal_id"), fallback=""),
            "goal_title": title,
            "source": self.source,
            "suggestions": suggestions[:5],
        }


def _suggestion(*, title: str, description: str, priority_hint: str) -> dict[str, str]:
    return {
        "title": title,
        "description": description,
        "priority_hint": priority_hint,
        "source": Planner.source,
    }


def _clean_text(value: object, *, fallback: str) -> str:
    cleaned = str(value or "").strip()
    return cleaned or fallback


def _score(value: object) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return min(1.0, max(0.0, parsed))


def _priority_hint(score: float) -> str:
    if score >= 0.75:
        return "high"
    if score >= 0.4:
        return "medium"
    return "low"
