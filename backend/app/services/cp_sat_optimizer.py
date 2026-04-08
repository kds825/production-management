"""CP-SAT 기반 스케줄 최적화 엔진

Google OR-Tools CP-SAT 솔버를 사용하여 납기 초과를 최소화하는 생산 배치를 결정한다.

── 설계 방침 ───────────────────────────────────────────────────────────────
1. CP-SAT 담당 범위:
   - 각 배치 그룹의 설비 배정 + 시작 시각 결정
   - 목적함수: 납기 초과 총 가중 일수(weighted tardiness) 최소화
   - 제약: 설비 충돌 없음, 공정 선후관계, CORE 선행, 소선경 클러스터 연속성

2. 기존 그리디 로직 재사용:
   - duration 계산(_compute_group_duration)
   - 설비 적격성 필터(_find_eligible_equipment, _narrow_by_stranding 등)
   - 파이프라인 겹침(CORE→ST first-drum overlap)은 CP-SAT 선후관계 제약으로 표현
   - ScheduleTask DB 저장, predecessor_map, audit log 등 후처리

3. 시간 단위: CP-SAT 변수는 분(minute) 정수, base_date 기준 상대 오프셋
   - 최대 계획 기간: 90일 (=129,600분)

4. 폴백: CP-SAT 실패(INFEASIBLE / 타임아웃) 시 기존 그리디로 자동 전환
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Any

from ortools.sat.python import cp_model
from sqlalchemy.orm import Session

from app.domain.constants import PROCESS_ORDER
from app.infrastructure.models.constraint_config import ConstraintConfig
from app.infrastructure.models.drum_lot_master import DrumLotMaster
from app.infrastructure.models.equipment_master import EquipmentMaster
from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.schedule_task import ScheduleTask
from app.infrastructure.models.speed_master import SpeedMaster
from app.infrastructure.models.wip_inventory import WipInventory
from app.services.audit_logger import log_decision
from app.services.calendar_engine import calculate_end_datetime
from app.services.schedule_optimizer import (
    PREDECESSOR_PROCESS,
    _DEFAULT_WELDING_MIN,
    _WIP_SKIP_PROCESSES,
    _extract_core_main_sq,
    _filter_by_sheath_routing,
    _find_available_slot,
    _find_eligible_equipment,
    _get_drum_winding_min,
    _group_earliest_due,
    _is_core_group,
    _narrow_by_stranding,
    _schedule_multi_equipment,
    _st_sq,
)

# 최대 계획 기간(분) — 90일
_MAX_HORIZON_MIN = 90 * 24 * 60

# CP-SAT 솔버 시간 제한(초) — 이 안에 최적해 또는 최량 feasible해 반환
_SOLVER_TIME_LIMIT_SEC = 30

# 납기 초과 가중치: 우선순위 customer_priority → weight 배율
# 낮을수록 긴급 (priority ≤ 3 → critical)
_TARDINESS_WEIGHT = {
    "critical": 100,
    "urgent": 10,
    "normal": 1,
}


def _priority_label(customer_priority: int | None) -> str:
    cp = customer_priority or 99
    if cp <= 3:
        return "critical"
    if cp <= 7:
        return "urgent"
    return "normal"


def _minutes_from_base(dt: datetime, base: datetime) -> int:
    """base_date 기준 분 오프셋 (음수 → 0으로 클램프)."""
    delta = (dt - base).total_seconds() / 60
    return max(0, int(delta))


def _dt_from_minutes(minutes: int, base: datetime) -> datetime:
    return base + timedelta(minutes=minutes)


def _compute_group_duration(
    group_batches: list[ProductionBatch],
    eligible: list[EquipmentMaster],
    speed_map: dict,
) -> float:
    """배치 그룹의 순수 작업 duration(분) 계산. 설업 시간 제외."""
    rep = group_batches[0]
    rep_speed = float(rep.line_speed_mpm or 0)
    if rep_speed <= 0:
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
        return hd
    else:
        total_dur = 0.0
        for b in group_batches:
            d = float(b.estimated_duration_min or 0)
            if d <= 0:
                total = float(b.total_length_m or 0) + float(b.extra_length_m or 0)
                ls = float(b.line_speed_mpm or 0) or line_speed
                d = total / ls if ls > 0 else 60
            total_dur += d
        return total_dur


def cp_sat_schedule(
    run_label: str,
    db: Session,
    *,
    base_date: datetime | None = None,
) -> dict:
    """
    CP-SAT 기반 자동 배치.

    Returns:
        {"total_tasks": int, "violations": list, "warnings": list,
         "solver_status": str, "objective_value": int}
    """
    result: dict[str, Any] = {
        "total_tasks": 0,
        "violations": [],
        "warnings": [],
        "solver_status": "UNKNOWN",
        "objective_value": 0,
    }

    # ── 1. 배치 로드 + WIP 스킵 (그리디와 동일) ───────────────────────────────
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
    batches.sort(
        key=lambda b: (
            PROCESS_ORDER.get(b.process_name, 50),
            b.batch_seq or 0,
            b.due_date or date.max,
            b.customer_priority or 99,
            -(float(b.sq_mm2 or 0)),
        )
    )

    if not batches:
        result["warnings"].append("배치 없음 — Stage 1을 먼저 실행하세요")
        return result

    wip_ids = {b.wip_matched_id for b in batches if b.wip_matched_id is not None}
    wip_stage_map: dict[int, str] = {}
    if wip_ids:
        wips = db.query(WipInventory).filter(WipInventory.wip_id.in_(wip_ids)).all()
        wip_stage_map = {w.wip_id: w.process_stage or "" for w in wips}

    schedulable: list[ProductionBatch] = []
    wip_skipped = 0
    for batch in batches:
        if batch.wip_matched_id and batch.wip_matched_id in wip_stage_map:
            skip_set = _WIP_SKIP_PROCESSES.get(wip_stage_map[batch.wip_matched_id], set())
            if batch.process_name in skip_set:
                batch.status = "wip_complete"
                wip_skipped += 1
                continue
        schedulable.append(batch)

    batches = schedulable
    if wip_skipped:
        result["wip_skipped"] = wip_skipped

    # ── 2. 기준일시 설정 ──────────────────────────────────────────────────────
    if base_date is None:
        try:
            date_part = run_label.split("_")[0]
            base_date = datetime(int(date_part[:4]), int(date_part[4:6]), int(date_part[6:8]), 8, 0, 0)
        except Exception:
            from zoneinfo import ZoneInfo
            kst_now = datetime.now(ZoneInfo("Asia/Seoul"))
            base_date = kst_now.replace(hour=8, minute=0, second=0, microsecond=0).replace(tzinfo=None)

    # ── 3. 마스터 데이터 로드 ─────────────────────────────────────────────────
    equipment_list = db.query(EquipmentMaster).all()
    equipment_by_process: dict[str, list[EquipmentMaster]] = {}
    for eq in equipment_list:
        equipment_by_process.setdefault(eq.process_name, []).append(eq)

    speed_records = db.query(SpeedMaster).all()
    speed_map: dict[tuple, SpeedMaster] = {
        (sr.equipment_code, float(sr.cross_section or 0)): sr for sr in speed_records
    }

    welding_cfg = (
        db.query(ConstraintConfig).filter(ConstraintConfig.constraint_id == "4-4").first()
    )
    welding_min = _DEFAULT_WELDING_MIN
    if welding_cfg and welding_cfg.params_json:
        welding_min = float(welding_cfg.params_json.get("welding_min", _DEFAULT_WELDING_MIN))

    sq_to_wire_d: dict[int, float] = {
        int(d.cross_section): float(d.wire_diameter)
        for d in db.query(DrumLotMaster).all()
        if d.wire_diameter is not None
    }

    # ── 4. batch_group 단위 그루핑 ───────────────────────────────────────────
    from collections import OrderedDict
    batch_groups: OrderedDict[str, list[ProductionBatch]] = OrderedDict()
    for batch in batches:
        key = batch.batch_group or f"_single_{batch.batch_id}"
        batch_groups.setdefault(key, []).append(batch)

    # ── 5. 그룹별 메타 계산 (eligible 설비, duration, 납기 등) ──────────────
    group_meta: dict[str, dict] = {}
    for gk, gb in batch_groups.items():
        rep = gb[0]
        candidate_equip = equipment_by_process.get(rep.process_name, [])

        # 시스 라우팅
        if rep.process_name in ("고압시스", "저압시스"):
            candidate_equip = _filter_by_sheath_routing(rep, candidate_equip)
        if gk.startswith("A120_"):
            candidate_equip = [e for e in candidate_equip if e.equipment_code == "SH-A120"]
        elif gk.startswith("A100_"):
            candidate_equip = [e for e in candidate_equip if e.equipment_code == "SH-A100"]

        eligible = _find_eligible_equipment(rep, candidate_equip)
        if rep.process_name == "연선":
            eligible = _narrow_by_stranding(rep, eligible)

        if not eligible:
            result["warnings"].append(
                f"배치그룹 {gk}: 공정 '{rep.process_name}' SQ={rep.sq_mm2} — 적합한 설비 없음"
            )
            continue

        work_dur = _compute_group_duration(gb, eligible, speed_map)
        setup_min = float(rep.setup_time_min or 0)
        drum_wind = _get_drum_winding_min(eligible[0].equipment_code, rep.sq_mm2, speed_map)
        total_dur = math.ceil(work_dur + setup_min + drum_wind)

        earliest_due = min((b.due_date for b in gb if b.due_date), default=None)
        due_offset = (
            _minutes_from_base(
                datetime(earliest_due.year, earliest_due.month, earliest_due.day, 22, 0, 0),
                base_date,
            )
            if earliest_due else _MAX_HORIZON_MIN
        )

        priority_lbl = _priority_label(rep.customer_priority)
        weight = _TARDINESS_WEIGHT[priority_lbl]

        group_meta[gk] = {
            "rep": rep,
            "batches": gb,
            "eligible": eligible,
            "work_dur": work_dur,
            "setup_min": setup_min,
            "drum_wind": drum_wind,
            "total_dur": total_dur,
            "due_offset": due_offset,
            "weight": weight,
            "priority": priority_lbl,
            "earliest_due": earliest_due,
            "is_stranding": rep.process_name == "연선",
            "is_core": _is_core_group(gk),
            "sq": int(rep.sq_mm2 or 0),
        }

    if not group_meta:
        result["warnings"].append("스케줄링 가능한 배치 그룹 없음")
        return result

    # ── 5-b. 멀티설비 분배 선처리 ────────────────────────────────────────────
    # 드럼 수 >= 2 이고 eligible 설비 >= 2인 연선(CORE 제외)/고압절연/고압시스 그룹은
    # CP-SAT 단일설비 배정 모델로 처리하기 어려우므로, 기존 _schedule_multi_equipment로
    # 먼저 처리하고 CP-SAT 대상에서 제외한다.
    # 이 단계는 CP-SAT 전에 실행되어야 하므로 타임라인/상태 추적 딕셔너리를 미리 생성.
    timeline: dict[str, list] = {}
    predecessor_map: dict[tuple, int] = {}
    tasks_created: list[ScheduleTask] = []
    last_batch_on_equip: dict[str, ProductionBatch] = {}
    sq_to_equip: dict[tuple[str, int], str] = {}
    process_end_by_sq: dict[tuple[str, int], datetime] = {}
    process_first_output_by_sq: dict[tuple[str, int], datetime] = {}
    core_first_drum_by_main_sq: dict[int, datetime] = {}
    first_insul_output: datetime | None = None

    multi_handled: set[str] = set()

    for gk in list(group_meta.keys()):
        meta = group_meta[gk]
        rep = meta["rep"]
        gb = meta["batches"]
        eligible = meta["eligible"]
        sq = meta["sq"]
        sq_key = (rep.process_name, sq)

        is_stranding = meta["is_stranding"]
        is_high_insul = rep.process_name == "고압절연"
        is_high_sheath = rep.process_name == "고압시스"

        header_batch_chk = next((b for b in gb if b.batch_seq == -1), None)
        if header_batch_chk:
            total_drums = int(header_batch_chk.drum_count or 0)
        else:
            total_drums = sum(int(b.drum_count or 0) for b in gb)

        multi_eligible = (
            (is_stranding and not _is_core_group(gk) and sq_key not in sq_to_equip)
            or is_high_insul
            or is_high_sheath
        )

        if multi_eligible and total_drums >= 2 and len(eligible) >= 2:
            split_ok = _schedule_multi_equipment(
                group_key=gk,
                group_batches=gb,
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
                multi_handled.add(gk)
                result["total_tasks"] += 1
                continue

    # CP-SAT 대상: 멀티설비로 처리된 그룹 제외
    for gk in multi_handled:
        group_meta.pop(gk, None)

    if not group_meta:
        # 모든 그룹이 멀티설비로 처리됨
        return result

    # ── 6. CP-SAT 모델 구성 ───────────────────────────────────────────────────
    model = cp_model.CpModel()
    groups = list(group_meta.keys())

    # 6-a. 설비별 고유 ID 매핑
    all_equip_codes = sorted({e.equipment_code for eqs in equipment_by_process.values() for e in eqs})
    equip_id = {code: i for i, code in enumerate(all_equip_codes)}

    # 6-b. 결정 변수 생성
    # start[gk]: 그룹 시작 시각 (분 오프셋, 0 ~ MAX_HORIZON)
    # equip_choice[gk][eq_code]: 해당 설비에 배정되면 1
    # tardiness[gk]: 납기 초과 분 (0 이상)
    start_vars: dict[str, cp_model.IntVar] = {}
    end_vars: dict[str, cp_model.IntVar] = {}
    equip_vars: dict[str, dict[str, cp_model.IntVar]] = {}  # gk → {eq_code → BoolVar}
    tardiness_vars: dict[str, cp_model.IntVar] = {}

    for gk in groups:
        meta = group_meta[gk]
        dur = meta["total_dur"]

        s = model.new_int_var(0, _MAX_HORIZON_MIN - dur, f"start_{gk}")
        e = model.new_int_var(dur, _MAX_HORIZON_MIN, f"end_{gk}")
        model.add(e == s + dur)
        start_vars[gk] = s
        end_vars[gk] = e

        # 납기 초과: max(0, end - due_offset)
        tard = model.new_int_var(0, _MAX_HORIZON_MIN, f"tard_{gk}")
        model.add_max_equality(tard, [e - meta["due_offset"], model.new_constant(0)])
        tardiness_vars[gk] = tard

        # 설비 선택 변수 (eligible 설비 중 하나)
        eq_bools: dict[str, cp_model.IntVar] = {}
        for eq in meta["eligible"]:
            bv = model.new_bool_var(f"eq_{gk}_{eq.equipment_code}")
            eq_bools[eq.equipment_code] = bv
        equip_vars[gk] = eq_bools

        # 반드시 하나의 설비를 선택
        model.add_exactly_one(eq_bools.values())

    # 6-c. 설비 충돌 방지 (같은 설비에 배치된 두 그룹은 겹치면 안 됨)
    # interval variable + no_overlap 제약 사용
    interval_vars: dict[tuple[str, str], Any] = {}  # (gk, eq_code) → IntervalVar

    for gk in groups:
        meta = group_meta[gk]
        dur = meta["total_dur"]
        for eq in meta["eligible"]:
            eq_code = eq.equipment_code
            assigned = equip_vars[gk][eq_code]
            # optional interval: 해당 설비가 선택된 경우에만 활성화
            itv = model.new_optional_interval_var(
                start_vars[gk], dur, end_vars[gk], assigned, f"itv_{gk}_{eq_code}"
            )
            interval_vars[(gk, eq_code)] = itv

    # 설비별 no_overlap
    # ── 5-b 멀티설비 선처리로 이미 점유된 슬롯을 fixed interval로 모델에 등록 ──
    # CP-SAT는 _schedule_multi_equipment가 배치한 태스크를 모르므로,
    # timeline의 기존 슬롯을 고정 구간으로 추가해야 CP-SAT 배치와 겹침을 방지한다.
    fixed_intervals_by_eq: dict[str, list] = {}
    for eq_code, slots in timeline.items():
        fixed_intervals_by_eq[eq_code] = []
        for slot_start_dt, slot_end_dt in slots:
            slot_s = _minutes_from_base(slot_start_dt, base_date)
            slot_e = _minutes_from_base(slot_end_dt, base_date)
            slot_dur = max(slot_e - slot_s, 1)
            fixed_itv = model.new_fixed_size_interval_var(
                slot_s, slot_dur, f"fixed_{eq_code}_{slot_s}"
            )
            fixed_intervals_by_eq[eq_code].append(fixed_itv)

    for eq_code in all_equip_codes:
        intervals_for_eq = [
            interval_vars[(gk, eq_code)]
            for gk in groups
            if eq_code in equip_vars.get(gk, {})
        ]
        # 기존 점유 슬롯(멀티설비 선처리 결과) 추가
        intervals_for_eq.extend(fixed_intervals_by_eq.get(eq_code, []))

        if len(intervals_for_eq) >= 2:
            model.add_no_overlap(intervals_for_eq)

    # 6-d. 공정 선후관계 제약 ─────────────────────────────────────────────────
    # 같은 SQ의 (연선 → 절연), (절연 → 시스) 등
    # "앞 공정 end ≤ 뒤 공정 start" (first-drum overlap 근사: 1/lot_count 비율로 완화)

    # SQ별 공정 그룹키 인덱스
    proc_groups_by_sq: dict[tuple[str, int], list[str]] = {}
    for gk in groups:
        meta = group_meta[gk]
        key = (meta["rep"].process_name, meta["sq"])
        proc_groups_by_sq.setdefault(key, []).append(gk)

    for gk in groups:
        meta = group_meta[gk]
        rep = meta["rep"]
        pred_proc = PREDECESSOR_PROCESS.get(rep.process_name)
        if not pred_proc:
            continue
        sq_i = meta["sq"]
        pred_gks = proc_groups_by_sq.get((pred_proc, sq_i), [])
        for pred_gk in pred_gks:
            pred_meta = group_meta[pred_gk]
            # 파이프라인 겹침: 앞 공정의 첫 드럼 완료 후 시작 가능
            # 첫 드럼 완료 ≈ pred.start + setup + work_dur / lot_count
            pred_header = next((b for b in pred_meta["batches"] if b.batch_seq == -1), None)
            if pred_header:
                lot_count = max(int(pred_header.drum_count or 1), 1)
            elif _is_core_group(pred_gk):
                lot_count = max(sum(int(b.drum_count or 1) for b in pred_meta["batches"]), 1)
            else:
                lot_count = max(len(pred_meta["batches"]), 1)

            first_drum_min = math.ceil(
                pred_meta["setup_min"] + pred_meta["work_dur"] / lot_count
            )
            # start[cur] >= start[pred] + first_drum_min
            model.add(start_vars[gk] >= start_vars[pred_gk] + first_drum_min)

    # 6-e. CORE → ST 선행 제약 (AL6BO 첫 드럼 → 54BO 시작)
    core_groups = [gk for gk in groups if _is_core_group(gk)]
    st_groups = [gk for gk in groups if gk.startswith("ST-")]
    for core_gk in core_groups:
        main_sq = _extract_core_main_sq(core_gk)
        if main_sq is None:
            continue
        core_meta = group_meta[core_gk]
        lot_count_core = max(
            sum(int(b.drum_count or 1) for b in core_meta["batches"]), 1
        )
        first_drum_core = math.ceil(
            core_meta["setup_min"] + core_meta["work_dur"] / lot_count_core
        )
        for st_gk in st_groups:
            if _st_sq(st_gk) == main_sq:
                model.add(start_vars[st_gk] >= start_vars[core_gk] + first_drum_core)

    # 6-f. 소선경 클러스터 연속성 — 소프트 제약으로 표현
    # 같은 wire_diameter 클러스터 내 연선 그룹이 다른 설비에 흩어지지 않도록
    # "같은 클러스터의 그룹은 같은 설비 선호" → 별도 penalty 대신 설비 선택 연동 제약
    # (같은 wd 클러스터 그룹이 eligible 설비를 공유하면 같은 설비 배정 시 bonus)
    # → 너무 복잡해지므로 CP-SAT에서는 하드 제약으로 표현하지 않고
    #   그룹 순서(납기 기반)에 의해 자연스럽게 클러스터링 유도

    # 6-g. 목적함수: 가중 납기 초과 최소화
    objective_terms = []
    for gk in groups:
        meta = group_meta[gk]
        objective_terms.append(meta["weight"] * tardiness_vars[gk])
    model.minimize(sum(objective_terms))

    # ── 7. 솔버 실행 ──────────────────────────────────────────────────────────
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = _SOLVER_TIME_LIMIT_SEC
    solver.parameters.num_search_workers = 4  # 멀티스레드 탐색
    solver.parameters.log_search_progress = False

    status = solver.solve(model)
    status_name = solver.status_name(status)
    result["solver_status"] = status_name

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        result["warnings"].append(
            f"CP-SAT 솔버 실패 ({status_name}) — 그리디 폴백으로 전환합니다"
        )
        return result  # 호출자가 폴백 처리

    result["objective_value"] = int(solver.objective_value)

    # ── 8. 솔버 결과를 DB에 저장 ──────────────────────────────────────────────
    # ★ 핵심 설계: CP-SAT의 start_vars는 "순서 결정"에만 사용한다.
    #   CP-SAT 내부 시간 단위(분)와 실제 캘린더(08~22시 근무, 주말 제외)는 1:1 대응이
    #   되지 않으므로 CP-SAT 시작 시각을 그대로 쓰면 겹침이 발생한다.
    #   CP-SAT가 결정한 (설비 배정 + 그룹 처리 순서)를 가져온 뒤,
    #   실제 시작 시각은 _find_available_slot + calculate_end_datetime으로 재계산한다.
    #
    # predecessor_map / tasks_created / last_batch_on_equip / sq_to_equip 은
    # 5-b 멀티설비 선처리 단계에서 이미 초기화됨 — 여기서 재선언하지 않음

    # CP-SAT 결과: 설비 배정 추출
    cpsat_assignment: dict[str, str] = {}  # gk → eq_code
    for gk in groups:
        chosen_eq_code = next(
            ec for ec in equip_vars[gk] if solver.value(equip_vars[gk][ec]) == 1
        )
        cpsat_assignment[gk] = chosen_eq_code

    # CP-SAT 결과: 설비 배정 기준 처리 순서 (CP-SAT start_vars 값으로 정렬)
    solved_order = sorted(groups, key=lambda gk: solver.value(start_vars[gk]))

    # 공정 선후관계 추적 (전체 공정 파이프라인 — 멀티설비 이미 반영된 값에서 이어서 추적)
    process_first_output_by_sq: dict[tuple[str, int], datetime] = {}
    core_first_drum_by_main_sq: dict[int, datetime] = {}
    first_insul_output: datetime | None = None

    for gk in solved_order:
        meta = group_meta[gk]
        rep = meta["rep"]
        gb = meta["batches"]
        chosen_eq_code = cpsat_assignment[gk]
        chosen_eq = next(e for e in meta["eligible"] if e.equipment_code == chosen_eq_code)
        sq_int = meta["sq"]

        # ── 동일 SQ 셋업 스킵 / 색상 교체 시간 계산 ────────────────────────
        actual_setup = meta["setup_min"]
        prev_batch = last_batch_on_equip.get(chosen_eq_code)
        if prev_batch is not None and prev_batch.sq_mm2 and rep.sq_mm2:
            if float(prev_batch.sq_mm2) == float(rep.sq_mm2):
                actual_setup = 0.0

        color_change_min = 0.0
        if prev_batch is not None and rep.process_name in ("저압시스", "고압시스", "HFCO시스"):
            prev_color = (prev_batch.sheath_color or "").strip()
            curr_color = (rep.sheath_color or "").strip()
            if prev_color and curr_color and prev_color != curr_color:
                sm_color = (
                    db.query(SpeedMaster.setup_color_min)
                    .filter(SpeedMaster.equipment_code == chosen_eq_code)
                    .first()
                )
                color_change_min = float(sm_color[0] or 120.0) if sm_color else 120.0

        total_dur = meta["work_dur"] + actual_setup + meta["drum_wind"] + color_change_min

        # ── 선행 공정 earliest 계산 (그리디와 동일한 로직) ──────────────────
        earliest = base_date

        pred_proc = PREDECESSOR_PROCESS.get(rep.process_name)
        if pred_proc:
            all_sqs = {int(b.sq_mm2 or 0) for b in gb}
            if len(all_sqs) > 1:
                valid_firsts = [
                    t for sq_i in all_sqs
                    if (t := process_first_output_by_sq.get((pred_proc, sq_i)))
                    and t < datetime.max
                ]
                if valid_firsts:
                    earliest = max(earliest, min(valid_firsts))
            else:
                sq_i = next(iter(all_sqs))
                pred_first = process_first_output_by_sq.get((pred_proc, sq_i))
                if pred_first and pred_first > earliest:
                    earliest = pred_first
            if rep.process_name == "고압시스":
                earliest += timedelta(hours=20)

        if gk.startswith("A100_") or gk.startswith("A120_"):
            if first_insul_output and first_insul_output > earliest:
                earliest = first_insul_output

        if gk.startswith("ST-") and rep.process_name == "연선":
            try:
                main_sq = int(gk.split("-")[1])
            except (IndexError, ValueError):
                main_sq = sq_int
            core_first = core_first_drum_by_main_sq.get(main_sq)
            if core_first and core_first > earliest:
                earliest = core_first

        # 개별 수주 predecessor
        _is_st_group = gk.startswith("ST-") and rep.process_name == "연선"
        _skip_individual = rep.process_name in (
            "저압절연", "고압절연", "저압시스", "고압시스", "연합", "T/P"
        ) or _is_st_group
        if not _skip_individual:
            for b in gb:
                pred_key = (b.sales_order_id, b.sales_order_line)
                pred_tid = predecessor_map.get(pred_key)
                if pred_tid:
                    pred_task = next(
                        (t for t in tasks_created if t.task_id == pred_tid), None
                    )
                    if pred_task and pred_task.end_datetime > earliest:
                        earliest = pred_task.end_datetime

        # ── 실제 캘린더 기반 슬롯 탐색 (겹침 방지의 핵심) ───────────────────
        slots = timeline.get(chosen_eq_code, [])
        best_start = _find_available_slot(earliest, total_dur, slots, db)
        end_dt = calculate_end_datetime(best_start, total_dur, db)

        # 정각 단위 올림
        if end_dt.minute > 0 or end_dt.second > 0:
            end_dt = end_dt.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)

        task = ScheduleTask(
            batch_id=rep.batch_id,
            equipment_code=chosen_eq_code,
            start_datetime=best_start,
            end_datetime=end_dt,
            setup_time_min=actual_setup,
            status="scheduled",
            run_label=run_label,
            batch_group=gk,
        )
        db.add(task)
        db.flush()

        # timeline 갱신 — 이후 그룹이 이 슬롯을 피할 수 있도록
        timeline.setdefault(chosen_eq_code, []).append((best_start, end_dt))

        # 파이프라인 겹침: 첫 드럼 출력 시각 갱신
        header_batch = next((b for b in gb if b.batch_seq == -1), None)
        if header_batch is not None:
            lot_count = max(int(header_batch.drum_count or 1), 1)
        elif _is_core_group(gk):
            lot_count = max(sum(int(b.drum_count or 1) for b in gb), 1)
        else:
            lot_count = max(len(gb), 1)
        first_drum_min = actual_setup + (meta["work_dur"] / lot_count)
        first_output_dt = calculate_end_datetime(best_start, first_drum_min, db)

        proc_sq_key = (rep.process_name, sq_int)
        if not _is_core_group(gk):
            if proc_sq_key not in process_first_output_by_sq or first_output_dt < process_first_output_by_sq[proc_sq_key]:
                process_first_output_by_sq[proc_sq_key] = first_output_dt

        if _is_core_group(gk):
            main_sq = _extract_core_main_sq(gk)
            if main_sq is not None:
                if main_sq not in core_first_drum_by_main_sq or first_output_dt < core_first_drum_by_main_sq[main_sq]:
                    core_first_drum_by_main_sq[main_sq] = first_output_dt

        if rep.process_name == "저압절연":
            if first_insul_output is None or first_output_dt < first_insul_output:
                first_insul_output = first_output_dt

        # predecessor_map 갱신
        for b in gb:
            predecessor_map[(b.sales_order_id, b.sales_order_line)] = task.task_id
            b.equipment_code = chosen_eq_code
            b.status = "scheduled"

        if rep.process_name == "연선" and not _is_core_group(gk):
            sq_to_equip[(rep.process_name, sq_int)] = chosen_eq_code

        last_batch_on_equip[chosen_eq_code] = gb[-1]
        tasks_created.append(task)

        # 납기 위반 기록
        earliest_due = meta["earliest_due"]
        if earliest_due and end_dt.date() > earliest_due:
            late_days = (end_dt.date() - earliest_due).days
            result["violations"].append({
                "batch_id": rep.batch_id,
                "task_id": task.task_id,
                "type": "delivery",
                "severity": "warning",
                "detail": f"납기 {earliest_due} 초과 → 완료 예정 {end_dt.date()} (+{late_days}일)",
            })

        # Audit log
        log_decision(
            db=db,
            run_label=run_label,
            stage="stage2",
            action_type="auto_assign",
            batch_id=rep.batch_id,
            task_id=task.task_id,
            reason=f"CP-SAT 배치 → {chosen_eq_code} @ {best_start:%Y-%m-%d %H:%M}",
        )

    # total_tasks: 5-b 멀티설비 건(이미 += 됨) + CP-SAT 단일설비 건
    result["total_tasks"] += len([t for t in tasks_created if t.batch_group not in multi_handled])
    return result
