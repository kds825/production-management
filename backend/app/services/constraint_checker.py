"""28개 제약조건 검증 엔진 — 스케줄링 결과 사후 검증"""

from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.schedule_task import ScheduleTask
from app.infrastructure.models.constraint_config import ConstraintConfig
from app.infrastructure.models.equipment_master import EquipmentMaster
from app.infrastructure.models.operation_calendar import OperationCalendar


def has_overlap(violations: list[dict]) -> bool:
    """validate_all 결과에 겹침 위반이 하나라도 있는지.

    DRY: 기존에는 호출자마다 `[v for v in violations if v.get("constraint_id") == "overlap"]`
    를 인라인으로 반복했음. overlap 검출 로직을 단일 함수로 집약해 유지보수성을 높인다.
    """
    return any(v.get("constraint_id") == "overlap" for v in violations)


def validate_overlap_only(run_label: str, db: Session) -> list[dict]:
    """재시도 판단 전용 경량 검증 — 겹침만 체크.

    왜 분리했는가:
      auto_schedule 의 재시도 루프는 "겹침이 있으면 다시 돌린다" 만 필요.
      전체 28개 체커를 돌리는 validate_all 은 재시도마다 공통 로드(tasks/batches/
      equipment/constraints 4 쿼리) + 체커 loop 를 반복해 불필요한 비용이 크다.
      이 함수는 ScheduleTask 만 로드하고 _check_overlap 만 실행해
      재시도 판단 속도를 높인다.

    최종 Stage2 응답에는 여전히 validate_all 을 사용해 모든 violation 을 반환.
    """
    tasks = db.query(ScheduleTask).filter(ScheduleTask.run_label == run_label).all()
    return _check_overlap(tasks)


def validate_all(run_label: str, db: Session) -> list[dict]:
    """모든 활성 제약조건으로 스케줄 검증. Returns list of violations."""

    violations = []

    # Load data
    tasks = db.query(ScheduleTask).filter(ScheduleTask.run_label == run_label).all()
    batches = {
        b.batch_id: b
        for b in db.query(ProductionBatch)
        .filter(ProductionBatch.run_label == run_label)
        .all()
    }
    equipment = {e.equipment_code: e for e in db.query(EquipmentMaster).all()}
    constraints = (
        db.query(ConstraintConfig).filter(ConstraintConfig.is_enabled == True).all()  # noqa: E712
    )

    constraint_map = {c.constraint_id: c for c in constraints}

    # 체커 함수 시그니처: (tasks, batches, equipment, config) → list[dict]
    # db 접근이 필요한 체커는 클로저로 db 캡처
    checkers = {
        "1-1": _check_priority_order,
        "1-2": _check_due_type,
        "3-3": _check_color_group,
        "4-1": _check_setup_time,
        "5-1": _check_sq_range,
        "6-1": _check_safety_education,
        "6-2": _check_friday_hours,
        "6-3": lambda t, b, e, c: _check_absence_hours(t, b, e, c, db),
        "6-4": lambda t, b, e, c: _check_holiday(t, b, e, c, db),
        "7-1": _check_defect_buffer,
        "7-2": lambda t, b, e, c: _check_equipment_utilization(t, b, e, c, db),
        "8-1": _check_material_availability,
        "8-2": _check_procurement_lead_time,
        "8-3": _check_raw_material_availability,
        "9-1": _check_precedence,
        "9-2": _check_gc_routing,
        "10-2": _check_material_separation,
    }

    # Also check universal constraints
    violations.extend(_check_overlap(tasks))
    violations.extend(_check_delivery(tasks, batches))

    for cid, checker_fn in checkers.items():
        if cid in constraint_map:
            try:
                v = checker_fn(tasks, batches, equipment, constraint_map[cid])
                violations.extend(v)
            except Exception as e:
                violations.append(
                    {
                        "constraint_id": cid,
                        "severity": "error",
                        "detail": f"체커 실행 오류: {str(e)}",
                    }
                )

    return violations


def _check_overlap(tasks: list) -> list[dict]:
    """동일 설비에서 시간 겹침 확인"""
    violations = []
    by_equip = {}
    for t in tasks:
        by_equip.setdefault(t.equipment_code, []).append(t)

    for eq_code, eq_tasks in by_equip.items():
        sorted_tasks = sorted(eq_tasks, key=lambda x: x.start_datetime)
        for i in range(len(sorted_tasks) - 1):
            if sorted_tasks[i].end_datetime > sorted_tasks[i + 1].start_datetime:
                violations.append(
                    {
                        "constraint_id": "overlap",
                        "task_id": sorted_tasks[i + 1].task_id,
                        "severity": "error",
                        "detail": f"설비 {eq_code}: 작업 {sorted_tasks[i].task_id}과 시간 겹침",
                    }
                )
    return violations


def _check_delivery(tasks, batches) -> list[dict]:
    """납기 초과 확인 — 사용자 요구 '납기는 반드시 지켜져야함' → severity=error"""
    violations = []
    for t in tasks:
        batch = batches.get(t.batch_id)
        if batch and batch.due_date and t.end_datetime.date() > batch.due_date:
            violations.append(
                {
                    "constraint_id": "1-1",
                    "task_id": t.task_id,
                    "batch_id": t.batch_id,
                    # 납기는 하드 제약 — 위반 시 error 로 격상하여 재시도/알림 트리거
                    "severity": "error",
                    "detail": f"납기 {batch.due_date} 초과 (완료 예정: {t.end_datetime.date()})",
                }
            )
    return violations


def _check_priority_order(tasks, batches, equipment, config) -> list[dict]:
    """같은 설비에서 우선순위 낮은 주문이 높은 주문보다 먼저 배치되었는지"""
    violations = []
    by_equip = {}
    for t in tasks:
        by_equip.setdefault(t.equipment_code, []).append(t)

    for eq_code, eq_tasks in by_equip.items():
        sorted_tasks = sorted(eq_tasks, key=lambda x: x.start_datetime)
        for i in range(len(sorted_tasks) - 1):
            b1 = batches.get(sorted_tasks[i].batch_id)
            b2 = batches.get(sorted_tasks[i + 1].batch_id)
            if b1 and b2:
                if (b1.customer_priority or 99) > (b2.customer_priority or 99):
                    if b1.due_date and b2.due_date and b1.due_date > b2.due_date:
                        # Lower priority task is scheduled first AND has later due date
                        violations.append(
                            {
                                "constraint_id": "1-1",
                                "task_id": sorted_tasks[i].task_id,
                                "severity": "warning",
                                "detail": (
                                    f"우선순위 역전: {b1.customer_name}(P{b1.customer_priority})"
                                    f" before {b2.customer_name}(P{b2.customer_priority})"
                                ),
                            }
                        )
    return violations


def _check_due_type(tasks, batches, equipment, config) -> list[dict]:
    """도착기준 고객은 운송일 1일 차감하여 실질 납기 체크"""

    violations = []
    transport_days = 1  # default
    if config.params_json:
        transport_days = config.params_json.get("transport_days", 1)

    for t in tasks:
        batch = batches.get(t.batch_id)
        if not batch or not batch.due_date:
            continue
        # Check if customer is 도착기준 type
        # For now, check customer_name against known 도착기준 customers
        if batch.customer_name and "아이마켓" in batch.customer_name:
            effective_due = batch.due_date - timedelta(days=transport_days)
            if t.end_datetime.date() > effective_due:
                violations.append(
                    {
                        "constraint_id": "1-2",
                        "task_id": t.task_id,
                        "severity": "warning",
                        "detail": (
                            f"도착기준 고객 {batch.customer_name}: 실질납기 {effective_due}"
                            f" 초과 (완료: {t.end_datetime.date()})"
                        ),
                    }
                )
    return violations


def _check_color_group(tasks, batches, equipment, config) -> list[dict]:
    """설비 색상그룹 제한 확인 (A120: 흑/청만)"""
    violations = []
    for t in tasks:
        batch = batches.get(t.batch_id)
        eq = equipment.get(t.equipment_code)
        if batch and eq and eq.color_group == "흑/청":
            color = (batch.sheath_color or "").strip()
            if color and color not in (
                "흑",
                "청",
                "흑색",
                "청색",
                "BLACK",
                "BLUE",
                "BK",
                "BL",
            ):
                violations.append(
                    {
                        "constraint_id": "3-3",
                        "task_id": t.task_id,
                        "severity": "error",
                        "detail": f"A120 설비에 {color} 색상 배정 (흑/청만 가능)",
                    }
                )
    return violations


def _check_setup_time(tasks, batches, equipment, config) -> list[dict]:
    """규격교체 시간이 연속 작업 간에 반영되었는지 확인"""
    violations = []
    setup_params = config.params_json or {}  # noqa: F841 — reserved for future param lookup

    by_equip = {}
    for t in tasks:
        by_equip.setdefault(t.equipment_code, []).append(t)

    for eq_code, eq_tasks in by_equip.items():
        sorted_tasks = sorted(eq_tasks, key=lambda x: x.start_datetime)
        for i in range(len(sorted_tasks) - 1):
            curr = sorted_tasks[i]
            next_task = sorted_tasks[i + 1]
            curr_batch = batches.get(curr.batch_id)
            next_batch = batches.get(next_task.batch_id)

            if not curr_batch or not next_batch:
                continue

            # SQ 변경 발생 시 규격교체 준비시간 확보 여부 확인
            if curr_batch.sq_mm2 != next_batch.sq_mm2:
                gap_min = (
                    next_task.start_datetime - curr.end_datetime
                ).total_seconds() / 60
                # Why:
                # schedule_optimizer 는 setup 을 task.setup_time_min 내부에 저장하고
                # eq_total_duration 에 포함(line 1309). single-equipment 경로는 next 에,
                # multi-equipment 경로는 curr 에 setup 을 기록하는 차이가 있음.
                # 따라서 "어느 쪽에든 setup 시간이 기록됐는가" 를 gap + curr + next 합으로
                # 판정해야 false positive (gap만 보는 과거 로직) 가 발생하지 않는다.
                curr_setup = float(curr.setup_time_min or 0)
                next_setup = float(next_task.setup_time_min or 0)
                required_setup = max(curr_setup, next_setup)
                effective_setup = gap_min + curr_setup + next_setup

                if required_setup > 0 and effective_setup < required_setup * 0.5:
                    violations.append(
                        {
                            "constraint_id": "4-1",
                            "task_id": next_task.task_id,
                            "severity": "warning",
                            "detail": (
                                f"설비 {eq_code}: SQ {curr_batch.sq_mm2}→{next_batch.sq_mm2}"
                                f" 교체, 확보된 setup {effective_setup:.0f}분"
                                f" (gap={gap_min:.0f} + curr.setup={curr_setup:.0f}"
                                f" + next.setup={next_setup:.0f}, 필요: {required_setup:.0f}분)"
                            ),
                        }
                    )
    return violations


def _check_sq_range(tasks, batches, equipment, config) -> list[dict]:
    """설비 SQ 범위 확인"""
    violations = []
    for t in tasks:
        batch = batches.get(t.batch_id)
        eq = equipment.get(t.equipment_code)
        if batch and eq and batch.sq_mm2:
            sq = float(batch.sq_mm2)
            if eq.range_min and sq < float(eq.range_min):
                violations.append(
                    {
                        "constraint_id": "5-1",
                        "task_id": t.task_id,
                        "severity": "error",
                        "detail": f"SQ {sq} < 설비 최소 {eq.range_min}",
                    }
                )
            if eq.range_max and sq > float(eq.range_max):
                violations.append(
                    {
                        "constraint_id": "5-1",
                        "task_id": t.task_id,
                        "severity": "error",
                        "detail": f"SQ {sq} > 설비 최대 {eq.range_max}",
                    }
                )
    return violations


def _check_safety_education(tasks, batches, equipment, config) -> list[dict]:
    """안전교육 시간대에 작업 배정 확인"""
    from app.services.calendar_engine import _is_last_two_mondays
    from datetime import time

    violations = []
    for t in tasks:
        d = t.start_datetime.date()
        if _is_last_two_mondays(d):
            if t.start_datetime.time() < time(10, 0):
                violations.append(
                    {
                        "constraint_id": "6-1",
                        "task_id": t.task_id,
                        "severity": "warning",
                        "detail": f"안전교육일 {d} 08~10시 작업 배정",
                    }
                )
    return violations


def _check_friday_hours(tasks, batches, equipment, config) -> list[dict]:
    """금요일 24시 이후 작업 확인"""
    violations = []
    for t in tasks:
        if t.start_datetime.weekday() == 5 and t.start_datetime.hour < 8:
            # Saturday before 08:00 means it ran past Friday midnight
            violations.append(
                {
                    "constraint_id": "6-2",
                    "task_id": t.task_id,
                    "severity": "warning",
                    "detail": "금요일 24시 이후 작업 연장",
                }
            )
    return violations


def _check_precedence(tasks, batches, equipment, config) -> list[dict]:
    """선행공정 완료 전 후속공정 시작 확인"""
    violations = []
    task_map = {t.task_id: t for t in tasks}
    for t in tasks:
        if t.predecessor_task_id:
            pred = task_map.get(t.predecessor_task_id)
            if pred and t.start_datetime < pred.end_datetime:
                violations.append(
                    {
                        "constraint_id": "9-1",
                        "task_id": t.task_id,
                        "severity": "error",
                        "detail": f"선행작업 {pred.task_id} 완료 전 시작",
                    }
                )
    return violations


def _check_material_separation(tasks, batches, equipment, config) -> list[dict]:
    """CU/AL 재질 설비 분리 확인"""
    violations = []
    for t in tasks:
        batch = batches.get(t.batch_id)
        eq = equipment.get(t.equipment_code)
        if batch and eq and eq.material_limit and eq.material_limit != "ALL":
            if (
                batch.conductor_material
                and batch.conductor_material != eq.material_limit
            ):
                violations.append(
                    {
                        "constraint_id": "10-2",
                        "task_id": t.task_id,
                        "severity": "error",
                        "detail": (
                            f"{batch.conductor_material} 제품이 "
                            f"{eq.material_limit} 전용 설비에 배정"
                        ),
                    }
                )
    return violations


# ---------------------------------------------------------------------------
# 신규 체커 함수 (7-1, 7-2, 8-1, 8-2, 8-3, 6-3, 6-4, 9-2)
# ---------------------------------------------------------------------------


def _check_defect_buffer(tasks, batches, equipment, config) -> list[dict]:
    """
    7-1: 불량 재작업 버퍼 검증.
    batch_grouping에서 total_length_m = ordered_qty * 1.05로 5% 버퍼가
    이미 포함되어 있으므로, total_length_m / (drum_length_m * drum_count) >= 1.05 인지 확인.
    params_json: {"defect_buffer_pct": 0.05}  → 기본 5%
    """
    violations = []
    params = config.params_json or {}
    buffer_pct = float(params.get("defect_buffer_pct", 0.05))

    for t in tasks:
        batch = batches.get(t.batch_id)
        if not batch:
            continue
        total = float(batch.total_length_m or 0)
        drum_len = float(batch.drum_length_m or 0)
        drum_cnt = int(batch.drum_count or 1)
        if total <= 0 or drum_len <= 0:
            continue
        ordered_qty = drum_len * drum_cnt
        expected_min = ordered_qty * (1 + buffer_pct)
        if total < expected_min * 0.99:  # 1% 허용오차
            violations.append(
                {
                    "constraint_id": "7-1",
                    "task_id": t.task_id,
                    "batch_id": t.batch_id,
                    "severity": "warning",
                    "detail": (
                        f"불량 버퍼 부족: total={total:.0f}m"
                        f" < 필요 {expected_min:.0f}m (주문 {ordered_qty:.0f}m × {(1 + buffer_pct):.2f})"
                    ),
                }
            )
    return violations


def _check_equipment_utilization(
    tasks, batches, equipment, config, db: Session
) -> list[dict]:
    """
    7-2: 설비 가동률 90% 초과 시 '고장 위험' 경고.
    스케줄 기간의 총 가용 시간 대비 실제 사용 시간 비율을 계산한다.
    params_json: {"utilization_threshold": 0.9}
    """
    violations = []
    params = config.params_json or {}
    threshold = float(params.get("utilization_threshold", 0.9))

    if not tasks:
        return violations

    # 스케줄 전체 기간 계산
    min_start = min(t.start_datetime for t in tasks)
    max_end = max(t.end_datetime for t in tasks)
    total_period_hours = (max_end - min_start).total_seconds() / 3600.0

    if total_period_hours <= 0:
        return violations

    # 설비별 총 사용 시간 합산
    usage_hours: dict[str, float] = {}
    for t in tasks:
        duration_h = (t.end_datetime - t.start_datetime).total_seconds() / 3600.0
        usage_hours[t.equipment_code] = (
            usage_hours.get(t.equipment_code, 0.0) + duration_h
        )

    for eq_code, used_h in usage_hours.items():
        eq = equipment.get(eq_code)
        # 설비의 base_working_hours/일 × 기간 일수로 가용 시간 산정
        base_h_day = float((eq.base_working_hours or 20)) if eq else 20.0
        period_days = total_period_hours / 24.0
        available_h = base_h_day * period_days

        if available_h > 0 and (used_h / available_h) > threshold:
            violations.append(
                {
                    "constraint_id": "7-2",
                    "equipment_code": eq_code,
                    "severity": "warning",
                    "detail": (
                        f"설비 {eq_code} 가동률 {used_h / available_h * 100:.1f}%"
                        f" (임계값 {threshold * 100:.0f}%) — 고장 위험"
                    ),
                }
            )
    return violations


def _check_material_availability(tasks, batches, equipment, config) -> list[dict]:
    """
    8-1: 테이프/컴파운드 자재수급 확인 (수동 플래그).
    params_json: {"material_available": true}  — false이면 경고 발생.
    """
    violations = []
    params = config.params_json or {}
    is_available = params.get("material_available", True)

    if not is_available:
        # 테이핑/시스 공정 배치에 경고 부착
        target_processes = {"T/P", "저압시스", "고압시스"}
        flagged = set()
        for t in tasks:
            batch = batches.get(t.batch_id)
            if batch and batch.process_name in target_processes:
                key = t.task_id
                if key not in flagged:
                    flagged.add(key)
                    violations.append(
                        {
                            "constraint_id": "8-1",
                            "task_id": t.task_id,
                            "batch_id": t.batch_id,
                            "severity": "warning",
                            "detail": (
                                f"테이프/컴파운드 자재 미확보 플래그 설정됨"
                                f" — 공정 '{batch.process_name}' 작업 주의"
                            ),
                        }
                    )
    return violations


def _check_raw_material_availability(tasks, batches, equipment, config) -> list[dict]:
    """
    8-3: CU/AL 원자재 수급 확인 (수동 플래그).
    params_json: {"cu_available": true, "al_available": true}
    """
    violations = []
    params = config.params_json or {}
    cu_ok = params.get("cu_available", True)
    al_ok = params.get("al_available", True)

    for t in tasks:
        batch = batches.get(t.batch_id)
        if not batch:
            continue
        material = (batch.conductor_material or "").upper()
        if material == "CU" and not cu_ok:
            violations.append(
                {
                    "constraint_id": "8-3",
                    "task_id": t.task_id,
                    "batch_id": t.batch_id,
                    "severity": "warning",
                    "detail": "CU 원자재 미확보 플래그 — 작업 착수 전 자재 확인 필요",
                }
            )
        elif material == "AL" and not al_ok:
            violations.append(
                {
                    "constraint_id": "8-3",
                    "task_id": t.task_id,
                    "batch_id": t.batch_id,
                    "severity": "warning",
                    "detail": "AL 원자재 미확보 플래그 — 작업 착수 전 자재 확인 필요",
                }
            )
    return violations


def _check_procurement_lead_time(tasks, batches, equipment, config) -> list[dict]:
    """
    8-2: 조달 리드타임 검증.
    배치 시작일이 오늘 + lead_time_days보다 이른 경우 경고.
    params_json: {"lead_time_days": 7}
    """
    violations = []
    params = config.params_json or {}
    lead_time_days = int(params.get("lead_time_days", 7))
    earliest_ok = date.today() + timedelta(days=lead_time_days)

    for t in tasks:
        if t.start_datetime.date() < earliest_ok:
            batch = batches.get(t.batch_id)
            violations.append(
                {
                    "constraint_id": "8-2",
                    "task_id": t.task_id,
                    "batch_id": t.batch_id,
                    "severity": "warning",
                    "detail": (
                        f"시작일 {t.start_datetime.date()} < 조달 리드타임 기준일 {earliest_ok}"
                        f" (리드타임 {lead_time_days}일)"
                    ),
                }
            )
    return violations


def _check_holiday(tasks, batches, equipment, config, db: Session) -> list[dict]:
    """
    6-4: 공휴일 작업 배정 확인.
    operation_calendar에서 rule_code = 'CAL-HOL'인 날짜를 읽어 겹치는 작업 탐지.
    """
    violations = []

    # 공휴일 날짜 목록 로드
    holidays = (
        db.query(OperationCalendar)
        .filter(OperationCalendar.rule_code == "CAL-HOL")
        .all()
    )
    holiday_dates = {h.specific_date for h in holidays if h.specific_date is not None}

    if not holiday_dates:
        return violations

    for t in tasks:
        task_date = t.start_datetime.date()
        if task_date in holiday_dates:
            violations.append(
                {
                    "constraint_id": "6-4",
                    "task_id": t.task_id,
                    "severity": "error",
                    "detail": f"공휴일 {task_date}에 작업 배정",
                }
            )
    return violations


def _check_absence_hours(tasks, batches, equipment, config, db: Session) -> list[dict]:
    """
    6-3: 부재자 계획 — 일별 스케줄 총 시간이 가용 시간(기준 - 부재 차감)을 초과하는지 확인.
    params_json: {"absence_reduction_hours": 8, "daily_available_hours": 40}
    absence_reduction_hours: 부재로 차감되는 인시 (기본 8hr = 1인 1일)
    daily_available_hours: 정상 일별 총 가용 인시 (기본 40hr = 2교대 × 2설비 등)
    """
    violations = []
    params = config.params_json or {}
    absence_h = float(params.get("absence_reduction_hours", 0))
    base_available_h = float(params.get("daily_available_hours", 40))

    if absence_h <= 0:
        # 부재 차감이 0이면 검증 불필요
        return violations

    adjusted_available_h = base_available_h - absence_h

    # 일별 총 스케줄 시간 집계
    daily_usage: dict[date, float] = {}
    for t in tasks:
        task_date = t.start_datetime.date()
        duration_h = (t.end_datetime - t.start_datetime).total_seconds() / 3600.0
        daily_usage[task_date] = daily_usage.get(task_date, 0.0) + duration_h

    for day, used_h in daily_usage.items():
        if used_h > adjusted_available_h:
            violations.append(
                {
                    "constraint_id": "6-3",
                    "severity": "warning",
                    "detail": (
                        f"{day}: 스케줄 {used_h:.1f}h > 가용 {adjusted_available_h:.1f}h"
                        f" (부재 차감 {absence_h:.1f}h 적용)"
                    ),
                }
            )
    return violations


def _check_gc_routing(tasks, batches, equipment, config) -> list[dict]:
    """
    9-2: GC 라우팅 제외 — GC 타입 제품이 B100 절연 설비에 배정되면 에러.
    GC 타입: product_group 또는 item_code에 'GC' 포함.
    """
    violations = []
    # B100 계열 절연 설비 코드 (equipment_code가 'B100'으로 시작하는 설비)
    _B100_PREFIX = "B100"

    for t in tasks:
        eq = equipment.get(t.equipment_code)
        if not eq:
            continue
        # B100 절연 설비 여부 확인
        is_b100 = (t.equipment_code or "").upper().startswith(_B100_PREFIX)
        if not is_b100:
            continue

        batch = batches.get(t.batch_id)
        if not batch:
            continue

        # GC 타입 여부 확인 — product_group 또는 remarks에서 탐지
        is_gc = (
            "GC" in (batch.product_group or "").upper()
            or "GC" in (batch.remarks or "").upper()
        )
        if is_gc:
            violations.append(
                {
                    "constraint_id": "9-2",
                    "task_id": t.task_id,
                    "batch_id": t.batch_id,
                    "severity": "error",
                    "detail": (
                        f"GC 타입 제품(배치 {t.batch_id})이 B100 절연 설비 {t.equipment_code}에 배정 — "
                        "GC는 B100 라우팅 제외 대상"
                    ),
                }
            )
    return violations
