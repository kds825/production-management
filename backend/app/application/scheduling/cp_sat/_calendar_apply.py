"""cp_sat_schedule() §8 — CP-SAT 결과를 캘린더 그리디로 실제 배치.

Phase 2 Task 2.10b~e (B-3.5) 분할 — 5 sub-step 으로 나누어 commit ≤ 200줄
강제. 각 함수는 호출 순서가 hash bitwise equality 보존의 핵심 invariant.

함수 묶음 (호출 순서):
1. resolve_first_due_by_strand_cluster (Task 2.10b) — ST- 연선 클러스터
   최초 납기 계산. wire_d_earliest dict 반환.
2. apply_sheath_color_sort (Task 2.10c) — 시스 색상 묶음 정렬, _solved_order
   생성. group_meta 의 pred_ready_wmin 을 in-place 갱신.
3. preload_existing_timeline (Task 2.10d) — 기존 scheduled 태스크를
   timeline 에 pre-load.
4. apply_calendar_greedy (Task 2.10e) — 실제 슬롯 할당 본체.
"""

from __future__ import annotations

from datetime import date

from app.application._shared.group_ops import _st_sq


def resolve_first_due_by_strand_cluster(
    *,
    groups: list[str],
    group_meta: dict,
    sq_to_wire_d: dict[int, float],
) -> dict[float, date]:
    """소선경 클러스터별 최초 납기 계산 (ST- 연선 그룹 연속 배치용).

    원본: orchestrator.py:451-460. schedule_optimizer 의 wire_d_earliest 와
    동일한 로직.
    """
    wire_d_earliest: dict[float, date] = {}
    for gk in groups:
        if gk.startswith("ST-"):
            wd = sq_to_wire_d.get(_st_sq(gk), 0.0)
            if wd > 0:
                ed = group_meta[gk]["earliest_due"]
                if ed and (wd not in wire_d_earliest or ed < wire_d_earliest[wd]):
                    wire_d_earliest[wd] = ed
    return wire_d_earliest
