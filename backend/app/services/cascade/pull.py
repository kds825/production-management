from .snap import Snap
from .bfs import successor_tasks, same_eq_prev_end
from .reasons import PullReason


def propose_for_successors(
    changed_task_id: str, snap: Snap, reverse_advance_fn
) -> list[dict]:
    """변경 task 가 짧아진 경우 후속 공정의 앞당김 제안.

    v1 버그 수정:
      - 같은 설비 앞 task 의 end 를 earliest 후보에 포함 (B3/B4 가드)
      - successor chain 순차 전파: 앞 successor 의 pull 된 new_end 를 다음 prev_end 로 사용

    반환: 각 pull 제안 dict. snap 에 직접 반영하지 않음 (제안 only).
    """
    changed = snap.get(changed_task_id)
    if changed.old_end is None:
        return []  # 아직 apply 안 됨
    if changed.end >= changed.old_end:
        return []  # 짧아지지 않음 — Pull 대상 아님

    pulls: list[dict] = []
    prev_end = changed.end  # changed task 의 새 end 가 첫 후속의 최소 시작점

    for S in successor_tasks(changed, snap):
        same_eq_prev = same_eq_prev_end(S, snap)
        earliest_candidates = [prev_end]
        if same_eq_prev is not None:
            earliest_candidates.append(same_eq_prev)
        earliest = max(earliest_candidates)

        if earliest < S.start:
            slack = S.start - earliest
            proposed_start = reverse_advance_fn(S.start, slack)
            proposed_end = proposed_start + (S.end - S.start)
            pulls.append(
                {
                    "task_id": S.task_id,
                    "equipment_code": S.equipment_code,
                    "batch_label": S.batch_id,
                    "old_start": S.start,
                    "old_end": S.end,
                    "new_start": proposed_start,
                    "new_end": proposed_end,
                    "reason": PullReason.successor_slack_available,
                }
            )
            prev_end = proposed_end
        else:
            prev_end = S.end  # slack 없음 — 다음 successor 도 기존 S.end 기준
    return pulls
