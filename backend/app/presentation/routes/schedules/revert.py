"""schedules/revert.py — revert (undo) + change_set diff 엔드포인트.

핸들러:
- POST /revert/{change_set_id}                  → revert
- GET /change-sets/{change_set_id}/diff         → get_change_set_diff

revert 는 가장 최근 change_set 1건만 되돌려 schedule_task 를 snapshot_before
로 복구. diff 는 같은 change_set 의 snapshot_before/after 를 비교해 moved /
added / removed / unchanged 분류로 반환 (DB 변경 없음).

`__init__.py` 가 본 모듈 router 를 prefix="/schedules" 패키지 router 에
include 한다.
"""

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.infrastructure.database import get_db
from app.infrastructure.models.production_batch import (
    ProductionBatch as ProductionBatchModel,
)
from app.infrastructure.models.schedule_change_set import ScheduleChangeSet
from app.infrastructure.models.schedule_task import (
    ScheduleTask as ScheduleTaskModel,
)
from app.observability.cascade_logging import log_cascade_request
from app.observability.metrics import cascade_revert_total


router = APIRouter()


# ---------------------------------------------------------------------------
# Endpoint: POST /api/schedules/revert/{change_set_id}
#
# Task 14: Undo — 가장 최근 change_set 1건만 되돌림.
#
# 계약:
#   - 404: 알 수 없는 change_set_id.
#   - 409: 이 change_set 이후 더 최근 change_set 이 존재 (freshness 실패).
#          PoC 단계에서는 최근 1건만 undo 스코프 — 중간 revert 는 일관성을 깰 수 있어 거부.
#   - 200: snapshot_before 로 task.start/end/equipment_code 복구 → change_set 삭제 후 커밋.
# ---------------------------------------------------------------------------


@router.post("/revert/{change_set_id}")
def revert(change_set_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    """change_set 한 건을 undo — snapshot_before 값을 schedule_task 에 재적용.

    왜 "최신 1건만": 여러 change_set 을 역순으로 뒤로 감는 full history 는 스냅샷
    간 교차 의존성(예: 두 change_set 이 같은 task 를 덮어쓴 경우) 을 해소해야 해서
    비용이 크다. PoC 는 직전 1건만 안전하게 되돌리는 계약으로 단순화.
    """
    # Task 23: 구조화 로그 + revert counter by status.
    # 404/409/200 경로 각각 status label 로 집계 — 대시보드에서 바로 retry 비율 측정.
    request_id = str(uuid.uuid4())
    with log_cascade_request(
        request_id, f"/revert/{change_set_id}", change_set_id=change_set_id
    ) as extra:
        cs = db.get(ScheduleChangeSet, change_set_id)
        if cs is None:
            cascade_revert_total.labels(status="not_found").inc()
            extra["revert_status"] = "not_found"
            raise HTTPException(status_code=404, detail="change_set_id not found")

        # Freshness 검증 — 이 change_set 이후 새 change_set 이 있으면 undo 거부.
        newer = (
            db.query(ScheduleChangeSet)
            .filter(ScheduleChangeSet.created_at > cs.created_at)
            .order_by(ScheduleChangeSet.created_at.asc())
            .first()
        )
        if newer is not None:
            cascade_revert_total.labels(status="conflict").inc()
            extra["revert_status"] = "conflict"
            extra["newer_change_set_id"] = newer.change_set_id
            raise HTTPException(
                status_code=409,
                detail=(
                    f"newer change_set exists: {newer.change_set_id} "
                    f"(created_at={newer.created_at.isoformat()})"
                ),
            )

        # snapshot_before 로 복구 — task_id 키는 bulk_update_v2 가 str 로 저장.
        # ScheduleTask.task_id 는 Integer PK 이므로 isdigit 이면 int 캐스팅.
        restored_n = 0
        for task_id_str, snap in (cs.snapshot_before or {}).items():
            task_pk: Any = int(task_id_str) if task_id_str.isdigit() else task_id_str
            t = db.get(ScheduleTaskModel, task_pk)
            if t is None:
                # 극단적 race — task 가 삭제된 경우 skip (409 보다 관대하게).
                continue
            t.start_datetime = datetime.fromisoformat(snap["start"])
            t.end_datetime = datetime.fromisoformat(snap["end"])
            if snap.get("equipment_code"):
                t.equipment_code = snap["equipment_code"]
            restored_n += 1

        # change_set 삭제 — 같은 id 로 재revert 방지 (멱등성 대신 1회 소비 선택).
        db.delete(cs)
        db.commit()

        cascade_revert_total.labels(status="success").inc()
        extra["revert_status"] = "success"
        extra["restored_n"] = restored_n
        return {"reverted": True, "change_set_id": change_set_id}


# ---------------------------------------------------------------------------
# Endpoint: GET /api/schedules/change-sets/{change_set_id}/diff
#
# 긴급수주 등 change_set 1건의 snapshot_before/after 를 비교해 "어떤 task 가 어떻게
# 바뀌었는지" 를 분류 반환. 프론트의 diff panel / side-by-side Gantt 데이터 소스.
#
# 분류 규칙:
#   - moved   : before/after 양쪽 존재 + start|end|equipment_code 중 하나라도 상이
#   - added   : after 에만 존재 (before 에 없음)
#   - removed : before 에만 존재 (after 에 없음, 정상 흐름에선 드뭄)
#   - unchanged: 완전 동일 (task_id 리스트만 반환 — payload 부피 축소)
#
# 시간 포맷: snapshot 의 start/end 는 ISO8601 문자열 그대로 유지. delta_hours 는
# float 로 별도 계산해 제공 (프론트가 raw parse 부담 없이 정렬/필터링 가능).
# ---------------------------------------------------------------------------


def _parse_iso_or_none(value: Any) -> datetime | None:
    """snapshot 내 ISO8601 문자열을 datetime 으로 변환. 잘못된 값이면 None.

    snapshot 은 JSONB 이므로 스키마가 강제되지 않는다 — 과거 레코드나 손상된
    데이터가 섞여 있을 수 있어 방어적으로 파싱한다 (fail-fast 대신 partial).
    """
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def _delta_hours(old_iso: Any, new_iso: Any) -> float | None:
    """두 ISO8601 문자열의 시간 차이를 시간 단위 float 로 반환.

    둘 중 하나라도 파싱 실패 시 None — 프론트가 '계산 불가' 상태를 표시할 수 있게.
    round(2) 로 소수점 2자리까지 (1분 해상도).
    """
    old_dt = _parse_iso_or_none(old_iso)
    new_dt = _parse_iso_or_none(new_iso)
    if old_dt is None or new_dt is None:
        return None
    return round((new_dt - old_dt).total_seconds() / 3600, 2)


def _build_task_meta_map(db: Session, task_ids: set[str]) -> dict[str, dict[str, Any]]:
    """task_id 문자열 집합 → 메타 dict 매핑 구축.

    ScheduleTask + ProductionBatch 를 batch_id 로 join 해 batch_group /
    process_name / sheath_color / cross_section / customer_priority / is_urgent
    필드를 추출.

    P6: 숫자 문자열 task_id 만 DB 조회 — "T1" 같은 synthetic id (테스트/스냅샷
    손상) 는 건너뛴다. 조회되지 않는 task_id 는 결과 dict 에 없음 →
    호출부에서 null 로 보강.

    is_urgent 규칙: customer_priority <= 7 이면 긴급 (NORMAL/CRITICAL/URGENT 의
    URGENT 이상). ProductionBatch 의 customer_priority 는 nullable 이지만 기본값 99.
    """
    if not task_ids:
        return {}

    numeric_ids: list[int] = []
    for tid in task_ids:
        if tid.isdigit():
            numeric_ids.append(int(tid))

    if not numeric_ids:
        return {}

    rows = (
        db.query(ScheduleTaskModel, ProductionBatchModel)
        .outerjoin(
            ProductionBatchModel,
            ScheduleTaskModel.batch_id == ProductionBatchModel.batch_id,
        )
        .filter(ScheduleTaskModel.task_id.in_(numeric_ids))
        .all()
    )

    meta_map: dict[str, dict[str, Any]] = {}
    for task, batch in rows:
        cp = (
            int(batch.customer_priority)
            if batch is not None and batch.customer_priority is not None
            else None
        )
        sq = (
            int(batch.sq_mm2)
            if batch is not None and batch.sq_mm2 is not None
            else None
        )
        meta_map[str(task.task_id)] = {
            "batch_group": (batch.batch_group if batch is not None else None)
            or task.batch_group,
            "process_name": batch.process_name if batch is not None else None,
            # sheath_color 는 시스 블록에서만 유의미 — 그 외 공정은 NULL 이 정상.
            "sheath_color": batch.sheath_color if batch is not None else None,
            "cross_section": sq,
            # PoC 규약: customer_priority <= 7 → 긴급(URGENT+). 99/기본값은 NORMAL.
            "is_urgent": cp is not None and cp <= 7,
            "customer_priority": cp,
        }
    return meta_map


def _merge_meta(entry: dict[str, Any], meta: dict[str, Any] | None) -> dict[str, Any]:
    """diff 엔트리에 batch 메타 필드 병합 — 메타 없을 때 null 로 채움.

    프론트가 필드 존재 여부가 아닌 값 null 체크로 처리하도록 스키마 일관성 유지.

    방어적 처리: meta 의 None 값은 default 를 덮어쓰지 않는다.
    현재 build_batch_meta_map 은 is_urgent (항상 bool) 를 제외하면 null 가능한
    필드만 반환하므로 defaults 와 덮어쓰기 결과가 같지만, 호출자가 부분 메타
    (예: {"batch_group": "A"}) 를 넘길 때 is_urgent=False default 가 보존되도록
    한다. key 가 defaults 에 없는 경우는 신규 메타로 간주하여 그대로 채택.
    """
    defaults = {
        "batch_group": None,
        "process_name": None,
        "sheath_color": None,
        "cross_section": None,
        "is_urgent": False,
        "customer_priority": None,
    }
    if meta:
        for key, value in meta.items():
            if value is not None or key not in defaults:
                defaults[key] = value
    return {**entry, **defaults}


@router.get("/change-sets/{change_set_id}/diff")
def get_change_set_diff(
    change_set_id: str,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """change_set 1건의 snapshot_before/after 를 비교해 변경 내역을 분류 반환.

    긴급수주 반영 후 "기존 계획 대비 어떤 배치가 어떻게 바뀌었는지" 를 UI 에
    노출하기 위한 읽기 전용 API. revert 와 달리 DB 를 수정하지 않는다.

    P6: moved/added/removed 각 항목에 batch_group / process_name / sheath_color
    / cross_section / is_urgent / customer_priority 메타 포함 (Stage 2 블록
    시각화 목적). removed_tasks 는 DB 에서 이미 삭제된 경우 meta null.
    unchanged_task_ids 는 id list 유지 — 회색 표시용이라 메타 불필요.
    """
    cs = db.get(ScheduleChangeSet, change_set_id)
    if cs is None:
        raise HTTPException(
            status_code=404,
            detail=f"change_set_id '{change_set_id}' not found",
        )

    before = cs.snapshot_before or {}
    after = cs.snapshot_after or {}

    # JSONB 는 정상 경로에서 dict 로 역직렬화되지만, 과거 데이터/수동 INSERT 로 인해
    # str (직렬화 누락) 이 들어올 가능성을 방어. 파싱 실패는 500 으로 올려 원인 가시화.
    if not isinstance(before, dict) or not isinstance(after, dict):
        raise HTTPException(
            status_code=500,
            detail="snapshot_before / snapshot_after must be JSON objects",
        )

    before_ids = set(before.keys())
    after_ids = set(after.keys())

    common_ids = before_ids & after_ids
    added_ids = after_ids - before_ids
    removed_ids = before_ids - after_ids

    # 메타 조회 — moved+added+removed 전체를 한 번에 조회해 N+1 방지.
    meta_map = _build_task_meta_map(db, before_ids | after_ids)

    moved_tasks: list[dict[str, Any]] = []
    unchanged_task_ids: list[str] = []

    for task_id in sorted(common_ids):
        b = before.get(task_id) or {}
        a = after.get(task_id) or {}
        if not isinstance(b, dict) or not isinstance(a, dict):
            # 개별 task 엔트리 손상 시 moved 로 간주 (보수적) — 진단 로그 대체.
            continue

        old_start = b.get("start")
        old_end = b.get("end")
        old_eq = b.get("equipment_code")
        new_start = a.get("start")
        new_end = a.get("end")
        new_eq = a.get("equipment_code")

        start_changed = old_start != new_start
        end_changed = old_end != new_end
        eq_changed = old_eq != new_eq

        if not (start_changed or end_changed or eq_changed):
            unchanged_task_ids.append(task_id)
            continue

        moved_tasks.append(
            _merge_meta(
                {
                    "task_id": task_id,
                    "old_start": old_start,
                    "old_end": old_end,
                    "old_equipment": old_eq,
                    "new_start": new_start,
                    "new_end": new_end,
                    "new_equipment": new_eq,
                    "start_delta_hours": _delta_hours(old_start, new_start),
                    "end_delta_hours": _delta_hours(old_end, new_end),
                    "equipment_changed": eq_changed,
                },
                meta_map.get(task_id),
            )
        )

    added_tasks: list[dict[str, Any]] = []
    for task_id in sorted(added_ids):
        a = after.get(task_id) or {}
        if not isinstance(a, dict):
            continue
        added_tasks.append(
            _merge_meta(
                {
                    "task_id": task_id,
                    "start": a.get("start"),
                    "end": a.get("end"),
                    "equipment": a.get("equipment_code"),
                },
                meta_map.get(task_id),
            )
        )

    removed_tasks: list[dict[str, Any]] = []
    for task_id in sorted(removed_ids):
        b = before.get(task_id) or {}
        if not isinstance(b, dict):
            continue
        # removed task 는 DB 에서 이미 사라졌을 수 있음 — meta_map 에 없으면 null 필드.
        removed_tasks.append(
            _merge_meta(
                {
                    "task_id": task_id,
                    "start": b.get("start"),
                    "end": b.get("end"),
                    "equipment": b.get("equipment_code"),
                },
                meta_map.get(task_id),
            )
        )

    # kind 컬럼이 없는 구 스키마 환경(migration 미적용) 에서도 안전하게 동작하도록
    # getattr 로 접근 — 없으면 기본값 "cascade" (model 의 default 와 동일).
    kind_value = getattr(cs, "kind", None) or "cascade"

    return {
        "change_set_id": cs.change_set_id,
        "kind": kind_value,
        "created_at": cs.created_at.isoformat() if cs.created_at else None,
        "preview_request_id": cs.preview_request_id,
        "summary": {
            "moved": len(moved_tasks),
            "added": len(added_tasks),
            "removed": len(removed_tasks),
            "unchanged": len(unchanged_task_ids),
            "total_before": len(before_ids),
            "total_after": len(after_ids),
        },
        "moved_tasks": moved_tasks,
        "added_tasks": added_tasks,
        "removed_tasks": removed_tasks,
        "unchanged_task_ids": unchanged_task_ids,
    }
