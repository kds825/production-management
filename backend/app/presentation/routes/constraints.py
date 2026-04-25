from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.domain.constraints import validate_task
from app.infrastructure.database import get_db
from app.infrastructure.memory_store import store
from app.infrastructure.models.constraint_config import ConstraintConfig
from app.infrastructure.models.constraint_config_history import ConstraintConfigHistory
from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.schedule_task import ScheduleTask
from app.infrastructure.models.speed_master import SpeedMaster
from app.presentation.schemas import (
    ConstraintValidateRequest,
    ConstraintValidateResponse,
    ConstraintViolationResponse,
)

router = APIRouter(prefix="/constraints", tags=["제약 조건"])


@router.post("/validate", response_model=ConstraintValidateResponse)
def validate_constraints(body: ConstraintValidateRequest) -> ConstraintValidateResponse:
    """
    단일 작업 또는 전체 스케줄에 대한 제약 조건 검증
    - validate_all=True: 모든 작업 통합 검증
    - validate_all=False: task_id 단건 검증
    """
    all_tasks = store.list_tasks()
    routes = store.list_routes()
    violations_list: list[ConstraintViolationResponse] = []

    if body.validate_all:
        # 전체 스케줄 검증 — 각 작업을 순회하며 제약 위반 수집
        for task in all_tasks:
            equipment = store.get_equipment(task.equipment_id)
            if equipment is None:
                continue
            raw_violations = validate_task(task, equipment, all_tasks, routes)
            for v in raw_violations:
                violations_list.append(
                    ConstraintViolationResponse(
                        type=v.type,
                        severity=v.severity,
                        message=v.message,
                        task_id=v.task_id,
                        related_task_id=v.related_task_id,
                    )
                )
        task_id_result = None
    else:
        # 단건 작업 검증
        task = store.get_task(body.task_id)
        if task is None:
            raise HTTPException(
                status_code=404,
                detail=f"작업 '{body.task_id}'를 찾을 수 없습니다.",
            )
        equipment = store.get_equipment(task.equipment_id)
        if equipment is None:
            raise HTTPException(
                status_code=404,
                detail=f"설비 '{task.equipment_id}'를 찾을 수 없습니다.",
            )
        raw_violations = validate_task(task, equipment, all_tasks, routes)
        for v in raw_violations:
            violations_list.append(
                ConstraintViolationResponse(
                    type=v.type,
                    severity=v.severity,
                    message=v.message,
                    task_id=v.task_id,
                    related_task_id=v.related_task_id,
                )
            )
        task_id_result = body.task_id

    error_count = sum(1 for v in violations_list if v.severity == "error")
    warning_count = sum(1 for v in violations_list if v.severity == "warning")

    return ConstraintValidateResponse(
        task_id=task_id_result,
        violations=violations_list,
        is_valid=error_count == 0,
        error_count=error_count,
        warning_count=warning_count,
    )


# ── 제약조건 마스터 CRUD (DB 기반) ───────────────────────────────────────────


@router.get("", summary="제약조건 목록 조회")
def list_constraints(db: Session = Depends(get_db)):
    rows = db.query(ConstraintConfig).order_by(ConstraintConfig.priority).all()
    return {
        "constraints": [
            {
                "constraint_id": r.constraint_id,
                "constraint_name": r.constraint_name,
                "category": r.category,
                "is_enabled": r.is_enabled,
                "priority": r.priority,
                "impact_level": r.impact_level,
                "params_json": r.params_json,
                "applicable_processes": r.applicable_processes,
                "implementation_type": r.implementation_type,
                "notes": r.notes,
            }
            for r in rows
        ],
        "total": len(rows),
        "enabled": sum(1 for r in rows if r.is_enabled),
    }


# NOTE: /drift-status 는 /{constraint_id}/history 보다 먼저 선언해야 한다.
# FastAPI 는 선언 순서대로 매칭하므로, 동적 경로(`{constraint_id}`) 보다
# 리터럴 경로(`drift-status`) 가 앞에 있어야 리터럴이 우선 매칭된다.
@router.get(
    "/drift-status", summary="ConstraintConfig/SpeedMaster 편집 후 재실행 필요 여부"
)
def get_drift_status(db: Session = Depends(get_db)):
    """Silent drift 방지 — UI 상단 배너 트리거.

    Why: ConstraintConfig 또는 SpeedMaster 최신 updated_at 이 ScheduleTask
    최신 created_at 보다 나중이면 'dirty'. ScheduleTask.created_at 을
    '마지막 auto_schedule 실행 시각' 프록시로 사용.
    """
    # Why: ConstraintConfig.updated_at 은 tz-aware (UTC), ScheduleTask.created_at
    # 은 naive (datetime.utcnow) — 직접 비교하면 TypeError. aware 쪽을 UTC 로
    # 변환 후 tzinfo 를 제거해 양쪽 모두 naive-UTC 로 맞춘다.
    from datetime import timezone as _tz

    def _to_naive_utc(dt):
        if dt is None or dt.tzinfo is None:
            return dt
        return dt.astimezone(_tz.utc).replace(tzinfo=None)

    latest_constraint = db.query(func.max(ConstraintConfig.updated_at)).scalar()
    latest_speed = db.query(func.max(SpeedMaster.updated_at)).scalar()
    latest_schedule = db.query(func.max(ScheduleTask.created_at)).scalar()

    candidates = [x for x in (latest_constraint, latest_speed) if x is not None]
    latest_edit = max(candidates) if candidates else None

    if latest_edit is None:
        dirty = False
    elif latest_schedule is None:
        dirty = True
    else:
        dirty = _to_naive_utc(latest_edit) > latest_schedule

    return {
        "dirty": dirty,
        "latest_constraint_updated_at": (
            latest_constraint.isoformat() if latest_constraint else None
        ),
        "latest_speed_master_updated_at": (
            latest_speed.isoformat() if latest_speed else None
        ),
        "latest_schedule_run_at": (
            latest_schedule.isoformat() if latest_schedule else None
        ),
    }


@router.patch("/{constraint_id}", summary="제약조건 수정 (on/off, 파라미터)")
def update_constraint(constraint_id: str, body: dict, db: Session = Depends(get_db)):
    row = (
        db.query(ConstraintConfig)
        .filter(ConstraintConfig.constraint_id == constraint_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"제약조건 '{constraint_id}' 없음")

    # 변경 이력 기록 — params_json 이 실제로 바뀐 경우만.
    # Why: 프론트(ParamEditor/SaveModal)는 "편집된 키만" patch 로 보낸다.
    # 그대로 대입하면 편집되지 않은 다른 키가 DB 에서 사라져 silent corruption
    # (예: stranding_min 만 바꿨는데 insulation_min/sheath_min/cv_min 이 날아가
    # resolve_spec_setup_min 이 default=0.0 으로 0분 스케줄). 따라서 merge.
    if "params_json" in body:
        old_params = dict(row.params_json or {})
        patch = dict(body["params_json"])
        merged = {**old_params, **patch}
        if old_params != merged:
            history = ConstraintConfigHistory(
                constraint_id=constraint_id,
                old_params_json=old_params,
                new_params_json=merged,
            )
            db.add(history)
        row.params_json = merged

    if "is_enabled" in body:
        row.is_enabled = body["is_enabled"]
    if "priority" in body:
        row.priority = body["priority"]
    db.commit()
    return {"constraint_id": constraint_id, "updated": True}


@router.get("/{constraint_id}/history", summary="제약조건 변경 이력")
def get_constraint_history(constraint_id: str, db: Session = Depends(get_db)):
    rows = (
        db.query(ConstraintConfigHistory)
        .filter(ConstraintConfigHistory.constraint_id == constraint_id)
        .order_by(ConstraintConfigHistory.changed_at.desc())
        .limit(50)
        .all()
    )
    return {
        "constraint_id": constraint_id,
        "history": [
            {
                "history_id": r.history_id,
                "changed_at": r.changed_at.isoformat(),
                "changed_by": r.changed_by,
                "old_params_json": r.old_params_json,
                "new_params_json": r.new_params_json,
            }
            for r in rows
        ],
    }


@router.post(
    "/{constraint_id}/preview-impact",
    summary="파라미터 변경 시 영향받는 planned 배치 개수/Δ",
)
def preview_impact(
    constraint_id: str,
    body: dict,
    db: Session = Depends(get_db),
):
    """PoC: 4-1 stranding_min 변경만 정확히 계산. 다른 제약은 count 0 반환."""
    new_params = (body or {}).get("new_params_json", {})

    if constraint_id == "4-1" and "stranding_min" in new_params:
        current_row = (
            db.query(ConstraintConfig)
            .filter(ConstraintConfig.constraint_id == "4-1")
            .first()
        )
        current = (
            float((current_row.params_json or {}).get("stranding_min", 210))
            if current_row
            else 210.0
        )
        new_val = float(new_params["stranding_min"])
        delta = new_val - current

        # planned 연선 배치 중 현재 setup_time_min 이 current 와 같은 건만 카운트
        count = (
            db.query(func.count(ProductionBatch.batch_id))
            .filter(
                ProductionBatch.status == "planned",
                ProductionBatch.setup_time_min == current,
            )
            .scalar()
            or 0
        )
        total_delta = delta * count

        return {
            "affected_batch_count": int(count),
            "total_delta_min": float(total_delta),
            "current_value": current,
            "new_value": new_val,
        }

    return {
        "affected_batch_count": 0,
        "total_delta_min": 0.0,
        "note": "preview-impact 는 PoC 범위에서 4-1 stranding_min 만 지원",
    }


# ── 베이스라인(스냅샷) 비교 ─────────────────────────────────────────────────
#
# Why: §8b 에 따라 "베이스라인" 은 별도 테이블이 아니라
# constraint_config_history.changed_by 가 'BASELINE_<tag>_<iso>' 패턴인
# 행 그룹으로 정의된다. 이 그룹의 new_params_json 묶음을 두 개 모아
# constraint_id × params 키 단위로 set-compare 하여 diff 를 만든다.


def _parse_baseline_tag(changed_by: str) -> str:
    """'BASELINE_<tag>_<iso>' 에서 사람이 읽는 <tag> 부분만 추출.

    Why: tag 는 사용자가 입력한 자유 문자열이고, 끝의 ISO timestamp 만 시스템이
    덧붙인다. 단순 split('_') 는 tag 자체에 '_' 가 포함된 경우 깨지므로
    rsplit 으로 마지막 segment(=ISO) 만 잘라낸다.
    """
    if not changed_by.startswith("BASELINE_"):
        return changed_by
    body = changed_by[len("BASELINE_") :]
    # 마지막 '_' 기준으로 한 번만 분리 → tag 에 '_' 가 있어도 보존
    if "_" in body:
        tag, _iso = body.rsplit("_", 1)
        return tag
    return body


@router.get("/baselines", summary="저장된 베이스라인 스냅샷 목록")
def list_baselines(db: Session = Depends(get_db)):
    """`changed_by LIKE 'BASELINE_%'` 인 history 그룹을 chronological 로 반환.

    응답: {baselines: [{tag, changed_by, created_at, row_count}]} —
    `created_at` 은 그룹 내 최신(=대표) changed_at.
    """
    rows = (
        db.query(
            ConstraintConfigHistory.changed_by,
            func.max(ConstraintConfigHistory.changed_at).label("created_at"),
            func.count(ConstraintConfigHistory.history_id).label("row_count"),
        )
        .filter(ConstraintConfigHistory.changed_by.like("BASELINE_%"))
        .group_by(ConstraintConfigHistory.changed_by)
        .order_by(func.max(ConstraintConfigHistory.changed_at).desc())
        .all()
    )
    return {
        "baselines": [
            {
                "tag": _parse_baseline_tag(r.changed_by),
                "changed_by": r.changed_by,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "row_count": int(r.row_count),
            }
            for r in rows
        ],
        "total": len(rows),
    }


def _latest_params_per_constraint(db: Session, changed_by: str) -> dict[str, dict]:
    """베이스라인 그룹 내에서 constraint_id 별 최신 new_params_json 을 집계.

    Why: 같은 베이스라인 태그 안에 동일 constraint_id 가 여러 번 들어있을 가능성은
    낮지만, history 정의상 가능하다. 결정론을 위해 changed_at DESC, history_id DESC
    로 첫 번째 행을 채택.
    """
    rows = (
        db.query(ConstraintConfigHistory)
        .filter(ConstraintConfigHistory.changed_by == changed_by)
        .order_by(
            ConstraintConfigHistory.changed_at.desc(),
            ConstraintConfigHistory.history_id.desc(),
        )
        .all()
    )
    out: dict[str, dict] = {}
    for r in rows:
        if r.constraint_id in out:
            continue  # 더 최신 행이 이미 채택됨
        out[r.constraint_id] = dict(r.new_params_json or {})
    return out


@router.get(
    "/versions/{a}/diff/{b}",
    summary="두 베이스라인 사이의 파라미터 차이",
)
def diff_versions(a: str, b: str, db: Session = Depends(get_db)):
    """두 `changed_by` 베이스라인 태그 사이의 params_json 차이를 반환.

    각 constraint_id 의 `new_params_json` 을 키 단위로 비교하여
    {constraint_id, field, value_a, value_b} 행을 emit. 한쪽에만 존재하는 키는
    상대 측 값을 None 으로 표기.

    404: a 또는 b 의 changed_by 그룹이 비어 있는 경우.
    """
    params_a = _latest_params_per_constraint(db, a)
    params_b = _latest_params_per_constraint(db, b)

    # Why: 베이스라인이 존재하는지 확인 — 빈 dict 면 history 가 없음.
    if not params_a:
        raise HTTPException(
            status_code=404,
            detail=f"베이스라인 '{a}' 를 찾을 수 없습니다.",
        )
    if not params_b:
        raise HTTPException(
            status_code=404,
            detail=f"베이스라인 '{b}' 를 찾을 수 없습니다.",
        )

    diff_rows: list[dict] = []
    all_constraint_ids = set(params_a.keys()) | set(params_b.keys())
    for cid in sorted(all_constraint_ids):
        pa = params_a.get(cid, {})
        pb = params_b.get(cid, {})
        all_keys = set(pa.keys()) | set(pb.keys())
        for k in sorted(all_keys):
            va = pa.get(k)
            vb = pb.get(k)
            if va != vb:
                diff_rows.append(
                    {
                        "constraint_id": cid,
                        "field": k,
                        "value_a": va,
                        "value_b": vb,
                    }
                )

    return {
        "version_a": a,
        "version_b": b,
        "diff": diff_rows,
        "total": len(diff_rows),
    }
