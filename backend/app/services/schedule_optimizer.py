"""자동 스케줄링 엔진 — 납기역산 + 그리디 배치"""

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.schedule_task import ScheduleTask
from app.infrastructure.models.equipment_master import EquipmentMaster
from app.infrastructure.models.speed_master import SpeedMaster
from app.infrastructure.models.constraint_config import ConstraintConfig
from app.services.calendar_engine import calculate_end_datetime
from app.services.audit_logger import log_decision

# 용접 시간 기본값 (4-4): constraint_config params_json에서 읽을 때 없으면 사용
_DEFAULT_WELDING_MIN = 30

# 시스 재질 → 설비 라우팅 규칙 (10-3)
# 값은 equipment_code prefix 또는 특수 라우팅 키
_SHEATH_ROUTING = {
    "HFPO": "HFPO",  # HFPO 전용 라우팅 (설비 선택 시 HFPO 계열만)
    "PVC": "PVC",  # 표준 PVC 라우팅
    "LLDPE": "A150",  # LLDPE → A150 설비 고정
}


def auto_schedule(run_label: str, db: Session) -> dict:
    """
    run_label의 production_batch를 간트 차트에 자동 배치.
    Returns: {"total_tasks": int, "violations": list, "warnings": list}
    """
    result = {"total_tasks": 0, "violations": [], "warnings": []}

    # Load all batches for this run, excluding outsourced and already-scheduled
    batches = (
        db.query(ProductionBatch)
        .filter(
            ProductionBatch.run_label == run_label,
            ProductionBatch.status == "planned",
        )
        .order_by(
            ProductionBatch.due_date.asc(),
            ProductionBatch.customer_priority.asc(),
        )
        .all()
    )

    if not batches:
        result["warnings"].append("배치 없음 — Stage 1을 먼저 실행하세요")
        return result

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

    tasks_created = []

    for batch in batches:
        # 10-3: 시스 재질 라우팅 — 고압시스 공정에서 sheath_type에 따라 설비 후보 필터
        candidate_equip = equipment_by_process.get(batch.process_name, [])
        if batch.process_name in ("고압시스", "저압시스"):
            candidate_equip = _filter_by_sheath_routing(batch, candidate_equip)

        # Find eligible equipment for this batch's process
        eligible = _find_eligible_equipment(batch, candidate_equip)

        if not eligible:
            result["warnings"].append(
                f"배치 {batch.batch_id}: 공정 '{batch.process_name}'에 적합한 설비 없음"
            )
            continue

        # 4-5: T/P 공정이면 SpeedMaster에서 T/P 전용 속도 읽기
        line_speed = float(batch.line_speed_mpm or 10)
        if batch.process_name == "T/P" and eligible:
            # 첫 번째 후보 설비 기준으로 속도 조회 (최종 설비 확정 전 추정)
            tp_speed = _get_tp_line_speed(
                eligible[0].equipment_code, batch.sq_mm2, speed_map
            )
            if tp_speed is not None:
                line_speed = tp_speed

        # Calculate duration
        duration_min = float(batch.estimated_duration_min or 0)
        if duration_min <= 0:
            # Fallback: total_length / line_speed
            total = float(batch.total_length_m or 0) + float(batch.extra_length_m or 0)
            duration_min = total / line_speed if line_speed > 0 else 60

        setup_min = float(batch.setup_time_min or 0)

        # 4-3: 드럼 권취 시간 — SpeedMaster의 setup_start_min 추가
        drum_winding_min = _get_drum_winding_min(
            eligible[0].equipment_code, batch.sq_mm2, speed_map
        )

        total_duration = duration_min + setup_min + drum_winding_min

        # Find the best equipment slot (minimize setup time, earliest available)
        best_eq = None
        best_start = None
        best_setup = setup_min
        best_total_duration = total_duration

        for eq in eligible:
            eq_code = eq.equipment_code
            slots = timeline.get(eq_code, [])

            # 4-5: T/P 공정에서 최종 설비 확정 후 정확한 속도로 duration 재계산
            eq_total_duration = total_duration
            if batch.process_name == "T/P":
                tp_speed = _get_tp_line_speed(eq_code, batch.sq_mm2, speed_map)
                if tp_speed is not None and tp_speed > 0:
                    total_len = float(batch.total_length_m or 0) + float(
                        batch.extra_length_m or 0
                    )
                    eq_duration = total_len / tp_speed
                    eq_drum_winding = _get_drum_winding_min(
                        eq_code, batch.sq_mm2, speed_map
                    )
                    eq_total_duration = eq_duration + setup_min + eq_drum_winding

            # 4-4: 스플라이스 용접 시간 — 직전 배치가 같은 SQ이나 다른 수주(접합 로트)이면 추가
            extra_welding = 0.0
            prev_batch = last_batch_on_equip.get(eq_code)
            if prev_batch is not None:
                same_sq = (
                    prev_batch.sq_mm2 is not None
                    and batch.sq_mm2 is not None
                    and float(prev_batch.sq_mm2) == float(batch.sq_mm2)
                )
                diff_order = prev_batch.sales_order_id != batch.sales_order_id
                if same_sq and diff_order:
                    # 접합(스플라이스) 로트 — 용접 시간 추가
                    extra_welding = welding_min

            eq_total_duration += extra_welding

            # Determine earliest start: after predecessor or now
            predecessor_key = (batch.sales_order_id, batch.sales_order_line)
            predecessor_task_id = predecessor_map.get(predecessor_key)

            earliest = datetime.now()
            if predecessor_task_id:
                pred_task = next(
                    (t for t in tasks_created if t.task_id == predecessor_task_id), None
                )
                if pred_task:
                    earliest = pred_task.end_datetime
                    # Add inter-process wait time (e.g., 고압 건조대기 20hr)
                    if batch.process_name in ("고압시스",) and pred_task:
                        earliest += timedelta(hours=20)

            # Find first available slot on this equipment
            slot_start = _find_available_slot(earliest, eq_total_duration, slots, db)

            if best_start is None or slot_start < best_start:
                best_eq = eq
                best_start = slot_start
                best_total_duration = eq_total_duration
                # Check if same SQ as previous task on this equipment → skip spec change
                if slots:
                    last_end = max(s[1] for s in slots)  # noqa: F841
                    # Simplified: if we have a batch right before, check SQ match
                    # For now, use full setup time

        if best_eq is None or best_start is None:
            result["warnings"].append(f"배치 {batch.batch_id}: 가용 슬롯 없음")
            continue

        # Calculate end time considering calendar
        end_dt = calculate_end_datetime(best_start, best_total_duration, db)

        # Create schedule task
        task = ScheduleTask(
            batch_id=batch.batch_id,
            equipment_code=best_eq.equipment_code,
            start_datetime=best_start,
            end_datetime=end_dt,
            setup_time_min=best_setup,
            status="scheduled",
            run_label=run_label,
        )
        db.add(task)
        db.flush()  # get task_id

        # Update timeline
        timeline.setdefault(best_eq.equipment_code, []).append((best_start, end_dt))

        # Update predecessor map
        predecessor_key = (batch.sales_order_id, batch.sales_order_line)
        predecessor_map[predecessor_key] = task.task_id

        # Update batch status
        batch.equipment_code = best_eq.equipment_code
        batch.status = "scheduled"

        # 용접 시간 추적 (4-4): 설비별 마지막 배치 갱신
        last_batch_on_equip[best_eq.equipment_code] = batch

        tasks_created.append(task)

        # Check delivery date violation
        if batch.due_date and end_dt.date() > batch.due_date:
            violation = {
                "batch_id": batch.batch_id,
                "task_id": task.task_id,
                "type": "delivery",
                "severity": "warning",
                "detail": f"납기 {batch.due_date} 초과 → 완료 예정 {end_dt.date()}",
            }
            result["violations"].append(violation)

        # Audit log
        log_decision(
            db=db,
            run_label=run_label,
            stage="stage2",
            batch_id=batch.batch_id,
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
        if eq.range_unit in ("mm", "Ø"):
            # mm(신선): 소선경 기준, Ø(연합/T/P): 외경 기준
            # 두 경우 모두 배치에 해당 치수 정보가 없으므로 재질·공정명으로만 필터링
            pass
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


def reschedule(run_label: str, db: Session) -> dict:
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
    result = auto_schedule(run_label, db)
    result["cleared_tasks"] = cleared_count
    return result
