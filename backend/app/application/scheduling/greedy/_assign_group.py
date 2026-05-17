"""optimization_loop 의 _assign_group — 그리디 슬롯 할당 본체 (~569줄).

Task 2.13 추출 (B-7.2): 단일 batch_group 을 적합한 설비에 배치하고
state / group_ctx / db / result 를 mutate 하는 그리디 핵심 함수.
timeline / equipment_state / 색상 클러스터 등 누적 state 가 있어 가장 큰
함수. mechanical cut/paste — 한 줄도 변경 없이 정확 보존.

`_get_sheath_type` 은 optimization_loop 에 정의되어 있으나, audit log 의
`10-3 시스 재질 라우팅` detail 한 곳에서만 사용. 순환 import 회피 위해
함수 내부에서 lazy import (slot_filters 와 동일 패턴).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.domain.constants import PREDECESSOR_PROCESS
from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.schedule_task import ScheduleTask
from app.infrastructure.models.speed_master import SpeedMaster
from app.application._shared.audit_logger import log_decision
from app.infrastructure.calendar_engine import calculate_end_datetime
from app.domain.constraint_rules import resolve_color_change_min
from app.application.scheduling.greedy.slot_finder import _find_available_slot
from app.application._shared.group_ops import (
    _extract_core_main_sq,
    _get_drum_winding_min,
    _get_stranding_setup_min,
    _is_core_group,
    _schedule_multi_equipment,
)
from app.application._shared.slot_filters import (
    _filter_by_sheath_routing,
    _find_eligible_equipment,
    _narrow_by_stranding,
    align_start_to_predecessor_end,
)
from app.application.scheduling.greedy.scheduler_state import SchedulerState
from app.application.scheduling.greedy._group_and_sort import (
    GroupingContext,
    _group_earliest_due,
)


def _assign_group(
    *,
    group_key: str,
    group_batches: list[ProductionBatch],
    state: SchedulerState,
    group_ctx: GroupingContext,
    base_date: datetime,
    run_label: str,
    db: Session,
    result: dict,
    last_batch_for_audit: "ProductionBatch | None",
) -> None:
    """단일 batch_group 을 적합한 설비에 배치하고 state / group_ctx / db / result mutate.

    처리 단계:
      1. 후보 설비 필터링 (process / 시스 라우팅 / A100/A120 색상 / 61연선
         T6BO·54BO / TP-2 / 소선경 grouping)
      2. 멀티설비 분배 (드럼 ≥ 2 + 적격 ≥ 2: 연선 CORE 제외 / 고압절연 / 고압시스)
      3. 그룹 duration 계산 (header batch 또는 per-batch 합산)
      4. 설비 선택 (eligible 별 setup / color change / predecessor /
         first-drum overlap / 시스 묶음 append 정책 → _find_available_slot)
      5. align_start_to_predecessor_end 로 파이프라인 유휴 최소화 + 정각 올림
      6. ScheduleTask 생성 + state 갱신
      7. 납기 위반 검증 + audit log

    부작용:
      - state.X / group_ctx.prev_cluster_on_eq / db (add+flush) / result mutate
      - 적합 설비 없음 / 가용 슬롯 없음 시 warning 후 early return

    Phase 4 step 2d 추출 단위 — 추출 전 outer for-loop body 549 LOC 통째 이동.
    """
    # Lazy import — `_get_sheath_type` 은 optimization_loop 에 정의되어 있고
    # 본 모듈은 optimization_loop 에서 import 되므로 순환 회피용 lazy import.
    from app.application.scheduling.greedy.optimization_loop import _get_sheath_type

    rep = group_batches[0]  # 대표 배치 (설비 선정용)

    # 10-3: 시스 재질 라우팅
    candidate_equip = state.equipment_by_process.get(rep.process_name, [])
    if rep.process_name in ("고압시스", "저압시스"):
        candidate_equip = _filter_by_sheath_routing(rep, candidate_equip)

    # 저압시스 A100/A120 색상별 설비 강제 라우팅
    # A120 배치(흑/청) → SH-A120 전용, A100 배치(갈/회/녹황) → SH-A100 전용
    if group_key.startswith("A120_"):
        candidate_equip = [e for e in candidate_equip if e.equipment_code == "SH-A120"]
    elif group_key.startswith("A100_"):
        candidate_equip = [e for e in candidate_equip if e.equipment_code == "SH-A100"]

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
        and sq_key in state.sq_to_equip
        and not _is_core_group(group_key)
    ):
        preferred_eq = state.sq_to_equip[sq_key]
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
    if (
        is_stranding
        and sq_key not in state.sq_to_equip
        and not _is_core_group(group_key)
    ):
        wire_d = group_ctx.sq_to_wire_d.get(sq, 0.0)
        if wire_d > 0:
            same_wd_equips = set()
            for (proc, s), eq_code in state.sq_to_equip.items():
                if proc == "연선" and group_ctx.sq_to_wire_d.get(s, -1.0) == wire_d:
                    same_wd_equips.add(eq_code)
            if same_wd_equips:
                wd_match = [e for e in eligible if e.equipment_code in same_wd_equips]
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
        state.process_end_by_sq[(rep.process_name, sq_int)] = datetime.max
        state.process_first_output_by_sq[(rep.process_name, sq_int)] = datetime.max
        return

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
            and sq_key not in state.sq_to_equip
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
            speed_map=state.speed_map,
            timeline=state.timeline,
            last_batch_on_equip=state.last_batch_on_equip,
            sq_to_equip=state.sq_to_equip,
            predecessor_map=state.predecessor_map,
            process_end_by_sq=state.process_end_by_sq,
            process_first_output_by_sq=state.process_first_output_by_sq,
            core_first_drum_by_main_sq=state.core_first_drum_by_main_sq,
            tasks_created=state.tasks_created,
            result=result,
            welding_min=state.welding_min,
            sq_to_wire_d=group_ctx.sq_to_wire_d,
        )
        if split_ok:
            return

    # ── 그룹 전체 duration 계산 ──────────────────────────────────────────
    # batch_seq=-1 헤더 배치가 있으면 그 estimated_duration_min을 직접 사용.
    # (연선 그룹: 실제 작업량 work_qty_g / 선속 — 수주 건수와 무관)
    # 헤더 없으면 기존 방식으로 각 배치 duration 합산.
    rep_speed = float(rep.line_speed_mpm or 0)
    if rep_speed <= 0:
        # SpeedMaster에서 해당 설비+SQ 조합의 line_speed 조회
        for eq in eligible:
            sm = state.speed_map.get((eq.equipment_code, float(rep.sq_mm2 or 0)))
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
        eligible[0].equipment_code, rep.sq_mm2, state.speed_map
    )
    total_duration = group_duration + setup_min + drum_winding_min

    # ── 설비 선택 (최적 슬롯 탐색) ───────────────────────────────────────
    best_eq = None
    best_start = None
    best_total_duration = total_duration
    pred_proc = PREDECESSOR_PROCESS.get(rep.process_name)

    for eq in eligible:
        eq_code = eq.equipment_code
        slots = state.timeline.get(eq_code, [])

        eq_total_duration = total_duration

        # 4-1: 연선 셋업 3-tier (동일SQ=0 / 동일소선경=선재교체 / 다른소선경=규격교체)
        prev_batch = state.last_batch_on_equip.get(eq_code)
        if prev_batch is not None and rep.process_name == "연선":
            compound_min = float(
                state.speed_map.get((eq_code, float(rep.sq_mm2 or 0)), None)
                and state.speed_map[
                    (eq_code, float(rep.sq_mm2 or 0))
                ].setup_compound_min
                or 0
            )
            actual_setup = _get_stranding_setup_min(
                float(prev_batch.sq_mm2) if prev_batch.sq_mm2 else None,
                float(rep.sq_mm2) if rep.sq_mm2 else None,
                group_ctx.sq_to_wire_d,
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
                    params=state.constraint_params,
                )
        eq_total_duration += color_change_min

        # ── 선행공정(predecessor) — 공정 순서에 따라 앞 공정 종료 후 시작
        earliest = base_date
        sq_int = int(rep.sq_mm2 or 0)

        # 파이프라인 겹침: 앞 공정에서 첫 번째 드럼이 나오면 후공정 시작 가능
        # 연선 1틀 완료 → 절연 시작 / 절연 1틀 완료 → 시스 시작
        if pred_proc:
            all_sqs = {int(b.sq_mm2 or 0) for b in group_batches}
            if len(all_sqs) > 1:
                # 색상 기준 혼합 SQ 그룹(시스): 어느 SQ든 첫 드럼이 나오면 시작 가능
                # → 그룹 내 SQ 중 가장 이른 첫 출력 시각을 선행 제약으로 사용
                valid_firsts = [
                    t
                    for sq_i in all_sqs
                    if (t := state.process_first_output_by_sq.get((pred_proc, sq_i)))
                    and t < datetime.max
                ]
                if valid_firsts:
                    pred_min = min(valid_firsts)
                    if pred_min > earliest:
                        earliest = pred_min
            else:
                # 단일 SQ 그룹: 해당 SQ의 선행 제약만 확인
                sq_i = next(iter(all_sqs))
                pred_first = state.process_first_output_by_sq.get((pred_proc, sq_i))
                if pred_first and pred_first > earliest:
                    earliest = pred_first
            if rep.process_name == "고압시스":
                earliest += timedelta(hours=20)

        # 시스 배치(A100/A120): 저압절연 첫 번째 드럼 출력 후 시작
        if group_key.startswith("A100_") or group_key.startswith("A120_"):
            if state.first_insul_output and state.first_insul_output > earliest:
                earliest = state.first_insul_output

        # 61연선 ST- 그룹: 동일 SQ의 CORE/AL-CORE 첫 드럼 출력 후 시작 (overlap)
        # 예: "ST-633-..." 그룹 → core_first_drum_by_main_sq[633] 이후 시작
        if group_key.startswith("ST-") and rep.process_name == "연선":
            try:
                main_sq = int(group_key.split("-")[1])
            except (IndexError, ValueError):
                main_sq = sq_int
            core_first = state.core_first_drum_by_main_sq.get(main_sq)
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
                pred_tid = state.predecessor_map.get(pred_key)
                if pred_tid:
                    pred_task = next(
                        (t for t in state.tasks_created if t.task_id == pred_tid),
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
            current_cluster_id = group_ctx.gk_to_cluster_id.get(group_key)
            prev_cluster = group_ctx.prev_cluster_on_eq.get(eq_code)
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
            eq_earliest,
            eq_total_duration,
            slots,
            db,
            eq.equipment_code,
            calendar_end_cache=state.calendar_end_cache,
        )

        if best_start is None or slot_start < best_start:
            best_eq = eq
            best_start = slot_start
            best_total_duration = eq_total_duration

    if best_eq is None or best_start is None:
        result["warnings"].append(f"배치그룹 {group_key}: 가용 슬롯 없음")
        return

    end_dt = calculate_end_datetime(
        best_start, best_total_duration, db, best_eq.equipment_code
    )

    # ── 파이프라인 유휴 최소 역산 공식 ────────────────────────────────────
    # 물리: 후공정은 선행 마지막 드럼이 나와야 자기 마지막 드럼을 처리 가능.
    # T_succ_end = T_pred_end + D_succ_per_drum
    # T_succ_start = T_succ_end - D_succ_total (블록 폭 유지)
    _per_drum_min = (
        group_duration / max(int(total_drums or 1), 1) if group_duration > 0 else 0.0
    )
    best_start, end_dt = align_start_to_predecessor_end(
        process_name=rep.process_name,
        pred_proc=pred_proc,
        group_sqs={int(b.sq_mm2 or 0) for b in group_batches},
        process_end_by_sq=state.process_end_by_sq,
        current_start=best_start,
        current_end=end_dt,
        duration_min=best_total_duration,
        tail_offset_min=_per_drum_min,
        slots=state.timeline.get(best_eq.equipment_code, []),
        db=db,
        equipment_code=best_eq.equipment_code,
    )

    # ── 시간 올림 — 간트 블록은 정각 단위로 표시 ────────────────────────
    if end_dt.minute > 0 or end_dt.second > 0 or end_dt.microsecond > 0:
        end_dt = end_dt.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)

    # ── 그룹당 1 schedule_task 생성 ──────────────────────────────────────
    # 체인 하이라이트 — 본 그룹의 상류 task id 를 predecessor 로 고정.
    # 같은 group 내 복수 order 가 있어도 대표 order 의 predecessor 로 일관 처리.
    rep_pred_task_id = state.predecessor_map.get(
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

    state.timeline.setdefault(best_eq.equipment_code, []).append((best_start, end_dt))

    # 공정+SQ별 종료 시각 갱신 (후공정 선행관계 추적)
    sq_int = int(rep.sq_mm2 or 0)
    proc_sq_key = (rep.process_name, sq_int)
    if (
        proc_sq_key not in state.process_end_by_sq
        or end_dt > state.process_end_by_sq[proc_sq_key]
    ):
        state.process_end_by_sq[proc_sq_key] = end_dt

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
        proc_sq_key not in state.process_first_output_by_sq
        or first_output_dt < state.process_first_output_by_sq[proc_sq_key]
    ):
        state.process_first_output_by_sq[proc_sq_key] = first_output_dt

    # 61연선 CORE-/AL-CORE- 그룹 첫 드럼 출력 시각 기록 — pipeline overlap
    # CU: "CORE-{main_sq}-...", AL: "AL-CORE-{main_sq}-..." 패턴
    if _is_core_group(group_key):
        main_sq = _extract_core_main_sq(group_key)
        if main_sq is not None:
            if (
                main_sq not in state.core_first_drum_by_main_sq
                or first_output_dt < state.core_first_drum_by_main_sq[main_sq]
            ):
                state.core_first_drum_by_main_sq[main_sq] = first_output_dt

    # 저압절연 첫 번째 드럼 출력 시각 — A100/A120 시스 그룹 시작 기준
    if rep.process_name == "저압절연":
        if (
            state.first_insul_output is None
            or first_output_dt < state.first_insul_output
        ):
            state.first_insul_output = first_output_dt

    # 그룹 내 모든 배치의 predecessor + status 갱신
    for b in group_batches:
        pred_key = (b.sales_order_id, b.sales_order_line)
        state.predecessor_map[pred_key] = task.task_id
        b.equipment_code = best_eq.equipment_code
        b.status = "scheduled"

    # 규칙 2: SQ→설비 매핑 기록 (CORE/AL-CORE 그룹 제외 — 코어는 ST설비 고정 대상 아님)
    if rep.process_name == "연선" and not _is_core_group(group_key):
        state.sq_to_equip[sq_key] = best_eq.equipment_code

    # 용접 시간 추적 (4-4): 설비별 마지막 배치 갱신 (그룹의 마지막 배치)
    state.last_batch_on_equip[best_eq.equipment_code] = group_batches[-1]

    # 시스 묶음 기반 append 정책 — 현재 그룹의 cluster_id 로 갱신
    if rep.process_name in ("저압시스", "고압시스"):
        _cid_g = group_ctx.gk_to_cluster_id.get(group_key)
        if _cid_g:
            group_ctx.prev_cluster_on_eq[best_eq.equipment_code] = _cid_g

    state.tasks_created.append(task)

    # Check delivery date violation — 그룹 내 가장 빠른 납기 기준
    # 납기는 사용자 요구 상 하드 제약 → severity=error 로 상향
    # (validate_all 이 이를 보고 재시도/알림을 유발하도록)
    earliest_due = min((b.due_date for b in group_batches if b.due_date), default=None)
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

    # Audit log — last_batch_for_audit 는 outer scope 의 batches[-1] (기존 outer
    # for-loop variable 누설 동치 행동 보존 — Phase 4 step 2c 참조)
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
                "detail": f"welding={state.welding_min:.0f}분 (스플라이스 로트 시 적용)",
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
            f"SQ={last_batch_for_audit.sq_mm2 if last_batch_for_audit else None}, "
            f"납기={last_batch_for_audit.due_date if last_batch_for_audit else None}, "
            f"소요={best_total_duration:.0f}분"
        ),
    )

    result["total_tasks"] += 1
