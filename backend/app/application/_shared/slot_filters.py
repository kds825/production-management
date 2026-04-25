"""설비/슬롯 후보 필터링 헬퍼.

배치 → eligible 설비 추출, 연선 케이스 좁히기, 시스 재질 라우팅,
선행공정 종료 기준 정렬 등 슬롯 결정에 직접 영향을 주는 함수들을 묶는다.

기존 위치: app.services.schedule_optimizer (Week 3 Task 3A.1 이전 분리됨,
Phase 1 step 3 에서 application/_shared/ 로 이동). schedule_optimizer 셸은
본 모듈 함수들을 D7-C invariant 보호 목적으로 계속 re-export.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from app.infrastructure.models.equipment_master import EquipmentMaster
from app.infrastructure.models.production_batch import ProductionBatch

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _find_eligible_equipment(
    batch: ProductionBatch, equipment: list[EquipmentMaster]
) -> list[EquipmentMaster]:
    """배치에 적합한 설비 필터링 (재질, SQ범위, 색상그룹)"""
    eligible = []
    for eq in equipment:
        # ── Material filter ────────────────────────────────────────────────
        if eq.material_limit and eq.material_limit != "ALL":
            if (
                batch.conductor_material
                and batch.conductor_material != eq.material_limit
            ):
                continue

        # ── Range filter ───────────────────────────────────────────────────
        if eq.range_unit == "mm":
            pass
        elif eq.range_unit == "Ø":
            # 연합 설비: SQ 기준으로 직접 분류 (小4BO: ~25SQ, 4BO: 35SQ~)
            sq = float(batch.sq_mm2) if batch.sq_mm2 else None
            if sq and eq.range_min and sq < float(eq.range_min):
                continue
            if sq and eq.range_max and sq > float(eq.range_max):
                continue
        else:
            sq = float(batch.sq_mm2) if batch.sq_mm2 else None
            if sq and eq.range_min and sq < float(eq.range_min):
                continue
            if sq and eq.range_max and sq > float(eq.range_max):
                continue

        # ── Color group filter (저압시스) ──────────────────────────────────
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

        eligible.append(eq)

    return eligible


def _narrow_by_stranding(
    batch: ProductionBatch, eligible: list[EquipmentMaster]
) -> list[EquipmentMaster]:
    """61연선 케이스 설비 선호도 좁히기 (fallback 있음).

    stranding_method 컬럼 또는 equipment_code 명칭으로 선호 설비를 추립니다.
    매칭 설비가 없으면 eligible 전체를 그대로 반환하여 스케줄링 skip을 방지합니다.
    """
    batch_st = (batch.stranding_type or "").strip()
    sq_val = float(batch.sq_mm2 or 0)

    if batch_st == "7연선코어":
        mat = (batch.conductor_material or "").upper()
        if mat == "AL":
            # AL 7연선코어 → AL6BO 선호 (material_limit="AL", stranding_method="7연선")
            preferred = [
                e
                for e in eligible
                if "AL6B" in (e.equipment_code or "").upper()
                or (
                    (e.stranding_method or "").strip() == "7연선"
                    and (e.material_limit or "").upper() == "AL"
                )
            ]
            return preferred if preferred else eligible
        # CU 7연선코어 → T6BO 선호
        preferred = [
            e
            for e in eligible
            if (e.stranding_method or "").strip() == "7연선"
            or "T6B" in (e.equipment_code or "").upper()
        ]
        return preferred if preferred else eligible

    if sq_val >= 300:
        # 54BO 선호: stranding_method="61연선" 또는 코드에 "54BO" 포함
        preferred = [
            e
            for e in eligible
            if (e.stranding_method or "").strip() == "61연선"
            or "54BO" in (e.equipment_code or "").upper()
        ]
        return preferred if preferred else eligible

    # 일반 연선: 7연선/61연선 전용 설비는 제외 (stranding_method 기준, 없으면 무시)
    excluded = [
        e for e in eligible if (e.stranding_method or "").strip() in ("7연선", "61연선")
    ]
    if len(excluded) < len(eligible):
        return [e for e in eligible if e not in excluded]
    return eligible


def align_start_to_predecessor_end(
    *,
    process_name: str,
    pred_proc: str | None,
    group_sqs: set[int],
    process_end_by_sq: dict[tuple[str, int], datetime],
    current_start: datetime,
    current_end: datetime,
    duration_min: float,
    tail_offset_min: float = 0.0,
    slots: list,
    db: "Session",
    equipment_code: str,
) -> tuple[datetime, datetime]:
    """후공정 종료가 선행공정 종료 + 후공정 1드럼 소요시간 이상이 되도록 시작을 지연한다.

    물리적 의미: 후공정은 선행공정 마지막 드럼이 나온 뒤에야 자기 마지막 드럼을 처리.
    → T_succ_end = T_pred_end + tail_offset_min (후공정 1드럼 wall-clock duration)

    불변식:
      - aligned_end >= T_target (T_target = calculate_end_datetime(pred_end, tail_offset_min))
      - aligned_end - aligned_start == duration_min (블록 폭 유지, 캘린더 보정 오차 허용)

    tail_offset_min 은 호출자가 `후공정 group_duration / drum_count` 로 산정한 per-drum 소요.
    시스 공정(저압시스/고압시스)은 pred_proc 외에 "연합" 종료도 함께 고려.
    pred_proc 가 None 이거나 process_end_by_sq 에 기록이 없으면 입력 그대로 반환.

    반환: (aligned_start, aligned_end)
    """
    # 지역 import — schedule_optimizer 모듈 namespace 의 이름을 그대로 사용해야
    # 테스트가 monkeypatch.setattr(schedule_optimizer, ...) 로 주입한 mock 이
    # 실시간으로 적용된다. calculate_start_datetime / calculate_end_datetime 은
    # calendar_engine 원본을 다시 import 하지 않고 schedule_optimizer 경유로
    # 가져와 patched 바인딩을 읽도록 한다 (Week 3 sub-commit D 호환성 유지).
    from app.services import schedule_optimizer as _so

    _find_available_slot = _so._find_available_slot
    calculate_start_datetime = _so.calculate_start_datetime
    calculate_end_datetime = _so.calculate_end_datetime

    pipeline_procs: list[str] = []
    if pred_proc:
        pipeline_procs.append(pred_proc)
    if process_name in ("저압시스", "고압시스"):
        pipeline_procs.append("연합")

    if not pipeline_procs:
        return current_start, current_end

    pred_end_latest: datetime | None = None
    for pp in pipeline_procs:
        for sq_i in group_sqs:
            pe = process_end_by_sq.get((pp, sq_i))
            if pe and pe < datetime.max:
                if pred_end_latest is None or pe > pred_end_latest:
                    pred_end_latest = pe

    if pred_end_latest is None:
        return current_start, current_end

    aligned_start = current_start
    aligned_end = current_end

    # Phase 1: aligned_end 를 pred_end 에 역산 정렬
    reverse_start = calculate_start_datetime(
        pred_end_latest, duration_min, db, equipment_code
    )
    if reverse_start > current_start:
        aligned_start = _find_available_slot(
            reverse_start, duration_min, slots, db, equipment_code
        )
        aligned_end = calculate_end_datetime(
            aligned_start, duration_min, db, equipment_code
        )
    if aligned_end < pred_end_latest:
        aligned_end = pred_end_latest

    # Phase 2: tail_offset_min 만큼 wall-clock 으로 shift (aligned_end > pred_end 보장)
    # 블록 폭(duration_min 기반 wall-clock)은 Phase 1 결과 그대로 유지.
    if tail_offset_min > 0 and aligned_start > current_start:
        shifted_start = aligned_start + timedelta(minutes=tail_offset_min)
        shifted_start = _find_available_slot(
            shifted_start, duration_min, slots, db, equipment_code
        )
        shifted_end = calculate_end_datetime(
            shifted_start, duration_min, db, equipment_code
        )
        aligned_start = shifted_start
        aligned_end = shifted_end

    return aligned_start, aligned_end


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
    # _get_sheath_type 과 _SHEATH_ROUTING 은 schedule_optimizer 에 남아 있음
    # (배치 모델 자체와 결합도가 높음). 지역 import 로 순환 회피.
    from app.services.schedule_optimizer import _SHEATH_ROUTING, _get_sheath_type

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
