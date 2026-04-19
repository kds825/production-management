from .snap import Snap
from .reasons import UnresolvedReason


def _entry(task, reason, detail=""):
    # _entry: validator 들이 반환하는 unresolved dict 공통 포맷을 한 곳에 모아 DRY 유지.
    # UI/service 단이 task_id/equipment_code/batch_label/reason/detail 을 기대하므로
    # 각 validator 가 동일 키셋을 내보내도록 강제한다.
    return {
        "task_id": task.task_id,
        "equipment_code": task.equipment_code,
        "batch_label": task.batch_id,
        "reason": reason,
        "detail": detail,
    }


def validate_due_date(snap: Snap) -> list[dict]:
    """Apply 된 task 중 end > due_date 인 것을 unresolved 로 반환.

    due_date 가 None 인 (납기 미지정) task 는 위반 대상이 아니므로 스킵.
    changed_tasks() 가 apply 가 한 번이라도 일어난 task 만 반환하므로, cascade 로 인해
    실제 이동된 task 의 납기만 검증한다.
    """
    return [
        _entry(
            t,
            UnresolvedReason.due_date_violation,
            f"end {t.end} > due {t.due_date}",
        )
        for t in snap.changed_tasks()
        if t.due_date is not None and t.end > t.due_date
    ]


def validate_horizon(snap: Snap, horizon_end) -> list[dict]:
    """Apply 된 task 중 end > horizon_end (계획 수평선) 인 것을 unresolved 로 반환.

    계획 수평선 밖으로 밀린 task 는 더 이상 전진 배치할 공간이 없는 것으로 간주
    (UnresolvedReason.no_space_forward).
    """
    return [
        _entry(
            t,
            UnresolvedReason.no_space_forward,
            f"end {t.end} > horizon {horizon_end}",
        )
        for t in snap.changed_tasks()
        if t.end > horizon_end
    ]


def validate_cycles(push_count: dict, max_waves: int) -> list[dict]:
    """한 task 가 max_waves 초과로 push 되면 cycle 로 간주.

    `push_count` 는 BFS 가 wave 루프 안에서 task 별 push 횟수 누적.
    경계: 정확히 max_waves 까지는 허용하고, 초과분(> max_waves)부터 cycle 로 판정.

    cycle 시점에는 원본 SnapTask 참조가 보장되지 않을 수 있어 task_id 만 신뢰하고
    equipment_code / batch_label 은 빈 문자열로 둔다 (서비스 레이어가 필요 시 조인).
    """
    result: list[dict] = []
    for tid, count in push_count.items():
        if count > max_waves:
            result.append(
                {
                    "task_id": tid,
                    "equipment_code": "",
                    "batch_label": "",
                    "reason": UnresolvedReason.cycle_detected,
                    "detail": f"pushed {count} times > MAX_WAVES={max_waves}",
                }
            )
    return result
