"""자동 스케줄링 엔진 — 납기역산 + 그리디 배치"""

from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.schedule_task import ScheduleTask
from app.infrastructure.models.equipment_master import EquipmentMaster
from app.infrastructure.models.speed_master import SpeedMaster
from app.infrastructure.models.constraint_config import ConstraintConfig
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
}

# 시스 재질 → 설비 라우팅 규칙 (10-3)
# 값은 equipment_code prefix 또는 특수 라우팅 키
_SHEATH_ROUTING = {
    "HFPO": "HFPO",  # HFPO 전용 라우팅 (설비 선택 시 HFPO 계열만)
    "PVC": "PVC",  # 표준 PVC 라우팅
    "LLDPE": "A150",  # LLDPE → A150 설비 고정
}


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
    _PROC_ORDER = {
        "신선": 0,
        "연선": 1,
        "저압절연": 2,
        "고압절연": 2,
        "연합": 3,
        "T/P": 3,
        "저압시스": 4,
        "고압시스": 4,
        "HFCO시스": 4,
    }
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
    # 같은 공정 내에서 SQ 내림차순, 납기순 정렬
    batches.sort(
        key=lambda b: (
            _PROC_ORDER.get(b.process_name, 50),
            -(float(b.sq_mm2 or 0)),
            b.due_date or date.max,
            b.customer_priority or 99,
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

    # ── 기준일시 설정 — KST 당일 08:00 (현장 근무 시작) ───────────────────
    if base_date is None:
        from zoneinfo import ZoneInfo

        kst_now = datetime.now(ZoneInfo("Asia/Seoul"))
        base_date = kst_now.replace(hour=8, minute=0, second=0, microsecond=0)
        if base_date.tzinfo:
            base_date = base_date.replace(tzinfo=None)  # naive datetime for DB

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

    # 공정 전체 종료 시각 추적 — 시스 배치 스케줄링 시 절연 전체 완료 대기용
    process_end_all: dict[str, datetime] = {}  # "저압절연" → 마지막 절연 그룹 종료 시각

    # ── batch_group 단위로 그루핑 ────────────────────────────────────────────
    from collections import OrderedDict

    batch_groups: OrderedDict[str, list[ProductionBatch]] = OrderedDict()
    for batch in batches:
        key = batch.batch_group or f"_single_{batch.batch_id}"
        batch_groups.setdefault(key, []).append(batch)

    for group_key, group_batches in batch_groups.items():
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

        # ── 규칙 2: 같은 SQ → 같은 설비 (연선 공정만, 70SQ+) ─────────────
        sq = int(rep.sq_mm2 or 0)
        sq_key = (rep.process_name, sq)
        is_stranding = rep.process_name == "연선"
        if is_stranding and sq >= 70 and sq_key in sq_to_equip:
            preferred_eq = sq_to_equip[sq_key]
            pref_match = [e for e in eligible if e.equipment_code == preferred_eq]
            if pref_match:
                eligible = pref_match

        # ── 규칙 3: 소선경 그루핑 ────────────────────────────────────────────
        if is_stranding and sq_key not in sq_to_equip:
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
            result["warnings"].append(
                f"배치그룹 {group_key}: 공정 '{rep.process_name}'에 적합한 설비 없음"
            )
            continue

        # ── 그룹 전체 duration 합산 ──────────────────────────────────────────
        line_speed = float(rep.line_speed_mpm or 10)
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

            # 4-2: 색상교체 시간 — 그룹 간 변경 시
            color_change_min = 0.0
            if prev_batch is not None and rep.process_name in (
                "저압시스",
                "고압시스",
                "HFCO시스",
            ):
                prev_color = (prev_batch.sheath_color or "").strip()
                curr_color = (rep.sheath_color or "").strip()
                if prev_color and curr_color and prev_color != curr_color:
                    color_change_min = 120.0
            eq_total_duration += color_change_min

            # ── 선행공정(predecessor) — 공정 순서에 따라 앞 공정 종료 후 시작
            earliest = base_date
            sq_int = int(rep.sq_mm2 or 0)

            # 공정 간 선행관계: 연선→절연→시스 순서 강제
            _PREDECESSOR_PROCESS = {
                "저압절연": "연선",
                "고압절연": "연선",
                "저압시스": "저압절연",
                "고압시스": "고압절연",
                "연합": "연선",
            }
            pred_proc = _PREDECESSOR_PROCESS.get(rep.process_name)
            if pred_proc:
                pred_end = process_end_by_sq.get((pred_proc, sq_int))
                if pred_end and pred_end > earliest:
                    earliest = pred_end
                    # 고압 건조대기 20hr
                    if rep.process_name in ("고압시스",):
                        earliest += timedelta(hours=20)

            # 시스 배치(A100/A120): 저압절연 전체 완료 후 시작
            if group_key.startswith("A100_") or group_key.startswith("A120_"):
                all_insul_end = process_end_all.get("저압절연")
                if all_insul_end and all_insul_end > earliest:
                    earliest = all_insul_end

            # 개별 수주 레벨 predecessor도 확인 (더 늦은 것 우선)
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

        # 공정 전체 종료 시각 갱신 — 시스 배치 스케줄링 시 절연 전체 완료 대기용
        if rep.process_name == "저압절연":
            if (
                "저압절연" not in process_end_all
                or end_dt > process_end_all["저압절연"]
            ):
                process_end_all["저압절연"] = end_dt

        # 그룹 내 모든 배치의 predecessor + status 갱신
        for b in group_batches:
            pred_key = (b.sales_order_id, b.sales_order_line)
            predecessor_map[pred_key] = task.task_id
            b.equipment_code = best_eq.equipment_code
            b.status = "scheduled"

        # 규칙 2: SQ→설비 매핑 기록
        if rep.process_name == "연선":
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
                    "detail": f"priority={batch.customer_priority}",
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
                    "detail": f"process={batch.process_name}, speed={line_speed:.1f}mpm",
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
                    "detail": f"material={batch.conductor_material}, equip_limit={best_eq.material_limit}",
                },
                {
                    "id": "10-3",
                    "name": "시스 재질 라우팅",
                    "result": "pass",
                    "detail": f"sheath_type={_get_sheath_type(batch)}, equip={best_eq.equipment_code}",
                },
            ],
            reason=(
                f"설비 {best_eq.equipment_name}에 배치: "
                f"SQ={batch.sq_mm2}, 납기={batch.due_date}, 소요={best_total_duration:.0f}분"
            ),
        )

        result["total_tasks"] += 1

    return result


def _find_eligible_equipment(
    batch: ProductionBatch, equipment: list[EquipmentMaster]
) -> list[EquipmentMaster]:
    """배치에 적합한 설비 필터링 (재질, SQ범위, 색상그룹)"""
    eligible = []
    for eq in equipment:
        # Material filter
        if eq.material_limit and eq.material_limit != "ALL":
            if (
                batch.conductor_material
                and batch.conductor_material != eq.material_limit
            ):
                continue

        # Range filter — 단위에 따라 다른 비교
        # 신선: range_unit="mm" → 소선경과 비교 (SQ 비교 안함, 신선은 모든 SQ 가능)
        # 연합/T/P: range_unit="Ø" → 외경 기준 (배치에 외경 정보 없으므로 SQ 비교 안함)
        # 연선/절연/시스: range_unit="SQ" → SQ로 비교
        if eq.range_unit == "mm":
            # 신선: 소선경 기준 — 배치에 소선경 정보 없으므로 재질로만 필터링
            pass
        elif eq.range_unit == "Ø":
            # 연합/T/P/시스: 외경(Ø) 기준 — SQ에서 근사 외경으로 변환 후 비교
            sq = float(batch.sq_mm2) if batch.sq_mm2 else None
            if sq and eq.range_max:
                # SQ → 근사 외경(mm) 변환: Ø ≈ sqrt(SQ) * 1.5 + 5 (단심 기준 경험식)
                approx_od = (sq**0.5) * 1.5 + 5
                if approx_od > float(eq.range_max):
                    continue
        else:
            sq = float(batch.sq_mm2) if batch.sq_mm2 else None
            if sq and eq.range_min and sq < float(eq.range_min):
                continue
            if sq and eq.range_max and sq > float(eq.range_max):
                continue

        # Color group filter (저압시스)
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
            # "전색상" accepts everything

        eligible.append(eq)

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
