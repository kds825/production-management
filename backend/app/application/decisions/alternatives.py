"""Alternative equipment simulator for a single decision batch.

What it answers
---------------
"이 배치를 다른 설비로 옮기면 어떻게 되는가?" — DecisionConstraintsModal 의
의사결정 지원 패널이 묻는 핵심 질문.

Scope (PoC, on-the-fly)
-----------------------
- Pure read-only — no DB writes, no solver re-run.
- Per-equipment compatibility check via EquipmentMaster ↔ ProductionBatch.
- Per-alternative slot-conflict count + earliest free slot + delay-days vs
  due_date.

Why on-the-fly: solver 가 후보 (equipment × time) 를 평가하지만 선택된 1개만
persist 한다. 모든 후보를 DB 에 저장하면 row 수 폭발이라 비현실적. 모달
open 시 ~80ms 추가 compute 가 기존 /latest fetch (~200-500ms) 옆에 묻힌다.

Layer
-----
application/decisions/ — orchestration 레이어. DB I/O 허용, 도메인 호출
허용. infrastructure 직접 import OK.

복잡 추상화는 도입하지 않는다 (CLAUDE.md `feedback_architecture_simplicity`).
dataclass + 함수.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.infrastructure.models.equipment_master import EquipmentMaster
from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.schedule_task import ScheduleTask


@dataclass(frozen=True)
class Alternative:
    """단일 대안 설비 평가 결과 (값 객체)."""

    equipment_code: str
    equipment_name: str
    # 현재 배정된 설비인가? — UI 가 "현재" 배지를 붙이는 데 사용.
    is_current: bool
    # 이 설비에 이미 배정된 task 중 batch 의 target slot (current_task 기간)
    # 과 시간이 겹치는 task 의 개수. 0 이면 자유 이동 가능.
    conflict_count: int
    # 충돌을 모두 회피한 뒤 이 설비에서 batch 가 시작 가능한 가장 이른 시점.
    # current_task 가 없으면 None (참조 slot 부재).
    earliest_available: Optional[datetime]
    # earliest_available + batch duration 이 batch.due_date 를 넘으면 양수.
    # due_date 가 없거나 earliest_available 계산 불가면 None.
    delay_days: Optional[int]


@dataclass(frozen=True)
class AlternativesReport:
    batch_id: int
    current_equipment_code: Optional[str]
    current_assigned_start: Optional[datetime]
    current_assigned_end: Optional[datetime]
    alternatives: list[Alternative]


def compute_alternatives(db: Session, batch_id: int) -> Optional[AlternativesReport]:
    """주어진 batch 에 대한 대안 설비 평가 리포트.

    Returns None when ``batch_id`` 가 존재하지 않을 때. 호출부 (route) 가
    404 로 매핑.
    """
    batch = (
        db.query(ProductionBatch)
        .filter(ProductionBatch.batch_id == batch_id)
        .one_or_none()
    )
    if batch is None:
        return None

    # 현재 schedule_task — target slot 의 anchor 이자 "현재 설비" source.
    # 같은 batch 에 여러 task (다공정) 가 있을 수 있으나 가장 최근 생성된 것
    # 하나만 본다 (수정/재배정 가능성 반영).
    current_task = (
        db.query(ScheduleTask)
        .filter(ScheduleTask.batch_id == batch_id)
        .order_by(ScheduleTask.created_at.desc())
        .first()
    )

    # batch 의 process_name 과 같은 설비만 후보로 (cross-process 이동 비현실적).
    candidates = (
        db.query(EquipmentMaster)
        .filter(EquipmentMaster.process_name == batch.process_name)
        .all()
    )

    compatible = [eq for eq in candidates if _is_compatible(batch, eq)]
    duration_min = _batch_duration_min(batch, current_task)

    alternatives = [
        _evaluate_equipment(db, batch, eq, current_task, duration_min)
        for eq in compatible
    ]

    # 정렬: 현재 배정 설비 먼저, 그다음 지연 짧은 순, 그다음 이름 abc 순.
    # 현재 설비를 먼저 두는 이유: UI 가 항상 "현재 vs 대안" 비교 기준점을
    # 첫 행에 두도록.
    def _sort_key(a: Alternative) -> tuple:
        return (
            0 if a.is_current else 1,
            a.delay_days if a.delay_days is not None else 10**9,
            a.equipment_name,
        )

    alternatives.sort(key=_sort_key)

    return AlternativesReport(
        batch_id=batch_id,
        current_equipment_code=(current_task.equipment_code if current_task else None),
        current_assigned_start=current_task.start_datetime if current_task else None,
        current_assigned_end=current_task.end_datetime if current_task else None,
        alternatives=alternatives,
    )


# ── Compatibility ────────────────────────────────────────────────────────


def _is_compatible(batch: ProductionBatch, eq: EquipmentMaster) -> bool:
    """SQ range + 재질 + 색상그룹 호환 여부.

    domain rule 매핑:
      - 5-1 SQ 기준 설비 배정 — range_min/range_max
      - 8-3 / 10-2 CU/AL 재질 분리 — material_limit
      - 3-3 설비별 색상그룹 제한 — color_group

    값이 NULL 이거나 "ALL"/"전색상" 이면 그 제약은 패스.
    """
    if batch.sq_mm2 is not None:
        sq = float(batch.sq_mm2)
        if eq.range_min is not None and sq < float(eq.range_min):
            return False
        if eq.range_max is not None and sq > float(eq.range_max):
            return False

    if eq.material_limit and eq.material_limit != "ALL":
        if batch.conductor_material and batch.conductor_material != eq.material_limit:
            return False

    if eq.color_group and eq.color_group not in ("전색상", "ALL"):
        # color_group 은 "흑/청" 처럼 슬래시 구분 또는 단일 색상.
        # batch.sheath_color 가 group string 에 포함되면 매칭.
        if batch.sheath_color and batch.sheath_color not in eq.color_group:
            return False

    return True


# ── Slot / duration evaluation ────────────────────────────────────────────


def _batch_duration_min(
    batch: ProductionBatch, current_task: Optional[ScheduleTask]
) -> int:
    """배치 생산 소요시간 (분). 우선순위: 현재 task slot > estimated > fallback."""
    if current_task is not None:
        delta = current_task.end_datetime - current_task.start_datetime
        return max(1, int(delta.total_seconds() / 60))
    if batch.estimated_duration_min is not None:
        return max(1, int(float(batch.estimated_duration_min)))
    return 60


def _evaluate_equipment(
    db: Session,
    batch: ProductionBatch,
    eq: EquipmentMaster,
    current_task: Optional[ScheduleTask],
    duration_min: int,
) -> Alternative:
    """단일 후보 설비에 대한 평가 — 충돌 수 + 가장 이른 가용 시점 + 지연일."""
    is_current = (
        current_task is not None and current_task.equipment_code == eq.equipment_code
    )

    # current_task 가 없으면 target slot 자체가 없어 충돌·지연 평가 불가.
    if current_task is None:
        return Alternative(
            equipment_code=eq.equipment_code,
            equipment_name=eq.equipment_name,
            is_current=False,
            conflict_count=0,
            earliest_available=None,
            delay_days=None,
        )

    target_start = current_task.start_datetime
    target_end = current_task.end_datetime

    # 이 설비에 배정된 다른 task 중 [target_start, target_end) 과 시간 겹침.
    # 같은 batch 자신은 제외 (현재 설비일 때 self-overlap 회피).
    conflicts = (
        db.query(ScheduleTask)
        .filter(ScheduleTask.equipment_code == eq.equipment_code)
        .filter(ScheduleTask.batch_id != batch.batch_id)
        .filter(ScheduleTask.start_datetime < target_end)
        .filter(ScheduleTask.end_datetime > target_start)
        .all()
    )
    conflict_count = len(conflicts)

    # 가용 시점: 충돌 있으면 가장 늦은 end_datetime 직후, 없으면 target_start.
    if conflicts:
        earliest_available = max(c.end_datetime for c in conflicts)
    else:
        earliest_available = target_start

    # 지연일: earliest_available + duration 이 due_date 를 넘으면 양수.
    delay_days: Optional[int] = None
    if batch.due_date is not None:
        finish = earliest_available + timedelta(minutes=duration_min)
        delay = (finish.date() - batch.due_date).days
        delay_days = max(0, delay)

    return Alternative(
        equipment_code=eq.equipment_code,
        equipment_name=eq.equipment_name,
        is_current=is_current,
        conflict_count=conflict_count,
        earliest_available=earliest_available,
        delay_days=delay_days,
    )
