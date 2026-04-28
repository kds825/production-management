"""자재 체커 — material_separation / defect_buffer / availability / lead_time.

constraint_checker.py 분할 (Task 1.6, B-4.2).
"""

from datetime import date, timedelta


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
