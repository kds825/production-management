from enum import Enum


class PushReason(str, Enum):
    same_equipment_conflict = "same_equipment_conflict"
    cross_equipment_conflict = "cross_equipment_conflict"
    successor_chain = "successor_chain"


class PullReason(str, Enum):
    successor_slack_available = "successor_slack_available"


class UnresolvedReason(str, Enum):
    due_date_violation = "due_date_violation"
    no_space_forward = "no_space_forward"
    cycle_detected = "cycle_detected"
    invalid_equipment = "invalid_equipment"
