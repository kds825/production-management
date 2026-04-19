"""긴급 수주 증분 반영 — 기존 스케줄을 최소한으로 건드리는 부분 재스케줄링

설계 원칙
─────────
1. EDD 기반 삽입: 긴급 수주의 납기보다 이른 기존 배치는 그대로 유지.
   납기가 더 늦은 배치들만 긴급 배치 삽입 이후 위치로 밀린다.

2. 동일 규격 합산 / 분할:
   - 같은 (공정, SQ, 납기주차) 배치가 존재하면 수량 합산 후
     detect_split_candidates 로 분할 여부 재검토.
   - 기존 배치와 납기 주차가 다르면 EDD 기준 적절한 위치에 새 배치 삽입.

3. 최소 파급: 영향 받는 (공정, SQ) 그룹의 배치만 삭제·재생성·재스케줄.
   나머지 배치의 ScheduleTask는 frozen timeline으로 보존.

4. 파이프라인 연쇄: 연선이 변경되면 절연→시스 순으로 downstream cascade.
   downstream도 동일한 merge/split 로직 적용.

공개 API
────────
apply_urgent_incremental(erp_content, run_label, db, *, gap_days=3) → dict
"""

from __future__ import annotations

import logging
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from app.domain.constants import PROCESS_ORDER
from app.infrastructure.models.item_master import ItemMaster
from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.sales_order import SalesOrder
from app.infrastructure.models.schedule_task import ScheduleTask
from app.services.batch_grouping import (
    create_batches,
    detect_split_candidates,
    execute_auto_splits,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# 공정 파이프라인: 연선 변경 → 절연 → 시스 순으로 cascade
_DOWNSTREAM: dict[str, list[str]] = {
    "연선":     ["저압절연", "고압절연"],
    "저압절연": ["연합", "T/P", "저압시스"],
    "고압절연": ["T/P", "고압시스"],
    "연합":     ["저압시스"],
    "T/P":      ["고압시스"],
}


# ──────────────────────────────────────────────────────────────────────────────
# 공개 진입점
# ──────────────────────────────────────────────────────────────────────────────

def apply_urgent_incremental(
    erp_content: bytes,
    run_label: str,
    db: Session,
    *,
    gap_days: int = 3,
) -> dict:
    """긴급 수주를 기존 run_label에 증분 반영한다.

    Returns:
        {
            "new_orders": int,
            "affected_sqs": list[int],
            "deleted_batch_count": int,
            "new_batch_count": int,
            "split_count": int,
            "reschedule_groups": list[str],
            "warnings": list[str],
        }
    """
    result: dict = {
        "new_orders": 0,
        "affected_sqs": [],
        "deleted_batch_count": 0,
        "new_batch_count": 0,
        "split_count": 0,
        "reschedule_groups": [],
        "warnings": [],
    }

    # ── 1. 긴급 수주 파싱 → SalesOrder 테이블 추가 ───────────────────────────
    from app.services.erp_parser import parse_erp_file_incremental

    before_order_keys = _get_order_keys(run_label, db)
    parse_result = parse_erp_file_incremental(erp_content, run_label, db)
    db.flush()
    after_order_keys = _get_order_keys(run_label, db)

    new_order_keys = after_order_keys - before_order_keys
    result["new_orders"] = len(new_order_keys)
    result["warnings"].extend(parse_result.get("warnings", []))

    if not new_order_keys:
        result["warnings"].append("새로 추가된 수주가 없습니다 (중복 또는 파일 이상).")
        return result

    logger.info("[UrgentScheduler] 신규 수주 %d건 파싱 완료", len(new_order_keys))

    # ── 2. 영향 SQ 그룹 식별 ─────────────────────────────────────────────────
    # 신규 수주의 item_code → ItemMaster.cross_section (SQ) 로 매핑
    new_orders: list[SalesOrder] = (
        db.query(SalesOrder)
        .filter(
            SalesOrder.run_label == run_label,
            SalesOrder.order_id.in_({k[0] for k in new_order_keys}),
        )
        .all()
    )
    # order_line까지 정확히 필터
    new_orders = [o for o in new_orders if (o.order_id, o.order_line) in new_order_keys]

    affected_sqs = _resolve_affected_sqs(new_orders, db)
    result["affected_sqs"] = sorted(affected_sqs)

    if not affected_sqs:
        result["warnings"].append("신규 수주의 SQ를 확인할 수 없습니다.")
        return result

    logger.info("[UrgentScheduler] 영향 SQ: %s", affected_sqs)

    # ── 3. frozen 배치 보호 목록 수집 ────────────────────────────────────────
    frozen_batches = (
        db.query(ProductionBatch)
        .filter(
            ProductionBatch.run_label == run_label,
            ProductionBatch.status.in_(["in_progress", "completed", "wip_complete"]),
        )
        .all()
    )
    frozen_order_keys: set[tuple[str, int]] = {
        (b.sales_order_id, b.sales_order_line)
        for b in frozen_batches
        if b.batch_seq is not None and b.batch_seq >= 1
    }
    frozen_batch_ids: set[int] = {b.batch_id for b in frozen_batches}

    # 같은 수주의 모든 공정 배치도 보호 (연선 in_progress → 절연/시스도 frozen)
    frozen_order_ids = {k[0] for k in frozen_order_keys}
    if frozen_order_ids:
        related = (
            db.query(ProductionBatch.batch_id)
            .filter(
                ProductionBatch.run_label == run_label,
                ProductionBatch.sales_order_id.in_(frozen_order_ids),
            )
            .all()
        )
        frozen_batch_ids |= {r.batch_id for r in related}

    # ── 4. 영향 SQ 그룹의 변경 가능 배치 삭제 ────────────────────────────────
    # 공정 순서 오름차순: 연선 → 절연 → 연합/T/P → 시스
    affected_processes = _collect_affected_processes(affected_sqs, run_label, db)
    deleted = _delete_mutable_batches(
        affected_processes, affected_sqs, run_label, frozen_batch_ids, db
    )
    result["deleted_batch_count"] = deleted
    db.flush()

    # ── 5. 영향 SQ 배치 재생성 ───────────────────────────────────────────────
    # 비영향 SQ의 기존 수주는 frozen_order_keys에 추가하여 재생성 방지
    non_affected_order_keys = _get_non_affected_order_keys(
        affected_sqs, run_label, db
    )
    all_frozen_for_create = frozen_order_keys | non_affected_order_keys

    batch_result = create_batches(
        run_label,
        db,
        frozen_order_keys=all_frozen_for_create if all_frozen_for_create else None,
    )
    result["new_batch_count"] = batch_result.get("total_batches", 0)
    result["warnings"].extend(batch_result.get("warnings", []))
    db.flush()

    # ── 6. 분할 검토 (기존 detect_split_candidates 재사용) ──────────────────
    try:
        auto_split_result = execute_auto_splits(run_label, db, gap_days=gap_days)
        result["split_count"] = auto_split_result.get("auto_split_count", 0)
    except Exception as exc:
        result["warnings"].append(f"자동 분할 실패 (계속 진행): {exc}")
        logger.warning("[UrgentScheduler] 자동 분할 실패: %s", exc)
    db.flush()

    # ── 7. 영향 그룹만 부분 재스케줄 ────────────────────────────────────────
    affected_batch_groups = _get_affected_batch_groups(affected_sqs, run_label, db)
    result["reschedule_groups"] = sorted(affected_batch_groups)

    if affected_batch_groups:
        from app.services.schedule_optimizer import reschedule_affected_groups
        try:
            sched_result = reschedule_affected_groups(
                run_label, db, affected_batch_groups
            )
            result["warnings"].extend(sched_result.get("warnings", []))
            logger.info(
                "[UrgentScheduler] 부분 재스케줄 완료: %d 그룹",
                len(affected_batch_groups),
            )
        except Exception as exc:
            result["warnings"].append(f"재스케줄 실패: {exc}")
            logger.error("[UrgentScheduler] 재스케줄 오류: %s", exc, exc_info=True)

    return result


# ──────────────────────────────────────────────────────────────────────────────
# 내부 헬퍼
# ──────────────────────────────────────────────────────────────────────────────

def _get_order_keys(run_label: str, db: Session) -> set[tuple[str, int]]:
    rows = (
        db.query(SalesOrder.order_id, SalesOrder.order_line)
        .filter(SalesOrder.run_label == run_label)
        .all()
    )
    return {(r.order_id, r.order_line) for r in rows}


def _resolve_affected_sqs(orders: list[SalesOrder], db: Session) -> set[int]:
    """수주 목록에서 cross_section(SQ) 값을 ItemMaster를 통해 해석."""
    item_codes = {o.item_code for o in orders if o.item_code}
    if not item_codes:
        return set()
    items = (
        db.query(ItemMaster)
        .filter(ItemMaster.item_code.in_(item_codes))
        .all()
    )
    item_map = {i.item_code: i for i in items}
    sqs: set[int] = set()
    for o in orders:
        item = item_map.get(o.item_code)
        if item and item.cross_section:
            sqs.add(int(item.cross_section))
    return sqs


def _collect_affected_processes(
    affected_sqs: set[int],
    run_label: str,
    db: Session,
) -> list[str]:
    """영향 SQ가 포함된 process_name 목록 (공정 순서 오름차순)."""
    rows = (
        db.query(ProductionBatch.process_name)
        .filter(
            ProductionBatch.run_label == run_label,
            ProductionBatch.sq_mm2.in_(affected_sqs),
        )
        .distinct()
        .all()
    )
    procs = {r.process_name for r in rows}
    return sorted(procs, key=lambda p: PROCESS_ORDER.get(p, 50))


def _delete_mutable_batches(
    processes: list[str],
    affected_sqs: set[int],
    run_label: str,
    frozen_batch_ids: set[int],
    db: Session,
) -> int:
    """frozen이 아닌 영향 배치와 해당 ScheduleTask를 삭제한다."""
    from app.infrastructure.models.audit_log import AuditLog
    from sqlalchemy import text

    target = (
        db.query(ProductionBatch.batch_id)
        .filter(
            ProductionBatch.run_label == run_label,
            ProductionBatch.process_name.in_(processes),
            ProductionBatch.sq_mm2.in_(affected_sqs),
        )
        .all()
    )
    target_ids = {r.batch_id for r in target} - frozen_batch_ids
    if not target_ids:
        return 0

    # FK 선해제
    db.query(ProductionBatch).filter(
        ProductionBatch.batch_id.in_(target_ids)
    ).update({"wip_matched_id": None}, synchronize_session=False)
    db.execute(
        text("UPDATE wip_inventory SET source_batch_id = NULL WHERE source_batch_id = ANY(:ids)"),
        {"ids": list(target_ids)},
    )

    # 자식 → 부모 순서로 삭제
    db.query(AuditLog).filter(AuditLog.batch_id.in_(target_ids)).delete(
        synchronize_session=False
    )
    db.query(ScheduleTask).filter(ScheduleTask.batch_id.in_(target_ids)).delete(
        synchronize_session=False
    )
    deleted = (
        db.query(ProductionBatch)
        .filter(ProductionBatch.batch_id.in_(target_ids))
        .delete(synchronize_session=False)
    )
    return deleted


def _get_non_affected_order_keys(
    affected_sqs: set[int],
    run_label: str,
    db: Session,
) -> set[tuple[str, int]]:
    """비영향 SQ 수주의 (order_id, order_line) — create_batches에서 재생성 방지."""
    rows = (
        db.query(SalesOrder.order_id, SalesOrder.order_line, ItemMaster.cross_section)
        .join(ItemMaster, SalesOrder.item_code == ItemMaster.item_code)
        .filter(SalesOrder.run_label == run_label)
        .all()
    )
    result: set[tuple[str, int]] = set()
    for r in rows:
        sq = int(r.cross_section) if r.cross_section else 0
        if sq not in affected_sqs:
            result.add((r.order_id, r.order_line))

    # item_code 없거나 ItemMaster 미등록 수주도 비영향으로 보호
    no_item_rows = (
        db.query(SalesOrder.order_id, SalesOrder.order_line)
        .filter(
            SalesOrder.run_label == run_label,
            SalesOrder.item_code.is_(None),
        )
        .all()
    )
    result |= {(r.order_id, r.order_line) for r in no_item_rows}
    return result


def _get_affected_batch_groups(
    affected_sqs: set[int],
    run_label: str,
    db: Session,
) -> set[str]:
    """새로 생성된 영향 SQ 배치들의 batch_group 목록."""
    rows = (
        db.query(ProductionBatch.batch_group)
        .filter(
            ProductionBatch.run_label == run_label,
            ProductionBatch.sq_mm2.in_(affected_sqs),
            ProductionBatch.batch_group.isnot(None),
        )
        .distinct()
        .all()
    )
    return {r.batch_group for r in rows}
