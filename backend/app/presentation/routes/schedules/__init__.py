"""schedules — 스케줄 라우트 sub-package.

Week 7 Task 7A.1 — 1,702 LOC 단일 파일 routes/schedules.py 를
{list, detail, bulk_update, cascade, revert} 5개 submodule + _shared 헬퍼로 분리.

D7-C 호환: 분리 이전에 `from app.presentation.routes.schedules import X` 로
가져오던 모든 이름은 이 `__init__.py` 의 re-export 로 계속 import 가능하다.
특히 `plan_cascade_preview` 는 `tests/test_cascade_preview_v2.py` /
`tests/test_observability_metrics.py` 가 `unittest.mock.patch(
"app.presentation.routes.schedules.plan_cascade_preview", ...)` 로 패치하므로
같은 경로에서 살아있어야 한다. cascade submodule 은 호출 시점에
`schedules_pkg.plan_cascade_preview` 를 참조해 patch 가 정상 적용되도록 한다.
"""

# 주의: `from __future__ import annotations` 를 쓰지 않는다 — FastAPI 는 함수
# annotation 을 runtime 에 평가해 응답 모델 / 상태코드 조합을 검증하기 때문에
# (예: status_code=204 + 반환형 None) lazy annotation 으로 돌리면 라우트
# 등록 자체가 AssertionError 로 실패한다. 대신 submodule 자동 attribute 등록을
# 명시적으로 정리하는 방식으로 builtin `list` shadowing 을 회피한다 (파일 하단).

import copy  # noqa: F401  — sub-commit B/C 이후 handler 본문이 사용
import uuid  # noqa: F401
from collections import deque  # noqa: F401
from datetime import datetime, timedelta  # noqa: F401
from typing import Any  # noqa: F401

from fastapi import APIRouter, Depends, Header, HTTPException, Query  # noqa: F401
from pydantic import BaseModel  # noqa: F401
from sqlalchemy import func  # noqa: F401
from sqlalchemy.orm import Session  # noqa: F401

from app.core.feature_flags import is_cascade_v2_enabled  # noqa: F401
from app.domain.entities import (  # noqa: F401
    ScheduleTask,
    TaskPriority,
    TaskStatus,
)
from app.infrastructure.database import get_db  # noqa: F401
from app.infrastructure.memory_store import store  # noqa: F401
from app.observability.cascade_logging import log_cascade_request  # noqa: F401
from app.observability.metrics import (  # noqa: F401
    cascade_feature_flag_state,
    cascade_preview_duration_seconds,
    cascade_revert_total,
    cascade_unresolved_total,
)
from app.infrastructure.models.equipment_master import (  # noqa: F401
    EquipmentMaster as EquipmentMasterModel,
)
from app.infrastructure.models.production_batch import (  # noqa: F401
    ProductionBatch as ProductionBatchModel,
)
from app.infrastructure.models.schedule_task import (  # noqa: F401
    ScheduleTask as ScheduleTaskModel,
)
from app.presentation.schemas import (  # noqa: F401
    ScheduleTaskCreate,
    ScheduleTaskResponse,
    ScheduleTaskUpdate,
)
from app.infrastructure.models.schedule_change_set import (  # noqa: F401
    ScheduleChangeSet,
)
from app.presentation.schemas.cascade import (  # noqa: F401
    BulkUpdateErrorCode,
    BulkUpdateRequestV2,
    BulkUpdateSuccess,
    CascadePreviewRequest as CascadePreviewRequestV2,
    CascadePreviewResponse as CascadePreviewResponseV2,
)
from app.services.batch_grouping import (  # noqa: F401
    format_spec_display,
    extract_sq,
)
from app.services.cascade import (  # noqa: F401
    plan_cascade_preview,
    UnresolvedReason,
)
from app.services.cascade.snap import build_snapshot  # noqa: F401
from app.services.schedule_optimizer import PREDECESSOR_PROCESS  # noqa: F401
from app.services.schedule_validators import (  # noqa: F401
    find_due_date_violation,
    find_predecessor_violation,
    find_same_eq_overlap,
)

# _shared 헬퍼/스키마 re-export (D7-C 호환).
# 분리 이전 `from app.presentation.routes.schedules import _versions` 등으로
# 접근하던 코드가 깨지지 않도록 동일 이름을 노출.
from app.presentation.routes.schedules._shared import (  # noqa: F401
    VersionDetailResponse,
    VersionSaveRequest,
    VersionSummaryResponse,
    _db_task_to_response,
    _parse_task_id,
    _to_response,
    _versions,
)

router = APIRouter(prefix="/schedules", tags=["스케줄"])

# 서브 라우터 import + 마운트는 본 파일 마지막에서 수행 — 본 모듈이 정의하는
# `@router.get("/tasks", response_model=list[...])` 가 builtin `list` 를 참조해야
# 하므로, submodule 이름 `list` 를 import 하면서 발생하는 builtin shadowing 을
# 모든 handler 정의가 끝난 뒤로 미룬다.


# ---------------------------------------------------------------------------
# 작업(Task) CRUD 엔드포인트 — detail.py 로 이동 (sub-commit C):
#   POST /tasks            → create_task
#   PUT  /tasks/{task_id}  → update_task
#   DELETE /tasks/{task_id} → delete_task
#   POST /versions         → save_version
# list_versions / get_version 은 list.py 로 이동 (sub-commit B).
# ---------------------------------------------------------------------------


# cascade_preview_legacy / cascade_preview_v2 → cascade.py (sub-commit E).
# 관련 헬퍼/스키마 (_collect_all_successors, _SUCCESSOR_PROCESSES, _reason_to_str,
# _push_to_schema, _unres_to_schema, CascadePreviewRequest, AffectedTask,
# CascadeConflict, CascadePreviewResponse) 도 함께 이동.


# bulk_update_tasks_legacy / bulk_update_v2 → bulk_update.py (sub-commit D).
# 관련 헬퍼 (_task_serializable, _coerce_task_id, _feature_disabled_error) 도 함께 이동.


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


# ---------------------------------------------------------------------------
# 서브 라우터 마운트 — handler 정의가 끝난 뒤에 import 한다.
# 왜 마지막에 + importlib 사용: 두 가지 충돌을 동시에 회피해야 한다.
#   1) submodule 이름 `list` 가 builtin `list` 를 그림자처리해 위쪽
#      `response_model=list[...]` 가 TypeError 가 된다.
#   2) `revert`/`cascade` 등은 이 파일 안에 같은 이름의 함수가 정의되어 있어
#      `from . import revert as _revert_mod` 가 함수를 가져온다.
# importlib.import_module 은 sys.modules 에서 직접 모듈을 꺼내 위 두 충돌 모두
# 회피한다. sub-commit B~F 가 진행되며 위쪽 handler 가 모두 비면 이 블록은
# 파일 상단의 평범한 import 로 정리할 예정.
# ---------------------------------------------------------------------------
import importlib as _importlib  # noqa: E402
import sys as _sys  # noqa: E402

# D7-C: 함수 `revert` 와 `list_tasks` 등 기존 import 가능 이름을 보존한다.
# Python import 시스템은 submodule load 시 자동으로 패키지 attribute 를
# 설정한다 (예: schedules.revert = <module>). 같은 이름의 함수가 이미 정의되어
# 있으면 덮어쓴다. import 전에 백업하고 import 후 attribute 를 정리·복원한다.
_revert_handler_fn = revert  # noqa: F821  (route handler defined above)

_list_mod = _importlib.import_module("app.presentation.routes.schedules.list")
_detail_mod = _importlib.import_module("app.presentation.routes.schedules.detail")
_bulk_update_mod = _importlib.import_module(
    "app.presentation.routes.schedules.bulk_update"
)
_cascade_mod = _importlib.import_module("app.presentation.routes.schedules.cascade")
_revert_mod = _importlib.import_module("app.presentation.routes.schedules.revert")

router.include_router(_list_mod.router)
router.include_router(_detail_mod.router)
router.include_router(_bulk_update_mod.router)
router.include_router(_cascade_mod.router)
router.include_router(_revert_mod.router)

# 패키지 attribute 정리 — submodule 이름이 builtin `list` 와 충돌하면
# 핸들러 안의 `list[...]` annotation 이 module-level attribute 를 먼저 찾아
# TypeError. 자동으로 등록된 submodule attribute 를 모두 삭제하고, 핸들러
# 함수 `revert` 만 명시적으로 복원한다. submodule 자체는 sys.modules 에 그대로
# 남으므로 외부에서 `from .list import router` 같은 import 는 여전히 가능.
_pkg = _sys.modules[__name__]
for _name in ("list", "detail", "bulk_update", "cascade", "revert"):
    if hasattr(_pkg, _name):
        delattr(_pkg, _name)

# 핸들러 함수 `revert` 복원 (D7-C).
revert = _revert_handler_fn  # noqa: F811

# D7-C: 핸들러 함수 re-export — submodule 로 옮긴 핸들러도 기존 import 경로
# (`from app.presentation.routes.schedules import list_tasks`) 로 접근 가능해야 한다.
# tests/test_sheath_spec_list.py 가 list_tasks 를 직접 호출.
list_tasks = _list_mod.list_tasks
list_versions = _list_mod.list_versions
get_version = _list_mod.get_version
create_task = _detail_mod.create_task
update_task = _detail_mod.update_task
delete_task = _detail_mod.delete_task
save_version = _detail_mod.save_version
bulk_update_tasks_legacy = _bulk_update_mod.bulk_update_tasks_legacy
bulk_update_v2 = _bulk_update_mod.bulk_update_v2
BulkTaskUpdate = _bulk_update_mod.BulkTaskUpdate
BulkUpdateRequest = _bulk_update_mod.BulkUpdateRequest
_task_serializable = _bulk_update_mod._task_serializable
_coerce_task_id = _bulk_update_mod._coerce_task_id
_feature_disabled_error = _bulk_update_mod._feature_disabled_error
cascade_preview_legacy = _cascade_mod.cascade_preview_legacy
cascade_preview_v2 = _cascade_mod.cascade_preview_v2
CascadePreviewRequest = _cascade_mod.CascadePreviewRequest
AffectedTask = _cascade_mod.AffectedTask
CascadeConflict = _cascade_mod.CascadeConflict
CascadePreviewResponse = _cascade_mod.CascadePreviewResponse
_SUCCESSOR_PROCESSES = _cascade_mod._SUCCESSOR_PROCESSES
_collect_all_successors = _cascade_mod._collect_all_successors
_reason_to_str = _cascade_mod._reason_to_str
_push_to_schema = _cascade_mod._push_to_schema
_unres_to_schema = _cascade_mod._unres_to_schema
