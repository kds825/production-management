"""batch_grouping 패키지 — 자동 분할 후보 탐지 + 적용 (`detect_split_candidates`,
`execute_auto_splits`).

설계 의도:
- Stage 1 직후 (create_batches commit 후) 호출되는 후처리 모듈.
- 연선 헤더(batch_seq=-1) 기준으로 같은 batch_group 내 드럼 간 납기 격차/설비
  과부하를 탐지해 새 batch_group ('_B' suffix) 으로 잘라낸다.
- detect 함수는 read-only — UI/디버깅용으로도 호출되며, execute 가 실제 mutation.
"""

import math as _math
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.infrastructure.models.drum_lot_master import DrumLotMaster
from app.infrastructure.models.production_batch import ProductionBatch


def detect_split_candidates(
    run_label: str,
    db: Session,
    *,
    gap_days: int = 3,
) -> list[dict]:
    """연선 배치 그룹 중 납기 간격이 큰 그룹을 분할 후보로 반환한다.

    알고리즘:
    1. run_label 기준으로 연선 배치를 조회한다.
       - batch_seq == -1: 그룹 헤더 (drum_count, total_length_m, equipment_code 포함)
       - batch_seq >= 1:  수주별 표시 배치 (due_date 포함)
    2. 헤더의 drum_count > 1인 그룹에 대해서만 검사한다.
    3. 수주별 배치를 due_date 오름차순으로 정렬한 뒤, DrumLotMaster.lot_stranding을
       기준으로 수주들을 드럼에 탐욕적(greedy)으로 할당한다.
    4. 드럼 N의 마지막 수주와 드럼 N+1의 첫 수주의 납기 간격이 gap_days 이상이면
       해당 그룹을 분할 후보로 표시한다.
    5. 같은 설비의 모든 연선 그룹의 estimated_duration_min 합계(설비 부하)도 함께 반환한다.

    Returns:
        [
            {
                "batch_group": str,
                "equipment_code": str | None,
                "sq_mm2": float,
                "lot_count": int,
                "total_length_m": float,
                "proposed_splits": [
                    {
                        "lot_index": int,        # 1-based 드럼 번호
                        "order_count": int,
                        "total_m": float,
                        "min_due": str,
                        "max_due": str,
                    },
                    ...
                ],
                "gaps_days": [int, ...],         # splits[i]와 splits[i+1] 사이 간격
                "equipment_load_hours": float | None,
            },
            ...
        ]
    """
    # ── 연선 헤더 배치 로드 ────────────────────────────────────────────────────
    headers = (
        db.query(ProductionBatch)
        .filter(
            ProductionBatch.run_label == run_label,
            ProductionBatch.process_name == "연선",
            ProductionBatch.batch_seq == -1,
        )
        .all()
    )

    if not headers:
        return []

    # ── drum_lot_master 일괄 로드 ─────────────────────────────────────────────
    drum_lots: dict[float, DrumLotMaster] = {
        float(d.cross_section): d
        for d in db.query(DrumLotMaster).all()
        if d.cross_section is not None
    }

    # ── 수주별 연선 배치 로드 (batch_seq >= 1) ────────────────────────────────
    order_batches = (
        db.query(ProductionBatch)
        .filter(
            ProductionBatch.run_label == run_label,
            ProductionBatch.process_name == "연선",
            ProductionBatch.batch_seq >= 1,
        )
        .all()
    )
    # batch_group → 수주 배치 목록
    group_order_map: dict[str, list[ProductionBatch]] = {}
    for b in order_batches:
        group_order_map.setdefault(b.batch_group or "", []).append(b)

    # ── 설비 부하 계산 ────────────────────────────────────────────────────────
    # 헤더 배치(batch_seq=-1) estimated_duration_min 합산 — 설비 코드별
    equip_load: dict[str, float] = {}
    for h in headers:
        eq = h.equipment_code
        if eq is not None and h.estimated_duration_min is not None:
            equip_load[eq] = equip_load.get(eq, 0.0) + float(h.estimated_duration_min)

    # ── 그룹별 분할 후보 검사 ─────────────────────────────────────────────────
    candidates: list[dict] = []

    # batch_group 이름 기준으로 중복 헤더 제거 (같은 그룹에 헤더가 2개 있을 수 있음)
    seen_groups: set[str] = set()
    unique_headers = []
    for h in headers:
        bg = h.batch_group or ""
        if bg not in seen_groups:
            seen_groups.add(bg)
            unique_headers.append(h)

    for header in unique_headers:
        lot_count = int(header.drum_count or 1)
        if lot_count <= 1:
            # 단일 드럼 그룹은 분할 불필요
            continue

        bg = header.batch_group or ""
        sq = float(header.sq_mm2 or 0)
        lot_info = drum_lots.get(sq)
        lot_stranding = (
            float(lot_info.lot_stranding)
            if lot_info and lot_info.lot_stranding
            else None
        )

        if lot_stranding is None or lot_stranding <= 0:
            # lot_stranding 정보 없으면 드럼 할당 불가 — 스킵
            continue

        order_rows = group_order_map.get(bg, [])
        if not order_rows:
            continue

        # due_date 오름차순 정렬 — None은 끝으로
        order_rows_sorted = sorted(
            order_rows,
            key=lambda b: (b.due_date or date.max, b.sales_order_id or ""),
        )

        # ── 탐욕적 드럼 할당 (용량 + 납기 경계 인식) ─────────────────────────
        # 행 단위로 드럼을 채운다. 두 가지 조건으로 다음 드럼 전환:
        #   1) 용량 초과: current_fill + order > lot_stranding
        #   2) 납기 경계: 다른 수주번호로 넘어갈 때 납기 gap >= threshold이고
        #      현재 드럼이 80% 이상 찼으면 새 드럼으로 (거의 찬 드럼에
        #      여유 납기 수주를 억지로 넣지 않는다)
        _FILL_RATIO_FOR_GAP_SPLIT = 0.8
        drums: list[list[ProductionBatch]] = []
        current_drum: list[ProductionBatch] = []
        current_fill: float = 0.0

        for idx, b in enumerate(order_rows_sorted):
            order_len = float(b.total_length_m or 0)

            # 조건 1: 용량 초과
            if current_drum and current_fill + order_len > lot_stranding:
                drums.append(current_drum)
                current_drum = [b]
                current_fill = order_len
                continue

            # 조건 2: 납기 경계 + 드럼 80%+ 충전
            if (
                current_drum
                and idx > 0
                and current_fill >= lot_stranding * _FILL_RATIO_FOR_GAP_SPLIT
            ):
                prev = order_rows_sorted[idx - 1]
                if (
                    (prev.sales_order_id or "") != (b.sales_order_id or "")
                    and prev.due_date
                    and b.due_date
                    and (b.due_date - prev.due_date).days >= gap_days
                ):
                    drums.append(current_drum)
                    current_drum = [b]
                    current_fill = order_len
                    continue

            current_drum.append(b)
            current_fill += order_len

        if current_drum:
            drums.append(current_drum)

        if len(drums) <= 1:
            # 실제로 드럼이 1개로 수렴하면 분할 불필요
            continue

        # ── Overload 사전 판정 (2026-04-18 추가, 2026-04-18 개선) ────────────
        # 헤더의 duration vs 납기 가용시간 비교 — 드럼 간 gap 무관하게 작동해야
        # 하므로 merge/gap filter 이전에 판정. PDF "1안 수정 5틀→3+2" 패턴.
        #
        # 가용시간 계산: calendar_engine 의 _PROCESS_HOURS 를 설비 카테고리별로
        # 누적. Mon-Thu 22h + Fri 14h + 토일 0h 의 정확한 weekday 가중치 적용.
        # 이전 근사값 (days × 20h) 은 주말 포함 calendar day 에 20h 를 곱해
        # 주말 있는 구간에서 40h 이상 과대 추정되던 bias 수정.
        # drum_count >= 3 에만 적용 (단일/2틀 분할 의미 없음).
        is_overload = False
        overload_reason = ""
        if header.due_date and header.estimated_duration_min and lot_count >= 3:
            from app.services.calendar_engine import (
                _PROCESS_HOURS as _CAL_HOURS,
                _get_category as _cal_cat,
            )

            _cat = _cal_cat(header.equipment_code)
            _hours_tbl = _CAL_HOURS.get(_cat, _CAL_HOURS["default"])
            _available_hr = 0.0
            _day = date.today()
            _due = header.due_date
            if _due > _day:
                while _day < _due:
                    _available_hr += _hours_tbl[_day.weekday()]
                    _day += timedelta(days=1)
                _required_hr = float(header.estimated_duration_min) / 60.0
                if _required_hr > _available_hr and _available_hr > 0:
                    is_overload = True
                    _days_cal = (header.due_date - date.today()).days
                    overload_reason = (
                        f"설비 과부하 — {header.equipment_code or _cat} 기준 "
                        f"납기 {header.due_date} 까지 {_days_cal}일 "
                        f"(실가동 {_available_hr:.0f}h) < 배치 요구 "
                        f"{_required_hr:.0f}h"
                    )

        # ── 드럼 간 납기 간격 계산 ───────────────────────────────────────────
        gaps: list[int] = []
        for i in range(len(drums) - 1):
            last_due_in_drum = max(
                (b.due_date for b in drums[i] if b.due_date), default=None
            )
            first_due_next_drum = min(
                (b.due_date for b in drums[i + 1] if b.due_date), default=None
            )
            if last_due_in_drum and first_due_next_drum:
                gap = (first_due_next_drum - last_due_in_drum).days
            else:
                gap = 0
            gaps.append(gap)

        max_gap = max(gaps) if gaps else 0
        # overload 은 납기 gap 무관하게 분할 대상. 아니면 기존 임계치(gap_days).
        if not is_overload and max_gap < gap_days:
            continue

        if not is_overload:
            # ── gap=0 연속 드럼을 하나의 청크로 병합 (기존 로직) ─────────────
            # overload 케이스는 개별 드럼 유지 (ceil(N/2) 분할 지점을 정확히
            # 잡기 위함).
            merged_chunks: list[list[ProductionBatch]] = [drums[0]]
            merged_gaps: list[int] = []
            for i, gap_val in enumerate(gaps):
                if gap_val == 0:
                    merged_chunks[-1].extend(drums[i + 1])
                else:
                    merged_gaps.append(gap_val)
                    merged_chunks.append(drums[i + 1])
            gaps = merged_gaps
            drums = merged_chunks

            if len(drums) <= 1:
                continue

        # ── 분할 제안 구성 ────────────────────────────────────────────────────
        today = date.today()
        proposed_splits = []
        drum_details = []
        has_urgent_in_later_drum = False

        for i, drum in enumerate(drums, start=1):
            dues = [b.due_date for b in drum if b.due_date]
            batch_ids = [b.batch_id for b in drum if b.batch_id]
            min_priority = min((b.customer_priority or 99 for b in drum), default=99)
            earliest_due = min(dues) if dues else None
            days_until = (earliest_due - today).days if earliest_due else 999
            is_urgent = min_priority <= 7 or (
                earliest_due is not None and days_until <= 7
            )
            if i >= 2 and is_urgent:
                has_urgent_in_later_drum = True

            proposed_splits.append(
                {
                    "lot_index": i,
                    "order_count": len(drum),
                    "total_m": round(
                        sum(float(b.total_length_m or 0) for b in drum), 1
                    ),
                    "min_due": str(min(dues)) if dues else None,
                    "max_due": str(max(dues)) if dues else None,
                    "order_ids": sorted(
                        set(b.sales_order_id for b in drum if b.sales_order_id)
                    ),
                    "batch_ids": batch_ids,
                    "has_urgent": is_urgent,
                    "min_priority": min_priority,
                    "days_until_due": days_until,
                }
            )
            drum_details.append(
                {
                    "lot_index": i,
                    "order_count": len(drum),
                    "min_priority": min_priority,
                    "earliest_due": str(earliest_due) if earliest_due else None,
                    "days_until_due": days_until,
                    "has_urgent": is_urgent,
                }
            )

        auto_split_recommended = has_urgent_in_later_drum or is_overload
        urgency_reason_parts: list[str] = []
        if has_urgent_in_later_drum:
            urgency_reason_parts.append(
                "후순위 드럼에 긴급/납기임박 수주 포함 (우선순위≤7 또는 납기7일 이내)"
            )
        if is_overload:
            urgency_reason_parts.append(overload_reason)
        urgency_reason = " / ".join(urgency_reason_parts)

        eq_code = header.equipment_code
        load_hours = (
            round(equip_load[eq_code] / 60.0, 2)
            if eq_code and eq_code in equip_load
            else None
        )

        candidates.append(
            {
                "batch_group": bg,
                "equipment_code": eq_code,
                "sq_mm2": sq,
                "lot_count": lot_count,
                "total_length_m": float(header.total_length_m or 0),
                "proposed_splits": proposed_splits,
                "gaps_days": gaps,
                "equipment_load_hours": load_hours,
                "drum_details": drum_details,
                "auto_split_recommended": auto_split_recommended,
                "urgency_reason": urgency_reason,
                "is_overload": is_overload,
                "has_urgent_in_later_drum": has_urgent_in_later_drum,
            }
        )

    return candidates


# ── 자동 분할 ──────────────────────────────────────────────────────────────────


def _apply_auto_split(
    batch_group: str,
    split_batch_ids: list[int],
    suffix: str,
    db: Session,
) -> dict:
    """배치 그룹을 2개로 분할한다 (WIP 재매칭 없이 단순 이동).

    split_batch_ids의 배치들을 '{batch_group}_{suffix}' 신규 그룹으로 이동하고,
    헤더 배치(batch_seq=-1)를 비율로 분할한다.
    Stage 1 직후 스케줄링 전에 호출되므로 schedule_task 정리는 불필요.
    """
    if not split_batch_ids:
        return {"skipped": True, "reason": "split_batch_ids 없음"}

    batch_id_set = set(split_batch_ids)
    new_group = f"{batch_group}_{suffix}"

    header = (
        db.query(ProductionBatch)
        .filter(
            ProductionBatch.batch_group == batch_group,
            ProductionBatch.batch_seq == -1,
        )
        .first()
    )
    if not header:
        return {"skipped": True, "reason": "헤더 없음"}

    all_individual = (
        db.query(ProductionBatch)
        .filter(
            ProductionBatch.batch_group == batch_group,
            ProductionBatch.batch_seq >= 1,
        )
        .all()
    )
    split_off = [b for b in all_individual if b.batch_id in batch_id_set]
    remaining = [b for b in all_individual if b.batch_id not in batch_id_set]

    if not split_off:
        return {"skipped": True, "reason": "이동 대상 배치 없음"}

    orig_dur = float(header.estimated_duration_min or 0)
    orig_len = float(header.total_length_m or 0)
    lot_size = float(header.drum_length_m or 0)
    core_mul = int(header.core_count or 1)

    def _calc_lots_len(batches: list) -> tuple[int, float]:
        net = sum(float(b.total_length_m or 0) for b in batches) * core_mul
        if lot_size > 0 and net > 0:
            lots = _math.ceil(net / lot_size)
            return lots, lots * lot_size
        elif net > 0:
            return 1, net
        return 0, 0.0

    split_lots, split_len = _calc_lots_len(split_off)
    remain_lots, remain_len = _calc_lots_len(remaining)

    split_len_raw = sum(float(b.total_length_m or 0) for b in split_off)
    remain_len_raw = sum(float(b.total_length_m or 0) for b in remaining)

    # Eng review 블로커 #1 (Task 5): split 후 각 헤더의 surplus 를 재계산.
    # 원 헤더 surplus 를 proportional 로 복사하면 틀 단위 올림(ceil) 때문에 오차 발생.
    # split_len_raw * core_mul = 분할 그룹의 실제 수주 환산량(net) — defect_buffer 는
    # 개별 배치 total_length_m 에 반영돼 있으면 그대로, 없으면 0 으로 처리됨.
    split_surplus = max(0.0, split_len - split_len_raw * core_mul)
    remain_surplus = max(0.0, remain_len - remain_len_raw * core_mul)

    total_work = split_len + remain_len
    split_dur = orig_dur * split_len / total_work if total_work > 0 else 0
    remain_dur = orig_dur * remain_len / total_work if total_work > 0 else 0

    split_due = min(
        (b.due_date for b in split_off if b.due_date), default=header.due_date
    )
    remain_due = min(
        (b.due_date for b in remaining if b.due_date), default=header.due_date
    )
    split_pri = min((b.customer_priority or 99 for b in split_off), default=99)
    remain_pri = min((b.customer_priority or 99 for b in remaining), default=99)

    # 신규 그룹 헤더 생성
    new_header = ProductionBatch(
        run_label=header.run_label,
        sales_order_id=split_off[0].sales_order_id
        if split_off
        else header.sales_order_id,
        sales_order_line=split_off[0].sales_order_line
        if split_off
        else header.sales_order_line,
        item_code=header.item_code,
        routing_code=header.routing_code,
        process_name=header.process_name,
        batch_seq=-1,
        drum_count=split_lots,
        drum_length_m=header.drum_length_m,
        total_length_m=split_len,
        extra_length_m=0,
        sq_mm2=header.sq_mm2,
        core_count=header.core_count,
        core_colors=header.core_colors,
        sheath_color=header.sheath_color,
        customer_name=split_off[0].customer_name if split_off else header.customer_name,
        due_date=split_due,
        customer_priority=split_pri,
        line_speed_mpm=header.line_speed_mpm,
        setup_time_min=header.setup_time_min,
        estimated_duration_min=split_dur,
        status="planned",
        product_group=header.product_group,
        voltage=header.voltage,
        conductor_material=header.conductor_material,
        stranding_type=header.stranding_type,
        batch_group=new_group,
        spec_raw=header.spec_raw,
        wip_output_expected_m=split_surplus,  # Task 5: split 후 surplus 재계산
        remarks=(
            f"연선그룹 {len(split_off)}건 {split_lots}틀 / "
            f"수주총량 {split_len_raw:.0f}m → 연선작업량 {split_len:.0f}m"
            f" (자동분할 {suffix}, 틀단위 {lot_size:.0f}m)"
        ),
    )
    db.add(new_header)

    # 원본 헤더 업데이트
    header.drum_count = remain_lots
    header.total_length_m = remain_len
    header.estimated_duration_min = remain_dur
    header.due_date = remain_due
    header.customer_priority = remain_pri
    header.wip_output_expected_m = remain_surplus  # Task 5: split 후 surplus 재계산
    header.remarks = (
        f"연선그룹 {len(remaining)}건 {remain_lots}틀 / "
        f"수주총량 {remain_len_raw:.0f}m → 연선작업량 {remain_len:.0f}m"
        f" (자동분할 잔여, 틀단위 {lot_size:.0f}m)"
    )

    # 개별 배치들 그룹 이동
    db.query(ProductionBatch).filter(
        ProductionBatch.batch_id.in_(split_batch_ids),
        ProductionBatch.batch_group == batch_group,
    ).update({ProductionBatch.batch_group: new_group}, synchronize_session=False)

    return {
        "original_group": batch_group,
        "new_group": new_group,
        "moved_count": len(split_off),
    }


def execute_auto_splits(
    run_label: str,
    db: Session,
    *,
    gap_days: int = 3,
    urgency_priority_threshold: int = 7,
    urgency_days_threshold: int = 7,
) -> dict:
    """납기 긴급 수주가 후순위 드럼에 포함된 그룹을 자동으로 분할한다.

    detect_split_candidates 결과 중 auto_split_recommended=True인 항목에 대해
    proposed_splits[1:] 의 batch_ids를 '{batch_group}_B' 신규 그룹으로 분리한다.
    Stage 1의 create_batches + db.commit() 직후에 호출한다.

    Returns:
        {"auto_split_count": int, "splits": [{"original_group": ..., "new_group": ...}, ...]}
    """
    candidates = detect_split_candidates(
        run_label,
        db,
        gap_days=gap_days,
    )

    results = []
    for c in candidates:
        if not c.get("auto_split_recommended"):
            continue
        proposed = c.get("proposed_splits", [])
        if len(proposed) < 2:
            continue

        # Split boundary:
        # - is_overload: ceil(N/2) 지점으로 균형 분할 (PDF "5틀→3+2" 패턴).
        #   먼저 처리되어야 할 초기 드럼이 더 많도록 앞쪽에 우선 배치.
        # - 단순 긴급(has_urgent_in_later_drum only): 기존 동작 유지,
        #   proposed_splits[1:] 전부를 뒤로 이동 (앞 드럼만 단독 보존).
        N = len(proposed)
        if c.get("is_overload"):
            split_idx = (N + 1) // 2  # 3→2, 4→2, 5→3
        else:
            split_idx = 1

        split_ids: list[int] = []
        for chunk in proposed[split_idx:]:
            split_ids.extend(chunk.get("batch_ids") or [])

        if not split_ids:
            continue

        result = _apply_auto_split(
            batch_group=c["batch_group"],
            split_batch_ids=split_ids,
            suffix="B",
            db=db,
        )
        if not result.get("skipped"):
            results.append(result)

    if results:
        db.commit()

    return {
        "auto_split_count": len(results),
        "splits": results,
    }
