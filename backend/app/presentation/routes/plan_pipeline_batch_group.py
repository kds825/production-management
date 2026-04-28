"""배치 그룹 관련 API — orders / process-flow / split / unassign / restore /
restore-at / batch-group-snapshots (Task 1.4).

원본: ``plan_pipeline.py`` 의 /batch-group/* + /batch-group-snapshots
endpoint 들을 sub-router 로 분리. URL path 변경 없음.

split endpoint 본문 (~410줄) 은 ``_batch_group_split.split_batch_group`` 으로
이관 — keyword-only 시그니처 (Appendix A.4 강제).
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.application.ingest import format_spec_display
from app.application.validation.batch_group_lifecycle import (
    BatchGroupNotFoundError,
    BatchGroupStatusError,
    compute_restore_at_plan,
)
from app.infrastructure.database import get_db
from app.infrastructure.models.production_batch import ProductionBatch
from app.presentation.routes._batch_group_split import (
    split_batch_group as _split_logic,
)
from app.presentation.schemas.restore_at import RestoreAtRequest, RestoreAtResponse

router = APIRouter()


@router.get("/batch-group/{batch_group:path}/orders", summary="배치 그룹 내 수주 목록")
def list_batch_group_orders(batch_group: str, db: Session = Depends(get_db)):
    """지정한 batch_group에 속하는 production_batch 목록을 반환한다.

    연선 그룹(ST- 접두사):
      - batch_seq=-1 헤더(틀단위 집계)와 batch_seq=1 개별 수주 배치로 구성된다.
      - 개별 수주 배치를 run_label+process_name+sq_mm2로 추가 조회하여 항상 포함한다.
        (batch_group이 구버전 값으로 저장돼 batch_group 필터로 찾히지 않는 경우 대비)
    기타 그룹: batch_group 필터 결과를 그대로 반환한다.
    """
    by_group = (
        db.query(ProductionBatch)
        .filter(ProductionBatch.batch_group == batch_group)
        .order_by(ProductionBatch.batch_seq.asc(), ProductionBatch.due_date.asc())
        .all()
    )

    if not by_group:
        raise HTTPException(
            status_code=404,
            detail=f"batch_group '{batch_group}'에 해당하는 배치가 없습니다.",
        )

    # batch_group 필터 결과를 그대로 사용 (분할된 그룹은 각자의 batch_group만 표시)
    batches = by_group

    # WIP 재고 수량 조회 — 연선/절연재고 사용 배치는 net qty에서 차감해야 함
    from app.infrastructure.models.wip_inventory import WipInventory as WipModel

    wip_ids = [b.wip_matched_id for b in batches if b.wip_matched_id is not None]
    wip_qty_map: dict[int, float] = {}
    wip_stage_map2: dict[int, str] = {}
    if wip_ids:
        wip_rows = (
            db.query(WipModel.wip_id, WipModel.total_length_m, WipModel.process_stage)
            .filter(WipModel.wip_id.in_(wip_ids))
            .all()
        )
        wip_qty_map = {wid: float(tl or 0) for wid, tl, _ in wip_rows}
        wip_stage_map2 = {wid: (ps or "") for wid, _, ps in wip_rows}

    from app.domain.batch_sheath_keys import _WIP_COVERED_PROCESSES

    def _to_dict(b: ProductionBatch) -> dict:
        raw_len = float(b.total_length_m or 0)
        wip_stage = wip_stage_map2.get(b.wip_matched_id, "") if b.wip_matched_id else ""
        # 이 배치의 공정이 WIP에 의해 커버되는 경우에만 차감
        # 예: 절연재고 WIP + 절연 공정 → 차감 / 절연재고 WIP + 시스 공정 → 차감 안 함
        is_wip = bool(wip_stage) and (
            b.process_name or ""
        ) in _WIP_COVERED_PROCESSES.get(wip_stage, set())
        wip_len = wip_qty_map.get(b.wip_matched_id, 0.0) if is_wip else 0.0
        net_len = max(raw_len - wip_len, 0.0)
        return {
            "batch_id": b.batch_id,
            "batch_seq": b.batch_seq,
            "sales_order_id": b.sales_order_id,
            "spec_raw": format_spec_display(
                getattr(b, "spec_raw", None), b.core_count or 1, float(b.sq_mm2 or 0)
            ),
            "sheath_color": b.sheath_color or "",
            "customer_name": b.customer_name or "",
            "due_date": str(b.due_date or ""),
            "drum_length_m": float(b.drum_length_m or 0),
            "drum_count": b.drum_count or 1,
            "total_length_m": raw_len,
            "wip_length_m": wip_len,  # WIP 재고 커버량
            "net_length_m": net_len,  # 실제 작업지시량 (WIP 제외)
            "wip_matched_id": b.wip_matched_id,
            "wip_stage": wip_stage or None,
            "product_group": b.product_group or "",
            "status": b.status or "",
            "core_count": int(b.core_count or 1),
        }

    return [_to_dict(b) for b in batches]


@router.get(
    "/batch-group/{batch_group:path}/process-flow",
    summary="배치 그룹의 연관 공정 흐름 조회",
)
def get_process_flow(batch_group: str, db: Session = Depends(get_db)):
    """주어진 batch_group과 동일 수주를 공유하는 모든 batch_group을 공정 순서로 반환한다.

    이전공정/다음공정 네비게이션에 사용된다.
    예: 연선(ST-240) → 절연(저압절연_240SQ) → 시스(A120_흑_240SQ) 순서로 반환.
    """
    from sqlalchemy import tuple_

    from app.domain.constants import PROCESS_ORDER
    from app.infrastructure.models.schedule_task import (
        ScheduleTask as ScheduleTaskModel,
    )

    # 1. 현재 batch_group의 run_label과 수주 키 조회
    current_batches = (
        db.query(
            ProductionBatch.sales_order_id,
            ProductionBatch.sales_order_line,
            ProductionBatch.run_label,
        )
        .filter(
            ProductionBatch.batch_group == batch_group,
            ProductionBatch.batch_seq != -1,  # 헤더 제외
        )
        .all()
    )

    if not current_batches:
        return []

    order_keys = list({(b.sales_order_id, b.sales_order_line) for b in current_batches})
    run_label = current_batches[0].run_label

    # 2. 동일 run_label 내에서 동일 수주를 포함하는 모든 batch_group 조회
    related = (
        db.query(
            ProductionBatch.batch_group,
            ProductionBatch.process_name,
        )
        .filter(
            ProductionBatch.run_label == run_label,
            ProductionBatch.batch_seq != -1,
            tuple_(
                ProductionBatch.sales_order_id,
                ProductionBatch.sales_order_line,
            ).in_(order_keys),
        )
        .distinct()
        .all()
    )

    # 3. batch_group별 공정 정보 집계
    groups: dict[str, dict] = {}
    for r in related:
        bg = r.batch_group
        if bg and bg not in groups:
            groups[bg] = {
                "batch_group": bg,
                "process_name": r.process_name,
                "order": PROCESS_ORDER.get(r.process_name, 50),
            }

    # 4. schedule_task에서 설비·시간 정보 보강
    for bg_info in groups.values():
        task = (
            db.query(
                ScheduleTaskModel.equipment_code,
                ScheduleTaskModel.start_datetime,
                ScheduleTaskModel.end_datetime,
            )
            .filter(ScheduleTaskModel.batch_group == bg_info["batch_group"])
            .first()
        )
        if task:
            bg_info["equipment_code"] = task.equipment_code
            bg_info["start_datetime"] = (
                task.start_datetime.isoformat() if task.start_datetime else None
            )
            bg_info["end_datetime"] = (
                task.end_datetime.isoformat() if task.end_datetime else None
            )

    # 5. 공정 순서 → batch_group 이름 순으로 정렬
    result = sorted(groups.values(), key=lambda x: (x["order"], x["batch_group"]))
    return result


@router.post("/batch-group/{batch_group:path}/split", summary="배치 그룹 분할")
def split_batch_group_endpoint(
    batch_group: str,
    body: dict,
    db: Session = Depends(get_db),
):
    """410줄 비즈니스 로직은 ``_batch_group_split.split_batch_group`` 에 위임.

    keyword-only 시그니처 (Appendix A.4) — positional 호출 금지.
    """
    return _split_logic(db=db, batch_group=batch_group, body=body)


# ---------------------------------------------------------------------------
# Task 2.3: batch_group 미배정 (unassign) — 단일 트랜잭션 엔드포인트
#
# 왜 여기서 commit하는가:
#   batch_group_lifecycle.unassign_batch_group은 flush만 수행 (Eng Critical #1).
#   라우트가 서비스 flush 직후 audit_log INSERT를 추가하고 단 한 번만 commit하여,
#   "unassign 상태 전이 + 감사 로그"가 원자적으로 함께 persist되거나 둘 다 롤백되게 한다.
# ---------------------------------------------------------------------------


@router.post(
    "/batch-group/{batch_group}/unassign",
    summary="batch_group 전체를 미배정으로 soft-delete (사유 기록)",
)
def unassign_batch_group_endpoint(
    batch_group: str,
    body: dict | None = None,
    db: Session = Depends(get_db),
) -> dict:
    """단일 트랜잭션: 서비스 flush + audit_log INSERT + commit.

    Body:
        { "reason": "자재지연" | "설비고장" | "납기재협상" | "기타" }
        생략 또는 None이면 서비스가 '기타'로 저장.

    Responses:
        200: { batch_group, affected_batches, affected_tasks, reason, idempotent }
        400: 상태(planned 외) / WIP 매칭 / reason 검증 오류
        404: batch_group 없음
    """
    from app.infrastructure.models.audit_log import AuditLog
    from app.application.validation.batch_group_lifecycle import (
        BatchGroupNotFoundError as _BGNotFound,
        BatchGroupReasonError,
        BatchGroupStatusError as _BGStatus,
        BatchGroupWipMatchedError,
        unassign_batch_group,
    )

    reason = (body or {}).get("reason")

    try:
        result = unassign_batch_group(db, batch_group, reason=reason)
    except _BGNotFound as exc:
        # 404: 존재하지 않는 batch_group — 상태 전이 없이 즉시 실패
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (
        _BGStatus,
        BatchGroupWipMatchedError,
        BatchGroupReasonError,
    ) as exc:
        # 400: 비즈니스 제약 위반 (상태/WIP/reason) — 서비스는 변경 전에 예외를 던지므로
        # rollback까지 할 필요는 없으나 세션 상태를 명시적으로 되돌려 다음 쿼리 안전 보장.
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # 멱등 호출(이미 모두 unassigned)에는 새 audit row를 남기지 않는다 — 감사 로그가
    # 실제 상태 전이에 1:1 대응하도록 유지 (중복 '변경 없음' 기록 방지).
    if not result.get("idempotent"):
        # run_label은 서비스 결과에 없으므로 첫 affected_batch에서 조회 (정통 소스).
        # 없으면 'manual' (sm_inventory.py 기존 컨벤션: wip.run_label or "manual").
        batch_run_label: str | None = None
        first_batch_id = (
            result["affected_batches"][0] if result["affected_batches"] else None
        )
        if first_batch_id is not None:
            batch_run_label = (
                db.query(ProductionBatch.run_label)
                .filter(ProductionBatch.batch_id == first_batch_id)
                .scalar()
            )

        db.add(
            AuditLog(
                run_label=batch_run_label or "manual",
                stage="stage1",
                action_type="BATCH_GROUP_UNASSIGNED",
                batch_id=first_batch_id,
                decision_reason=(
                    f"batch_group {batch_group} unassigned "
                    f"(reason={result['reason']}, "
                    f"tasks={len(result['affected_tasks'])})"
                ),
            )
        )

    db.commit()
    return result


# ---------------------------------------------------------------------------
# POST /pipeline/batch-group/{bg}/restore — unassigned → planned 복원 (Task 3.3)
#
# 왜 단일 트랜잭션:
#   batch_group_lifecycle.restore_batch_group은 flush만 수행 (Eng Critical #2).
#   라우트가 서비스 flush 직후 audit_log INSERT를 추가하고 한 번만 commit하여,
#   "상태 복원 + 감사 로그"가 원자적으로 persist 되거나 둘 다 롤백되게 한다.
#
# conflicts가 있을 경우:
#   서비스는 mutate하지 않고 반환하므로 commit/rollback 없이 바로 409로 매핑.
# ---------------------------------------------------------------------------


@router.post(
    "/batch-group/{batch_group}/restore",
    summary="unassigned batch_group을 원래 자리로 복원 (status flip)",
)
def restore_batch_group_endpoint(
    batch_group: str, db: Session = Depends(get_db)
) -> dict:
    """단일 트랜잭션: 서비스 flush → audit_log INSERT → commit.

    Responses:
        200: 성공 — restored_tasks 배열 (멱등이면 빈 배열)
        404: batch_group 없음
        400: unassigned 외 상태 혼재
        409: 원래 자리 점유됨 — detail.conflicts 에 상세 반환
    """
    from app.infrastructure.models.audit_log import AuditLog
    from app.application.validation.batch_group_lifecycle import (
        BatchGroupNotFoundError as _BGNotFound,
        BatchGroupStatusError as _BGStatus,
        restore_batch_group,
    )

    try:
        result = restore_batch_group(db, batch_group)
    except _BGNotFound as exc:
        # 404: 존재하지 않는 batch_group — 상태 전이 없이 즉시 실패
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except _BGStatus as exc:
        # 400: planned 외 상태 혼재 — 서비스가 변경 전 예외 throw. 세션 clean 유지.
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if result["conflicts"]:
        # 서비스는 아무것도 mutate하지 않고 반환 — commit 하지 않아도 세션 clean.
        # detail은 dict로 전달해 프론트가 conflicts 배열을 바로 파싱할 수 있게 함.
        raise HTTPException(
            status_code=409,
            detail={"conflicts": result["conflicts"]},
        )

    # 멱등 호출(이미 모두 planned)에는 새 audit row를 남기지 않는다 — 감사 로그가
    # 실제 상태 전이에 1:1 대응하도록 유지 (unassign 엔드포인트와 동일 규칙).
    if not result.get("idempotent"):
        # run_label은 서비스 결과에 없으므로 복원된 batch 중 하나에서 조회.
        # 없으면 'manual' (unassign 엔드포인트와 동일 컨벤션).
        batch = (
            db.query(ProductionBatch)
            .filter(ProductionBatch.batch_group == batch_group)
            .first()
        )
        run_label = (batch.run_label if batch else None) or "manual"
        first_batch_id = batch.batch_id if batch else None

        db.add(
            AuditLog(
                run_label=run_label,
                stage="stage1",
                action_type="BATCH_GROUP_RESTORED",
                batch_id=first_batch_id,
                decision_reason=(
                    f"batch_group {batch_group} restored, "
                    f"tasks={len(result['restored_tasks'])}"
                ),
            )
        )

    db.commit()
    return result


# ---------------------------------------------------------------------------
# POST /pipeline/batch-group/{bg}/restore-at — anchor 기준 재배치 preview (Task 1.2)
#
# no-mutation preview: 서비스가 flush/commit 없이 계산 결과만 반환한다.
# 프론트가 CascadePreviewModal로 렌더 후 확정 시 bulk-update-v2로 일괄 반영.
# ---------------------------------------------------------------------------


@router.post(
    "/batch-group/{batch_group}/restore-at",
    summary="unassigned batch_group 을 anchor 위치 기준으로 재배치 preview (no mutation)",
)
def restore_batch_group_at_endpoint(
    batch_group: str,
    body: RestoreAtRequest,
    db: Session = Depends(get_db),
) -> RestoreAtResponse:
    """no-mutation preview — 프론트가 이 결과를 CascadePreviewModal 로 렌더 →
    확정 시 bulk-update-v2 로 일괄 반영.

    Responses:
        200: RestoreAtResponse (task_positions + cascade-preview-v2 호환 필드)
        400: unassigned 외 상태 혼재 (BatchGroupStatusError)
        404: batch_group 없음 (BatchGroupNotFoundError)
        422: body 검증 실패 (Pydantic)
    """
    try:
        result = compute_restore_at_plan(
            db,
            batch_group=batch_group,
            anchor_equipment_code=body.anchor_equipment_code,
            anchor_start=body.anchor_start,
        )
    except BatchGroupNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except BatchGroupStatusError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return RestoreAtResponse(
        batch_group=result.batch_group,
        task_positions=[tp.__dict__ for tp in result.task_positions],
        pushes=result.pushes,
        pulls=result.pulls,
        unresolved=result.unresolved,
        request_id=result.request_id,
        can_auto_resolve=result.can_auto_resolve,
        iter_count=result.iter_count,
        truncated=result.truncated,
    )


# ---------------------------------------------------------------------------
# GET /pipeline/batch-group-snapshots — unassigned batch_group 목록 (Task 3.3)
#
# 프론트 OrderInbox가 새로고침 시 호출. batch_group 단위 집계 + unassign_reason 포함.
#
# equipment_group 관련:
#   백엔드 ProductionBatch에는 equipment_group 컬럼이 없다 (Task 3.1 검증).
#   process_name을 그대로 노출하고, 프론트의 toEquipmentGroup(process_name, batch_group)
#   헬퍼가 canonicalize 한다 — 백엔드/프론트 계약 단순화.
# ---------------------------------------------------------------------------


@router.get(
    "/batch-group-snapshots",
    summary="unassigned 상태 batch_group 목록 (사유 + 공정체인 포함)",
)
def list_batch_group_snapshots(db: Session = Depends(get_db)) -> dict:
    """Returns: { "groups": [BatchGroupSnapshot] } — 프론트 OrderInbox 용."""
    rows = (
        db.query(ProductionBatch).filter(ProductionBatch.status == "unassigned").all()
    )
    groups: dict[str, dict] = {}
    for b in rows:
        g = groups.setdefault(
            b.batch_group,
            {
                "batch_group": b.batch_group,
                "customer": b.customer_name or "",
                "spec": b.spec_raw or "",
                "color": b.sheath_color or "",
                "total_length_m": 0.0,
                "delivery_date": b.due_date.isoformat() if b.due_date else "",
                "processes": [],
                "order_count": 0,
                "unassign_reason": b.unassign_reason or "기타",
            },
        )
        g["total_length_m"] += float(b.total_length_m or 0)
        g["order_count"] += 1
        # equipment_group은 백엔드 모델에 없음 — process_name을 그대로 노출.
        # 프론트의 toEquipmentGroup(process_name, batch_group)이 canonicalize.
        g["processes"].append(
            {
                "process": b.process_name or "",
                "equipment_group": b.process_name or "",
            }
        )
    return {"groups": list(groups.values())}
