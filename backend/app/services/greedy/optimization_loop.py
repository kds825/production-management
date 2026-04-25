"""Greedy calendar-aware slot assignment loop — `_run_optimization_once`.

Extracted from ``app.services.greedy.auto_schedule`` (Week 9 SRP cleanup).

This module owns ONE responsibility: given a set of `planned`
`ProductionBatch` rows for a run, walk them in priority order and
allocate calendar-aware slots on the eligible equipment, writing
`ScheduleTask` rows + audit logs as it goes.

Why a separate module: ``auto_schedule.py`` holds the **retry harness**
(CP-SAT-or-greedy + overlap detection + 3-level fallback +
tardiness-boost). Mixing that retry logic with the 900-line greedy core
inflated the file to 1500+ lines and obscured the responsibility split.

Why a single 900-line function still: the greedy pass shares dozens of
local-state caches (timeline, predecessor_map, sq_to_equip,
process_first_output_by_sq, ...). A correct decomposition into smaller
functions requires designing a state-bag dataclass and threading it
through; that's a separate engineering task tracked in the post-pilot
backlog. The extraction here gives file-level visibility while
preserving exact behaviour.

Why monkeypatching still works: the public re-export shell
``app.services.schedule_optimizer`` re-exports
``_run_optimization_once`` from this module, so existing tests that
patch ``schedule_optimizer._run_optimization_once`` see the new
location transparently.
"""

from __future__ import annotations

from collections import OrderedDict
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.domain.constants import (
    PREDECESSOR_PROCESS,
    PROCESS_ORDER,
)
from app.infrastructure.models.drum_lot_master import DrumLotMaster
from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.schedule_task import ScheduleTask
from app.infrastructure.models.speed_master import SpeedMaster
from app.services.audit_logger import log_decision
from app.infrastructure.calendar_engine import calculate_end_datetime
from app.services.constraint_params import resolve_color_change_min
from app.services.greedy.loaders.base_date import (  # noqa: F401  # used in setup
    resolve_base_date,
)
from app.services.greedy.loaders.master_data import (  # noqa: F401  # used in setup
    load_master_data,
)
from app.services.greedy.loaders.planned_batches import (  # noqa: F401  # used in setup
    load_planned_batches,
)
from app.services.greedy.loaders.wip_filter import (  # noqa: F401  # used in setup
    filter_wip_skippable,
)
from app.services.greedy.slot_finder import _find_available_slot
from app.services.scheduling_shared.group_ops import (
    _extract_core_main_sq,
    _get_drum_winding_min,
    _get_stranding_setup_min,
    _is_core_group,
    _is_sheath_group,
    _schedule_multi_equipment,
    _st_sq,
)
from app.services.scheduling_shared.slot_filters import (
    _filter_by_sheath_routing,
    _find_eligible_equipment,
    _narrow_by_stranding,
    align_start_to_predecessor_end,
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


def _run_optimization_once(
    run_label: str, db: Session, *, base_date: datetime | None = None
) -> dict:
    """
    run_label의 production_batch를 간트 차트에 자동 배치.

    Args:
        base_date: 스케줄 시작 기준일시. None이면 KST 당일 08:00.
    Returns: {"total_tasks": int, "violations": list, "warnings": list}
    """
    result = {"total_tasks": 0, "violations": [], "warnings": []}

    # Phase 2 추출: greedy/loaders/planned_batches.py
    batches = load_planned_batches(run_label, db)

    if not batches:
        result["warnings"].append("배치 없음 — Stage 1을 먼저 실행하세요")
        return result

    # Phase 2 추출: greedy/loaders/wip_filter.py
    batches, wip_skipped = filter_wip_skippable(batches, db)
    if wip_skipped:
        result["wip_skipped"] = wip_skipped

    # Phase 2 추출: greedy/loaders/base_date.py
    base_date = resolve_base_date(run_label, base_date)

    # Phase 2 추출: greedy/loaders/master_data.py
    _md = load_master_data(db)
    equipment_by_process = _md.equipment_by_process
    speed_map = _md.speed_map
    constraint_params = _md.constraint_params
    welding_min = _md.welding_min

    # Load existing tasks (to check overlaps)
    existing_tasks = (
        db.query(ScheduleTask)
        .filter(
            ScheduleTask.run_label == run_label,
        )
        .all()
    )

    # Build equipment timeline: equipment_code → list of (start, end) occupied slots
    timeline = {}
    for t in existing_tasks:
        timeline.setdefault(t.equipment_code, []).append(
            (t.start_datetime, t.end_datetime)
        )

    # ── 부분 재스케줄용: 기존 tasks에서 파이프라인 상태 사전 초기화 ────────────
    # 전체 재스케줄(full reschedule)에서는 existing_tasks가 비어 있으므로 no-op.
    # 부분 재스케줄 시 비영향 그룹 tasks가 existing_tasks로 전달되므로
    # 이를 바탕으로 process_end_by_sq / process_first_output_by_sq 등을 미리 채운다.
    _seed_pipeline_process_end: dict[tuple[str, int], datetime] = {}
    _seed_pipeline_first_output: dict[tuple[str, int], datetime] = {}
    _seed_first_insul_output: datetime | None = None
    _seed_core_first_drum: dict[int, datetime] = {}

    if existing_tasks:
        _seed_batch_ids = {t.batch_id for t in existing_tasks if t.batch_id is not None}
        _seed_batches = (
            (
                db.query(ProductionBatch)
                .filter(ProductionBatch.batch_id.in_(_seed_batch_ids))
                .all()
            )
            if _seed_batch_ids
            else []
        )
        _seed_batch_map = {b.batch_id: b for b in _seed_batches}

        for t in existing_tasks:
            if t.start_datetime is None or t.end_datetime is None:
                continue
            b = _seed_batch_map.get(t.batch_id)
            if b is None:
                continue
            proc = b.process_name or ""
            sq_int = int(b.sq_mm2 or 0)
            proc_sq = (proc, sq_int)

            # process_end_by_sq: 해당 (공정, SQ)의 최대 종료 시각
            if (
                proc_sq not in _seed_pipeline_process_end
                or t.end_datetime > _seed_pipeline_process_end[proc_sq]
            ):
                _seed_pipeline_process_end[proc_sq] = t.end_datetime

            # process_first_output_by_sq: 첫 번째 드럼 출력 시각
            if not _is_core_group(b.batch_group or ""):
                _setup_min = float(t.setup_time_min or 0)
                _dur = float(b.estimated_duration_min or 0)
                _lot_count = max(int(b.drum_count or 1), 1)
                _first_drum_min = _setup_min + (
                    _dur / _lot_count if _lot_count else _dur
                )
                _first_out = calculate_end_datetime(
                    t.start_datetime, _first_drum_min, db, t.equipment_code
                )
                if (
                    proc_sq not in _seed_pipeline_first_output
                    or _first_out < _seed_pipeline_first_output[proc_sq]
                ):
                    _seed_pipeline_first_output[proc_sq] = _first_out
                if proc in ("저압절연", "고압절연"):
                    if (
                        _seed_first_insul_output is None
                        or _first_out < _seed_first_insul_output
                    ):
                        _seed_first_insul_output = _first_out
            else:
                # CORE 그룹 → core_first_drum_by_main_sq 채우기
                _setup_min = float(t.setup_time_min or 0)
                _dur = float(b.estimated_duration_min or 0)
                _lot_count = max(int(b.drum_count or 1), 1)
                _first_drum_min = _setup_min + (
                    _dur / _lot_count if _lot_count else _dur
                )
                _first_out = calculate_end_datetime(
                    t.start_datetime, _first_drum_min, db, t.equipment_code
                )
                _msq = _extract_core_main_sq(b.batch_group or "")
                if _msq is not None:
                    if (
                        _msq not in _seed_core_first_drum
                        or _first_out < _seed_core_first_drum[_msq]
                    ):
                        _seed_core_first_drum[_msq] = _first_out

    # Track predecessor tasks by (sales_order_id, sales_order_line)
    predecessor_map = {}  # (order_id, order_line) → last task_id for this order

    # 용접 시간 추적 (4-4): equipment_code → last placed batch (sq_mm2, sales_order_id)
    last_batch_on_equip: dict[str, ProductionBatch] = {}

    # ── 규칙 2: 19연선 이상(70SQ+)은 같은 SQ→같은 설비 고정 ────────────────
    # 이미 배정된 SQ→설비 매핑을 추적하여 동일 SQ는 같은 설비에 배치
    sq_to_equip: dict[tuple[str, int], str] = {}  # (process_name, sq) → equipment_code

    tasks_created = []

    # 공정 간 선행관계 추적 — SQ 단위로 앞 공정의 종료 시각 기록
    # 연선_120SQ 종료 → 저압절연_120SQ 시작 가능
    # 저압절연_120SQ 종료 → A100_120SQ / A120_120SQ 시작 가능
    process_end_by_sq: dict[tuple[str, int], datetime] = dict(
        _seed_pipeline_process_end
    )
    # key: (공정명, SQ) → value: 해당 공정+SQ 그룹의 종료 시각

    # 파이프라인 겹침용: 앞 공정에서 첫 번째 드럼이 출력되는 시각
    # 연선에서 1틀이 나오면 절연 시작 가능, 절연 1틀 나오면 시스 시작 가능
    # = task.start_datetime + setup_min + (group_run_duration / drum_count)
    process_first_output_by_sq: dict[tuple[str, int], datetime] = dict(
        _seed_pipeline_first_output
    )

    # 저압절연 전체 중 가장 이른 첫 번째 드럼 출력 시각 — A100/A120 시스 그룹 시작 기준
    first_insul_output: datetime | None = _seed_first_insul_output

    # 61연선 코어(T6B0/AL6BO) 첫 드럼 출력 시각 — pipeline overlap 기준
    # "CORE-300-..." 첫 드럼 완료 후 "ST-300-..." 시작 가능
    core_first_drum_by_main_sq: dict[int, datetime] = dict(_seed_core_first_drum)

    # ── batch_group 단위로 그루핑 ────────────────────────────────────────────
    from collections import OrderedDict

    batch_groups: OrderedDict[str, list[ProductionBatch]] = OrderedDict()
    for batch in batches:
        key = batch.batch_group or f"_single_{batch.batch_id}"
        batch_groups.setdefault(key, []).append(batch)

    # ── drum_lot_master에서 SQ별 소선경(wire_diameter) 로드 ──────────────────
    # ST- 연선 그룹 정렬 시 동일 소선경 그룹이 연속 배치되도록 하기 위해 사용
    sq_to_wire_d: dict[int, float] = {
        int(d.cross_section): float(d.wire_diameter)
        for d in db.query(DrumLotMaster).all()
        if d.wire_diameter is not None
    }

    # 소선경 클러스터별 최초 납기: wire_diameter → min(due_date of all ST- groups in cluster)
    # 가장 급한 소선경 클러스터를 먼저 처리하기 위해 사용
    wire_d_earliest: dict[float, date] = {}
    for gk, gb in batch_groups.items():
        if gk.startswith("ST-"):
            wd = sq_to_wire_d.get(_st_sq(gk), 0.0)
            if wd > 0:
                ed = _group_earliest_due(gb)
                if wd not in wire_d_earliest or ed < wire_d_earliest[wd]:
                    wire_d_earliest[wd] = ed

    # ── 시스 색상 묶음 lookup — CP-SAT 와 동일 규칙 ─────────────────────────
    # 그리디 경로의 batch_groups 는 {gk: [batches...]} 형식이라 묶음 빌더가 기대하는
    # {gk: {"batches": [...], "earliest_due": ..., "cpsat_dur": ..., "pred_ready": ...}}
    # 형식으로 wrapping 한 뒤 전달한다.
    from app.domain.sheath_cluster import (
        build_sheath_clusters,
        cluster_sort_key,
    )

    _gm_for_cluster = {
        gk: {
            "batches": gb,
            "earliest_due": _group_earliest_due(gb),
            "cpsat_dur": int(sum(float(b.estimated_duration_min or 0) for b in gb)),
            "pred_ready": None,
        }
        for gk, gb in batch_groups.items()
    }
    _sheath_clusters_g = build_sheath_clusters(_gm_for_cluster)
    _sorted_clusters_g = sorted(
        _sheath_clusters_g, key=lambda c: cluster_sort_key(c, _gm_for_cluster)
    )
    _cluster_rank_g: dict[str, tuple[int, int]] = {}
    for _ci, _cluster in enumerate(_sorted_clusters_g):
        for _gi, _gk_c in enumerate(_cluster.group_keys):
            _cluster_rank_g[_gk_c] = (_ci, _gi)

    # 설비별 마지막 처리 시스 묶음 ID 추적 — 묶음 내부/경계 append 정책에 사용.
    # auto_schedule 호출 당 초기화 (모듈 레벨 상태 공유 방지).
    _gk_to_cluster_id_g: dict[str, str] = {}
    for _c in _sorted_clusters_g:
        for _gk_c in _c.group_keys:
            _gk_to_cluster_id_g[_gk_c] = _c.cluster_id
    _prev_cluster_on_eq_g: dict[str, str] = {}  # equipment_code → last cluster_id

    # ── 그룹 처리 순서 결정 ──────────────────────────────────────────────────
    # 우선순위:
    #   0 = CORE/AL-CORE: 선행 공정이므로 반드시 먼저 스케줄링
    #   1 = ST- 연선 그룹: 소선경(wire_diameter) 클러스터 단위로 연속 배치
    #       클러스터 내 정렬: 클러스터 최초납기 → 소선경 → 그룹 최초납기
    #   2 = 그 외 공정(절연·시스 등): 공정 순서(PROCESS_ORDER) 최우선 → EDD
    #       파이프라인 보장: 절연(2)이 시스(4)보다 항상 먼저 스케줄링되어야
    #       process_first_output_by_sq에 절연 데이터가 등록된 후 시스가 참조 가능.
    #
    # 시스 그룹 전용 체인 정렬:
    #   - 1차: 납기 주 버킷(H1/H2) — 납기 최우선
    #   - 2차: 색상 순위 (_SHEATH_COLOR_RANK: 흑→갈→회→청…) — 같은 주차 내 묶기
    #   - 3차: 실제 납기일 (같은 주+색상 내 stable EDD)
    #   → 납기를 지키면서 같은 주차 내에서만 색상을 묶어 교체 비용 최소화.
    #     색상을 1차로 두면 멀리 있는 주차의 같은 색상 그룹이 먼저 끌려와
    #     급한 납기(다른 색상)가 뒤로 밀리는 현상이 발생해 사용자 룰과 상충.
    #   Tradeoff: 같은 주차 내에 여러 색상이 있으면 그만큼 교체가 발생한다.
    #     하지만 시스 설비는 주당 그룹 수가 제한적(≤ ~4건)이라 주차별 교체는
    #     최대 2~3회 수준으로 수렴. 납기 준수 이득이 더 크다.
    def _group_sort_key(kv):
        gk, gb = kv
        tier = 0 if _is_core_group(gk) else (1 if gk.startswith("ST-") else 2)
        proc_order = PROCESS_ORDER.get(gb[0].process_name, 50) if gb else 50
        earliest_due = _group_earliest_due(gb)
        cust_prio = gb[0].customer_priority or 99 if gb else 99

        # 시스 체인: 묶음 단위 정렬 — CP-SAT _solved_order_key 와 동일 규칙
        # _cluster_rank_g 는 _group_sort_key 정의 전에 build_sheath_clusters 로 구성된
        # lookup 테이블로, (cluster_idx, position_in_cluster) 를 제공한다.
        if _is_sheath_group(gk, gb):
            rank = _cluster_rank_g.get(gk, (10**9, 10**9))
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
            # 시스 정렬키와 길이를 맞추기 위한 padding (비교 시 영향 없도록 동일 상수)
            0,
            date.max,
            cust_prio,
        )

    ordered_group_items = sorted(batch_groups.items(), key=_group_sort_key)

    for group_key, group_batches in ordered_group_items:
        rep = group_batches[0]  # 대표 배치 (설비 선정용)

        # 10-3: 시스 재질 라우팅
        candidate_equip = equipment_by_process.get(rep.process_name, [])
        if rep.process_name in ("고압시스", "저압시스"):
            candidate_equip = _filter_by_sheath_routing(rep, candidate_equip)

        # 저압시스 A100/A120 색상별 설비 강제 라우팅
        # A120 배치(흑/청) → SH-A120 전용, A100 배치(갈/회/녹황) → SH-A100 전용
        if group_key.startswith("A120_"):
            candidate_equip = [
                e for e in candidate_equip if e.equipment_code == "SH-A120"
            ]
        elif group_key.startswith("A100_"):
            candidate_equip = [
                e for e in candidate_equip if e.equipment_code == "SH-A100"
            ]

        eligible = _find_eligible_equipment(rep, candidate_equip)

        sq = int(rep.sq_mm2 or 0)
        sq_key = (rep.process_name, sq)
        is_stranding = rep.process_name == "연선"

        # ── 61연선 설비 선호도 좁히기 (T6BO / 54BO) ──────────────────────
        # 매칭 설비 없으면 eligible 전체 유지 → 스케줄링 skip 방지
        if is_stranding:
            eligible = _narrow_by_stranding(rep, eligible)

        # ── 규칙 2: 같은 SQ → 같은 설비 (연선 공정만, 70SQ+) ─────────────
        # CORE-/AL-CORE- 그룹 제외: 61연선에서 CORE와 ST는 서로 다른 설비를 타야 함
        if (
            is_stranding
            and sq >= 70
            and sq_key in sq_to_equip
            and not _is_core_group(group_key)
        ):
            preferred_eq = sq_to_equip[sq_key]
            pref_match = [e for e in eligible if e.equipment_code == preferred_eq]
            if pref_match:
                eligible = pref_match

        # ── T/P 공정 preferred 설비: TP-2 (not alphabetical TP-1) ────────────
        # Why: _find_speed.equipment_map["T/P"] = ["TP-2"] 이므로 duration 계산과
        # 실제 배정 설비를 일관되게 유지한다. PDF 1안도 T/P#2 만 사용.
        # 만약 TP-2 가 eligible 에서 제외 (color/range 필터 등) 되면 fallback.
        if rep.process_name == "T/P":
            tp2_match = [e for e in eligible if e.equipment_code == "TP-2"]
            if tp2_match:
                eligible = tp2_match

        # ── 규칙 3: 소선경 그루핑 ────────────────────────────────────────────
        # drum_lot_master.wire_diameter 기준 — 동일 소선경 SQ는 같은 설비 선호
        if is_stranding and sq_key not in sq_to_equip and not _is_core_group(group_key):
            wire_d = sq_to_wire_d.get(sq, 0.0)
            if wire_d > 0:
                same_wd_equips = set()
                for (proc, s), eq_code in sq_to_equip.items():
                    if proc == "연선" and sq_to_wire_d.get(s, -1.0) == wire_d:
                        same_wd_equips.add(eq_code)
                if same_wd_equips:
                    wd_match = [
                        e for e in eligible if e.equipment_code in same_wd_equips
                    ]
                    if wd_match:
                        eligible = wd_match

        if not eligible:
            candidate_codes = [e.equipment_code for e in candidate_equip]
            result["warnings"].append(
                f"배치그룹 {group_key}: 공정 '{rep.process_name}' SQ={rep.sq_mm2} "
                f"재질={rep.conductor_material} — 적합한 설비 없음 "
                f"(후보설비={candidate_codes})"
            )
            # 미스케줄된 공정을 max 시간으로 등록 → 후행 공정이 이 공정 없이 시작하는 것을 방지
            sq_int = int(rep.sq_mm2 or 0)
            process_end_by_sq[(rep.process_name, sq_int)] = datetime.max
            process_first_output_by_sq[(rep.process_name, sq_int)] = datetime.max
            continue

        # ── 멀티설비 분배: 드럼 수 >= 2 이고 적격 설비 >= 2 일 때
        #    드럼을 설비 수로 균등 분할하여 병렬 배치 ─────────────────────────
        #    대상: 연선(CORE 제외) + 고압절연(CV#1/CV#2 분배)
        header_batch_chk = next((b for b in group_batches if b.batch_seq == -1), None)
        if header_batch_chk:
            total_drums = int(header_batch_chk.drum_count or 0)
        else:
            # 고압절연 등 헤더 없는 공정: 그룹 내 배치 drum_count 합산
            total_drums = sum(int(b.drum_count or 0) for b in group_batches)
        is_high_insul = rep.process_name == "고압절연"
        is_high_sheath = rep.process_name == "고압시스"
        multi_eligible = (
            (
                is_stranding
                and not _is_core_group(group_key)
                and sq_key not in sq_to_equip
            )
            or is_high_insul
            or is_high_sheath
        )
        if multi_eligible and total_drums >= 2 and len(eligible) >= 2:
            split_ok = _schedule_multi_equipment(
                group_key=group_key,
                group_batches=group_batches,
                eligible=eligible,
                total_drums=total_drums,
                header_batch=header_batch_chk,
                base_date=base_date,
                run_label=run_label,
                db=db,
                speed_map=speed_map,
                timeline=timeline,
                last_batch_on_equip=last_batch_on_equip,
                sq_to_equip=sq_to_equip,
                predecessor_map=predecessor_map,
                process_end_by_sq=process_end_by_sq,
                process_first_output_by_sq=process_first_output_by_sq,
                core_first_drum_by_main_sq=core_first_drum_by_main_sq,
                tasks_created=tasks_created,
                result=result,
                welding_min=welding_min,
                sq_to_wire_d=sq_to_wire_d,
            )
            if split_ok:
                continue

        # ── 그룹 전체 duration 계산 ──────────────────────────────────────────
        # batch_seq=-1 헤더 배치가 있으면 그 estimated_duration_min을 직접 사용.
        # (연선 그룹: 실제 작업량 work_qty_g / 선속 — 수주 건수와 무관)
        # 헤더 없으면 기존 방식으로 각 배치 duration 합산.
        rep_speed = float(rep.line_speed_mpm or 0)
        if rep_speed <= 0:
            # SpeedMaster에서 해당 설비+SQ 조합의 line_speed 조회
            for eq in eligible:
                sm = speed_map.get((eq.equipment_code, float(rep.sq_mm2 or 0)))
                if sm and sm.line_speed_mpm and float(sm.line_speed_mpm) > 0:
                    rep_speed = float(sm.line_speed_mpm)
                    break
        line_speed = rep_speed if rep_speed > 0 else 10
        header_batch = next((b for b in group_batches if b.batch_seq == -1), None)
        if header_batch is not None:
            hd = float(header_batch.estimated_duration_min or 0)
            if hd <= 0:
                total = float(header_batch.total_length_m or 0)
                ls = float(header_batch.line_speed_mpm or 0) or line_speed
                hd = total / ls if ls > 0 else 60
            group_duration = hd
        else:
            group_duration = 0.0
            for b in group_batches:
                d = float(b.estimated_duration_min or 0)
                if d <= 0:
                    total = float(b.total_length_m or 0) + float(b.extra_length_m or 0)
                    ls = float(b.line_speed_mpm or 0) or line_speed
                    d = total / ls if ls > 0 else 60
                group_duration += d

        setup_min = float(rep.setup_time_min or 0)
        drum_winding_min = _get_drum_winding_min(
            eligible[0].equipment_code, rep.sq_mm2, speed_map
        )
        total_duration = group_duration + setup_min + drum_winding_min

        # ── 설비 선택 (최적 슬롯 탐색) ───────────────────────────────────────
        best_eq = None
        best_start = None
        best_total_duration = total_duration

        for eq in eligible:
            eq_code = eq.equipment_code
            slots = timeline.get(eq_code, [])

            eq_total_duration = total_duration

            # 4-1: 연선 셋업 3-tier (동일SQ=0 / 동일소선경=선재교체 / 다른소선경=규격교체)
            prev_batch = last_batch_on_equip.get(eq_code)
            if prev_batch is not None and rep.process_name == "연선":
                compound_min = float(
                    speed_map.get((eq_code, float(rep.sq_mm2 or 0)), None)
                    and speed_map[(eq_code, float(rep.sq_mm2 or 0))].setup_compound_min
                    or 0
                )
                actual_setup = _get_stranding_setup_min(
                    float(prev_batch.sq_mm2) if prev_batch.sq_mm2 else None,
                    float(rep.sq_mm2) if rep.sq_mm2 else None,
                    sq_to_wire_d,
                    spec_min=setup_min,
                    compound_min=compound_min,
                )
            else:
                actual_setup = setup_min
                if prev_batch is not None:
                    same_sq = (
                        prev_batch.sq_mm2 is not None
                        and rep.sq_mm2 is not None
                        and float(prev_batch.sq_mm2) == float(rep.sq_mm2)
                    )
                    if same_sq:
                        actual_setup = 0.0
            eq_total_duration = eq_total_duration - setup_min + actual_setup

            # 4-2: 색상교체 시간 — 그룹 간 변경 시 (SpeedMaster 조회)
            color_change_min = 0.0
            if prev_batch is not None and rep.process_name in (
                "저압시스",
                "고압시스",
                "HFCO시스",
            ):
                prev_color = (prev_batch.sheath_color or "").strip()
                curr_color = (rep.sheath_color or "").strip()
                if prev_color and curr_color and prev_color != curr_color:
                    # SpeedMaster에서 해당 설비의 색상교체 시간 조회
                    sm_color = (
                        db.query(SpeedMaster.setup_color_min)
                        .filter(SpeedMaster.equipment_code == eq.equipment_code)
                        .first()
                    )
                    sm_color_val = sm_color[0] if sm_color else None
                    color_change_min = resolve_color_change_min(
                        sm_color_min=sm_color_val,
                        params=constraint_params,
                    )
            eq_total_duration += color_change_min

            # ── 선행공정(predecessor) — 공정 순서에 따라 앞 공정 종료 후 시작
            earliest = base_date
            sq_int = int(rep.sq_mm2 or 0)

            # 파이프라인 겹침: 앞 공정에서 첫 번째 드럼이 나오면 후공정 시작 가능
            # 연선 1틀 완료 → 절연 시작 / 절연 1틀 완료 → 시스 시작
            pred_proc = PREDECESSOR_PROCESS.get(rep.process_name)
            if pred_proc:
                all_sqs = {int(b.sq_mm2 or 0) for b in group_batches}
                if len(all_sqs) > 1:
                    # 색상 기준 혼합 SQ 그룹(시스): 어느 SQ든 첫 드럼이 나오면 시작 가능
                    # → 그룹 내 SQ 중 가장 이른 첫 출력 시각을 선행 제약으로 사용
                    valid_firsts = [
                        t
                        for sq_i in all_sqs
                        if (t := process_first_output_by_sq.get((pred_proc, sq_i)))
                        and t < datetime.max
                    ]
                    if valid_firsts:
                        pred_min = min(valid_firsts)
                        if pred_min > earliest:
                            earliest = pred_min
                else:
                    # 단일 SQ 그룹: 해당 SQ의 선행 제약만 확인
                    sq_i = next(iter(all_sqs))
                    pred_first = process_first_output_by_sq.get((pred_proc, sq_i))
                    if pred_first and pred_first > earliest:
                        earliest = pred_first
                if rep.process_name == "고압시스":
                    earliest += timedelta(hours=20)

            # 시스 배치(A100/A120): 저압절연 첫 번째 드럼 출력 후 시작
            if group_key.startswith("A100_") or group_key.startswith("A120_"):
                if first_insul_output and first_insul_output > earliest:
                    earliest = first_insul_output

            # 61연선 ST- 그룹: 동일 SQ의 CORE/AL-CORE 첫 드럼 출력 후 시작 (overlap)
            # 예: "ST-633-..." 그룹 → core_first_drum_by_main_sq[633] 이후 시작
            if group_key.startswith("ST-") and rep.process_name == "연선":
                try:
                    main_sq = int(group_key.split("-")[1])
                except (IndexError, ValueError):
                    main_sq = sq_int
                core_first = core_first_drum_by_main_sq.get(main_sq)
                if core_first and core_first > earliest:
                    earliest = core_first

            # 개별 수주 레벨 predecessor — 절연/시스/연합/T/P는 first-drum overlap만 사용
            # ST-* 연선 그룹: CORE first-drum overlap 사용 → 개별 predecessor 스킵
            # 개별 predecessor end_datetime을 쓰면 전체 완료를 기다리게 되어 overlap 무효화
            _is_st_group = group_key.startswith("ST-") and rep.process_name == "연선"
            _skip_individual = (
                rep.process_name
                in (
                    "저압절연",
                    "고압절연",
                    "저압시스",
                    "고압시스",
                    "연합",
                    "T/P",
                )
                or _is_st_group
            )
            if not _skip_individual:
                for b in group_batches:
                    pred_key = (b.sales_order_id, b.sales_order_line)
                    pred_tid = predecessor_map.get(pred_key)
                    if pred_tid:
                        pred_task = next(
                            (t for t in tasks_created if t.task_id == pred_tid),
                            None,
                        )
                        if pred_task and pred_task.end_datetime > earliest:
                            earliest = pred_task.end_datetime

            # ── 시스 묶음 기반 append 정책 (CP-SAT 와 동일) ─────────────────
            # 묶음 내부: 무조건 append (색상 체인 유지)
            # 묶음 경계: 조건부 append (납기 초과 예상이면 earliest 유지 →
            # _find_available_slot 이 빈 공간 사용 → 납기 보호)
            eq_earliest = earliest
            if rep.process_name in ("저압시스", "고압시스"):
                current_cluster_id = _gk_to_cluster_id_g.get(group_key)
                prev_cluster = _prev_cluster_on_eq_g.get(eq_code)
                if slots and current_cluster_id:
                    last_end = max(s[1] for s in slots)
                    append_earliest = max(eq_earliest, last_end)
                    if prev_cluster == current_cluster_id:
                        eq_earliest = append_earliest
                    else:
                        append_end = calculate_end_datetime(
                            append_earliest, eq_total_duration, db, eq_code
                        )
                        due = _group_earliest_due(group_batches)
                        if not due or append_end.date() <= due:
                            eq_earliest = append_earliest

            slot_start = _find_available_slot(
                eq_earliest, eq_total_duration, slots, db, eq.equipment_code
            )

            if best_start is None or slot_start < best_start:
                best_eq = eq
                best_start = slot_start
                best_total_duration = eq_total_duration

        if best_eq is None or best_start is None:
            result["warnings"].append(f"배치그룹 {group_key}: 가용 슬롯 없음")
            continue

        end_dt = calculate_end_datetime(
            best_start, best_total_duration, db, best_eq.equipment_code
        )

        # ── 파이프라인 유휴 최소 역산 공식 ────────────────────────────────────
        # 물리: 후공정은 선행 마지막 드럼이 나와야 자기 마지막 드럼을 처리 가능.
        # T_succ_end = T_pred_end + D_succ_per_drum
        # T_succ_start = T_succ_end - D_succ_total (블록 폭 유지)
        _per_drum_min = (
            group_duration / max(int(total_drums or 1), 1)
            if group_duration > 0
            else 0.0
        )
        best_start, end_dt = align_start_to_predecessor_end(
            process_name=rep.process_name,
            pred_proc=pred_proc,
            group_sqs={int(b.sq_mm2 or 0) for b in group_batches},
            process_end_by_sq=process_end_by_sq,
            current_start=best_start,
            current_end=end_dt,
            duration_min=best_total_duration,
            tail_offset_min=_per_drum_min,
            slots=timeline.get(best_eq.equipment_code, []),
            db=db,
            equipment_code=best_eq.equipment_code,
        )

        # ── 시간 올림 — 간트 블록은 정각 단위로 표시 ────────────────────────
        if end_dt.minute > 0 or end_dt.second > 0 or end_dt.microsecond > 0:
            end_dt = end_dt.replace(minute=0, second=0, microsecond=0) + timedelta(
                hours=1
            )

        # ── 그룹당 1 schedule_task 생성 ──────────────────────────────────────
        # 체인 하이라이트 — 본 그룹의 상류 task id 를 predecessor 로 고정.
        # 같은 group 내 복수 order 가 있어도 대표 order 의 predecessor 로 일관 처리.
        rep_pred_task_id = predecessor_map.get(
            (rep.sales_order_id, rep.sales_order_line)
        )

        task = ScheduleTask(
            batch_id=rep.batch_id,  # 대표 배치 ID
            equipment_code=best_eq.equipment_code,
            start_datetime=best_start,
            end_datetime=end_dt,
            setup_time_min=setup_min,
            status="scheduled",
            run_label=run_label,
            batch_group=group_key,
            predecessor_task_id=rep_pred_task_id,
        )
        db.add(task)
        db.flush()

        timeline.setdefault(best_eq.equipment_code, []).append((best_start, end_dt))

        # 공정+SQ별 종료 시각 갱신 (후공정 선행관계 추적)
        sq_int = int(rep.sq_mm2 or 0)
        proc_sq_key = (rep.process_name, sq_int)
        if (
            proc_sq_key not in process_end_by_sq
            or end_dt > process_end_by_sq[proc_sq_key]
        ):
            process_end_by_sq[proc_sq_key] = end_dt

        # ── 파이프라인 겹침: 첫 번째 드럼 출력 시각 계산 ────────────────────
        # 연선 ST-: 헤더 배치(seq=-1)의 drum_count = 실제 틀 수
        # CORE-/AL-CORE-: drum_count 합산 — 드럼 하나씩 완료될 때마다 ST 시작 가능
        #   (AL6BO에서 한 드럼 완료 → 54BO 즉시 시작하는 파이프라인)
        # 절연/시스 등: 헤더 없으므로 그룹 내 배치 수 = 순차 처리 단위 수
        if header_batch is not None:
            lot_count = max(int(header_batch.drum_count or 1), 1)
        else:
            # CORE 그룹 포함, 절연/시스 등 헤더 없는 그룹 모두 drum_count 합산
            lot_count = max(sum(int(b.drum_count or 1) for b in group_batches), 1)
        first_drum_min = setup_min + (group_duration / lot_count)
        first_output_dt = calculate_end_datetime(
            best_start, first_drum_min, db, best_eq.equipment_code
        )
        # CORE-/AL-CORE- 그룹 제외: 절연은 ST(54BO) 첫 드럼 기준으로 시작해야 함
        # (CORE 첫 드럼은 너무 이르므로 후행 공정 선행 제약으로 부적합)
        if not _is_core_group(group_key) and (
            proc_sq_key not in process_first_output_by_sq
            or first_output_dt < process_first_output_by_sq[proc_sq_key]
        ):
            process_first_output_by_sq[proc_sq_key] = first_output_dt

        # 61연선 CORE-/AL-CORE- 그룹 첫 드럼 출력 시각 기록 — pipeline overlap
        # CU: "CORE-{main_sq}-...", AL: "AL-CORE-{main_sq}-..." 패턴
        if _is_core_group(group_key):
            main_sq = _extract_core_main_sq(group_key)
            if main_sq is not None:
                if (
                    main_sq not in core_first_drum_by_main_sq
                    or first_output_dt < core_first_drum_by_main_sq[main_sq]
                ):
                    core_first_drum_by_main_sq[main_sq] = first_output_dt

        # 저압절연 첫 번째 드럼 출력 시각 — A100/A120 시스 그룹 시작 기준
        if rep.process_name == "저압절연":
            if first_insul_output is None or first_output_dt < first_insul_output:
                first_insul_output = first_output_dt

        # 그룹 내 모든 배치의 predecessor + status 갱신
        for b in group_batches:
            pred_key = (b.sales_order_id, b.sales_order_line)
            predecessor_map[pred_key] = task.task_id
            b.equipment_code = best_eq.equipment_code
            b.status = "scheduled"

        # 규칙 2: SQ→설비 매핑 기록 (CORE/AL-CORE 그룹 제외 — 코어는 ST설비 고정 대상 아님)
        if rep.process_name == "연선" and not _is_core_group(group_key):
            sq_to_equip[sq_key] = best_eq.equipment_code

        # 용접 시간 추적 (4-4): 설비별 마지막 배치 갱신 (그룹의 마지막 배치)
        last_batch_on_equip[best_eq.equipment_code] = group_batches[-1]

        # 시스 묶음 기반 append 정책 — 현재 그룹의 cluster_id 로 갱신
        if rep.process_name in ("저압시스", "고압시스"):
            _cid_g = _gk_to_cluster_id_g.get(group_key)
            if _cid_g:
                _prev_cluster_on_eq_g[best_eq.equipment_code] = _cid_g

        tasks_created.append(task)

        # Check delivery date violation — 그룹 내 가장 빠른 납기 기준
        # 납기는 사용자 요구 상 하드 제약 → severity=error 로 상향
        # (validate_all 이 이를 보고 재시도/알림을 유발하도록)
        earliest_due = min(
            (b.due_date for b in group_batches if b.due_date), default=None
        )
        if earliest_due and end_dt.date() > earliest_due:
            violation = {
                "batch_id": rep.batch_id,
                "task_id": task.task_id,
                "type": "delivery",
                "severity": "error",
                "detail": f"납기 {earliest_due} 초과 → 완료 예정 {end_dt.date()}",
            }
            result["violations"].append(violation)
            result.setdefault("warnings", []).append(
                f"납기 위반 예상: 배치그룹 {group_key} end={end_dt.date()} > due={earliest_due}"
            )

        # Audit log
        log_decision(
            db=db,
            run_label=run_label,
            stage="stage2",
            batch_id=rep.batch_id,
            task_id=task.task_id,
            action_type="schedule_placed",
            constraints_applied=[
                {
                    "id": "1-1",
                    "name": "거래처 우선순위",
                    "result": "pass",
                    "detail": f"priority={rep.customer_priority}",
                },
                {
                    "id": "4-3",
                    "name": "드럼 권취 시간",
                    "result": "pass",
                    "detail": f"drum_winding={drum_winding_min:.0f}분 추가",
                },
                {
                    "id": "4-4",
                    "name": "용접 시간",
                    "result": "pass",
                    "detail": f"welding={welding_min:.0f}분 (스플라이스 로트 시 적용)",
                },
                {
                    "id": "4-5",
                    "name": "테이핑 속도 제한",
                    "result": "pass",
                    "detail": f"process={rep.process_name}, speed={line_speed:.1f}mpm",
                },
                {
                    "id": "5-1",
                    "name": "SQ 기준 설비 배정",
                    "result": "pass",
                    "detail": f"{best_eq.equipment_name} (range {best_eq.range_min}~{best_eq.range_max})",
                },
                {
                    "id": "10-2",
                    "name": "CU/AL 재질 분리",
                    "result": "pass",
                    "detail": f"material={rep.conductor_material}, equip_limit={best_eq.material_limit}",
                },
                {
                    "id": "10-3",
                    "name": "시스 재질 라우팅",
                    "result": "pass",
                    "detail": f"sheath_type={_get_sheath_type(rep)}, equip={best_eq.equipment_code}",
                },
            ],
            reason=(
                f"설비 {best_eq.equipment_name}에 배치: "
                f"SQ={batch.sq_mm2}, 납기={batch.due_date}, 소요={best_total_duration:.0f}분"
            ),
        )

        result["total_tasks"] += 1

    return result


# ── 보조 헬퍼 — 모듈 외부에서 호출되거나 audit-log 에 직접 사용 ────────────


def _get_tp_line_speed(
    equipment_code: str,
    sq_mm2,
    speed_map: dict,
) -> float | None:
    """
    4-5: T/P 설비 전용 라인 속도 반환.
    SpeedMaster에서 해당 설비 + SQ 조합의 line_speed_mpm을 읽는다.
    매칭 레코드 없으면 None 반환 (호출자가 기본값 사용).
    """
    if sq_mm2 is None:
        return None
    sq_key = float(sq_mm2)
    record = speed_map.get((equipment_code, sq_key))
    if record is None:
        # 근사 SQ 탐색
        candidates = [
            (abs(k[1] - sq_key), v)
            for k, v in speed_map.items()
            if k[0] == equipment_code
        ]
        if candidates:
            record = min(candidates, key=lambda x: x[0])[1]
    if record is None:
        return None
    speed = record.line_speed_mpm
    return float(speed) if speed else None


def _get_sheath_type(batch: ProductionBatch) -> str:
    """
    10-3: 배치에서 시스 재질 추출.
    ProductionBatch에 별도 sheath_type 컬럼이 없으므로 remarks 필드를
    우선 확인하고, 없으면 product_group에서 추론한다.
    인식 가능한 값: HFPO, LLDPE, PVC (기본값)
    """
    # remarks 또는 product_group에 재질 명시된 경우
    for field_val in (batch.remarks or "", batch.product_group or ""):
        upper = field_val.upper()
        if "HFPO" in upper:
            return "HFPO"
        if "LLDPE" in upper:
            return "LLDPE"
        if "PVC" in upper:
            return "PVC"
    return "PVC"  # 기본값
