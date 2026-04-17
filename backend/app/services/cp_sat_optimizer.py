"""CP-SAT 기반 스케줄 최적화 엔진

── 설계 방침 ───────────────────────────────────────────────────────────────
CP-SAT 담당: 모든 배치 그룹의 처리 순서 결정 + 납기 초과 최소화
  - 목적함수: customer_priority 가중 납기 초과 근무일 합산 최소화
  - Hard 제약: 설비 충돌 없음, 공정 선후관계(연선→절연→시스), CORE 선행

실제 배치(캘린더): CP-SAT가 결정한 순서대로 그리디 캘린더 엔진 수행
  - 멀티설비 그룹 → _schedule_multi_equipment
  - 단일설비 그룹 → _find_available_slot + calculate_end_datetime
  - 실제 시작/종료는 항상 08~22시 근무 캘린더 기준

CP-SAT 시간 단위: 근무 분(working minute), 하루 = 840분(14h×60)
  - 캘린더와 직접 1:1 대응은 불가하지만 근무일 단위로 근사하여
    납기 제약의 방향성(어떤 그룹을 먼저 처리할지)을 올바르게 결정

폴백: CP-SAT 실패(INFEASIBLE / 타임아웃) 시 기존 그리디로 자동 전환
"""

from __future__ import annotations

import math
from collections import OrderedDict
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
    _get_stranding_setup_min,
    _is_core_group,
    _narrow_by_stranding,
    _schedule_multi_equipment,
    _st_sq,
)

# 하루 근무 시간(분): 08:00~22:00
_WORK_MIN_PER_DAY = 14 * 60  # 840분

# CP-SAT 최대 계획 기간(근무 분) — 90 근무일
_MAX_HORIZON_MIN = 90 * _WORK_MIN_PER_DAY

# CP-SAT 솔버 시간 제한(초)
_SOLVER_TIME_LIMIT_SEC = 30

# 납기 초과 가중치 — 납기는 사용자 요구 상 하드 제약.
# CP-SAT 에서 실제 'hard' add() 는 INFEASIBLE 위험(과거 납기 등) 때문에 피하고,
# 아이들(1)/체인(1)/선점 등 다른 목적함수 항들을 _DUE_HARD_WEIGHT 로 압도하여
# 실질적 hard 로 동작시킨다. 납기 맞출 해가 있으면 솔버는 그 해를 반드시 선택.
_DUE_HARD_WEIGHT = 100000
_TARDINESS_WEIGHT = {
    "critical": _DUE_HARD_WEIGHT * 100,
    "urgent": _DUE_HARD_WEIGHT * 10,
    "normal": _DUE_HARD_WEIGHT,
}


# ── 헬퍼 ──────────────────────────────────────────────────────────────────


def _priority_label(customer_priority: int | None) -> str:
    cp = customer_priority or 99
    if cp <= 3:
        return "critical"
    if cp <= 7:
        return "urgent"
    return "normal"


def _work_days_between(d1: date, d2: date) -> int:
    """d1(포함) ~ d2(미포함) 사이의 근무일 수(토·일 제외)."""
    days = 0
    cur = d1
    while cur < d2:
        if cur.weekday() < 5:
            days += 1
        cur += timedelta(days=1)
    return days


def _due_work_min(due: date, base: datetime) -> int:
    """납기일까지 남은 근무 분(CP-SAT 내부 단위)."""
    wd = _work_days_between(base.date(), due)
    return wd * _WORK_MIN_PER_DAY


def _compute_group_duration(
    group_batches: list[ProductionBatch],
    eligible: list[EquipmentMaster],
    speed_map: dict,
) -> float:
    """배치 그룹의 순수 작업 duration(분, 설업 제외)."""
    rep = group_batches[0]
    rep_speed = float(rep.line_speed_mpm or 0)
    if rep_speed <= 0:
        for eq in eligible:
            sm = speed_map.get((eq.equipment_code, float(rep.sq_mm2 or 0)))
            if sm and sm.line_speed_mpm and float(sm.line_speed_mpm) > 0:
                rep_speed = float(sm.line_speed_mpm)
                break
    line_speed = rep_speed if rep_speed > 0 else 10

    header = next((b for b in group_batches if b.batch_seq == -1), None)
    if header is not None:
        hd = float(header.estimated_duration_min or 0)
        if hd <= 0:
            ls = float(header.line_speed_mpm or 0) or line_speed
            hd = float(header.total_length_m or 0) / ls if ls > 0 else 60
        return hd

    total = 0.0
    for b in group_batches:
        d = float(b.estimated_duration_min or 0)
        if d <= 0:
            ls = float(b.line_speed_mpm or 0) or line_speed
            d = (
                (float(b.total_length_m or 0) + float(b.extra_length_m or 0)) / ls
                if ls > 0
                else 60
            )
        total += d
    return total


def _is_multi_equip_group(
    gk: str,
    gb: list[ProductionBatch],
    eligible: list[EquipmentMaster],
    sq_to_equip: dict,
) -> tuple[bool, int]:
    """멀티설비 분배 대상 여부와 총 드럼 수 반환."""
    rep = gb[0]
    is_stranding = rep.process_name == "연선"
    sq_key = (rep.process_name, int(rep.sq_mm2 or 0))

    header = next((b for b in gb if b.batch_seq == -1), None)
    total_drums = (
        int(header.drum_count or 0)
        if header
        else sum(int(b.drum_count or 0) for b in gb)
    )

    multi_eligible = (
        (is_stranding and not _is_core_group(gk) and sq_key not in sq_to_equip)
        or rep.process_name == "고압절연"
        or rep.process_name == "고압시스"
    )
    return (multi_eligible and total_drums >= 2 and len(eligible) >= 2), total_drums


# ── 선점 스케줄링 헬퍼 ─────────────────────────────────────────────────────


def _drums_completable(
    task_start: datetime,
    preempt_at: datetime,
    setup_min: float,
    work_dur_min: float,
    total_drums: int,
    eq_code: str | None,
    db,
) -> int:
    """작업 시작부터 preempt_at 직전까지 완료 가능한 드럼 수 (이진탐색).

    setup이 끝나기 전에 preempt_at이 오면 0 반환.
    """
    if total_drums <= 0 or preempt_at <= task_start:
        return 0
    drum_min = work_dur_min / max(total_drums, 1)
    lo, hi = 0, total_drums
    while lo < hi:
        mid = (lo + hi + 1) // 2
        end_mid = calculate_end_datetime(
            task_start, setup_min + mid * drum_min, db, eq_code
        )
        if end_mid <= preempt_at:
            lo = mid
        else:
            hi = mid - 1
    return lo


def _try_preempt_for_urgent(
    earliest: datetime,
    chosen_eq_code: str,
    run_label: str,
    timeline: dict[str, list],
    db: Session,
    urgent_priority: int = 7,
) -> list[ProductionBatch]:
    """긴급 배치를 위해 chosen_eq_code의 블로킹 태스크를 선점한다.

    earliest 시점을 가로막는 슬롯을 처리하는 두 가지 전략:

    A) 멀티드럼(drum_count >= 2): 드럼 경계에서 분할
       - 기존 ScheduleTask 의 end_datetime 을 earliest 이전으로 단축
       - 잔여 드럼 분량의 새 ProductionBatch (status='planned') 생성

    B) 단드럼(drum_count < 2) + 비긴급 블로킹 배치: 밀어내기(deferral)
       - 블로킹 ScheduleTask 삭제 + 해당 배치 status='planned' 리셋
       - 원 배치를 반환 → 긴급 배치 완료 후 재스케줄링

    두 전략 모두 timeline 인플레이스 갱신 후 remainder 배치 목록을 반환한다.

    Args:
        urgent_priority: 긴급 배치의 customer_priority (이 값 이하인 블로킹 배치는 밀지 않음)
    """
    slots = list(timeline.get(chosen_eq_code, []))
    if not slots:
        return []

    remainder_batches: list[ProductionBatch] = []

    for slot_start, slot_end in sorted(slots, key=lambda s: s[0]):
        if slot_end <= earliest:
            continue  # earliest 이전에 이미 끝난 슬롯 → 무시
        if slot_start >= earliest:
            break  # earliest 이후 시작 → 긴급 배치가 앞에 끼어들 여지가 있음

        # slot_start < earliest < slot_end → 진행 중인 블록이 earliest를 가로막는 경우
        task = (
            db.query(ScheduleTask)
            .filter(
                ScheduleTask.run_label == run_label,
                ScheduleTask.equipment_code == chosen_eq_code,
                ScheduleTask.start_datetime == slot_start,
                ScheduleTask.end_datetime == slot_end,
            )
            .first()
        )
        if task is None:
            break

        src_batch = (
            db.query(ProductionBatch)
            .filter(ProductionBatch.batch_id == task.batch_id)
            .first()
        )
        if src_batch is None:
            break

        total_drums = int(src_batch.drum_count or 1)
        setup_min = float(task.setup_time_min or 0)
        work_dur_min = float(src_batch.estimated_duration_min or 0)

        blocking_priority = int(src_batch.customer_priority or 99)

        if total_drums < 2:
            # ── 전략 B: 단드럼 밀어내기 ───────────────────────────────────────
            # 블로킹 배치도 긴급/중요 수준이면 양보 불가
            if blocking_priority <= urgent_priority:
                break  # 동급 이상 긴급 배치 — 밀 수 없음

            # 비긴급 단드럼 배치: ScheduleTask 삭제 후 재스케줄링 대상으로 반환
            db.delete(task)
            db.flush()

            # timeline에서 슬롯 제거 (긴급 배치가 이 자리를 사용)
            tl = timeline[chosen_eq_code]
            tl.remove((slot_start, slot_end))

            # 배치 상태 planned로 되돌리고 재스케줄링 대상에 추가
            src_batch.status = "planned"
            src_batch.equipment_code = chosen_eq_code  # 같은 설비에서 재스케줄링
            db.flush()
            remainder_batches.append(src_batch)
            break

        # ── 전략 A: 멀티드럼 분할 ─────────────────────────────────────────
        k = _drums_completable(
            slot_start,
            earliest,
            setup_min,
            work_dur_min,
            total_drums,
            chosen_eq_code,
            db,
        )
        if k == 0:
            # 셋업조차 완료 불가 → 단드럼 밀어내기와 동일 처리 (비긴급인 경우)
            if blocking_priority <= urgent_priority:
                break  # 동급 이상 긴급 → 포기
            db.delete(task)
            db.flush()
            tl = timeline[chosen_eq_code]
            tl.remove((slot_start, slot_end))
            src_batch.status = "planned"
            src_batch.equipment_code = chosen_eq_code
            db.flush()
            remainder_batches.append(src_batch)
            break

        # k > 0: earliest 전에 k 드럼 완료 → 분할 처리
        drum_min = work_dur_min / total_drums
        trim_dur = setup_min + k * drum_min
        new_end = calculate_end_datetime(slot_start, trim_dur, db, chosen_eq_code)

        # 기존 태스크 단축
        task.end_datetime = new_end

        # timeline 갱신
        tl = timeline[chosen_eq_code]
        tl.remove((slot_start, slot_end))
        tl.append((slot_start, new_end))

        # 잔여 배치 생성 (remain_drums 드럼, setup 없음)
        remain_drums = total_drums - k
        remain_dur = remain_drums * drum_min
        remain_len = float(src_batch.total_length_m or 0) * remain_drums / total_drums
        new_bg = (
            f"{src_batch.batch_group}_REMAIN"
            if src_batch.batch_group
            else f"REMAIN_{src_batch.batch_id}"
        )

        rem_b = ProductionBatch(
            run_label=run_label,
            sales_order_id=src_batch.sales_order_id,
            sales_order_line=src_batch.sales_order_line,
            item_code=src_batch.item_code,
            routing_code=src_batch.routing_code,
            process_name=src_batch.process_name,
            equipment_code=chosen_eq_code,
            batch_seq=src_batch.batch_seq,
            drum_length_m=src_batch.drum_length_m,
            drum_count=remain_drums,
            total_length_m=remain_len,
            extra_length_m=src_batch.extra_length_m,
            sq_mm2=src_batch.sq_mm2,
            core_count=src_batch.core_count,
            core_colors=src_batch.core_colors,
            sheath_color=src_batch.sheath_color,
            customer_name=src_batch.customer_name,
            due_date=src_batch.due_date,
            customer_priority=src_batch.customer_priority,
            line_speed_mpm=src_batch.line_speed_mpm,
            setup_time_min=0,
            estimated_duration_min=remain_dur,
            status="planned",
            remarks=f"[선점분할 잔여] 원배치={src_batch.batch_id} ({k}/{total_drums}드럼 선점)",
            product_group=src_batch.product_group,
            voltage=src_batch.voltage,
            conductor_material=src_batch.conductor_material,
            stranding_type=src_batch.stranding_type,
            batch_group=new_bg,
            spec_raw=src_batch.spec_raw,
        )
        db.add(rem_b)
        db.flush()
        remainder_batches.append(rem_b)

        # 원 배치 drum_count / length / duration 를 완료분(k)으로 갱신
        orig_total_m = float(src_batch.total_length_m or 0)
        src_batch.drum_count = k
        src_batch.total_length_m = orig_total_m * k / total_drums
        src_batch.estimated_duration_min = trim_dur - setup_min
        db.flush()

        break  # 보통 한 슬롯만 처리

    return remainder_batches


# ── 메인 함수 ─────────────────────────────────────────────────────────────


def cp_sat_schedule(
    run_label: str,
    db: Session,
    *,
    base_date: datetime | None = None,
    random_seed: int = 0,
) -> dict:
    """
    CP-SAT 기반 자동 배치.

    CP-SAT → 전체 그룹의 처리 순서 결정
    캘린더 그리디 → 그 순서대로 실제 시작/종료 시각 계산 및 DB 저장

    Args:
        random_seed: CP-SAT 솔버의 random_seed. retry wrapper 가 시도 번호를
            전달해 결정론적 동일 해가 반복되는 것을 방지한다 (기본 0).

    Returns:
        {"total_tasks", "violations", "warnings", "solver_status", "objective_value"}
    """
    result: dict[str, Any] = {
        "total_tasks": 0,
        "violations": [],
        "warnings": [],
        "solver_status": "UNKNOWN",
        "objective_value": 0,
    }

    # ── 1. 배치 로드 ──────────────────────────────────────────────────────
    batches = (
        db.query(ProductionBatch)
        .filter(
            ProductionBatch.run_label == run_label, ProductionBatch.status == "planned"
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

    # WIP 스킵
    wip_ids = {b.wip_matched_id for b in batches if b.wip_matched_id}
    wip_stage_map: dict[int, str] = {}
    if wip_ids:
        from app.infrastructure.models.wip_inventory import WipInventory

        wips = db.query(WipInventory).filter(WipInventory.wip_id.in_(wip_ids)).all()
        wip_stage_map = {w.wip_id: w.process_stage or "" for w in wips}

    schedulable: list[ProductionBatch] = []
    wip_skipped = 0
    for b in batches:
        if b.wip_matched_id and b.wip_matched_id in wip_stage_map:
            skip_set = _WIP_SKIP_PROCESSES.get(wip_stage_map[b.wip_matched_id], set())
            if b.process_name in skip_set:
                b.status = "wip_complete"
                wip_skipped += 1
                continue
        schedulable.append(b)
    batches = schedulable
    if wip_skipped:
        result["wip_skipped"] = wip_skipped

    # ── 2. 기준일시 ───────────────────────────────────────────────────────
    if base_date is None:
        try:
            dp = run_label.split("_")[0]
            base_date = datetime(int(dp[:4]), int(dp[4:6]), int(dp[6:8]), 8, 0, 0)
        except Exception:
            from zoneinfo import ZoneInfo

            kst = datetime.now(ZoneInfo("Asia/Seoul"))
            base_date = kst.replace(hour=8, minute=0, second=0, microsecond=0).replace(
                tzinfo=None
            )

    # ── 3. 마스터 데이터 로드 ─────────────────────────────────────────────
    equipment_list = db.query(EquipmentMaster).all()
    equipment_by_process: dict[str, list[EquipmentMaster]] = {}
    for eq in equipment_list:
        equipment_by_process.setdefault(eq.process_name, []).append(eq)

    speed_map: dict[tuple, SpeedMaster] = {
        (sr.equipment_code, float(sr.cross_section or 0)): sr
        for sr in db.query(SpeedMaster).all()
    }

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

    # SQ → 소선경 매핑 (연선 셋업 3-tier 계산용)
    sq_to_wire_d: dict[int, float] = {
        int(d.cross_section): float(d.wire_diameter)
        for d in db.query(DrumLotMaster).all()
        if d.wire_diameter is not None
    }

    # ── 4. 그루핑 ─────────────────────────────────────────────────────────
    batch_groups: OrderedDict[str, list[ProductionBatch]] = OrderedDict()
    for b in batches:
        key = b.batch_group or f"_single_{b.batch_id}"
        batch_groups.setdefault(key, []).append(b)

    # ── 5. 그룹별 메타 계산 ───────────────────────────────────────────────
    group_meta: dict[str, dict] = {}
    for gk, gb in batch_groups.items():
        rep = gb[0]
        candidate = equipment_by_process.get(rep.process_name, [])

        if rep.process_name in ("고압시스", "저압시스"):
            candidate = _filter_by_sheath_routing(rep, candidate)
        if gk.startswith("A120_"):
            candidate = [e for e in candidate if e.equipment_code == "SH-A120"]
        elif gk.startswith("A100_"):
            candidate = [e for e in candidate if e.equipment_code == "SH-A100"]

        eligible = _find_eligible_equipment(rep, candidate)
        if rep.process_name == "연선":
            eligible = _narrow_by_stranding(rep, eligible)

        if not eligible:
            result["warnings"].append(
                f"배치그룹 {gk}: 공정 '{rep.process_name}' SQ={rep.sq_mm2} — 적합한 설비 없음"
            )
            continue

        work_dur = _compute_group_duration(gb, eligible, speed_map)
        setup_min = float(rep.setup_time_min or 0)
        drum_wind = _get_drum_winding_min(
            eligible[0].equipment_code, rep.sq_mm2, speed_map
        )
        # CP-SAT 내부 duration: 실제 근무 분 그대로 사용 (최소 1분)
        # 종전 840분 단위 올림은 모든 작업이 같은 크기로 보여 EDD 정렬이 불가능했음
        cpsat_dur = max(1, int(math.ceil(work_dur + setup_min + drum_wind)))

        earliest_due = min((b.due_date for b in gb if b.due_date), default=None)
        due_wmin = (
            _due_work_min(earliest_due, base_date) if earliest_due else _MAX_HORIZON_MIN
        )

        group_meta[gk] = {
            "rep": rep,
            "batches": gb,
            "eligible": eligible,
            "work_dur": work_dur,
            "setup_min": setup_min,
            "drum_wind": drum_wind,
            "cpsat_dur": cpsat_dur,
            "due_wmin": due_wmin,
            "weight": _TARDINESS_WEIGHT[_priority_label(rep.customer_priority)],
            "earliest_due": earliest_due,
            # 인접 쌍 chain_terms 계산용 ordinal — None 안전
            "due_date_ord": earliest_due.toordinal() if earliest_due else None,
            "sq": int(rep.sq_mm2 or 0),
        }

    if not group_meta:
        result["warnings"].append("스케줄링 가능한 배치 그룹 없음")
        return result

    # ── 6. CP-SAT 모델 구성 ───────────────────────────────────────────────
    model = cp_model.CpModel()
    groups = list(group_meta.keys())

    # 6-a. 설비 유니버스
    all_eq_codes = sorted(
        {e.equipment_code for eqs in equipment_by_process.values() for e in eqs}
    )

    # 6-b. 결정변수: start / end / equip_bool / tardiness
    start_vars: dict[str, cp_model.IntVar] = {}
    end_vars: dict[str, cp_model.IntVar] = {}
    equip_vars: dict[str, dict[str, cp_model.IntVar]] = {}
    tardiness_vars: dict[str, cp_model.IntVar] = {}

    for gk in groups:
        meta = group_meta[gk]
        dur = meta["cpsat_dur"]

        s = model.new_int_var(0, _MAX_HORIZON_MIN - dur, f"s_{gk}")
        e = model.new_int_var(dur, _MAX_HORIZON_MIN, f"e_{gk}")
        model.add(e == s + dur)
        start_vars[gk] = s
        end_vars[gk] = e

        tard = model.new_int_var(0, _MAX_HORIZON_MIN, f"t_{gk}")
        # tardiness = max(0, end - due)
        model.add_max_equality(tard, [e - meta["due_wmin"], model.new_constant(0)])
        tardiness_vars[gk] = tard

        eq_bools: dict[str, cp_model.IntVar] = {}
        for eq in meta["eligible"]:
            eq_bools[eq.equipment_code] = model.new_bool_var(
                f"eq_{gk}_{eq.equipment_code}"
            )
        equip_vars[gk] = eq_bools
        model.add_exactly_one(eq_bools.values())

    # 6-c. 설비 충돌 방지 (no_overlap)
    itv_vars: dict[tuple[str, str], Any] = {}
    for gk in groups:
        dur = group_meta[gk]["cpsat_dur"]
        for eq_code, bv in equip_vars[gk].items():
            itv = model.new_optional_interval_var(
                start_vars[gk], dur, end_vars[gk], bv, f"itv_{gk}_{eq_code}"
            )
            itv_vars[(gk, eq_code)] = itv

    for eq_code in all_eq_codes:
        itvs = [
            itv_vars[(gk, eq_code)]
            for gk in groups
            if eq_code in equip_vars.get(gk, {})
        ]
        if len(itvs) >= 2:
            model.add_no_overlap(itvs)

    # 6-d. 공정 선후관계: 앞 공정 첫 드럼 완료 후 뒤 공정 시작
    proc_groups_by_sq: dict[tuple[str, int], list[str]] = {}
    for gk in groups:
        meta = group_meta[gk]
        proc_groups_by_sq.setdefault((meta["rep"].process_name, meta["sq"]), []).append(
            gk
        )

    for gk in groups:
        meta = group_meta[gk]
        pred_proc = PREDECESSOR_PROCESS.get(meta["rep"].process_name)
        if not pred_proc:
            continue
        for pred_gk in proc_groups_by_sq.get((pred_proc, meta["sq"]), []):
            pred_meta = group_meta[pred_gk]
            pred_header = next(
                (b for b in pred_meta["batches"] if b.batch_seq == -1), None
            )
            if pred_header:
                lot_count = max(int(pred_header.drum_count or 1), 1)
            elif _is_core_group(pred_gk):
                lot_count = max(
                    sum(int(b.drum_count or 1) for b in pred_meta["batches"]), 1
                )
            else:
                lot_count = max(len(pred_meta["batches"]), 1)
            first_drum = max(1, math.ceil(pred_meta["cpsat_dur"] / lot_count))
            model.add(start_vars[gk] >= start_vars[pred_gk] + first_drum)
            # 파이프라인 유휴 최소 역산: 후공정 끝 ≥ 선행공정 끝
            model.add(end_vars[gk] >= end_vars[pred_gk])

    # 6-e. CORE → ST 선행 (AL6BO 첫 드럼 → 54BO 시작)
    for core_gk in [gk for gk in groups if _is_core_group(gk)]:
        main_sq = _extract_core_main_sq(core_gk)
        if main_sq is None:
            continue
        core_meta = group_meta[core_gk]
        lot_c = max(sum(int(b.drum_count or 1) for b in core_meta["batches"]), 1)
        first_drum = max(1, math.ceil(core_meta["cpsat_dur"] / lot_c))
        for st_gk in [
            gk for gk in groups if gk.startswith("ST-") and _st_sq(gk) == main_sq
        ]:
            model.add(start_vars[st_gk] >= start_vars[core_gk] + first_drum)

    # 6-f. 목적함수: 가중 납기 초과 최소화 + 파이프라인 유휴 최소화
    # 유휴 = succ_end - pred_end (≥ 0, 6-d 하드 제약으로 보장). 납기 가중치(수십~수백)
    # 대비 훨씬 낮은 _IDLE_WEIGHT 로 soft 최적화 — 파이프라인이 빠른 공정일수록
    # 솔버가 start_vars 를 늦춰서 pred_end 와 succ_end 를 정렬시킨다.
    idle_terms: list = []
    for _gk in groups:
        _pred_proc = PREDECESSOR_PROCESS.get(group_meta[_gk]["rep"].process_name)
        if not _pred_proc:
            continue
        for _pred_gk in proc_groups_by_sq.get((_pred_proc, group_meta[_gk]["sq"]), []):
            _idle = model.new_int_var(0, _MAX_HORIZON_MIN, f"idle_{_pred_gk}_{_gk}")
            model.add(_idle == end_vars[_gk] - end_vars[_pred_gk])
            idle_terms.append(_idle)

    _IDLE_WEIGHT = 1
    _objective = sum(
        meta["weight"] * tardiness_vars[gk] for gk, meta in group_meta.items()
    )
    if idle_terms:
        _objective = _objective + _IDLE_WEIGHT * sum(idle_terms)

    # 6-g. 시스 색상 체인 보너스 — 같은 설비 카테고리(A100/A120) 내 같은 색상 그룹
    # 쌍에 대해 |start_a - start_b| 를 최소화. 체인지오버 비용을 간접적으로 penalize.
    # 가중치는 IDLE 과 동일 (1) — 납기 가중치(수십~수백) 대비 훨씬 낮음.
    sheath_groups_by_color: dict[tuple[str, str], list[str]] = {}
    for _gk, _meta in group_meta.items():
        _rep = _meta["rep"]
        if _rep.process_name not in ("저압시스", "고압시스"):
            continue
        _color = (_rep.sheath_color or "").strip() or "기타"
        # 설비 카테고리: group_key prefix (A100 / A120 / 저압시스 / 고압시스)
        if _gk.startswith("A120_"):
            _eq_cat = "A120"
        elif _gk.startswith("A100_"):
            _eq_cat = "A100"
        else:
            _eq_cat = _rep.process_name
        sheath_groups_by_color.setdefault((_eq_cat, _color), []).append(_gk)

    # chain_terms: 같은 (설비카테고리, 색상) 그룹 간 start_var 근접성 페널티.
    # 과거 O(n²) 전체 쌍 구성 → H1/H2 반주차 분할 이후 그룹 수가 급증하면
    # 모델 변수·제약이 폭증해 CP-SAT 이 타임아웃될 위험이 있음.
    # 개선: due_date 정렬 후 '인접한 쌍' 만 묶고, 납기 14일 이상 벌어지면 스킵.
    # → O(n) 으로 감소, 멀리 있는 그룹들 간 chain bonus 는 의미가 없으므로 품질 손실 없음.
    chain_terms: list = []
    for (_cat, _color), _gks in sheath_groups_by_color.items():
        if len(_gks) < 2:
            continue
        # due_date_ord 기준 정렬 — None 은 뒤로 밀기 위해 큰 값(date.max ordinal)
        _MAX_ORD = date.max.toordinal()
        sorted_gks = sorted(
            _gks, key=lambda gk: group_meta[gk].get("due_date_ord") or _MAX_ORD
        )
        for i in range(len(sorted_gks) - 1):
            _gk_a, _gk_b = sorted_gks[i], sorted_gks[i + 1]
            _due_a = group_meta[_gk_a].get("due_date_ord")
            _due_b = group_meta[_gk_b].get("due_date_ord")
            # 납기 14일 이상 벌어지면 chain 묶음 해체 (멀리 있는 쌍은 관계 없음)
            if _due_a is not None and _due_b is not None and abs(_due_b - _due_a) > 14:
                continue
            _diff = model.new_int_var(
                0, _MAX_HORIZON_MIN, f"chain_diff_{_gk_a}_{_gk_b}"
            )
            model.add_abs_equality(_diff, start_vars[_gk_a] - start_vars[_gk_b])
            chain_terms.append(_diff)

    _CHAIN_WEIGHT = 1
    if chain_terms:
        _objective = _objective + _CHAIN_WEIGHT * sum(chain_terms)

    model.minimize(_objective)

    # ── 7. 솔버 실행 ──────────────────────────────────────────────────────
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = _SOLVER_TIME_LIMIT_SEC
    solver.parameters.num_search_workers = 4
    solver.parameters.log_search_progress = False
    # 재시도 시 다른 탐색 경로를 시도하도록 seed 변동 (Fix P0-4B)
    solver.parameters.random_seed = int(random_seed)

    status = solver.solve(model)
    status_name = solver.status_name(status)
    result["solver_status"] = status_name

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        result["warnings"].append(
            f"CP-SAT 솔버 실패 ({status_name}) — 그리디 폴백으로 전환합니다"
        )
        return result

    result["objective_value"] = int(solver.objective_value)

    # ── 8. CP-SAT 순서대로 캘린더 그리디로 실제 배치 ─────────────────────
    #
    # CP-SAT는 "어떤 순서로, 어떤 설비에" 처리할지만 결정한다.
    # 실제 시작/종료 시각은 항상 캘린더 인식 엔진(_find_available_slot +
    # calculate_end_datetime)으로 계산하므로 겹침이 발생하지 않는다.
    #
    # 처리 순서: CP-SAT start_vars 값 오름차순
    #   → 납기 빠른 그룹이 앞에 오도록 솔버가 결정한 순서
    # 처리 순서: 공정 선후관계 → 납기일 오름차순(EDD) → 고객 우선순위
    # ── 소선경 클러스터별 최초 납기 계산 (ST- 연선 그룹 연속 배치용) ─────────
    # schedule_optimizer의 wire_d_earliest와 동일한 로직
    wire_d_earliest: dict[float, date] = {}
    for gk in groups:
        if gk.startswith("ST-"):
            wd = sq_to_wire_d.get(_st_sq(gk), 0.0)
            if wd > 0:
                ed = group_meta[gk]["earliest_due"]
                if ed and (wd not in wire_d_earliest or ed < wire_d_earliest[wd]):
                    wire_d_earliest[wd] = ed

    # 처리 순서 결정:
    #   CORE/AL-CORE: 공정순 최우선 (ST- 선행)
    #   ST- 연선: 공정순 → 소선경 클러스터 최초납기 → 소선경값 → 그룹 EDD
    #     (같은 소선경 그룹을 연속 배치 → 선재교체 비용 최소화)
    #   그 외 공정(절연·시스 등): proc_level(공정순) → EDD → 고객 우선순위
    #       절연(proc=2)이 시스(proc=4)보다 항상 먼저 스케줄링 → 파이프라인 데이터 등록 보장
    #   시스 색상 클러스터 정렬 제거 — 납기 준수가 색상 연속성보다 우선
    def _solved_order_key(gk: str):
        meta = group_meta[gk]
        proc_level = PROCESS_ORDER.get(meta["rep"].process_name, 50)
        # CORE/AL-CORE: ST- 선행 공정이므로 반드시 먼저 실행 (date.min으로 최우선)
        if _is_core_group(gk):
            return (
                proc_level,
                date.min,  # ST- 그룹보다 항상 앞에 오도록
                -1.0,
                meta["earliest_due"] or date.max,
                meta["rep"].customer_priority or 99,
                solver.value(start_vars[gk]),
            )
        if gk.startswith("ST-") and meta["rep"].process_name == "연선":
            wd = sq_to_wire_d.get(_st_sq(gk), 0.0)
            cluster_due = wire_d_earliest.get(wd, date.max)
            return (
                proc_level,
                cluster_due,  # 소선경 클러스터 최초 납기 (클러스터 우선순위)
                wd,  # 소선경값 (같은 클러스터 내 안정 정렬 → 연속 배치)
                meta["earliest_due"] or date.max,  # 그룹 자체 EDD
                meta["rep"].customer_priority or 99,
                solver.value(start_vars[gk]),
            )
        # 절연·시스 등: EDD 순 — 색상 클러스터 우선 정렬 없음 (납기 준수 최우선)
        return (
            proc_level,
            meta["earliest_due"] or date.max,  # 그룹 자체 EDD
            0.0,
            meta["earliest_due"] or date.max,
            meta["rep"].customer_priority or 99,
            solver.value(start_vars[gk]),
        )

    solved_order = sorted(groups, key=_solved_order_key)

    # CP-SAT가 선택한 설비
    cpsat_eq: dict[str, str] = {
        gk: next(ec for ec, bv in equip_vars[gk].items() if solver.value(bv) == 1)
        for gk in groups
    }

    # 상태 추적 딕셔너리
    predecessor_map: dict[tuple, int] = {}
    tasks_created: list[ScheduleTask] = []
    last_batch_on_equip: dict[str, ProductionBatch] = {}
    sq_to_equip: dict[tuple[str, int], str] = {}
    process_end_by_sq: dict[tuple[str, int], datetime] = {}
    process_first_output_by_sq: dict[tuple[str, int], datetime] = {}
    core_first_drum_by_main_sq: dict[int, datetime] = {}

    # ── 기존 scheduled 태스크를 timeline에 pre-load ───────────────────────
    # 긴급수주 추가 후 재스케줄링 시 이미 확정된 블록과의 겹침을 방지한다.
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
    first_insul_output: datetime | None = None
    preempted_remainder: list[ProductionBatch] = []  # 선점 분할된 잔여 배치

    for gk in solved_order:
        meta = group_meta[gk]
        rep = meta["rep"]
        gb = meta["batches"]
        chosen_eq_code = cpsat_eq[gk]
        sq_int = meta["sq"]
        sq_key = (rep.process_name, sq_int)

        # 이 그룹이 멀티설비 분배 대상인지 판단
        # (sq_to_equip은 이미 배치된 SQ→설비 매핑을 반영하므로 순서 의존적으로 정확함)
        is_multi, total_drums = _is_multi_equip_group(
            gk, gb, meta["eligible"], sq_to_equip
        )

        if is_multi:
            # ── 멀티설비: _schedule_multi_equipment에 위임 ────────────────
            header_batch = next((b for b in gb if b.batch_seq == -1), None)
            split_ok = _schedule_multi_equipment(
                group_key=gk,
                group_batches=gb,
                eligible=meta["eligible"],
                total_drums=total_drums,
                header_batch=header_batch,
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
                result["total_tasks"] += 1
                continue
            # split 실패 시 단일설비로 폴백 (아래 로직 계속)

        # ── 단일설비 배치 ─────────────────────────────────────────────────
        eligible = meta["eligible"]

        # 연선 셋업 3-tier / 그 외 동일SQ 스킵
        actual_setup = meta["setup_min"]
        prev_batch = last_batch_on_equip.get(chosen_eq_code)
        if prev_batch and rep.process_name == "연선":
            compound_min = float(
                speed_map.get((chosen_eq_code, float(rep.sq_mm2 or 0)), None)
                and speed_map[
                    (chosen_eq_code, float(rep.sq_mm2 or 0))
                ].setup_compound_min
                or 0
            )
            actual_setup = _get_stranding_setup_min(
                float(prev_batch.sq_mm2) if prev_batch.sq_mm2 else None,
                float(rep.sq_mm2) if rep.sq_mm2 else None,
                sq_to_wire_d,
                spec_min=meta["setup_min"],
                compound_min=compound_min,
            )
        elif prev_batch and prev_batch.sq_mm2 and rep.sq_mm2:
            if float(prev_batch.sq_mm2) == float(rep.sq_mm2):
                actual_setup = 0.0

        # 색상 교체
        color_change_min = 0.0
        if prev_batch and rep.process_name in ("저압시스", "고압시스", "HFCO시스"):
            pc = (prev_batch.sheath_color or "").strip()
            cc = (rep.sheath_color or "").strip()
            if pc and cc and pc != cc:
                sm_c = (
                    db.query(SpeedMaster.setup_color_min)
                    .filter(SpeedMaster.equipment_code == chosen_eq_code)
                    .first()
                )
                color_change_min = float(sm_c[0] or 120.0) if sm_c else 120.0

        total_dur = (
            meta["work_dur"] + actual_setup + meta["drum_wind"] + color_change_min
        )

        # 공정 선후관계 earliest 계산
        earliest = base_date
        pred_proc = PREDECESSOR_PROCESS.get(rep.process_name)
        if pred_proc:
            all_sqs = {int(b.sq_mm2 or 0) for b in gb}
            if len(all_sqs) > 1:
                valid = [
                    t
                    for sq_i in all_sqs
                    if (t := process_first_output_by_sq.get((pred_proc, sq_i)))
                    and t < datetime.max
                ]
                if valid:
                    earliest = max(earliest, min(valid))
            else:
                pf = process_first_output_by_sq.get((pred_proc, next(iter(all_sqs))))
                if pf and pf > earliest:
                    earliest = pf
            if rep.process_name == "고압시스":
                earliest += timedelta(hours=20)

        if gk.startswith(("A100_", "A120_")):
            if first_insul_output and first_insul_output > earliest:
                earliest = first_insul_output

        if gk.startswith("ST-") and rep.process_name == "연선":
            try:
                main_sq = int(gk.split("-")[1])
            except (IndexError, ValueError):
                main_sq = sq_int
            cf = core_first_drum_by_main_sq.get(main_sq)
            if cf and cf > earliest:
                earliest = cf

        _is_st = gk.startswith("ST-") and rep.process_name == "연선"
        _skip_ind = (
            rep.process_name
            in ("저압절연", "고압절연", "저압시스", "고압시스", "연합", "T/P")
            or _is_st
        )
        if not _skip_ind:
            for b in gb:
                pt = predecessor_map.get((b.sales_order_id, b.sales_order_line))
                if pt:
                    ptask = next((t for t in tasks_created if t.task_id == pt), None)
                    if ptask and ptask.end_datetime > earliest:
                        earliest = ptask.end_datetime

        # ── 긴급/중요 배치: 납기 위반 예상 시 선점 분할 시도 ────────────────────
        if (
            _priority_label(rep.customer_priority) in ("urgent", "critical")
            and meta["earliest_due"]
        ):
            slots_sim = timeline.get(chosen_eq_code, [])
            sim_start = _find_available_slot(
                earliest, total_dur, slots_sim, db, chosen_eq_code
            )
            sim_end = calculate_end_datetime(sim_start, total_dur, db, chosen_eq_code)
            if sim_end.date() > meta["earliest_due"] and sim_start > earliest:
                # 납기 초과 + earliest보다 늦게 시작 → 선점 가능 여부 시도
                rem_list = _try_preempt_for_urgent(
                    earliest=earliest,
                    chosen_eq_code=chosen_eq_code,
                    run_label=run_label,
                    timeline=timeline,
                    db=db,
                    urgent_priority=int(rep.customer_priority or 7),
                )
                if rem_list:
                    preempted_remainder.extend(rem_list)
                    result["warnings"].append(
                        f"선점분할: 배치그룹 {gk} 납기 {meta['earliest_due']} 맞추기 위해 "
                        f"{rem_list[0].batch_group} 잔여 {rem_list[0].drum_count}드럼 후처리 예약"
                    )

        # 캘린더 인식 슬롯 탐색 — 겹침 완전 방지
        slots = timeline.get(chosen_eq_code, [])
        best_start = _find_available_slot(
            earliest, total_dur, slots, db, chosen_eq_code
        )
        end_dt = calculate_end_datetime(best_start, total_dur, db, chosen_eq_code)

        # ── 파이프라인 겹침 보정: 후공정이 선행공정 종료 전에 끝나지 않도록 ───
        # 절연은 연선 첫 드럼 출력 후 시작하지만 선속이 빠르면 연선보다 먼저 끝나는 현상 방지.
        # 최소 end_dt = 선행공정 마지막 틀 완료 시각 + 후공정 1틀 소요시간.
        _pipeline_procs: list[str] = []
        if pred_proc:
            _pipeline_procs.append(pred_proc)
        if rep.process_name in ("저압시스", "고압시스"):
            _pipeline_procs.append("연합")
        if _pipeline_procs:
            _all_sqs_p = {int(b.sq_mm2 or 0) for b in gb}
            # 현재 그룹 틀 수를 직접 계산 (lot_count는 아직 미설정)
            header_batch_p = next((b for b in gb if b.batch_seq == -1), None)
            if header_batch_p is not None:
                _p_lot_count = max(int(header_batch_p.drum_count or 1), 1)
            else:
                _p_lot_count = max(sum(int(b.drum_count or 1) for b in gb), 1)
            _per_drum_p = meta["work_dur"] / _p_lot_count
            for _pp in _pipeline_procs:
                for _sq_i in _all_sqs_p:
                    _pred_last = process_end_by_sq.get((_pp, _sq_i))
                    if (
                        _pred_last
                        and _pred_last < datetime.max
                        and _pred_last > best_start
                    ):
                        _min_end = calculate_end_datetime(
                            _pred_last, _per_drum_p, db, chosen_eq_code
                        )
                        if _min_end > end_dt:
                            end_dt = _min_end

        # 정각 올림
        if end_dt.minute > 0 or end_dt.second > 0:
            end_dt = end_dt.replace(minute=0, second=0, microsecond=0) + timedelta(
                hours=1
            )

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

        # timeline 갱신 (이후 그룹이 이 슬롯을 피할 수 있도록)
        timeline.setdefault(chosen_eq_code, []).append((best_start, end_dt))

        # 파이프라인 첫 드럼 출력 시각 갱신
        header_batch = next((b for b in gb if b.batch_seq == -1), None)
        if header_batch:
            lot_count = max(int(header_batch.drum_count or 1), 1)
        else:
            # CORE 그룹 포함, 헤더 없는 그룹 모두 drum_count 합산 (len(gb) 아님)
            lot_count = max(sum(int(b.drum_count or 1) for b in gb), 1)
        first_drum_min = actual_setup + (meta["work_dur"] / lot_count)
        first_output_dt = calculate_end_datetime(
            best_start, first_drum_min, db, chosen_eq_code
        )

        proc_sq_key = (rep.process_name, sq_int)
        if not _is_core_group(gk):
            if (
                proc_sq_key not in process_first_output_by_sq
                or first_output_dt < process_first_output_by_sq[proc_sq_key]
            ):
                process_first_output_by_sq[proc_sq_key] = first_output_dt
        else:
            msq = _extract_core_main_sq(gk)
            if msq and (
                msq not in core_first_drum_by_main_sq
                or first_output_dt < core_first_drum_by_main_sq[msq]
            ):
                core_first_drum_by_main_sq[msq] = first_output_dt

        if rep.process_name == "저압절연":
            if first_insul_output is None or first_output_dt < first_insul_output:
                first_insul_output = first_output_dt

        if (
            proc_sq_key not in process_end_by_sq
            or end_dt > process_end_by_sq[proc_sq_key]
        ):
            process_end_by_sq[proc_sq_key] = end_dt

        for b in gb:
            predecessor_map[(b.sales_order_id, b.sales_order_line)] = task.task_id
            b.equipment_code = chosen_eq_code
            b.status = "scheduled"

        if rep.process_name == "연선" and not _is_core_group(gk):
            sq_to_equip[sq_key] = chosen_eq_code

        last_batch_on_equip[chosen_eq_code] = gb[-1]
        tasks_created.append(task)

        # 납기 위반 기록 — hard constraint 위반이므로 error 격상
        if meta["earliest_due"] and end_dt.date() > meta["earliest_due"]:
            late_days = (end_dt.date() - meta["earliest_due"]).days
            result["violations"].append(
                {
                    "batch_id": rep.batch_id,
                    "task_id": task.task_id,
                    "type": "delivery",
                    "severity": "error",
                    "detail": f"납기 {meta['earliest_due']} 초과 → 완료 {end_dt.date()} (+{late_days}일)",
                }
            )

        log_decision(
            db=db,
            run_label=run_label,
            stage="stage2",
            action_type="auto_assign",
            batch_id=rep.batch_id,
            task_id=task.task_id,
            reason=f"CP-SAT 순서 → {chosen_eq_code} @ {best_start:%Y-%m-%d %H:%M}",
        )
        result["total_tasks"] += 1

    # ── 9. 선점 잔여 배치 후속 배치 ───────────────────────────────────────────
    # 선점 분할로 생성된 잔여 배치들을 같은 설비에서 순서대로 스케줄링한다.
    # (이미 긴급 배치 슬롯이 timeline에 등록되어 있으므로 겹치지 않는다.)
    for rem_b in preempted_remainder:
        eq_code = rem_b.equipment_code
        if not eq_code:
            continue
        # 밀어낸 단드럼 배치는 setup_time을 유지; 분할 잔여는 setup=0 (이미 설정됨)
        rem_setup = float(rem_b.setup_time_min or 0)
        work_dur = float(rem_b.estimated_duration_min or 0)
        total_rem_dur = work_dur + rem_setup
        slots_rem = timeline.get(eq_code, [])
        rem_start = _find_available_slot(
            base_date, total_rem_dur, slots_rem, db, eq_code
        )
        rem_end = calculate_end_datetime(rem_start, total_rem_dur, db, eq_code)
        if rem_end.minute > 0 or rem_end.second > 0:
            rem_end = rem_end.replace(minute=0, second=0, microsecond=0) + timedelta(
                hours=1
            )

        rem_task = ScheduleTask(
            batch_id=rem_b.batch_id,
            equipment_code=eq_code,
            start_datetime=rem_start,
            end_datetime=rem_end,
            setup_time_min=rem_setup,
            status="scheduled",
            run_label=run_label,
            batch_group=rem_b.batch_group,
        )
        db.add(rem_task)
        db.flush()

        timeline.setdefault(eq_code, []).append((rem_start, rem_end))
        rem_b.status = "scheduled"
        rem_b.equipment_code = eq_code
        result["total_tasks"] += 1

    return result
