from goal_ops_console.config import GOAL_QUEUE_STATUS_MAP, SPEC_VERSION

TRANSITION_RULES = {
    "goal": {
        "draft": {"allowed_owner": "scheduler", "allowed_targets": ["active"]},
        "active": {
            "allowed_owner": "state_manager",
            "allowed_targets": ["completed", "blocked", "escalation_pending", "cancelled"],
        },
        "blocked": {
            "allowed_owner": "state_manager",
            "allowed_targets": ["active", "completed", "archived"],
        },
        "escalation_pending": {
            "allowed_owner": "state_manager",
            "allowed_targets": ["active", "archived"],
        },
        "completed": {"allowed_owner": "state_manager", "allowed_targets": ["archived"]},
        "cancelled": {"allowed_owner": "state_manager", "allowed_targets": ["archived"]},
    },
    "task": {
        "pending": {"allowed_owner": "execution_layer", "allowed_targets": ["running"]},
        "running": {"allowed_owner": "execution_layer", "allowed_targets": ["succeeded", "failed"]},
        "failed": {
            "allowed_owner": "execution_layer",
            "allowed_targets": ["running", "exhausted", "poison"],
        },
        "exhausted": {"allowed_owner": "state_manager", "allowed_targets": []},
        "poison": {"allowed_owner": "state_manager", "allowed_targets": []},
        "succeeded": {"allowed_owner": "state_manager", "allowed_targets": []},
    },
    "learning": {
        "created": {"allowed_owner": "evaluator", "allowed_targets": ["evaluated"]},
        "evaluated": {
            "allowed_owner": "state_manager",
            "allowed_targets": ["promotion_pending", "expired"],
        },
        "promotion_pending": {
            "allowed_owner": "state_manager",
            "allowed_targets": ["promoted", "promotion_rejected"],
        },
    },
}


def validate_transition(entity_type: str, from_state: str, to_state: str, owner: str) -> tuple[bool, str]:
    entity_rules = TRANSITION_RULES.get(entity_type, {})
    current_rule = entity_rules.get(from_state)
    if current_rule is None:
        return False, f"unknown current state '{from_state}' for {entity_type}"
    if current_rule["allowed_owner"] != owner:
        return False, f"owner '{owner}' cannot transition {entity_type} from '{from_state}'"
    if to_state not in current_rule["allowed_targets"]:
        return False, f"target '{to_state}' is not allowed from '{from_state}'"
    return True, "ok"


def queue_status_for_goal_state(goal_state: str) -> str | None:
    return GOAL_QUEUE_STATUS_MAP.get(goal_state)
