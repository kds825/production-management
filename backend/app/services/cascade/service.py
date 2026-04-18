from dataclasses import dataclass

MAX_WAVES = 4
HARD_TASK_LIMIT = 500


@dataclass
class CascadePreviewResult:
    request_id: str
    summary: str
    pushes: list
    pulls: list
    unresolved: list
    can_auto_resolve: bool
    iter_count: int
    truncated: bool


def plan_cascade_preview(
    task_id, new_start, new_end, new_equipment_code, db
) -> CascadePreviewResult:
    raise NotImplementedError
