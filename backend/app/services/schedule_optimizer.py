"""자동 스케줄링 엔진 — 납기역산 + 그리디 배치"""

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.schedule_task import ScheduleTask
from app.infrastructure.models.equipment_master import EquipmentMaster
from app.services.calendar_engine import calculate_end_datetime
from app.services.audit_logger import log_decision


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

    tasks_created = []

    for batch in batches:
        # Find eligible equipment for this batch's process
        eligible = _find_eligible_equipment(
            batch, equipment_by_process.get(batch.process_name, [])
        )

        if not eligible:
            result["warnings"].append(
                f"배치 {batch.batch_id}: 공정 '{batch.process_name}'에 적합한 설비 없음"
            )
            continue

        # Calculate duration
        duration_min = float(batch.estimated_duration_min or 0)
        if duration_min <= 0:
            # Fallback: use total_length / 10 m/min default
            total = float(batch.total_length_m or 0) + float(batch.extra_length_m or 0)
            speed = float(batch.line_speed_mpm or 10)
            duration_min = total / speed if speed > 0 else 60

        setup_min = float(batch.setup_time_min or 0)
        total_duration = duration_min + setup_min

        # Find the best equipment slot (minimize setup time, earliest available)
        best_eq = None
        best_start = None
        best_setup = setup_min

        for eq in eligible:
            eq_code = eq.equipment_code
            slots = timeline.get(eq_code, [])

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
            slot_start = _find_available_slot(earliest, total_duration, slots, db)

            if best_start is None or slot_start < best_start:
                best_eq = eq
                best_start = slot_start
                # Check if same SQ as previous task on this equipment → skip spec change
                if slots:
                    last_end = max(s[1] for s in slots)  # noqa: F841
                    # Simplified: if we have a batch right before, check SQ match
                    # For now, use full setup time

        if best_eq is None or best_start is None:
            result["warnings"].append(f"배치 {batch.batch_id}: 가용 슬롯 없음")
            continue

        # Calculate end time considering calendar
        end_dt = calculate_end_datetime(best_start, total_duration, db)

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
            ],
            reason=(
                f"설비 {best_eq.equipment_name}에 배치: "
                f"SQ={batch.sq_mm2}, 납기={batch.due_date}, 소요={total_duration:.0f}분"
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

        # SQ range filter
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
