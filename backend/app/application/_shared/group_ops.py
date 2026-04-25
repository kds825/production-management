"""그룹 분류·SQ 추출·multi-equipment 스케줄링 헬퍼.

연선/절연/시스 등 공정 그룹의 식별 (CORE / ST / sheath), 키에서의 SQ 추출,
설비별 duration map 계산, 그리고 멀티-설비 분배 스케줄링 로직을 묶는다.

기존 위치: app.services.cp_sat_optimizer / app.services.schedule_optimizer
(Week 3 Task 3A.1 이전 분리됨). Phase 1 step 3 에서 application/_shared/ 로
이동, schedule_optimizer 셸은 본 모듈 함수들을 D7-C invariant 보호 목적으로
계속 re-export (Phase 5 §9.4 에서 셸 + retarget 동시 정리).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

from app.domain.constants import PREDECESSOR_PROCESS
from app.infrastructure.models.equipment_master import EquipmentMaster
from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.schedule_task import ScheduleTask
from app.infrastructure.calendar_engine import calculate_end_datetime

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


# ── 그룹 분류 ────────────────────────────────────────────────────────────────


def _is_core_group(group_key: str) -> bool:
    """CORE 또는 AL-CORE 그룹 키인지 판별 (CU/AL 공통)."""
    return group_key.startswith("CORE-") or group_key.startswith("AL-CORE-")


def _st_sq(group_key: str) -> int:
    """ST-{sq}-... 그룹 키에서 SQ 정수를 추출한다. 실패 시 0."""
    try:
        return int(group_key.split("-")[1])
    except (IndexError, ValueError):
        return 0


def _is_sheath_group(group_key: str, batches: list) -> bool:
    """시스 공정 그룹 판별 — batch_group prefix + 대표 배치 공정명 폴백.

    create_batches 가 할당한 A120_*/A100_* 외에도, 수동 시드로 batch_group
    이 비어 있는 시스 배치도 체인 정렬 대상에 포함시킨다.
    """
    if group_key.startswith("A120_") or group_key.startswith("A100_"):
        return True
    if batches and batches[0].process_name in ("저압시스", "고압시스"):
        return True
    return False


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


# ── duration / multi-equip 후보 판정 ─────────────────────────────────────────


def _compute_group_duration_map(
    group_batches: list[ProductionBatch],
    eligible: list[EquipmentMaster],
    speed_map: dict,
) -> dict[str, float]:
    """배치 그룹의 설비별 duration map (Round 2 HIGH #5).

    Returns:
        {equipment_code: work_duration_min} — 각 eligible 설비로 배치했을 때
        예상되는 순수 작업 시간(분, setup/drum_wind 제외).

    Why: 기존 `_compute_group_duration` 은 단일 스칼라 반환. 같은 배치를 설비
    A/B 에 할당해도 모델은 duration 이 동일하다고 가정 → solver 가 "빠른 설비
    우선" 을 인지 못 함. SpeedMaster 의 `(eq, sq)` 별 line_speed_mpm 을 활용해
    설비마다 실제 예상 duration 계산.

    Fallback 규칙:
      - 배치의 explicit estimated_duration_min (>0) 가 있으면 설비 무관 그 값
        사용 (이미 확정된 것이므로 설비 선택에 무영향).
      - SpeedMaster (eq, sq) 항목이 있으면 그 line_speed_mpm 사용.
      - 없으면 eligible 의 평균 speed 사용 (배치의 line_speed_mpm 도 고려).
      - 최종 fallback: _compute_group_duration 과 동일 평균치 (단일 스칼라).

    Note: hard-coded line_speed=10 fallback 은 신규 코드에서 제거. eligible
    설비 중 유효 speed 가 하나도 없으면 warning 용으로 모든 설비에 대해
    scalar fallback 값을 동일하게 반환한다 (solver 가 설비 구분 불가한 상태).
    """
    if not eligible:
        return {}

    rep = group_batches[0]
    sq = float(rep.sq_mm2 or 0)

    # 1) 각 설비의 line_speed 수집
    eq_speeds: dict[str, float] = {}
    for eq in eligible:
        sm = speed_map.get((eq.equipment_code, sq))
        if sm and sm.line_speed_mpm and float(sm.line_speed_mpm) > 0:
            eq_speeds[eq.equipment_code] = float(sm.line_speed_mpm)

    # 배치 자체 line_speed_mpm (배치 rep 에서 fallback)
    rep_line_speed = float(rep.line_speed_mpm or 0)

    # Fallback speed: 설비 개별 speed 못 찾은 경우 사용할 값
    fallback_speed = rep_line_speed if rep_line_speed > 0 else 0.0
    if fallback_speed <= 0 and eq_speeds:
        # eligible 중 일부만 speed 있고 나머지는 없을 때 — 평균으로 대체
        fallback_speed = sum(eq_speeds.values()) / len(eq_speeds)

    # 2) 설비별 duration 계산
    result: dict[str, float] = {}
    header = next((b for b in group_batches if b.batch_seq == -1), None)

    for eq in eligible:
        ls = eq_speeds.get(eq.equipment_code, fallback_speed)

        if header is not None:
            hd = float(header.estimated_duration_min or 0)
            if hd <= 0:
                _ls = float(header.line_speed_mpm or 0) or ls
                hd = float(header.total_length_m or 0) / _ls if _ls > 0 else 0
            result[eq.equipment_code] = hd
            continue

        total = 0.0
        for b in group_batches:
            d = float(b.estimated_duration_min or 0)
            if d <= 0:
                _ls = float(b.line_speed_mpm or 0) or ls
                d = (
                    (float(b.total_length_m or 0) + float(b.extra_length_m or 0)) / _ls
                    if _ls > 0
                    else 0
                )
            total += d
        result[eq.equipment_code] = total

    return result


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


# ── duration-component 헬퍼 ──────────────────────────────────────────────────


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


def _get_stranding_setup_min(
    prev_sq: float | None,
    curr_sq: float | None,
    sq_to_wire_d: dict,
    spec_min: float,
    compound_min: float,
) -> float:
    """연선 공정 셋업 시간 결정 (3-tier).

    동일 SQ          → 0분 (교체 없음)
    다른 SQ, 동일 소선경 → compound_min (선재교체만, 기본 120분)
    다른 SQ, 다른 소선경 → spec_min     (규격교체만, 기본 240분)
    """
    if prev_sq is None or curr_sq is None:
        return spec_min
    if prev_sq == curr_sq:
        return 0.0
    prev_wd = sq_to_wire_d.get(int(prev_sq), None)
    curr_wd = sq_to_wire_d.get(int(curr_sq), None)
    if prev_wd and curr_wd and prev_wd == curr_wd:
        return compound_min  # 동일 소선경: 선재교체만
    return spec_min  # 다른 소선경: 규격교체만


# ── 멀티설비 분배 스케줄링 ──────────────────────────────────────────────────


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
    sq_to_wire_d: dict | None = None,
) -> bool:
    """연선/고압절연 그룹의 드럼을 eligible 설비에 균등 분배하여 병렬 스케줄링.

    드럼 수를 설비 수로 나눠 각 설비에 proportional duration의 task를 생성한다.
    process_end_by_sq / process_first_output_by_sq는 가장 이른 완료 기준으로 갱신.

    - 연선: header_batch(seq=-1)의 duration 사용
    - 고압절연: header 없음 → 그룹 내 배치 duration 합산

    Returns:
        True면 분배 성공 (호출측에서 continue), False면 단일설비 경로로 폴백.
    """
    # _find_available_slot, align_start_to_predecessor_end 는 schedule_optimizer
    # 에 남아 있어 (Week 3 sub-commit D 이후 slot_filters 로 이동). re-export
    # 셸이 라우팅을 처리하므로 여기서는 schedule_optimizer 경유로 import 한다.
    # 지역 import 이유: top-level 로 두면 schedule_optimizer ↔ scheduling_shared
    # 순환 import 가 발생.
    from app.services.schedule_optimizer import (
        _find_available_slot,
        align_start_to_predecessor_end,
    )

    rep = group_batches[0]
    sq = int(rep.sq_mm2 or 0)

    # 그룹 전체 duration 계산
    # line_speed fallback: rep에 없으면 speed_map에서 설비별 기본값 조회
    rep_speed = float(rep.line_speed_mpm or 0)
    if rep_speed <= 0:
        # SpeedMaster에서 해당 설비+SQ 조합의 line_speed 조회
        for eq in eligible:
            sm = speed_map.get((eq.equipment_code, float(rep.sq_mm2 or 0)))
            if sm and sm.line_speed_mpm and float(sm.line_speed_mpm) > 0:
                rep_speed = float(sm.line_speed_mpm)
                break
    line_speed = rep_speed if rep_speed > 0 else 10  # 최종 fallback 10 mpm

    if header_batch is not None:
        # 연선: header batch(seq=-1)의 estimated_duration_min 직접 사용
        hd = float(header_batch.estimated_duration_min or 0)
        if hd <= 0:
            total_len = float(header_batch.total_length_m or 0)
            ls = float(header_batch.line_speed_mpm or 0) or line_speed
            hd = total_len / ls if ls > 0 else 60
        group_duration = hd
    else:
        # 고압절연 등 헤더 없는 공정: 각 배치 duration 합산
        group_duration = 0.0
        for b in group_batches:
            d = float(b.estimated_duration_min or 0)
            if d <= 0:
                total_len = float(b.total_length_m or 0) + float(b.extra_length_m or 0)
                ls = float(b.line_speed_mpm or 0) or line_speed
                d = total_len / ls if ls > 0 else 60
            group_duration += d
        if group_duration <= 0:
            return False  # duration 계산 불가 시 단일설비 폴백

    setup_min = float(rep.setup_time_min or 0)

    # 드럼을 설비 수로 균등 분할
    num_eq = len(eligible)
    drums_per_eq = []
    base_drums = total_drums // num_eq
    remainder = total_drums % num_eq
    for i in range(num_eq):
        drums_per_eq.append(base_drums + (1 if i < remainder else 0))

    # 선행공정 earliest 계산 — 단일설비 경로와 동일하되,
    # 혼합 SQ 그룹(고압시스_흑_적 등)은 모든 SQ의 predecessor를 확인
    earliest = base_date
    sq_int = sq

    pred_proc = PREDECESSOR_PROCESS.get(rep.process_name)
    if pred_proc:
        # 혼합 SQ 그룹: 그룹 내 모든 SQ의 predecessor 중 가장 이른 first output
        all_sqs = {int(b.sq_mm2 or 0) for b in group_batches}
        if len(all_sqs) > 1:
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
            pred_first = process_first_output_by_sq.get((pred_proc, sq_int))
            if pred_first and pred_first > earliest:
                earliest = pred_first

        # 고압시스: 절연 경화 대기 시간 20h (단일설비 경로와 동일)
        if rep.process_name == "고압시스":
            earliest += timedelta(hours=20)

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
    # 절연/시스/연합/T/P는 first-drum overlap만 사용 (단일설비 경로와 동일)
    # ST-* 연선 그룹: CORE first-drum overlap 사용 → 개별 predecessor 스킵
    is_st_group = group_key.startswith("ST-") and rep.process_name == "연선"
    skip_individual_pred = (
        rep.process_name
        in (
            "저압절연",
            "고압절연",
            "저압시스",
            "고압시스",
            "연합",
            "T/P",
        )
        or is_st_group
    )
    if not skip_individual_pred:
        for b in group_batches:
            pred_key = (b.sales_order_id, b.sales_order_line)
            pred_tid = predecessor_map.get(pred_key)
            if pred_tid:
                pred_task = next(
                    (t for t in tasks_created if t.task_id == pred_tid), None
                )
                if pred_task and pred_task.end_datetime > earliest:
                    earliest = pred_task.end_datetime

    # ── earliest 확정 후: 설비 우선순위 정렬 + 수주 납기 우선 배정 ───────────
    # 각 설비의 예상 최초 가용 시각 계산 (earliest 반영)
    one_drum_dur = (group_duration / max(total_drums, 1)) + setup_min
    machine_est_starts = []
    for eq in eligible:
        slots = timeline.get(eq.equipment_code, [])
        est_start = _find_available_slot(
            earliest, one_drum_dur, slots, db, eq.equipment_code
        )
        machine_est_starts.append((est_start, eq))
    # 가장 빨리 시작 가능한 설비 순으로 정렬
    machine_est_starts.sort(key=lambda x: x[0])
    sorted_eligible = [eq for _, eq in machine_est_starts]

    # 개별 수주(seq >= 1)를 납기 오름차순으로 정렬 → 급한 수주를 빠른 설비에 배정
    order_batches_sorted = sorted(
        [b for b in group_batches if (b.batch_seq or 0) >= 1],
        key=lambda b: (b.due_date or date.max, b.customer_priority or 99),
    )
    machine_order_assignments: list[list] = [[] for _ in range(num_eq)]
    order_cursor = 0
    for i in range(num_eq):
        drums_left = drums_per_eq[i]
        while drums_left > 0 and order_cursor < len(order_batches_sorted):
            b = order_batches_sorted[order_cursor]
            machine_order_assignments[i].append(b)
            drums_left -= int(b.drum_count or 1)
            order_cursor += 1
    # 미처리 수주는 마지막 설비에 추가
    if order_cursor < len(order_batches_sorted):
        machine_order_assignments[-1].extend(order_batches_sorted[order_cursor:])

    # 설비별 서브배치의 납기: 해당 설비에 배정된 수주 중 가장 이른 납기
    sub_due_dates: list[date | None] = [
        min((b.due_date for b in orders if b.due_date), default=None)
        for orders in machine_order_assignments
    ]

    # 각 설비에 분배 task 생성 (납기 우선 배정된 sorted_eligible 순서)
    split_tasks = []
    split_end_dts = []
    split_first_outputs = []
    split_sub_dues: list[date | None] = []

    for i, eq in enumerate(sorted_eligible):
        eq_drums = drums_per_eq[i]
        if eq_drums <= 0:
            continue

        eq_code = eq.equipment_code
        # proportional duration (배정된 드럼 수 기반)
        eq_duration = group_duration * (eq_drums / total_drums)

        drum_winding_min = _get_drum_winding_min(eq_code, rep.sq_mm2, speed_map)

        # 4-1: 연선 셋업 3-tier (동일SQ=0 / 동일소선경=선재교체 / 다른소선경=규격교체)
        prev_batch = last_batch_on_equip.get(eq_code)
        if prev_batch is not None and rep.process_name == "연선" and sq_to_wire_d:
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

        eq_total_duration = eq_duration + actual_setup + drum_winding_min

        slots = timeline.get(eq_code, [])
        slot_start = _find_available_slot(
            earliest, eq_total_duration, slots, db, eq_code
        )
        end_dt = calculate_end_datetime(slot_start, eq_total_duration, db, eq_code)

        # ── 파이프라인 유휴 최소 역산 — 서브태스크별 독립 적용 ────────────────
        # duration(= eq_total_duration)은 드럼 수 비례이므로 서브태스크마다 다름.
        # 각 서브태스크가 선행공정 종료 + 후공정 1드럼 소요 이상에서 끝나도록 개별 정렬.
        _per_drum_min = eq_duration / max(int(eq_drums or 1), 1)
        slot_start, end_dt = align_start_to_predecessor_end(
            process_name=rep.process_name,
            pred_proc=pred_proc,
            group_sqs={int(b.sq_mm2 or 0) for b in group_batches},
            process_end_by_sq=process_end_by_sq,
            current_start=slot_start,
            current_end=end_dt,
            duration_min=eq_total_duration,
            tail_offset_min=_per_drum_min,
            slots=slots,
            db=db,
            equipment_code=eq_code,
        )

        # 시간 올림 — 간트 블록은 정각 단위
        if end_dt.minute > 0 or end_dt.second > 0 or end_dt.microsecond > 0:
            end_dt = end_dt.replace(minute=0, second=0, microsecond=0) + timedelta(
                hours=1
            )

        # 체인 하이라이트 — 분할 배치에서도 동일 원칙.
        # 모든 split 서브태스크는 같은 predecessor FK 를 가짐 (상류 group 의 대표 task id).
        rep_pred_task_id = predecessor_map.get(
            (rep.sales_order_id, rep.sales_order_line)
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
            predecessor_task_id=rep_pred_task_id,
        )
        db.add(task)
        db.flush()

        timeline.setdefault(eq_code, []).append((slot_start, end_dt))
        last_batch_on_equip[eq_code] = group_batches[-1]
        split_tasks.append(task)
        split_end_dts.append(end_dt)
        split_sub_dues.append(sub_due_dates[i] if i < len(sub_due_dates) else None)

        # 첫 번째 드럼 출력 시각
        first_drum_min = actual_setup + (eq_duration / eq_drums)
        first_output_dt = calculate_end_datetime(
            slot_start, first_drum_min, db, eq_code
        )
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

    # ── 납기 위반 체크: 서브배치별 독립 검사 ─────────────────────────────────
    # 각 설비 서브배치는 자신에게 배정된 수주의 가장 이른 납기를 기준으로 위반 여부 판정
    for j, (task_j, end_j, due_j) in enumerate(
        zip(split_tasks, split_end_dts, split_sub_dues)
    ):
        if due_j and end_j.date() > due_j:
            late_days = (end_j.date() - due_j).days
            result["violations"].append(
                {
                    "batch_id": rep.batch_id,
                    "task_id": task_j.task_id,
                    "type": "delivery",
                    "severity": "warning",
                    "detail": (
                        f"[분할배치 {j + 1}/{len(split_tasks)}] 납기 {due_j} 초과 "
                        f"→ 완료 {end_j.date()} (+{late_days}일)"
                    ),
                }
            )

    return True
