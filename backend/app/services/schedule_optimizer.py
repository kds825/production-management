"""자동 스케줄링 엔진 — 납기역산 + 그리디 배치

── Task #7 조사 결과 ─────────────────────────────────────────────────
1. batch_seq 정렬:
   - 61연선의 7연선 코어 배치(batch_seq=0)가 본 배치(batch_seq=1)보다 먼저
     스케줄링되도록 batches query에서 batch_seq.asc()를 포함한다 (line 78).
   - 이는 predecessor_map을 통해 코어→본 배치 선행관계를 올바르게 구성하기 위함이다.

2. 공정 간 선행관계:
   - _PREDECESSOR_PROCESS dict가 연선→절연→시스 파이프라인을 강제한다.
   - process_end_by_sq로 같은 SQ의 앞 공정 종료 시각을 추적하여 후공정 시작을 지연시킨다.
   - A100/A120 시스 배치는 저압절연 첫 번째 드럼 출력 후 시작 (파이프라인 겹침).

3. 제한사항:
   - 프론트엔드 간트에서의 수동 블록 이동 시에는 이 파이프라인 제약이 재적용되지 않는다.
   - scheduleStore.ts의 moveTask는 같은 order_id의 후공정만 연동하며,
     cross-equipment cascade는 미구현 상태이다.
"""

from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.schedule_task import ScheduleTask
from app.infrastructure.models.equipment_master import EquipmentMaster
from app.infrastructure.models.speed_master import SpeedMaster
from app.infrastructure.models.constraint_config import ConstraintConfig
from app.domain.constants import PROCESS_ORDER
from app.services.calendar_engine import calculate_end_datetime
from app.services.audit_logger import log_decision

# WIP 공정 스킵 매핑: process_stage → 간트 미배치 공정 목록
# 절연재고: 신선/연선/절연까지 이미 완료 → 해당 공정 스케줄 불필요
# 연선재고: 신선/연선까지 이미 완료
_WIP_SKIP_PROCESSES: dict[str, set[str]] = {
    "절연재고": {"신선", "연선", "저압절연", "고압절연"},
    "연선재고": {"신선", "연선"},
}

# 용접 시간 기본값 (4-4): constraint_config params_json에서 읽을 때 없으면 사용
_DEFAULT_WELDING_MIN = 30

# ── 연선 설비 배정 규칙 (KBI 공정설비 규격 정리 기준) ──────────────────────
# SQ → 소선경(mm) 매핑 — 같은 소선경 SQ를 같은 설비에 연속 배치 (규칙 3)
_SQ_TO_WIRE_DIAMETER: dict[int, float] = {
    16: 1.75,
    25: 2.21,
    35: 2.64,
    50: 3.06,  # 7연선
    70: 2.21,
    95: 2.64,
    120: 2.92,  # 19연선
    150: 2.34,
    185: 2.60,
    240: 3.06,  # 37연선
    300: 2.60,
    400: 2.92,  # 61연선
    633: 1.20,  # 1250kcmil 압축연선 499본
}

# 시스 재질 → 설비 라우팅 규칙 (10-3)
# 값은 equipment_code prefix 또는 특수 라우팅 키
_SHEATH_ROUTING = {
    "HFPO": "HFPO",  # HFPO 전용 라우팅 (설비 선택 시 HFPO 계열만)
    "PVC": "PVC",  # 표준 PVC 라우팅
    "LLDPE": "A150",  # LLDPE → A150 설비 고정
}

# 공정 간 선행/후행 관계 — 연선→절연→시스 파이프라인 강제
# schedules.py cascade_preview 와 공유하는 단일 진실 공급원(single source of truth)
PREDECESSOR_PROCESS: dict[str, str] = {
    "저압절연": "연선",
    "고압절연": "연선",
    "저압시스": "저압절연",
    "고압시스": "고압절연",
    "연합": "연선",
}


def _is_core_group(group_key: str) -> bool:
    """CORE 또는 AL-CORE 그룹 키인지 판별 (CU/AL 공통)."""
    return group_key.startswith("CORE-") or group_key.startswith("AL-CORE-")


def _extract_core_main_sq(group_key: str) -> int | None:
    """CORE/AL-CORE 그룹 키에서 main SQ를 추출한다.

    "CORE-633-35kV" → 633, "AL-CORE-633-35kV" → 633
    """
    try:
        parts = group_key.split("-")
        if group_key.startswith("AL-CORE-"):
            return int(parts[2])  # AL-CORE-{sq}-...
        if group_key.startswith("CORE-"):
            return int(parts[1])  # CORE-{sq}-...
    except (IndexError, ValueError):
        pass
    return None


def auto_schedule(
    run_label: str, db: Session, *, base_date: datetime | None = None
) -> dict:
    """
    run_label의 production_batch를 간트 차트에 자동 배치.

    Args:
        base_date: 스케줄 시작 기준일시. None이면 KST 당일 08:00.
    Returns: {"total_tasks": int, "violations": list, "warnings": list}
    """
    result = {"total_tasks": 0, "violations": [], "warnings": []}

    # Load all batches for this run, excluding outsourced and already-scheduled
    # 공정 순서를 포함하여 정렬 — 같은 수주의 연선이 절연보다 먼저 스케줄링되어야
    # predecessor_map이 올바르게 동작함
    # PROCESS_ORDER: 공정 순서 상수 (domain.constants에서 공유)
    batches = (
        db.query(ProductionBatch)
        .filter(
            ProductionBatch.run_label == run_label,
            ProductionBatch.status == "planned",
        )
        .order_by(
            ProductionBatch.due_date.asc(),
            ProductionBatch.customer_priority.asc(),
            ProductionBatch.batch_seq.asc(),
        )
        .all()
    )
    # Python 레벨 재정렬: batch_seq는 라우팅 내 공정 순서이지만,
    # batch_group 스케줄링: 공정 순서 최우선 (연선→절연→시스 파이프라인)
    # 같은 공정 내에서 납기→우선순위→SQ 순으로 정렬 (실제 공장 스케줄링 기준)
    batches.sort(
        key=lambda b: (
            PROCESS_ORDER.get(b.process_name, 50),
            b.batch_seq or 0,  # 61연선 코어(seq=0)가 메인(seq=1)보다 먼저
            b.due_date or date.max,
            b.customer_priority or 99,
            -(float(b.sq_mm2 or 0)),
        )
    )

    if not batches:
        result["warnings"].append("배치 없음 — Stage 1을 먼저 실행하세요")
        return result

    # ── WIP 공정 스킵: 재고로 대체 가능한 공정은 간트에 미배치 ───────────────
    from app.infrastructure.models.wip_inventory import WipInventory

    wip_ids = {b.wip_matched_id for b in batches if b.wip_matched_id is not None}
    wip_stage_map: dict[int, str] = {}
    if wip_ids:
        wips = db.query(WipInventory).filter(WipInventory.wip_id.in_(wip_ids)).all()
        wip_stage_map = {w.wip_id: w.process_stage or "" for w in wips}

    schedulable: list[ProductionBatch] = []
    wip_skipped = 0
    for batch in batches:
        if batch.wip_matched_id and batch.wip_matched_id in wip_stage_map:
            wip_stage = wip_stage_map[batch.wip_matched_id]
            skip_set = _WIP_SKIP_PROCESSES.get(wip_stage, set())
            if batch.process_name in skip_set:
                batch.status = "wip_complete"
                wip_skipped += 1
                continue
        schedulable.append(batch)

    batches = schedulable
    if wip_skipped:
        result["wip_skipped"] = wip_skipped

    # ── 기준일시 설정 — 계획 생성일(run_label) 08:00 ─────────────────────
    # run_label 형식: "YYYYMMDD_HHMMSS" — 앞 8자리를 날짜로 파싱한다.
    # 파싱 실패 시 KST 당일 08:00으로 폴백.
    if base_date is None:
        try:
            date_part = run_label.split("_")[0]  # "20260406"
            base_date = datetime(
                int(date_part[:4]),
                int(date_part[4:6]),
                int(date_part[6:8]),
                8,
                0,
                0,
            )
        except Exception:
            from zoneinfo import ZoneInfo

            kst_now = datetime.now(ZoneInfo("Asia/Seoul"))
            base_date = kst_now.replace(
                hour=8, minute=0, second=0, microsecond=0
            ).replace(tzinfo=None)

    # Load equipment into memory
    equipment_list = db.query(EquipmentMaster).all()
    equipment_by_process = {}
    for eq in equipment_list:
        equipment_by_process.setdefault(eq.process_name, []).append(eq)

    # Load speed master into memory: (equipment_code, sq_mm2) → SpeedMaster row
    speed_records = db.query(SpeedMaster).all()
    speed_map: dict[tuple, SpeedMaster] = {}
    for sr in speed_records:
        speed_map[(sr.equipment_code, float(sr.cross_section or 0))] = sr

    # 용접 시간 (4-4): constraint_config에서 welding_min 읽기
    welding_cfg = (
        db.query(ConstraintConfig)
        .filter(ConstraintConfig.constraint_id == "4-4")
        .first()
    )
    welding_min = _DEFAULT_WELDING_MIN
    if welding_cfg and welding_cfg.params_json:
        welding_min = float(
            welding_cfg.params_json.get("welding_min", _DEFAULT_WELDING_MIN)
        )

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
    process_end_by_sq: dict[tuple[str, int], datetime] = {}
    # key: (공정명, SQ) → value: 해당 공정+SQ 그룹의 종료 시각

    # 파이프라인 겹침용: 앞 공정에서 첫 번째 드럼이 출력되는 시각
    # 연선에서 1틀이 나오면 절연 시작 가능, 절연 1틀 나오면 시스 시작 가능
    # = task.start_datetime + setup_min + (group_run_duration / drum_count)
    process_first_output_by_sq: dict[tuple[str, int], datetime] = {}

    # 저압절연 전체 중 가장 이른 첫 번째 드럼 출력 시각 — A100/A120 시스 그룹 시작 기준
    first_insul_output: datetime | None = None

    # 61연선 코어(T6B0/AL6BO) 첫 드럼 출력 시각 — pipeline overlap 기준
    # "CORE-300-..." 첫 드럼 완료 후 "ST-300-..." 시작 가능
    core_first_drum_by_main_sq: dict[int, datetime] = {}

    # ── batch_group 단위로 그루핑 ────────────────────────────────────────────
    from collections import OrderedDict

    batch_groups: OrderedDict[str, list[ProductionBatch]] = OrderedDict()
    for batch in batches:
        key = batch.batch_group or f"_single_{batch.batch_id}"
        batch_groups.setdefault(key, []).append(batch)

    # CORE-/AL-CORE- 그룹(7연선 코어)을 ST- 그룹보다 먼저 처리 — 선행 스케줄링 보장
    # Python sort는 stable하므로 동일 우선순위 내 삽입 순서 유지
    ordered_group_items = sorted(
        batch_groups.items(),
        key=lambda kv: 0 if _is_core_group(kv[0]) else 1,
    )

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

        # ── 규칙 3: 소선경 그루핑 ────────────────────────────────────────────
        if is_stranding and sq_key not in sq_to_equip and not _is_core_group(group_key):
            wire_d = _SQ_TO_WIRE_DIAMETER.get(sq, 0)
            if wire_d > 0:
                same_wd_equips = set()
                for (proc, s), eq_code in sq_to_equip.items():
                    if proc == "연선" and _SQ_TO_WIRE_DIAMETER.get(s, -1) == wire_d:
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

        # ── 멀티설비 분배: 연선 공정에서 드럼 수 >= 2 이고 적격 설비 >= 2 일 때
        #    드럼을 설비 수로 균등 분할하여 병렬 배치 ─────────────────────────
        header_batch_chk = next((b for b in group_batches if b.batch_seq == -1), None)
        total_drums = int(header_batch_chk.drum_count or 0) if header_batch_chk else 0
        if (
            is_stranding
            and not _is_core_group(group_key)
            and total_drums >= 2
            and len(eligible) >= 2
            # 같은 SQ가 이미 단일 설비에 고정된 경우 분배하지 않음 (규칙 2)
            and sq_key not in sq_to_equip
        ):
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
            )
            if split_ok:
                continue

        # ── 그룹 전체 duration 계산 ──────────────────────────────────────────
        # batch_seq=-1 헤더 배치가 있으면 그 estimated_duration_min을 직접 사용.
        # (연선 그룹: 실제 작업량 work_qty_g / 선속 — 수주 건수와 무관)
        # 헤더 없으면 기존 방식으로 각 배치 duration 합산.
        line_speed = float(rep.line_speed_mpm or 10)
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

            # 4-1: 동일SQ 셋업 스킵
            prev_batch = last_batch_on_equip.get(eq_code)
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
                    color_change_min = (
                        float(sm_color[0] or 120.0) if sm_color else 120.0
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

            # 개별 수주 레벨 predecessor도 확인 (더 늦은 것 우선)
            # 시스 공정은 제외: 혼합 SQ 그룹에서 개별 predecessor를 모두 대기하면
            # 가장 느린 절연 배치까지 기다려야 해서 26일 지연됨.
            # 시스는 위의 process-level first-drum overlap(lines 414-434)만으로
            # 파이프라인 시작 시점을 올바르게 결정한다.
            if rep.process_name not in ("저압시스", "고압시스"):
                for b in group_batches:
                    pred_key = (b.sales_order_id, b.sales_order_line)
                    pred_tid = predecessor_map.get(pred_key)
                    if pred_tid:
                        pred_task = next(
                            (t for t in tasks_created if t.task_id == pred_tid), None
                        )
                        if pred_task and pred_task.end_datetime > earliest:
                            earliest = pred_task.end_datetime

            slot_start = _find_available_slot(earliest, eq_total_duration, slots, db)

            if best_start is None or slot_start < best_start:
                best_eq = eq
                best_start = slot_start
                best_total_duration = eq_total_duration

        if best_eq is None or best_start is None:
            result["warnings"].append(f"배치그룹 {group_key}: 가용 슬롯 없음")
            continue

        end_dt = calculate_end_datetime(best_start, best_total_duration, db)

        # ── 시간 올림 — 간트 블록은 정각 단위로 표시 ────────────────────────
        if end_dt.minute > 0 or end_dt.second > 0 or end_dt.microsecond > 0:
            end_dt = end_dt.replace(minute=0, second=0, microsecond=0) + timedelta(
                hours=1
            )

        # ── 그룹당 1 schedule_task 생성 ──────────────────────────────────────
        task = ScheduleTask(
            batch_id=rep.batch_id,  # 대표 배치 ID
            equipment_code=best_eq.equipment_code,
            start_datetime=best_start,
            end_datetime=end_dt,
            setup_time_min=setup_min,
            status="scheduled",
            run_label=run_label,
            batch_group=group_key,
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
        # 연선: 헤더 배치(seq=-1)의 drum_count = 실제 틀 수
        # 절연/시스 등: 헤더 없으므로 그룹 내 배치 수 = 순차 처리 단위 수
        if header_batch is not None:
            lot_count = max(int(header_batch.drum_count or 1), 1)
        else:
            lot_count = max(len(group_batches), 1)
        first_drum_min = setup_min + (group_duration / lot_count)
        first_output_dt = calculate_end_datetime(best_start, first_drum_min, db)
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

        tasks_created.append(task)

        # Check delivery date violation — 그룹 내 가장 빠른 납기 기준
        earliest_due = min(
            (b.due_date for b in group_batches if b.due_date), default=None
        )
        if earliest_due and end_dt.date() > earliest_due:
            violation = {
                "batch_id": rep.batch_id,
                "task_id": task.task_id,
                "type": "delivery",
                "severity": "warning",
                "detail": f"납기 {earliest_due} 초과 → 완료 예정 {end_dt.date()}",
            }
            result["violations"].append(violation)

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


# ── 멀티설비 분배 ────────────────────────────────────────────────────────────
def _schedule_multi_equipment(
    *,
    group_key: str,
    group_batches: list,
    eligible: list,
    total_drums: int,
    header_batch,
    base_date: datetime,
    run_label: str,
    db: "Session",
    speed_map: dict,
    timeline: dict,
    last_batch_on_equip: dict,
    sq_to_equip: dict,
    predecessor_map: dict,
    process_end_by_sq: dict,
    process_first_output_by_sq: dict,
    core_first_drum_by_main_sq: dict,
    tasks_created: list,
    result: dict,
    welding_min: float,
) -> bool:
    """연선 그룹의 드럼을 eligible 설비에 균등 분배하여 병렬 스케줄링.

    드럼 수를 설비 수로 나눠 각 설비에 proportional duration의 task를 생성한다.
    process_end_by_sq / process_first_output_by_sq는 가장 이른 완료 기준으로 갱신.

    Returns:
        True면 분배 성공 (호출측에서 continue), False면 단일설비 경로로 폴백.
    """

    rep = group_batches[0]
    sq = int(rep.sq_mm2 or 0)
    sq_key = (rep.process_name, sq)

    # 그룹 전체 duration 계산 (header 기준)
    line_speed = float(rep.line_speed_mpm or 10)
    if header_batch is not None:
        hd = float(header_batch.estimated_duration_min or 0)
        if hd <= 0:
            total_len = float(header_batch.total_length_m or 0)
            ls = float(header_batch.line_speed_mpm or 0) or line_speed
            hd = total_len / ls if ls > 0 else 60
        group_duration = hd
    else:
        return False  # 헤더 없으면 분배 불가

    setup_min = float(rep.setup_time_min or 0)

    # 드럼을 설비 수로 균등 분할
    num_eq = len(eligible)
    drums_per_eq = []
    base_drums = total_drums // num_eq
    remainder = total_drums % num_eq
    for i in range(num_eq):
        drums_per_eq.append(base_drums + (1 if i < remainder else 0))

    # 선행공정 earliest 계산 (단일설비 경로와 동일 로직)
    earliest = base_date
    sq_int = sq

    pred_proc = PREDECESSOR_PROCESS.get(rep.process_name)
    if pred_proc:
        pred_first = process_first_output_by_sq.get((pred_proc, sq_int))
        if pred_first and pred_first > earliest:
            earliest = pred_first

    # 61연선 ST- 그룹: CORE 첫 드럼 출력 후 시작 (pipeline overlap)
    if group_key.startswith("ST-") and rep.process_name == "연선":
        try:
            main_sq = int(group_key.split("-")[1])
        except (IndexError, ValueError):
            main_sq = sq_int
        core_first = core_first_drum_by_main_sq.get(main_sq)
        if core_first and core_first > earliest:
            earliest = core_first

    # 개별 수주 predecessor 확인
    for b in group_batches:
        pred_key = (b.sales_order_id, b.sales_order_line)
        pred_tid = predecessor_map.get(pred_key)
        if pred_tid:
            pred_task = next((t for t in tasks_created if t.task_id == pred_tid), None)
            if pred_task and pred_task.end_datetime > earliest:
                earliest = pred_task.end_datetime

    # 각 설비에 분배 task 생성
    split_tasks = []
    split_end_dts = []
    split_first_outputs = []

    for i, eq in enumerate(eligible):
        eq_drums = drums_per_eq[i]
        if eq_drums <= 0:
            continue

        eq_code = eq.equipment_code
        # proportional duration
        eq_duration = group_duration * (eq_drums / total_drums)

        drum_winding_min = _get_drum_winding_min(eq_code, rep.sq_mm2, speed_map)

        # 4-1: 동일SQ 셋업 스킵
        prev_batch = last_batch_on_equip.get(eq_code)
        actual_setup = setup_min
        if prev_batch is not None:
            same_sq = (
                prev_batch.sq_mm2 is not None
                and rep.sq_mm2 is not None
                and float(prev_batch.sq_mm2) == float(rep.sq_mm2)
            )
            if same_sq:
                actual_setup = 0.0

        eq_total_duration = eq_duration + actual_setup + drum_winding_min

        slots = timeline.get(eq_code, [])
        slot_start = _find_available_slot(earliest, eq_total_duration, slots, db)
        end_dt = calculate_end_datetime(slot_start, eq_total_duration, db)

        # 시간 올림 — 간트 블록은 정각 단위
        if end_dt.minute > 0 or end_dt.second > 0 or end_dt.microsecond > 0:
            end_dt = end_dt.replace(minute=0, second=0, microsecond=0) + timedelta(
                hours=1
            )

        task = ScheduleTask(
            batch_id=rep.batch_id,
            equipment_code=eq_code,
            start_datetime=slot_start,
            end_datetime=end_dt,
            setup_time_min=actual_setup,
            status="scheduled",
            run_label=run_label,
            batch_group=group_key,
        )
        db.add(task)
        db.flush()

        timeline.setdefault(eq_code, []).append((slot_start, end_dt))
        last_batch_on_equip[eq_code] = group_batches[-1]
        split_tasks.append(task)
        split_end_dts.append(end_dt)

        # 첫 번째 드럼 출력 시각
        first_drum_min = actual_setup + (eq_duration / eq_drums)
        first_output_dt = calculate_end_datetime(slot_start, first_drum_min, db)
        split_first_outputs.append(first_output_dt)

        tasks_created.append(task)
        result["total_tasks"] += 1

    if not split_tasks:
        return False

    # 공정+SQ별 종료/첫출력 시각 — 가장 늦은 종료, 가장 이른 첫출력
    proc_sq_key = (rep.process_name, sq_int)
    latest_end = max(split_end_dts)
    earliest_first = min(split_first_outputs)

    if (
        proc_sq_key not in process_end_by_sq
        or latest_end > process_end_by_sq[proc_sq_key]
    ):
        process_end_by_sq[proc_sq_key] = latest_end

    if (
        proc_sq_key not in process_first_output_by_sq
        or earliest_first < process_first_output_by_sq[proc_sq_key]
    ):
        process_first_output_by_sq[proc_sq_key] = earliest_first

    # 그룹 내 배치 status + equipment 갱신 (첫 번째 설비를 대표로)
    for b in group_batches:
        pred_key = (b.sales_order_id, b.sales_order_line)
        predecessor_map[pred_key] = split_tasks[0].task_id
        b.equipment_code = split_tasks[0].equipment_code
        b.status = "scheduled"

    # 규칙 2 매핑은 기록하지 않음 — 분배된 그룹은 여러 설비를 사용하므로

    # 납기 위반 체크
    earliest_due = min((b.due_date for b in group_batches if b.due_date), default=None)
    if earliest_due and latest_end.date() > earliest_due:
        violation = {
            "batch_id": rep.batch_id,
            "task_id": split_tasks[0].task_id,
            "type": "delivery",
            "severity": "warning",
            "detail": f"납기 {earliest_due} 초과 → 완료 예정 {latest_end.date()}",
        }
        result["violations"].append(violation)

    return True


def _find_eligible_equipment(
    batch: ProductionBatch, equipment: list[EquipmentMaster]
) -> list[EquipmentMaster]:
    """배치에 적합한 설비 필터링 (재질, SQ범위, 색상그룹)"""
    eligible = []
    for eq in equipment:
        # ── Material filter ────────────────────────────────────────────────
        if eq.material_limit and eq.material_limit != "ALL":
            if (
                batch.conductor_material
                and batch.conductor_material != eq.material_limit
            ):
                continue

        # ── Range filter ───────────────────────────────────────────────────
        if eq.range_unit == "mm":
            pass
        elif eq.range_unit == "Ø":
            sq = float(batch.sq_mm2) if batch.sq_mm2 else None
            if sq and eq.range_max:
                approx_od = (sq**0.5) * 1.5 + 5
                if approx_od > float(eq.range_max):
                    continue
        else:
            sq = float(batch.sq_mm2) if batch.sq_mm2 else None
            if sq and eq.range_min and sq < float(eq.range_min):
                continue
            if sq and eq.range_max and sq > float(eq.range_max):
                continue

        # ── Color group filter (저압시스) ──────────────────────────────────
        if eq.color_group:
            color = (batch.sheath_color or "").strip()
            if eq.color_group == "흑/청":
                if color not in (
                    "흑",
                    "청",
                    "흑색",
                    "청색",
                    "BLACK",
                    "BLUE",
                    "BK",
                    "BL",
                    "",
                ):
                    continue

        eligible.append(eq)

    return eligible


def _narrow_by_stranding(
    batch: ProductionBatch, eligible: list[EquipmentMaster]
) -> list[EquipmentMaster]:
    """61연선 케이스 설비 선호도 좁히기 (fallback 있음).

    stranding_method 컬럼 또는 equipment_code 명칭으로 선호 설비를 추립니다.
    매칭 설비가 없으면 eligible 전체를 그대로 반환하여 스케줄링 skip을 방지합니다.
    """
    batch_st = (batch.stranding_type or "").strip()
    sq_val = float(batch.sq_mm2 or 0)

    if batch_st == "7연선코어":
        mat = (batch.conductor_material or "").upper()
        if mat == "AL":
            # AL 7연선코어 → AL6BO 선호 (material_limit="AL", stranding_method="7연선")
            preferred = [
                e
                for e in eligible
                if "AL6B" in (e.equipment_code or "").upper()
                or (
                    (e.stranding_method or "").strip() == "7연선"
                    and (e.material_limit or "").upper() == "AL"
                )
            ]
            return preferred if preferred else eligible
        # CU 7연선코어 → T6BO 선호
        preferred = [
            e
            for e in eligible
            if (e.stranding_method or "").strip() == "7연선"
            or "T6B" in (e.equipment_code or "").upper()
        ]
        return preferred if preferred else eligible

    if sq_val >= 300:
        # 54BO 선호: stranding_method="61연선" 또는 코드에 "54BO" 포함
        preferred = [
            e
            for e in eligible
            if (e.stranding_method or "").strip() == "61연선"
            or "54BO" in (e.equipment_code or "").upper()
        ]
        return preferred if preferred else eligible

    # 일반 연선: 7연선/61연선 전용 설비는 제외 (stranding_method 기준, 없으면 무시)
    excluded = [
        e for e in eligible if (e.stranding_method or "").strip() in ("7연선", "61연선")
    ]
    if len(excluded) < len(eligible):
        return [e for e in eligible if e not in excluded]
    return eligible


def _find_available_slot(
    earliest: datetime, duration_min: float, occupied_slots: list, db=None
) -> datetime:
    """설비에서 가용한 첫 번째 슬롯 찾기"""
    candidate = earliest
    sorted_slots = sorted(occupied_slots, key=lambda s: s[0])

    for slot_start, slot_end in sorted_slots:
        if candidate + timedelta(minutes=duration_min) <= slot_start:
            # Fits before this slot
            return candidate
        if candidate < slot_end:
            candidate = slot_end  # Push after this slot

    return candidate


def _get_drum_winding_min(
    equipment_code: str,
    sq_mm2,
    speed_map: dict,
) -> float:
    """
    4-3: 드럼 권취 시간 반환.
    SpeedMaster.setup_start_min을 (equipment_code, sq_mm2) 키로 조회.
    매칭되는 레코드가 없으면 0 반환.
    """
    if sq_mm2 is None:
        return 0.0
    sq_key = float(sq_mm2)
    record = speed_map.get((equipment_code, sq_key))
    if record is None:
        # SQ 정확 매칭 실패 시 가장 가까운 SQ 레코드 탐색
        candidates = [
            (abs(k[1] - sq_key), v)
            for k, v in speed_map.items()
            if k[0] == equipment_code
        ]
        if candidates:
            record = min(candidates, key=lambda x: x[0])[1]
    if record is None:
        return 0.0
    return float(record.setup_start_min or 0)


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


def _filter_by_sheath_routing(
    batch: ProductionBatch,
    equipment: list[EquipmentMaster],
) -> list[EquipmentMaster]:
    """
    10-3: 시스 재질에 따른 설비 라우팅 필터.
    - HFPO → equipment_code 또는 equipment_name에 'HFPO' 포함 설비만
    - LLDPE → equipment_code == 'A150' 설비만
    - PVC   → 표준 라우팅 (필터 없음)
    """
    sheath_type = _get_sheath_type(batch)
    routing_key = _SHEATH_ROUTING.get(sheath_type, "PVC")

    if routing_key == "PVC":
        # 표준 라우팅 — 제약 없음
        return equipment

    if routing_key == "HFPO":
        # HFPO 전용 설비 필터
        filtered = [
            eq
            for eq in equipment
            if "HFPO" in (eq.equipment_code or "").upper()
            or "HFPO" in (eq.equipment_name or "").upper()
        ]
        # HFPO 전용 설비가 없으면 전체 허용 (fallback — 경고는 checker에서 처리)
        return filtered if filtered else equipment

    if routing_key == "A150":
        # LLDPE → A150 고정
        filtered = [eq for eq in equipment if eq.equipment_code == "A150"]
        return filtered if filtered else equipment

    return equipment


def reschedule(
    run_label: str, db: Session, *, base_date: datetime | None = None
) -> dict:
    """
    1-3: 긴급 변경 대응 — 기존 스케줄을 초기화하고 재스케줄링 수행.

    기존 schedule_tasks를 모두 삭제하고, 관련 production_batch 상태를
    'planned'으로 초기화한 뒤 auto_schedule을 재실행한다.
    Returns: auto_schedule과 동일한 결과 dict + "cleared_tasks" 수
    """
    # 기존 스케줄 태스크 삭제
    existing_tasks = (
        db.query(ScheduleTask).filter(ScheduleTask.run_label == run_label).all()
    )
    cleared_count = len(existing_tasks)
    for task in existing_tasks:
        db.delete(task)

    # 배치 상태 초기화 (planned으로 되돌림)
    batches = (
        db.query(ProductionBatch).filter(ProductionBatch.run_label == run_label).all()
    )
    for batch in batches:
        if batch.status == "scheduled":
            batch.status = "planned"
            batch.equipment_code = None

    db.flush()  # 삭제 반영 후 재스케줄

    # 재스케줄링 실행
    result = auto_schedule(run_label, db, base_date=base_date)
    result["cleared_tasks"] = cleared_count
    return result
