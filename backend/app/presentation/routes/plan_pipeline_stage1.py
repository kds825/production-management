"""Stage 1 (ERP → 작업지시서) API endpoints.

원본: ``plan_pipeline.py`` 의 /stage1/* + /wip-template endpoint 들을
sub-router 로 분리 (Task 1.3). URL path 변경 없음.
"""

import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.application.ingest import (
    create_batches,
    detect_split_candidates,
    execute_auto_splits,
    format_spec_display,
)
from app.application.ingest.pipeline_orchestrator import execute_stage1_ingest
from app.application.ingest.run_labeler import (
    new_run_label as _alloc_run_label,
    parse_base_date_yyyymmdd,
    parse_date_yyyymmdd,
)
from app.application.ingest.wip_matching import match_wip
from app.infrastructure.database import get_db
from app.infrastructure.exporters.excel_exporter import export_plan
from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.wip_inventory import WipInventory

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/stage1", summary="ERP 업로드 → 작업지시서 생성")
async def run_stage1(
    erp_file: UploadFile = File(..., description="ERP 수주 파일 (.xls)"),
    wip_file: UploadFile | None = File(None, description="재공 재고 파일 (선택)"),
    date_from: str | None = Form(None, description="납기 시작일 (YYYYMMDD)"),
    date_to: str | None = Form(None, description="납기 종료일 (YYYYMMDD)"),
    split_gap_days: int = Form(
        3, description="연선 그룹 분할 후보 납기 간격 임계값 (일)"
    ),
    base_date: str | None = Form(
        None,
        description=(
            "계획 기준일자 (YYYYMMDD). 프론트의 '계획 기준일자' 입력값. "
            "Stage 1 응답에 echo 되어 Stage 2 호출 시 동일 값이 사용되도록 한다."
        ),
    ),
    db: Session = Depends(get_db),
) -> dict:
    """Stage 1 파이프라인 실행 (HTTP 어댑터 — 파싱·UploadFile.read() 만 담당).

    Pipeline 본체는 ``services.pipeline.orchestrator.execute_stage1_ingest`` 에
    이관되었다. 이 핸들러는 (a) UploadFile 을 await-read 하고 (b) 폼
    파라미터를 파싱한 뒤 (c) 결과를 그대로 반환한다.

    Returns:
        run_label, 파싱 결과, WIP 매칭 결과, 배치 생성 결과, 통합 경고 목록,
        split_candidates (분할 후보 연선 그룹 목록), base_date (echo).
    """
    erp_content = await erp_file.read()
    wip_content = await wip_file.read() if wip_file else None
    parsed_from = parse_date_yyyymmdd(date_from, field_name="date_from")
    parsed_to = parse_date_yyyymmdd(date_to, field_name="date_to")
    parsed_base_date = parse_base_date_yyyymmdd(base_date)

    return execute_stage1_ingest(
        erp_content=erp_content,
        wip_content=wip_content,
        parsed_from=parsed_from,
        parsed_to=parsed_to,
        split_gap_days=split_gap_days,
        base_date=parsed_base_date,
        db=db,
    )


@router.post("/stage1/update", summary="Stage 1 증분/전체 업데이트 (Freeze & Rebuild)")
async def run_stage1_update(
    erp_file: UploadFile = File(..., description="ERP 수주 파일 (.xls/.xlsx)"),
    wip_file: UploadFile | None = File(None, description="재공 재고 파일 (선택)"),
    upload_mode: str = Form(..., description="incremental 또는 full"),
    parent_run_label: str | None = Form(
        None, description="기존 계획 실행의 run_label (미지정 시 최신 run 자동 감지)"
    ),
    split_gap_days: int = Form(
        3, description="연선 그룹 분할 후보 납기 간격 임계값 (일)"
    ),
    base_date: str | None = Form(
        None,
        description=(
            "계획 기준일자 (YYYYMMDD). Stage 1에서는 필터가 아니라 기록/Stage 2 "
            "자동배열 앵커 용도 — 응답에 echo 되어 Stage 2 호출 시 동일 값이 "
            "사용되도록 한다. frozen 기준은 상태(status)만 사용: "
            "in_progress / completed / wip_complete 만 보존, 나머지 "
            "planned / scheduled 는 업데이트된 수주로 재생성."
        ),
    ),
    db: Session = Depends(get_db),
) -> dict:
    """Stage 1 증분/전체 업데이트 — 신규 run_label 발급 + Freeze 복제.

    이전 설계와의 차이:
      - parent_run_label 을 재사용하지 않고, 매 호출마다 new_run_label (타임스탬프)
        을 새로 발급한다. 이전 run 의 ProductionBatch / ScheduleTask 는
        그대로 보존 → 버전 비교 가능.
      - frozen (in_progress / completed / wip_complete) 배치와 그 수주의 다른 공정
        배치, 해당 ScheduleTask 를 new_run_label 로 복제 (parent_run_label 기록).
      - 중복 배치 방지: frozen_order_keys 는 status-only 로 계산 (batch_seq 필터 제거).
        create_batches 가 frozen_order_keys 를 수신해 해당 수주는 재배치하지 않는다.
      - SalesOrder 의 PK 는 (order_id, order_line) 이라 run_label 버전 불가.
        비동결 SalesOrder 는 new_run_label 로 forward-roll (UPDATE) 하고, 동결
        SalesOrder 는 parent run_label 을 그대로 유지해 list_runs 외주 집계를 보존.

    upload_mode:
      - "incremental": 기존 수주를 유지하고 새 수주만 추가
      - "full": 동결 수주 외 전부 삭제 후 새 파일로 교체
    """
    # ── 입력 검증 (fail-fast) ────────────────────────────────────────────────
    # 모든 검증은 DB mutation 이전에 끝낸다 — 이후 단계에서 검증 실패가 raise
    # 되면 commit/rollback 경계가 어긋나 InFailedSqlTransaction 으로 connection
    # pool 이 오염될 수 있다. (회귀 사례: parse_base_date_yyyymmdd 호출을
    # return 직전에 두고 commit 이후 raise 된 NameError 가 후속 요청을
    # 깨뜨림 — 2026-05-21.)
    if upload_mode not in ("incremental", "full"):
        raise HTTPException(
            status_code=400,
            detail=f"upload_mode는 'incremental' 또는 'full'이어야 합니다: {upload_mode}",
        )
    # base_date 형식 검증 + 분할 로직의 의사 today 로 사용.
    # 사용자가 frontend 에서 선택한 "계획 기준일자" 를 splitter 의 overload 가용시간
    # 계산·D-day·is_urgent 판정에 그대로 적용한다 (date.today() 대체).
    parsed_base_date = parse_base_date_yyyymmdd(base_date)

    # parent_run_label 자동 감지 — 미지정 시 최신 run_label 사용.
    # `test-%` 는 conftest savepoint rollback 누수 가능성이 있어 자동 선택에서
    # 제외 (schedules/list.py 와 동일 방어).
    if not parent_run_label:
        latest = (
            db.query(ProductionBatch.run_label)
            .filter(~ProductionBatch.run_label.like("test-%"))
            .order_by(ProductionBatch.created_at.desc())
            .first()
        )
        parent_run_label = latest[0] if latest else None

    # parent_run_label에 해당하는 배치가 존재하는지 확인
    existing_count = 0
    if parent_run_label:
        existing_count = (
            db.query(func.count(ProductionBatch.batch_id))
            .filter(ProductionBatch.run_label == parent_run_label)
            .scalar()
        )
    if not existing_count and upload_mode == "incremental":
        raise HTTPException(
            status_code=404,
            detail="증분 업데이트할 기존 계획이 없습니다. 먼저 '전체 교체'로 초기 계획을 생성하세요.",
        )

    # 신규 run_label 발급 — 이전 run 은 건드리지 않고 새 버전으로 분기
    new_run_label = _alloc_run_label()

    try:
        from app.infrastructure.models.sales_order import SalesOrder
        from app.infrastructure.models.schedule_task import ScheduleTask
        from app.infrastructure.parsers.erp_parser import parse_erp_file_incremental
        from sqlalchemy import and_, or_

        # ── 1. Frozen 배치 식별 (parent_run_label 기준) ─────────────────────────
        # 룰: 상태(status) 기준만 사용 — in_progress / completed / wip_complete 만 보존.
        # base_date 는 Stage 1 에서 필터 기준이 아님 (Stage 2 자동배열 앵커 용도).
        if parent_run_label:
            frozen = (
                db.query(ProductionBatch)
                .filter(
                    ProductionBatch.run_label == parent_run_label,
                    ProductionBatch.status.in_(
                        ["in_progress", "completed", "wip_complete"]
                    ),
                )
                .all()
            )
        else:
            frozen = []

        # frozen_order_keys: status-only (batch_seq 필터 제거 — 기존 버그 수정).
        # 연선 헤더(batch_seq=-1) 도 frozen 이면 포함해야 Full 모드에서 해당 수주가
        # create_batches 재배치에서 제외되어 중복 배치가 발생하지 않는다.
        frozen_order_keys: set[tuple] = {
            (b.sales_order_id, b.sales_order_line)
            for b in frozen
            if b.sales_order_id and b.sales_order_line is not None
        }
        frozen_order_ids = {k[0] for k in frozen_order_keys}
        frozen_wip_ids: set[int] = {
            b.wip_matched_id for b in frozen if b.wip_matched_id is not None
        }
        frozen_batch_ids: set[int] = {b.batch_id for b in frozen}

        # ── 2. "보존" 대상 배치: frozen 자신 + 동일 수주의 다른 공정 배치 ──────
        # 연선이 in_progress인데 절연/시스가 planned 이면 이 세 배치 모두 new_run_label
        # 로 복제되어야 버전 B 에서도 동일 수주의 공정 체인이 끊어지지 않는다.
        #
        # Bug fix: "동일 수주" 식별은 (sales_order_id, sales_order_line) tuple
        # 기준. 이전 구현은 `sales_order_id.in_(frozen_order_ids)` 로 order_id
        # 만 비교해서, 같은 order_id 의 **다른 line** (non-frozen) 배치까지
        # 복제 대상에 포함됨 → create_batches 재생성 경로와 교차 → 중복 insert
        # (run 20260420_224009 에서 107건 영향). tuple 매칭으로 교정.
        if frozen_order_keys and parent_run_label:
            related_order_cond = or_(
                *[
                    and_(
                        ProductionBatch.sales_order_id == oid,
                        ProductionBatch.sales_order_line == oline,
                    )
                    for oid, oline in frozen_order_keys
                ]
            )
            related_batches = (
                db.query(ProductionBatch)
                .filter(
                    ProductionBatch.run_label == parent_run_label,
                    or_(
                        related_order_cond,
                        ProductionBatch.batch_id.in_(frozen_batch_ids),
                    ),
                )
                .all()
            )
        else:
            related_batches = list(frozen)
        protected_batch_ids: set[int] = {b.batch_id for b in related_batches}

        # ── 1.4 Full 모드 diff pre-snapshot (T2b) ─────────────────────────────
        # 새 파일과 대사해 added/updated/deleted/preserved 분류를 낸다. mutation
        # 직전에 order_id set 을 찍어 둬야 사후 비교가 가능.
        pre_order_ids_full: set[str] = set()
        if upload_mode == "full" and parent_run_label:
            pre_order_ids_full = {
                row[0]
                for row in db.query(SalesOrder.order_id)
                .filter(SalesOrder.run_label == parent_run_label)
                .distinct()
                .all()
                if row[0]
            }

        # ── 3. 보존 배치를 new_run_label 로 복제 (parent_run_label 기록) ───────
        # SQLAlchemy 컬럼 이름 자동 추출로 모든 데이터 그대로 복사. batch_id 는
        # autoincrement 이므로 제외, run_label / parent_run_label 은 재지정.
        old_to_new_batch_id: dict[int, int] = {}
        batch_columns = [
            c.name
            for c in ProductionBatch.__table__.columns
            if c.name not in ("batch_id", "created_at")
        ]
        for b in related_batches:
            data = {col: getattr(b, col) for col in batch_columns}
            data["run_label"] = new_run_label
            data["parent_run_label"] = parent_run_label
            new_b = ProductionBatch(**data)
            db.add(new_b)
            db.flush()  # batch_id 확정
            old_to_new_batch_id[b.batch_id] = new_b.batch_id

        # ── 4. 보존 배치의 ScheduleTask 를 new_run_label 로 복제 ──────────────
        # 간트 표시에 필요한 설비/시간 슬롯 정보. Stage 2 의 _purge_run_tasks 가
        # frozen 배치 task 는 보존하도록 이미 수정되어 있음 (해당 PR 참조).
        if old_to_new_batch_id:
            old_batch_ids = list(old_to_new_batch_id.keys())
            old_tasks = (
                db.query(ScheduleTask)
                .filter(
                    ScheduleTask.run_label == parent_run_label,
                    ScheduleTask.batch_id.in_(old_batch_ids),
                )
                .all()
            )
            task_columns = [
                c.name
                for c in ScheduleTask.__table__.columns
                if c.name not in ("task_id", "created_at")
            ]
            for t in old_tasks:
                data = {col: getattr(t, col) for col in task_columns}
                data["run_label"] = new_run_label
                data["batch_id"] = old_to_new_batch_id[t.batch_id]
                db.add(ScheduleTask(**data))
            db.flush()

        # ── 5. 비동결 SalesOrder / WipInventory 를 new_run_label 로 forward-roll ──
        # SalesOrder PK = (order_id, order_line) 이므로 run_label 버전 불가.
        # 동결 수주는 parent 라벨을 유지 → list_runs 외주 집계 보존.
        # 비동결만 new_run_label 로 옮겨서 create_batches / wip_matching 이
        # new_run_label 스코프에서 일관되게 동작하도록 한다.
        if parent_run_label:
            if frozen_order_keys:
                frozen_cond = or_(
                    *[
                        and_(
                            SalesOrder.order_id == oid,
                            SalesOrder.order_line == oline,
                        )
                        for oid, oline in frozen_order_keys
                    ]
                )
                db.query(SalesOrder).filter(
                    SalesOrder.run_label == parent_run_label,
                    ~frozen_cond,
                ).update({"run_label": new_run_label}, synchronize_session=False)
            else:
                db.query(SalesOrder).filter(
                    SalesOrder.run_label == parent_run_label,
                ).update({"run_label": new_run_label}, synchronize_session=False)
            # WipInventory: 동결 WIP 제외하고 forward-roll
            wip_update_q = db.query(WipInventory).filter(
                WipInventory.run_label == parent_run_label,
            )
            if frozen_wip_ids:
                wip_update_q = wip_update_q.filter(
                    WipInventory.wip_id.notin_(frozen_wip_ids)
                )
            wip_update_q.update({"run_label": new_run_label}, synchronize_session=False)
            db.flush()

        # ── 6. Sales Order 처리 (new_run_label 스코프) ─────────────────────────
        erp_content = await erp_file.read()
        if not erp_content:
            raise HTTPException(status_code=400, detail="ERP 파일이 비어 있습니다.")

        deleted_counts = {"audit_log": 0, "schedule_task": 0, "production_batch": 0}

        if upload_mode == "incremental":
            # 기존 orders (forward-roll 로 new_run_label 라벨 완료) 유지 + 새 수주만 append
            pre_parse_order_keys: set[tuple] = {
                (row[0], row[1])
                for row in db.query(SalesOrder.order_id, SalesOrder.order_line)
                .filter(SalesOrder.run_label == new_run_label)
                .all()
            }
            parse_result = parse_erp_file_incremental(erp_content, new_run_label, db)
        else:
            # full: 방금 forward-roll 된 비동결 SalesOrder 를 전부 삭제 후 새 파일로 교체.
            # 동결 SalesOrder 는 parent run_label 에 남아 있으므로 이 쿼리 영향 없음.
            db.query(SalesOrder).filter(
                SalesOrder.run_label == new_run_label,
            ).delete(synchronize_session=False)
            db.flush()
            # pre-parse 스냅샷 (new_run_label 스코프) — 이 파일로 실제 추가된 수주 계산용
            pre_parse_order_keys = set()
            # 새 파일 파싱 — incremental 파서로 동일 (order_id, drum_length_m) 중복 감지
            parse_result = parse_erp_file_incremental(erp_content, new_run_label, db)

        # ── 7. WIP 처리 ──────────────────────────────────────────────────────
        # WIP 파싱/매칭은 실패 시 경고만 남기고 후속 단계를 계속 진행해야 한다.
        # PostgreSQL 은 트랜잭션 안에서 한 statement 가 실패하면 이후 모든 statement
        # 가 InFailedSqlTransaction 으로 거부되므로, swallowed exception 직후
        # create_batches 가 cascade 실패하는 회귀가 있었다. SAVEPOINT
        # (db.begin_nested()) 로 감싸 실패해도 부분 rollback 만 일어나도록 한다.
        wip_warnings: list[str] = []
        if wip_file:
            sp = db.begin_nested()
            try:
                from app.infrastructure.parsers.wip_parser import parse_wip_file

                wip_content = await wip_file.read()
                if wip_content:
                    # new_run_label 스코프의 기존 WIP 삭제 전에 FK cycle 을 끊는다.
                    # wip_inventory 는 production_batch.wip_matched_id 와
                    # sales_order.wip_id 에서 NO ACTION 정책으로 참조되므로,
                    # 비동결 배치/주문의 FK 를 먼저 NULL 화해야 ForeignKeyViolation
                    # 없이 DELETE 가 가능하다. (_purge_run_data 와 동일 패턴.)
                    # raw SQL subquery 로 FK cycle 해제
                    from sqlalchemy import text as _text

                    _wip_sub = "(SELECT wip_id FROM wip_inventory WHERE run_label = :rl)"
                    db.execute(
                        _text("UPDATE production_batch SET wip_matched_id = NULL "
                              f"WHERE wip_matched_id IN {_wip_sub}"),
                        {"rl": new_run_label},
                    )
                    db.execute(
                        _text("UPDATE sales_order SET wip_id = NULL, use_wip = FALSE, "
                              "wip_type = NULL, actual_length_m = NULL "
                              f"WHERE wip_id IN {_wip_sub}"),
                        {"rl": new_run_label},
                    )
                    db.execute(
                        _text("DELETE FROM wip_inventory WHERE run_label = :rl"),
                        {"rl": new_run_label},
                    )
                    db.flush()

                    # 새 WIP 파싱 — new_run_label 로 적재
                    wip_parse = parse_wip_file(wip_content, db, run_label=new_run_label)
                    wip_warnings.extend(wip_parse.get("warnings", []))
                    if wip_parse["total"] > 0:
                        wip_warnings.append(
                            f"재공실사 {wip_parse['total']}건 등록 완료."
                        )
                sp.commit()
            except Exception as exc:
                sp.rollback()
                wip_warnings.append(f"재공 파일 파싱 실패: {exc}")

        # ── 8. WIP 매칭 — frozen WIP 제외 ─────────────────────────────────────
        sp = db.begin_nested()
        try:
            wip_result = match_wip(
                new_run_label,
                db,
                exclude_wip_ids=frozen_wip_ids if frozen_wip_ids else None,
            )
            sp.commit()
        except Exception as exc:
            sp.rollback()
            wip_warnings.append(f"WIP 매칭 실패 (계속 진행): {exc}")
            wip_result = {"matched": 0, "skipped": 0, "details": []}

        # ── 9. Batch Grouping — frozen orders 제외 (중복 배치 방지 불변식) ─────
        # frozen_order_keys 는 status-only 로 뽑혔으므로 헤더(batch_seq=-1) 까지
        # 포함되어 있어, 해당 수주가 어떤 공정에서든 물리 시작/완료되었으면 재배치 X.
        try:
            batch_result = create_batches(
                new_run_label,
                db,
                frozen_order_keys=frozen_order_keys if frozen_order_keys else None,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=500, detail=f"배치 생성 실패: {exc}"
            ) from exc

        # ── 9a. "이 파일로 추가된 주문에서 나온 배치" 분리 집계 ───────────────
        # Why: batch_result.total_batches 는 "비동결 수주 전체를 다시 배치화한
        # 재생성 총계" 다. 사용자가 업로드한 파일의 실제 증분이 얼마인지 알기
        # 어려워 오해가 발생 (e.g. 20건 업로드 → 952 배치 표시). pre-parse 스냅샷
        # 과 차집합으로 new_from_file_order_keys 를 계산, 해당 주문에 속한
        # planned 배치만 공정별로 다시 집계해 new_from_file 로 응답에 포함.
        post_parse_order_keys: set[tuple] = {
            (row[0], row[1])
            for row in db.query(SalesOrder.order_id, SalesOrder.order_line)
            .filter(SalesOrder.run_label == new_run_label)
            .all()
        }
        new_from_file_keys = post_parse_order_keys - pre_parse_order_keys
        new_from_file_summary: dict = {"total_batches": 0, "by_process": {}}
        if new_from_file_keys:
            new_order_ids = {k[0] for k in new_from_file_keys}
            new_file_batches = (
                db.query(ProductionBatch.process_name)
                .filter(
                    ProductionBatch.run_label == new_run_label,
                    ProductionBatch.status == "planned",
                    ProductionBatch.sales_order_id.in_(new_order_ids),
                )
                .all()
            )
            by_proc: dict[str, int] = {}
            for row in new_file_batches:
                proc = row[0] or "기타"
                by_proc[proc] = by_proc.get(proc, 0) + 1
            new_from_file_summary = {
                "total_batches": len(new_file_batches),
                "by_process": by_proc,
            }

        db.commit()

        # parsed_base_date(datetime) → date. splitter 내부 시점 비교는 date 단위.
        base_date_ref = parsed_base_date.date() if parsed_base_date else None

        # ── 10. Overload 자동 분할 (시스템 결정) ─────────────────────────────
        # 정책 (2026-05-21): 단일 설비 가용시간 < 요구시간인 overload 그룹만
        # 자동 ceil(N/2) 균형 분할 → 두 설비 병렬 배정. 일반 납기 차이는 11단계
        # 의견 카드(BatchSplitReview)로 노출하여 사용자가 직접 결정한다.
        auto_split_result: dict = {"auto_split_count": 0, "splits": []}
        try:
            auto_split_result = execute_auto_splits(
                new_run_label,
                db,
                gap_days=split_gap_days,
                base_date=base_date_ref,
            )
            if auto_split_result["auto_split_count"] > 0:
                logger.info(
                    "[Stage1 Update] Overload 자동 분할 %d건 적용",
                    auto_split_result["auto_split_count"],
                )
        except Exception as exc:
            logger.warning(
                "[Stage1 Update] Overload 자동 분할 실패 (계속 진행): %s", exc
            )

        # ── 11. Split 후보 감지 → 사용자 의견 카드 (overload 제외) ───────────
        # include_overload=False — 10단계에서 이미 처리된 overload 후보는
        # 다시 묻지 않는다. 사용자에게 보이는 카드는 "납기 차이 큼" 케이스만.
        # 단일 드럼(lot_count==1)은 batch_splitter.py:121 에서 스킵.
        try:
            split_candidates = detect_split_candidates(
                new_run_label,
                db,
                gap_days=split_gap_days,
                base_date=base_date_ref,
                include_overload=False,
            )
        except Exception as exc:
            logger.warning("[Stage1 Update] 분할 후보 감지 실패: %s", exc)
            split_candidates = []

        warnings = (
            parse_result.get("warnings", [])
            + wip_warnings
            + batch_result.get("warnings", [])
        )

        # ── 진단 warning: frozen 판정 결과를 명시적으로 노출 ───────────────────
        # Why: "이전 런의 어떤 배치가 frozen 으로 복제돼 새 런에서도 그 자리에
        # 박혔는지" 가 EDD 역전·비정상 배치 원인 추적의 핵심. 기존에는 frozen
        # 카운트만 response 에 있어 "어떤 batch_group 이 왜 frozen 됐나" 를 UI
        # 에서 알 수 없었음. status 별 요약 + 상위 N 개 batch_group 을 warning
        # 에 평문 추가해 사용자/개발자가 plan_pipeline 응답만으로 진단 가능.
        if frozen:
            _frozen_by_status: dict[str, int] = {}
            _frozen_groups: dict[str, int] = {}
            for _fb in frozen:
                _frozen_by_status[_fb.status] = _frozen_by_status.get(_fb.status, 0) + 1
                _bg = _fb.batch_group or f"_single_{_fb.batch_id}"
                _frozen_groups[_bg] = _frozen_groups.get(_bg, 0) + 1
            _status_parts = ", ".join(
                f"{_s}×{_c}" for _s, _c in sorted(_frozen_by_status.items())
            )
            warnings.append(
                f"[Frozen] parent_run={parent_run_label} → {len(frozen)}건 복제 "
                f"({_status_parts}) / batch_group {len(_frozen_groups)}개"
            )
            # 상위 10개 batch_group 을 별도 warning 으로 노출 (UI 에서 잘림 방지).
            _top_groups = sorted(_frozen_groups.items(), key=lambda kv: -kv[1])[:10]
            if _top_groups:
                warnings.append(
                    "[Frozen groups] "
                    + ", ".join(f"{_bg}×{_c}" for _bg, _c in _top_groups)
                )

        # ── 12. Frozen 배치 요약 (T2a) — 복제된 new_run_label 기준 ───────────
        # 프론트엔드가 "진행중/완료 보존" 뱃지로 표시할 수 있도록 frozen 배치의
        # 최소 필드를 직렬화해 응답에 포함. 복제본의 batch_id (new_run_label 안)
        # 을 내려줘야 UI 에서 해당 run 상세 조회 시 연결된다.
        frozen_batches_payload = []
        if old_to_new_batch_id and frozen:
            # 복제된 frozen 배치를 new_run_label 에서 다시 로드
            new_frozen_ids = [
                old_to_new_batch_id[b.batch_id]
                for b in frozen
                if b.batch_id in old_to_new_batch_id
            ]
            if new_frozen_ids:
                new_frozen = (
                    db.query(ProductionBatch)
                    .filter(ProductionBatch.batch_id.in_(new_frozen_ids))
                    .all()
                )
                frozen_batches_payload = [
                    {
                        "batch_id": b.batch_id,
                        "batch_group": b.batch_group,
                        "process_name": b.process_name,
                        "status": b.status,
                        "customer_name": b.customer_name,
                        "due_date": b.due_date.isoformat() if b.due_date else None,
                        "item_code": b.item_code,
                        "product_group": b.product_group,
                        "voltage": b.voltage,
                        "sq_mm2": float(b.sq_mm2) if b.sq_mm2 is not None else None,
                        "sheath_color": b.sheath_color,
                        "drum_count": b.drum_count,
                        "total_length_m": (
                            float(b.total_length_m)
                            if b.total_length_m is not None
                            else None
                        ),
                        "sales_order_id": b.sales_order_id,
                        "sales_order_line": b.sales_order_line,
                        "equipment_code": b.equipment_code,
                    }
                    for b in new_frozen
                ]

        # ── 13. Full 모드 diff 요약 (T2b) ───────────────────────────────────
        # 새 파일 파싱 후 post-snapshot 을 pre 와 대사해 added/updated/deleted/
        # preserved_frozen 분류. order_id 레벨 (드럼/line 무관).
        # post_order_ids: new_run_label 의 SalesOrder + parent 에 남은 frozen 수주.
        diff_summary: dict | None = None
        if upload_mode == "full":
            post_order_ids_new = {
                row[0]
                for row in db.query(SalesOrder.order_id)
                .filter(SalesOrder.run_label == new_run_label)
                .distinct()
                .all()
                if row[0]
            }
            post_order_ids = post_order_ids_new | frozen_order_ids
            _common = pre_order_ids_full & post_order_ids
            diff_summary = {
                "added": len(post_order_ids - pre_order_ids_full),
                "updated": len(_common - frozen_order_ids),
                "deleted": len(pre_order_ids_full - post_order_ids),
                "preserved_frozen": len(_common & frozen_order_ids),
            }

        return {
            "run_label": new_run_label,
            "parent_run_label": parent_run_label,
            "upload_mode": upload_mode,
            "base_date": base_date,
            "frozen": {
                "batch_count": len(frozen_batch_ids),
                "order_count": len(frozen_order_keys),
                "wip_count": len(frozen_wip_ids),
                "protected_batch_count": len(protected_batch_ids),
                "copied_batch_count": len(old_to_new_batch_id),
            },
            "frozen_batches": frozen_batches_payload,
            "diff_summary": diff_summary,
            "deleted": deleted_counts,
            "parse": parse_result,
            "wip": wip_result,
            "batches": batch_result,
            # T2a-2: 이 파일로 추가된 주문에서 나온 planned 배치 집계 (라벨 구분용)
            "new_from_file": new_from_file_summary,
            "warnings": warnings,
            "split_candidates": split_candidates,
            "auto_split": auto_split_result,
            # 프론트엔드 토스트 메시지용
            "added_orders": parse_result.get("inserted", 0),
            "created_batch_groups": batch_result.get("total_batches", 0),
            "preserved_batches": len(old_to_new_batch_id),
            "auto_split_count": auto_split_result["auto_split_count"],
        }

    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("[Stage1 Update] 실패 — 롤백 완료")
        raise HTTPException(
            status_code=500,
            detail=f"Stage 1 업데이트 실패 (롤백 완료): {exc}",
        ) from exc


@router.post(
    "/stage1/urgent",
    summary="[REMOVED] 긴급 수주 — /stage1/update upload_mode=incremental 로 대체",
    deprecated=True,
    status_code=410,
)
async def apply_urgent_order(
    erp_file: UploadFile | None = File(None),  # noqa: ARG001 — schema parity
    run_label: str | None = Form(None),  # noqa: ARG001
    gap_days: int = Form(3),  # noqa: ARG001
) -> None:
    """410 Gone — 사용 중단 (frontend 미호출, urgent_scheduler 의 FK cycle 미패치).

    현재 긴급수주 플로우:
      POST /api/pipeline/stage1/update
        upload_mode=incremental
        parent_run_label=<기존 run>
        base_date=<YYYY-MM-DD>

    이 엔드포인트의 실제 핸들러였던 `urgent_scheduler.apply_urgent_incremental`
    는 newly_created header batch 가 wip_inventory.source_batch_id 로 참조될 때
    FK NO ACTION 위반을 일으킨다 (Week 9 통합 테스트에서 발견). 패치 대신 해당
    플로우의 단일 진입점을 `/stage1/update` 로 일원화하여 표면을 줄였다.
    """
    raise HTTPException(
        status_code=410,
        detail=(
            "이 엔드포인트는 사용 중단되었습니다. "
            "긴급수주는 POST /api/pipeline/stage1/update 에 "
            "upload_mode='incremental' + parent_run_label + base_date 를 함께 보내 주세요."
        ),
    )


@router.get("/stage1/{run_label}/batches", summary="배치 목록 JSON")
def list_batches(run_label: str, db: Session = Depends(get_db)) -> list[dict]:
    """지정한 run_label의 production_batch 데이터를 JSON으로 반환한다.

    공정 순서(연선→B100→A100→A120) 내림차순 SQ 정렬.
    """
    # 공정 정렬 우선순위 — CASE WHEN 대신 Python 후처리
    PROCESS_ORDER = {"연선": 0, "B100": 1, "A100": 2, "A120": 3}
    # 시스색 정렬 — 원본 Excel 기준: 흑→갈→회→청→녹/황→흑/적
    COLOR_ORDER = {"흑": 0, "갈": 1, "회": 2, "청": 3, "녹/황": 4, "흑/적": 5}

    from app.infrastructure.models.sales_order import SalesOrder

    # production_batch + sales_order + wip_inventory JOIN
    rows = (
        db.query(
            ProductionBatch,
            SalesOrder.order_status,
            WipInventory.length_m,
            WipInventory.count,
            WipInventory.core_colors,
            WipInventory.process_stage,
        )
        .outerjoin(
            SalesOrder,
            (ProductionBatch.sales_order_id == SalesOrder.order_id)
            & (ProductionBatch.sales_order_line == SalesOrder.order_line),
        )
        .outerjoin(
            WipInventory,
            ProductionBatch.wip_matched_id == WipInventory.wip_id,
        )
        .filter(
            ProductionBatch.run_label == run_label,
            ProductionBatch.batch_seq != -1,  # 헤더 배치(스케줄러 전용) 제외
        )
        .all()
    )
    batches = [
        (b, os or "대기", wip_len_m, wip_cnt, wip_cc, wip_ps)
        for b, os, wip_len_m, wip_cnt, wip_cc, wip_ps in rows
    ]
    if not batches:
        raise HTTPException(
            status_code=404,
            detail=f"run_label '{run_label}'에 해당하는 배치가 없습니다.",
        )

    # 공정 순서 → SQ 내림차순 → 색상 지정 순서 정렬
    batches.sort(
        key=lambda item: (
            PROCESS_ORDER.get(item[0].process_name, 99),
            -(float(item[0].sq_mm2 or 0)),
            COLOR_ORDER.get(item[0].sheath_color, 99),
        )
    )

    return [
        {
            "batch_id": b.batch_id,
            "process_name": b.process_name,
            "sq_mm2": float(b.sq_mm2 or 0),
            "core_count": b.core_count or 1,
            "sheath_color": b.sheath_color or "",
            "total_length_m": float(b.total_length_m or 0),
            "drum_count": b.drum_count,
            "drum_length_m": float(b.drum_length_m or 0),
            "customer_name": b.customer_name,
            "due_date": str(b.due_date or ""),
            "product_group": b.product_group,
            "sales_order_id": b.sales_order_id,
            "wip_matched_id": b.wip_matched_id,
            "wip_process_stage": wip_ps or None,
            "wip_length_m": float(wip_len_m) if wip_len_m is not None else None,
            "wip_count": int(wip_cnt) if wip_cnt is not None else None,
            "wip_core_colors": wip_cc or None,
            "status": b.status,
            "order_status": order_status,
            "remarks": b.remarks,
            "spec_raw": format_spec_display(
                getattr(b, "spec_raw", None), b.core_count or 1, float(b.sq_mm2 or 0)
            ),
            "core_colors": b.core_colors or "",
            "voltage": b.voltage,
            "equipment_code": b.equipment_code,
            "batch_group": b.batch_group,
        }
        for b, order_status, wip_len_m, wip_cnt, wip_cc, wip_ps in batches
    ]


@router.get("/stage1/{run_label}/wip-inventory", summary="SM 재공 재고 목록")
def list_wip_inventory(run_label: str, db: Session = Depends(get_db)) -> list[dict]:
    """wip_inventory 테이블을 직접 조회해서 반환한다.

    - run_label에 해당하는 모든 WIP 재고 (status 무관)
    - production_batch.wip_matched_id 로 어떤 배치에 매칭됐는지 batch_id / batch_group 포함
    """
    from app.infrastructure.models.sales_order import SalesOrder
    from sqlalchemy import or_

    # wip_inventory 전체 조회
    # run_label이 일치하거나 NULL인 경우 모두 포함 (기존 데이터 호환)
    wip_rows = (
        db.query(WipInventory)
        .filter(
            or_(
                WipInventory.run_label == run_label,
                WipInventory.run_label.is_(None),
            )
        )
        .order_by(WipInventory.process_stage, WipInventory.cross_section.desc())
        .all()
    )

    # WIP process_stage 룩업 (연선재고→연선, 절연재고→절연 등 공정 매칭용)
    wip_process_stages: dict[int, str] = {
        w.wip_id: (w.process_stage or "") for w in wip_rows
    }

    # 매칭 정보: wip_id → (batch_id, batch_group) — 한 번에 로드해서 N+1 방지
    # wip_matched_id가 같은 배치가 여러 개일 수 있음(연선·절연·시스 모두 전파됨).
    # WIP process_stage와 batch process_name이 일치하는 배치를 우선 선택한다.
    matched_batches = (
        db.query(
            ProductionBatch.wip_matched_id,
            ProductionBatch.batch_id,
            ProductionBatch.batch_group,
            ProductionBatch.process_name,
        )
        .filter(
            ProductionBatch.run_label == run_label,
            ProductionBatch.wip_matched_id.isnot(None),
        )
        .all()
    )
    wip_match_map: dict[int, dict] = {}
    for row in matched_batches:
        wip_process = wip_process_stages.get(row.wip_matched_id, "")
        proc = row.process_name or ""
        # WIP 공정과 배치 공정이 일치하는지 확인
        is_match = (
            ("연선" in wip_process and "연선" in proc)
            or ("절연" in wip_process and ("절연" in proc or "B100" in proc))
            or ("시스" in wip_process and "시스" in proc)
        )
        # 아직 미등록이거나 공정이 더 잘 맞는 배치로 갱신
        if row.wip_matched_id not in wip_match_map or is_match:
            wip_match_map[row.wip_matched_id] = {
                "matched_batch_id": row.batch_id,
                "matched_batch_group": row.batch_group,
            }

    # wip_id별 실제 사용량(m) — 환산수량(ordered_qty_m × core_count) 합산
    # 4C 케이블은 심선 4가닥 소비하므로 WIP 드럼에서 ×4 차감
    from sqlalchemy import func as sqla_func, case

    used_m_rows = (
        db.query(
            SalesOrder.wip_id,
            sqla_func.sum(
                SalesOrder.ordered_qty_m
                * case((SalesOrder.core_count > 0, SalesOrder.core_count), else_=1)
            ),
        )
        .filter(
            SalesOrder.run_label == run_label,
            SalesOrder.wip_id.isnot(None),
        )
        .group_by(SalesOrder.wip_id)
        .all()
    )
    wip_used_m: dict[int, float] = {
        wid: float(total or 0) for wid, total in used_m_rows
    }

    return [
        {
            "wip_id": w.wip_id,
            "process_stage": w.process_stage or "",
            "voltage_class": w.voltage_class or "",
            "material": w.material or "",
            "product_name": w.product_name or "",
            "spec": w.spec or "",
            "cross_section": float(w.cross_section) if w.cross_section else None,
            "length_m": float(w.length_m) if w.length_m else 0,
            "count": w.count or 1,
            "total_length_m": float(w.total_length_m) if w.total_length_m else 0,
            "core": w.core,
            "core_colors": w.core_colors or "",
            "status": w.status or "",
            "used_m": wip_used_m.get(w.wip_id, 0),
            "matched_batch_id": wip_match_map.get(w.wip_id, {}).get("matched_batch_id"),
            "matched_batch_group": wip_match_map.get(w.wip_id, {}).get(
                "matched_batch_group"
            ),
        }
        for w in wip_rows
    ]


@router.get("/stage1/{run_label}/outsourced", summary="외주 분류 수주 목록")
def list_outsourced_orders(run_label: str, db: Session = Depends(get_db)) -> list[dict]:
    """지정한 run_label에서 외주로 분류된 수주(is_outsourced=True) 목록을 반환한다.

    외주 품목은 production_batch에 포함되지 않으므로 sales_order에서 직접 조회한다.
    """
    from app.infrastructure.models.sales_order import SalesOrder

    orders = (
        db.query(SalesOrder)
        .filter(
            SalesOrder.run_label == run_label,
            SalesOrder.is_outsourced == True,  # noqa: E712
        )
        .order_by(SalesOrder.due_date, SalesOrder.order_id)
        .all()
    )

    return [
        {
            "order_id": o.order_id,
            "order_line": o.order_line,
            "product_group": o.product_group or "",
            "spec_raw": o.spec_raw or "",
            "sheath_color": o.sheath_color or "",
            "customer_name": o.customer_name or "",
            "due_date": str(o.due_date or ""),
            "total_length_m": float(o.ordered_qty_m or 0),
            "drum_length_m": float(o.drum_length_m or 0),
            "drum_count": o.drum_count or 0,
            "voltage": o.voltage or "",
            "order_status": o.order_status or "대기",
            "reason": "ERP 외주계획 지정",
        }
        for o in orders
    ]


@router.get("/stage1/{run_label}/ai-summary", summary="AI 배치 분석 요약")
def get_ai_summary(run_label: str, db: Session = Depends(get_db)):
    """run_label 전체 배치를 LLM으로 분석하여 핵심 인사이트를 반환한다.

    Returns:
        totalBatches, totalProductionM, riskCount, highlights, insights
    """
    from app.application.decisions.summarize_run import generate_batch_summary_sync

    return generate_batch_summary_sync(run_label, db)


@router.get("/stage1/{run_label}/export", summary="작업지시서 Excel 다운로드")
def export_stage1(run_label: str, db: Session = Depends(get_db)) -> StreamingResponse:
    """지정한 run_label의 production_batch 데이터를 Excel(.xlsx)로 변환하여 반환한다.

    파일명: 작업지시서_{run_label}.xlsx
    Content-Disposition: attachment (브라우저에서 다운로드 트리거)
    """
    try:
        output = export_plan(run_label, db)
    except ValueError as exc:
        # run_label에 해당하는 데이터가 없는 경우
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Excel 생성 실패: {exc}") from exc

    # RFC 5987 인코딩 없이 ASCII 파일명 사용; 한글은 Content-Disposition 파라미터 오염 방지를 위해
    # filename*=UTF-8''... 형식이 이상적이나 브라우저 호환성 이슈로 단순 ASCII 사용
    safe_label = run_label.replace(" ", "_")
    filename = f"production_plan_{safe_label}.xlsx"

    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/wip-template", summary="재공실사 Excel 템플릿 다운로드")
def download_wip_template() -> StreamingResponse:
    """드롭다운 validation이 포함된 재공실사 데이터 입력 템플릿을 반환한다."""
    from app.infrastructure.exporters.wip_template import generate_wip_template

    output = generate_wip_template()
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=wip_template.xlsx"},
    )
