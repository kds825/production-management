"""optimization_loop 의 그룹 정렬 단계.

Task 2.12 추출 (B-7.1): GroupingContext dataclass + _group_and_sort 함수.
원본: optimization_loop.py:168-310. 본 함수는 batch list 를 받아
batch_group 별로 묶고 (CORE → ST → 절연/시스 tier 순), 시스 색상 묶음
lookup 을 빌드한 뒤 GroupingContext + 정렬된 (group_key, batches) 리스트를
반환한다. _assign_group 의 outer loop 입력 producer.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy.orm import Session

from app.domain.constants import PROCESS_ORDER
from app.infrastructure.models.drum_lot_master import DrumLotMaster
from app.infrastructure.models.production_batch import ProductionBatch
from app.application._shared.group_ops import (
    _is_core_group,
    _is_sheath_group,
    _st_sq,
)


def _group_earliest_due(batches: list) -> date:
    """Earliest non-null due date in a batch list, or ``date.max``.

    Co-located here (not imported from auto_schedule) so this module
    has zero dependency on the retry harness — eliminating the cycle
    that would otherwise occur when ``auto_schedule.py`` imports back
    from this module via the schedule_optimizer shell.
    """
    dates = [b.due_date for b in batches if b.due_date is not None]
    return min(dates) if dates else date.max


@dataclass
class GroupingContext:
    """`_group_and_sort` 의 lookup + per-equipment mutable state.

    추출 의도 (Phase 4 step 2c): per-call mutable + read-only lookup 을 한
    객체로 묶어 _assign_group 에 단일 인자로 전달. SchedulerState 와 의미
    분리:
      - SchedulerState: 파이프라인 진행 상태 (timeline / predecessor / 첫
        드럼 출력 등 — retry 사이 cross-contamination 회피 대상)
      - GroupingContext: 그룹핑/정렬용 lookup + 시스 묶음 boundary 추적
        (per-equipment last cluster_id 만 mutable, 나머지는 build 후 read-only)

    `prev_cluster_on_eq` 는 inner loop 에서 best_eq 갱신 시 mutate 되는
    유일한 mutable field — 시스 묶음 append 정책 (CP-SAT 와 동일 규칙) 의
    boundary 판단에 사용.
    """

    sq_to_wire_d: dict[int, float] = field(default_factory=dict)
    wire_d_earliest: dict[float, date] = field(default_factory=dict)
    cluster_rank: dict[str, tuple[int, int]] = field(default_factory=dict)
    gk_to_cluster_id: dict[str, str] = field(default_factory=dict)
    prev_cluster_on_eq: dict[str, str] = field(default_factory=dict)


def _group_and_sort(
    batches: list[ProductionBatch], db: Session
) -> tuple[list[tuple[str, list[ProductionBatch]]], GroupingContext]:
    """batch_groups 빌드 + ST 소선경 grouping + 시스 색상 묶음 lookup + 정렬.

    출력:
      - ordered_group_items: 정렬된 (group_key, [batches]) 리스트
        정렬 우선순위:
          tier 0 = CORE/AL-CORE (선행 공정)
          tier 1 = ST- 연선 (소선경 클러스터 단위 연속 배치)
          tier 2 = 절연/시스 등 (PROCESS_ORDER → EDD)
        시스 그룹: 묶음 단위(date 주차 / 색상 / EDD) — CP-SAT 와 동일
        규칙. 색상은 같은 주차 내에서만 묶어 납기 우선 정책 위반 회피.

      - ctx: GroupingContext (sq_to_wire_d, wire_d_earliest, cluster_rank,
        gk_to_cluster_id, prev_cluster_on_eq=빈 dict)
    """
    from app.domain.sheath_cluster import (
        build_sheath_clusters,
        cluster_sort_key,
    )

    batch_groups: OrderedDict[str, list[ProductionBatch]] = OrderedDict()
    for batch in batches:
        key = batch.batch_group or f"_single_{batch.batch_id}"
        batch_groups.setdefault(key, []).append(batch)

    # ── drum_lot_master에서 SQ별 소선경(wire_diameter) 로드 ──────────────
    sq_to_wire_d: dict[int, float] = {
        int(d.cross_section): float(d.wire_diameter)
        for d in db.query(DrumLotMaster).all()
        if d.wire_diameter is not None
    }

    # 소선경 클러스터별 최초 납기: 가장 급한 소선경 클러스터를 먼저 처리.
    wire_d_earliest: dict[float, date] = {}
    for gk, gb in batch_groups.items():
        if gk.startswith("ST-"):
            wd = sq_to_wire_d.get(_st_sq(gk), 0.0)
            if wd > 0:
                ed = _group_earliest_due(gb)
                if wd not in wire_d_earliest or ed < wire_d_earliest[wd]:
                    wire_d_earliest[wd] = ed

    # ── 시스 색상 묶음 lookup — CP-SAT 와 동일 규칙 ─────────────────────
    # 그리디 경로의 batch_groups 는 {gk: [batches...]} 형식이라 묶음 빌더가
    # 기대하는 {gk: {"batches": ..., "earliest_due": ..., ...}} 형식으로
    # wrapping 한 뒤 전달.
    gm_for_cluster = {
        gk: {
            "batches": gb,
            "earliest_due": _group_earliest_due(gb),
            "cpsat_dur": int(sum(float(b.estimated_duration_min or 0) for b in gb)),
            "pred_ready": None,
        }
        for gk, gb in batch_groups.items()
    }
    sheath_clusters = build_sheath_clusters(gm_for_cluster)
    sorted_clusters = sorted(
        sheath_clusters, key=lambda c: cluster_sort_key(c, gm_for_cluster)
    )
    cluster_rank: dict[str, tuple[int, int]] = {}
    for ci, cluster in enumerate(sorted_clusters):
        for gi, gk_c in enumerate(cluster.group_keys):
            cluster_rank[gk_c] = (ci, gi)

    gk_to_cluster_id: dict[str, str] = {}
    for c in sorted_clusters:
        for gk_c in c.group_keys:
            gk_to_cluster_id[gk_c] = c.cluster_id

    def _group_sort_key(kv):
        gk, gb = kv
        tier = 0 if _is_core_group(gk) else (1 if gk.startswith("ST-") else 2)
        proc_order = PROCESS_ORDER.get(gb[0].process_name, 50) if gb else 50
        earliest_due = _group_earliest_due(gb)
        cust_prio = gb[0].customer_priority or 99 if gb else 99

        # 시스 체인: 묶음 단위 정렬 — CP-SAT _solved_order_key 와 동일 규칙.
        # cluster_rank 는 build_sheath_clusters 로 구성된 lookup 으로,
        # (cluster_idx, position_in_cluster) 를 제공한다.
        if _is_sheath_group(gk, gb):
            rank = cluster_rank.get(gk, (10**9, 10**9))
            return (
                tier,
                date.max,  # ST- 클러스터 납기 (비해당)
                0.0,  # ST- 소선경 (비해당)
                proc_order,
                rank[0],  # 1차: 묶음 순위 (납기 임박 묶음 먼저)
                rank[1],  # 2차: 묶음 내 순서
                earliest_due,  # 3차: 실제 EDD (tiebreak)
                cust_prio,
            )

        # 비시스 기존 정렬 (호환성 유지)
        return (
            tier,
            wire_d_earliest.get(sq_to_wire_d.get(_st_sq(gk), 0.0), date.max)
            if gk.startswith("ST-")
            else date.max,
            sq_to_wire_d.get(_st_sq(gk), 0.0) if gk.startswith("ST-") else 0.0,
            proc_order,
            earliest_due,
            # 시스 정렬키와 길이를 맞추기 위한 padding
            0,
            date.max,
            cust_prio,
        )

    ordered = sorted(batch_groups.items(), key=_group_sort_key)

    ctx = GroupingContext(
        sq_to_wire_d=sq_to_wire_d,
        wire_d_earliest=wire_d_earliest,
        cluster_rank=cluster_rank,
        gk_to_cluster_id=gk_to_cluster_id,
    )
    return ordered, ctx
