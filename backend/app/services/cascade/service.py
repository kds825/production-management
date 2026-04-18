"""Cascade preview orchestrator.

Wave 기반 BFS 로 변경된 task 로부터 파급 영향(push/pull/unresolved)을 산출한다.
Snap 을 주입받아 실행되므로 pure unit test 가능. DB / calendar skip 은
advance_fn / reverse_advance_fn 으로 주입받아 production 과 테스트를 분리.

설계 원칙:
- MAX_WAVES 로 BFS 깊이를 유한하게 bound → 순환/과도 확산 방지.
- push_count 는 wave 간 누적되어 validate_cycles 가 MAX_WAVES 초과를 탐지.
- Pull 제안은 wave 종료 후 changed task 가 '짧아진' 경우에만 별도 수행.
- plan_cascade_preview (DB 통합 entry) 는 Task 10 에서 채움.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass

from .snap import Snap, SnapTask
from .bfs import same_equipment_overlapping, same_eq_prev_end
from .validators import validate_due_date, validate_horizon, validate_cycles
from .pull import propose_for_successors
from .reasons import PushReason, UnresolvedReason

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


def plan_cascade_preview_on_snap(
    snap: Snap,
    changed_task_id: str,
    advance_fn,
    reverse_advance_fn,
    horizon_end,
) -> CascadePreviewResult:
    """Wave 기반 BFS cascade orchestrator.

    snap 은 이미 changed_task 에 apply() 된 상태로 들어와야 한다 (caller 가 apply 후 전달).
    advance_fn / reverse_advance_fn 은 테스트에선 identity, production 에선 calendar 유틸 주입.
    """
    pushes: list[dict] = []
    pulls: list[dict] = []
    unresolved: list[dict] = []
    push_count: dict[str, int] = defaultdict(int)
    truncated = False

    frontier: list[SnapTask] = [snap.get(changed_task_id)]
    wave_used = 0

    for wave in range(MAX_WAVES):
        if not frontier:
            break
        # HARD_TASK_LIMIT: 무한 확산 방어 — push 큐가 500 건 넘으면 중단.
        if len(pushes) > HARD_TASK_LIMIT:
            unresolved.append(
                {
                    "task_id": changed_task_id,
                    "equipment_code": "",
                    "batch_label": "",
                    "reason": UnresolvedReason.cycle_detected,
                    "detail": "HARD_TASK_LIMIT exceeded",
                }
            )
            truncated = True
            break
        wave_used = wave + 1
        next_frontier: list[SnapTask] = []
        # processed_in_wave: 동일 wave 내에서 같은 task 를 same-eq 와 successor
        # 두 경로로 중복 push 하지 않도록 dedupe (T11 요구사항).
        processed_in_wave: set[str] = set()

        for T in frontier:
            # (a) same-equipment 양방향 overlap — T 와 겹치는 동일설비 task 를 뒤로 밀기.
            for N in same_equipment_overlapping(T, snap):
                if N.task_id in processed_in_wave:
                    continue
                # N 의 새 시작 = max(T.end, 같은 설비의 N 앞 task end) — 이중 겹침 방지.
                prev_candidates = [T.end]
                prev_end = same_eq_prev_end(N, snap)
                if prev_end is not None and prev_end != N.start:
                    prev_candidates.append(prev_end)
                base = max(prev_candidates)
                new_N_start = advance_fn(base)
                new_N_end = new_N_start + (N.end - N.start)
                pushes.append(
                    _push_entry(
                        N, PushReason.same_equipment_conflict, new_N_start, new_N_end
                    )
                )
                snap.apply(N.task_id, new_N_start, new_N_end)
                push_count[N.task_id] += 1
                processed_in_wave.add(N.task_id)
                next_frontier.append(snap.get(N.task_id))

            # (b) successor chain — 같은 SO-line 의 후공정이 T.end 전에 시작하면 밀기.
            # NOTE: bfs.successor_tasks 는 `o.start >= t.end` 로 현재 위치 기준 필터링하므로
            # cascade 중에 겹친(뒤 공정이 앞 공정과 시간상 겹치게 된) successor 를 놓친다.
            # service 레벨에선 **원본 순서**(old_start/old_end) 기준으로 후공정을 탐지.
            for S in _successors_by_original_order(T, snap):
                if S.task_id in processed_in_wave:
                    continue
                prev_candidates = [T.end]
                prev_end = same_eq_prev_end(S, snap)
                if prev_end is not None and prev_end != S.start:
                    prev_candidates.append(prev_end)
                earliest = max(prev_candidates)
                if S.start < earliest:
                    new_S_start = advance_fn(earliest)
                    new_S_end = new_S_start + (S.end - S.start)
                    # 이유 분기: 선행 공정 때문이면 successor_chain,
                    # 자기 설비의 다른 task 때문이면 cross_equipment_conflict.
                    reason = (
                        PushReason.successor_chain
                        if earliest == T.end
                        else PushReason.cross_equipment_conflict
                    )
                    pushes.append(_push_entry(S, reason, new_S_start, new_S_end))
                    snap.apply(S.task_id, new_S_start, new_S_end)
                    push_count[S.task_id] += 1
                    processed_in_wave.add(S.task_id)
                    next_frontier.append(snap.get(S.task_id))

        # next_frontier dedup by task_id (동일 task 가 여러 경로로 들어온 경우 한 번만 확장).
        seen: dict[str, SnapTask] = {}
        for t in next_frontier:
            seen[t.task_id] = t
        frontier = list(seen.values())

    # wave 수렴 실패: MAX_WAVES 내에 frontier 가 비지 않으면 cycle 로 판정.
    if frontier:
        for t in frontier:
            unresolved.append(
                {
                    "task_id": t.task_id,
                    "equipment_code": t.equipment_code,
                    "batch_label": t.batch_id,
                    "reason": UnresolvedReason.cycle_detected,
                    "detail": f"cascade depth > {MAX_WAVES}",
                }
            )

    unresolved += validate_due_date(snap)
    unresolved += validate_horizon(snap, horizon_end)
    unresolved += validate_cycles(push_count, MAX_WAVES)

    # Pull 제안은 changed task 가 실제로 '짧아진' 경우에만.
    changed = snap.get(changed_task_id)
    if changed.old_end is not None and changed.end < changed.old_end:
        pulls = propose_for_successors(changed_task_id, snap, reverse_advance_fn)

    can_auto_resolve = len(unresolved) == 0
    summary = _build_summary(changed, pushes, pulls, unresolved)
    return CascadePreviewResult(
        request_id=str(uuid.uuid4()),
        summary=summary,
        pushes=pushes,
        pulls=pulls,
        unresolved=unresolved,
        can_auto_resolve=can_auto_resolve,
        iter_count=wave_used,
        truncated=truncated,
    )


def _successors_by_original_order(t: SnapTask, snap: Snap) -> list[SnapTask]:
    """같은 SO-line 의 **원본 순서** 기준 후공정.

    cascade BFS 는 wave 진행 중 task 들의 start/end 가 변하므로 bfs.successor_tasks
    의 `o.start >= t.end` 필터로는 "원래 후공정이었지만 지금은 겹침" 케이스를 놓친다.
    여기서는 old_* (apply 전 원본) 를 기준으로 후공정을 판정하고, 아직 apply 가 없었다면
    현재 start/end 를 원본으로 간주한다.
    """
    if t.sales_order_id is None or t.sales_order_line is None:
        return []
    t_end_orig = t.old_end if t.old_end is not None else t.end
    out: list[SnapTask] = []
    for o in snap.by_id.values():
        if o.task_id == t.task_id:
            continue
        if (
            o.sales_order_id != t.sales_order_id
            or o.sales_order_line != t.sales_order_line
        ):
            continue
        o_start_orig = o.old_start if o.old_start is not None else o.start
        if o_start_orig >= t_end_orig:
            out.append(o)
    out.sort(key=lambda x: x.old_start if x.old_start is not None else x.start)
    return out


def _push_entry(task: SnapTask, reason, new_start, new_end):
    return {
        "task_id": task.task_id,
        "equipment_code": task.equipment_code,
        "batch_label": task.batch_id,
        "old_start": task.start,
        "old_end": task.end,
        "new_start": new_start,
        "new_end": new_end,
        "reason": reason,
    }


def _build_summary(changed: SnapTask, pushes, pulls, unresolved) -> str:
    """사용자 표시용 한국어 요약.

    changed.old_* 가 None 이면 실제 apply 가 없었으므로 '변경 없음'.
    방향은 실제 duration 차이로 결정 (start/end 모두 변경되는 경우 대응).
    """
    n_push = len(pushes)
    n_pull = len(pulls)
    n_unres = len(unresolved)
    if changed.old_start is None:
        return "변경 없음."
    delta = (changed.end - changed.start) - (changed.old_end - changed.old_start)
    delta_h = int(delta.total_seconds() / 3600)
    if delta_h > 0:
        direction = "늘어"
    elif delta_h < 0:
        direction = "줄어"
    else:
        direction = "변경"
    parts = [f"{changed.batch_id} {abs(delta_h)}h {direction}"]
    if n_push:
        parts.append(f"{n_push}건 재배치")
    if n_pull:
        parts.append(f"{n_pull}건 앞당김 제안")
    if n_unres:
        parts.append(f"{n_unres}건 해소 불가")
    return ", ".join(parts) + "."


# Task 10 에서 DB 통합 — 지금은 stub 유지.
def plan_cascade_preview(
    task_id, new_start, new_end, new_equipment_code, db
) -> CascadePreviewResult:
    raise NotImplementedError("Task 10 DB 통합에서 구현 예정")
