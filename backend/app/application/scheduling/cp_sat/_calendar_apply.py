"""cp_sat_schedule() §8 — CP-SAT 결과를 캘린더 그리디로 실제 배치.

Phase 2 Task 2.10b~e (B-3.5) 분할 — 5 sub-step 으로 나누어 commit ≤ 200줄
강제. 각 함수는 호출 순서가 hash bitwise equality 보존의 핵심 invariant.

함수 묶음 (호출 순서):
1. resolve_first_due_by_strand_cluster (Task 2.10b) — ST- 연선 클러스터
   최초 납기 계산. wire_d_earliest dict 반환.
2. apply_sheath_color_sort (Task 2.10c) — 시스 색상 묶음 정렬, solved_order
   + cpsat_eq + gk_to_cluster_id 생성. group_meta 의 pred_ready_wmin 을
   in-place 갱신.
3. preload_existing_timeline (Task 2.10d) — 기존 scheduled 태스크를
   timeline 에 pre-load.
4. apply_calendar_greedy (Task 2.10e) — 실제 슬롯 할당 본체.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.application._shared.group_ops import _is_core_group, _is_sheath_group, _st_sq
from app.domain.constants import PREDECESSOR_PROCESS, PROCESS_ORDER
from app.domain.sheath_cluster import build_sheath_clusters, cluster_sort_key


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


@dataclass
class _SortedOrderResult:
    """apply_sheath_color_sort 의 결과 묶음.

    호출자가 §8 main body 에서 다음 dict 들을 사용한다:
    - solved_order: 처리 순서 list (sorted by _solved_order_key)
    - cpsat_eq: gk → 솔버가 선택한 설비 코드
    - gk_to_cluster_id: gk → 시스 클러스터 ID (None 가능)
    - sorted_clusters: pred_ready_wmin 순으로 정렬된 시스 클러스터 list
    """

    solved_order: list[str]
    cpsat_eq: dict[str, str]
    gk_to_cluster_id: dict[str, str]
    sorted_clusters: list


def apply_sheath_color_sort(
    *,
    groups: list[str],
    group_meta: dict,
    solver,
    start_vars: dict,
    equip_vars: dict,
    wire_d_earliest: dict[float, date],
    sq_to_wire_d: dict[int, float],
) -> _SortedOrderResult:
    """시스 색상 묶음 기반 정렬 + solved_order + cpsat_eq + 클러스터 매핑 빌드.

    원본: orchestrator.py:463-598 본문 그대로. group_meta 의
    ``pred_ready_wmin`` 을 in-place 갱신 (원본 동작 보존).
    """
    # 처리 순서 결정:
    #   CORE/AL-CORE: 공정순 최우선 (ST- 선행)
    #   ST- 연선: 공정순 → 소선경 클러스터 최초납기 → 소선경값 → 그룹 EDD
    #     (같은 소선경 그룹을 연속 배치 → 선재교체 비용 최소화)
    #   시스(저압/고압): 공정순 → 주 버킷(H1/H2) → 색상 → 실제 EDD
    #     Why: 시스는 **납기 최우선, 그 다음 색상 우선**. 납기 주차 bucket
    #     안에서만 색상을 묶어 교체 비용 최소화. 주차가 다르면 납기 순서 유지.
    #     (색상을 1차로 두면 회 W15 그룹이 갈 W18 뒤로 밀려 11일+ 지연 발생)
    #     solver 가 tardiness 최소화로 주차 윈도우를 보장하므로, post-solve
    #     ordering 은 (주차, 색상) 로 안전하게 정렬 가능.
    #     schedule_optimizer._group_sort_key 의 시스 분기와 동일 규칙.
    #   그 외 공정(절연 등): 공정순 → EDD → 고객 우선순위
    #       절연(proc=2)이 시스(proc=4)보다 항상 먼저 스케줄링 → 파이프라인 데이터 등록 보장
    #
    # 모든 분기가 동일 길이 7-tuple 을 반환한다 (color_rank / due_wk 필드에
    # 비시스 분기는 중립값을 채움). solver.value(start_vars) 는 색상 키가
    # 이미 클러스터링을 확정하므로 제거.
    _COLOR_RANK_PAD = 99  # 비시스: 색상 키 무효
    _DUE_WK_PAD = 999999  # 비시스: 주 버킷 무효

    # ── 시스 색상 묶음 기반 정렬 ─────────────────────────────────────────────
    # 1차 키: 묶음의 pred_ready_wmin (선행공정 first-drum 완료 시각, working-min).
    #        solver start_vars 값으로 선행공정 그룹의 대략적 완료 시점을 계산해
    #        "설비가 빨리 사용 가능한 묶음" 부터 처리 → 설비 idle 최소화.
    # 2차 키: latest_due (같은 pred_ready 이면 납기 순)
    # 3차 키: color_rank (같은 납기면 색상 체인 유도)

    # 각 그룹의 pred_ready_wmin 계산 (솔버 결과 기반 근사)
    for _gk, _meta in group_meta.items():
        _rep = _meta["rep"]
        _pred_proc = PREDECESSOR_PROCESS.get(_rep.process_name)
        _meta["pred_ready_wmin"] = 0
        if not _pred_proc:
            continue
        _pred_sq = _meta["sq"]
        _pred_candidates = [
            g
            for g, m in group_meta.items()
            if m["rep"].process_name == _pred_proc and m["sq"] == _pred_sq
        ]
        if not _pred_candidates:
            continue
        _best: int | None = None
        for _pg in _pred_candidates:
            _pg_start = solver.value(start_vars[_pg])
            _pg_dur = group_meta[_pg]["cpsat_dur"]
            _pg_drums = max(int(group_meta[_pg]["rep"].drum_count or 1), 1)
            _pg_first_drum = _pg_start + max(1, _pg_dur // _pg_drums)
            if _best is None or _pg_first_drum < _best:
                _best = _pg_first_drum
        _meta["pred_ready_wmin"] = _best or 0

    _sheath_clusters = build_sheath_clusters(group_meta)
    _sorted_clusters = sorted(
        _sheath_clusters, key=lambda c: cluster_sort_key(c, group_meta)
    )
    _cluster_rank: dict[str, tuple[int, int]] = {}
    for ci, _cluster in enumerate(_sorted_clusters):
        for gi, _gk in enumerate(_cluster.group_keys):
            _cluster_rank[_gk] = (ci, gi)

    def _solved_order_key(gk: str):
        meta = group_meta[gk]
        rep = meta["rep"]
        proc_level = PROCESS_ORDER.get(rep.process_name, 50)
        earliest = meta["earliest_due"] or date.max
        cust_prio = rep.customer_priority or 99

        # CORE/AL-CORE: ST- 선행 공정이므로 반드시 먼저 실행 (date.min으로 최우선)
        if _is_core_group(gk):
            return (
                proc_level,
                date.min,  # ST- 그룹보다 항상 앞에 오도록
                -1.0,
                _COLOR_RANK_PAD,
                _DUE_WK_PAD,
                earliest,
                cust_prio,
            )
        # ST- 연선: 소선경 클러스터 연속 배치
        if gk.startswith("ST-") and rep.process_name == "연선":
            wd = sq_to_wire_d.get(_st_sq(gk), 0.0)
            cluster_due = wire_d_earliest.get(wd, date.max)
            return (
                proc_level,
                cluster_due,  # 소선경 클러스터 최초 납기
                wd,  # 소선경값 (같은 클러스터 내 연속 배치)
                _COLOR_RANK_PAD,
                _DUE_WK_PAD,
                earliest,
                cust_prio,
            )
        # 시스: CP-SAT 의 start_var 를 primary 로 사용 (solver 의 tardiness/idle
        # 최소화 결정을 존중) → 빈 설비 idle 을 achievable future 클러스터로 채우게.
        # Why: cluster_rank (latest_due primary) 는 overdue 클러스터를 앞에 두어
        # achievable 클러스터의 idle-gap 활용 기회를 놓친다. 예: W17H1 흑 25 의
        # pred 가 4/9 ready 지만 SH-A120 의 4/7~4/10 gap 대신 W16H2 흑 뒤(4/21)
        # 에 배치되어 납기 초과. solver 는 chain_terms 로 color chain 도 선호하므로
        # start_var 순서는 (a) 공정 선후, (b) tardiness 최소, (c) color chain 을
        # 모두 반영한 결정이다.
        # Tiebreak: cluster_rank 로 같은 시각에 여러 그룹이 올 때 색상 체인 유지.
        if _is_sheath_group(gk, meta["batches"]):
            rank = _cluster_rank.get(gk, (10**9, 10**9))
            start_val = int(solver.value(start_vars[gk]))
            return (
                proc_level,
                date.max,
                0.0,
                start_val,
                rank[0],
                rank[1],
                earliest,
                cust_prio,
            )
        # 절연 등 기타 공정: EDD 순
        return (
            proc_level,
            earliest,
            0.0,
            _COLOR_RANK_PAD,
            _DUE_WK_PAD,
            earliest,
            cust_prio,
        )

    solved_order = sorted(groups, key=_solved_order_key)

    # CP-SAT가 선택한 설비
    cpsat_eq: dict[str, str] = {
        gk: next(ec for ec, bv in equip_vars[gk].items() if solver.value(bv) == 1)
        for gk in groups
    }

    # 시스 묶음 기반 append 정책용 — group_key → cluster_id (downstream 에서 사용)
    gk_to_cluster_id: dict[str, str] = {}
    for _c in _sorted_clusters:
        for _gk_iter in _c.group_keys:
            gk_to_cluster_id[_gk_iter] = _c.cluster_id

    return _SortedOrderResult(
        solved_order=solved_order,
        cpsat_eq=cpsat_eq,
        gk_to_cluster_id=gk_to_cluster_id,
        sorted_clusters=_sorted_clusters,
    )


def preload_existing_timeline(
    *,
    db,
    run_label: str,
) -> dict[str, list]:
    """기존 scheduled 태스크를 timeline 에 pre-load.

    원본: orchestrator.py:487-503. 긴급수주 추가 후 재스케줄링 시 이미
    확정된 블록과의 겹침을 방지한다.
    """
    # ── 기존 scheduled 태스크를 timeline에 pre-load ───────────────────────
    # 긴급수주 추가 후 재스케줄링 시 이미 확정된 블록과의 겹침을 방지한다.
    # Inline import — boundary crossing module dependency 명시.
    from app.infrastructure.models.schedule_task import ScheduleTask

    timeline: dict[str, list] = {}
    existing_tasks = (
        db.query(ScheduleTask)
        .filter(
            ScheduleTask.run_label == run_label,
            ScheduleTask.equipment_code.isnot(None),
            ScheduleTask.start_datetime.isnot(None),
            ScheduleTask.end_datetime.isnot(None),
        )
        .all()
    )
    for et in existing_tasks:
        timeline.setdefault(et.equipment_code, []).append(
            (et.start_datetime, et.end_datetime)
        )
    return timeline
