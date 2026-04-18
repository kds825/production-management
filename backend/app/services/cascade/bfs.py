from .snap import Snap, SnapTask


def same_equipment_overlapping(t: SnapTask, snap: Snap) -> list[SnapTask]:
    """t 와 같은 설비에서 **겹침(overlap)** 있는 다른 task 목록 (양방향).

    겹침 조건 (경계 제외):
        other.start < t.end AND t.start < other.end

    v1 B2 수정: prev neighbor 도 반환 (설비 변경 드래그 시 새 설비 앞 task 와의 겹침 감지).
    """
    out: list[SnapTask] = []
    for other in snap.by_equipment(t.equipment_code):
        if other.task_id == t.task_id:
            continue
        if other.start < t.end and t.start < other.end:
            out.append(other)
    return out


def same_eq_prev_end(t: SnapTask, snap: Snap):
    """같은 설비의 t.start 이전(또는 동시)에 끝나는 task 중 **가장 큰 end** 반환. 없으면 None.

    경계 포함 (end == t.start 인 이웃도 인정) — Pull 제안에서 slack 계산의 기준점으로 사용.
    """
    candidates = [
        o
        for o in snap.by_equipment(t.equipment_code)
        if o.task_id != t.task_id and o.end <= t.start
    ]
    if not candidates:
        return None
    return max(c.end for c in candidates)


def successor_tasks(t: SnapTask, snap: Snap) -> list[SnapTask]:
    raise NotImplementedError
