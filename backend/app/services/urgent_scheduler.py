"""긴급 수주 증분 반영 — 기존 스케줄을 최소한으로 건드리는 부분 재스케줄링

설계 원칙
─────────
1. 기존 배치 보존: 기존 배치를 삭제하지 않는다.
   긴급 수주 배치만 새로 생성하거나 기존 batch_group에 합산한다.

2. 합산(merge) 우선:
   - 긴급 수주의 (SQ, 전압, 연선방식)가 기존 비동결 연선 그룹과 일치하면
     해당 그룹 헤더 배치에 수량 합산 후 분할 후보 재검토.
   - 절연·시스는 헤더 배치가 없으므로 동일 batch_group에 배치 추가만 하면 됨.

3. 신규 그룹 생성:
   - 기존 비동결 그룹이 없으면 새 batch_group으로 생성 (일반 create_batches 경로).

4. 최소 파급: 수정·신규 batch_group 만 부분 재스케줄.
   비영향 그룹의 ScheduleTask는 frozen timeline으로 보존.

5. 파이프라인 연쇄: 연선 합산/분할 시 절연·시스 batch_group도 수정 대상 포함.

공개 API
────────
apply_urgent_incremental(erp_content, run_label, db, *, gap_days=3) → dict
"""

from __future__ import annotations

import logging
import math
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.sales_order import SalesOrder
from app.infrastructure.models.schedule_task import ScheduleTask

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_FROZEN_STATUSES = frozenset({"in_progress", "completed", "wip_complete"})


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

    기존 배치를 삭제하지 않고, 긴급 수주 배치만 생성하거나 기존 그룹에 합산한다.

    Returns:
        {
            "new_orders": int,
            "merged_groups": list[str],   # 기존 그룹에 합산된 batch_group
            "new_groups": list[str],      # 새로 생성된 batch_group
            "split_count": int,
            "rescheduled_groups": list[str],
            "warnings": list[str],
        }
    """
    result: dict = {
        "new_orders": 0,
        "merged_groups": [],
        "new_groups": [],
        "split_count": 0,
        "rescheduled_groups": [],
        "warnings": [],
    }

    # ── 1. 긴급 수주 파싱 ─────────────────────────────────────────────────────
    from app.services.erp_parser import parse_erp_file_incremental

    before_keys = _get_order_keys(run_label, db)
    parse_result = parse_erp_file_incremental(erp_content, run_label, db)
    db.flush()
    after_keys = _get_order_keys(run_label, db)

    new_order_keys = after_keys - before_keys
    result["new_orders"] = len(new_order_keys)
    result["warnings"].extend(parse_result.get("warnings", []))

    if not new_order_keys:
        result["warnings"].append("새로 추가된 수주가 없습니다 (중복 또는 파일 이상).")
        return result

    logger.info("[Urgent] 신규 수주 %d건 파싱 완료", len(new_order_keys))

    # ── 2. 긴급 수주만 대상으로 배치 생성 ───────────────────────────────────
    # frozen_order_keys = 기존 모든 수주 → create_batches가 신규 수주만 처리
    from app.services.batch_grouping import create_batches, execute_auto_splits

    batch_result = create_batches(
        run_label,
        db,
        frozen_order_keys=before_keys if before_keys else None,
    )
    db.flush()
    result["warnings"].extend(batch_result.get("warnings", []))

    # ── 3. 새로 생성된 배치 수집 ─────────────────────────────────────────────
    # new_order_keys에 속하는 수주의 배치만 신규 배치로 간주
    new_order_ids = {k[0] for k in new_order_keys}
    newly_created: list[ProductionBatch] = (
        db.query(ProductionBatch)
        .filter(
            ProductionBatch.run_label == run_label,
            ProductionBatch.sales_order_id.in_(new_order_ids),
        )
        .all()
    )
    # order_line까지 정확히 필터
    newly_created = [
        b for b in newly_created
        if (b.sales_order_id, b.sales_order_line) in new_order_keys
    ]

    if not newly_created:
        result["warnings"].append("신규 배치 생성 결과가 없습니다.")
        return result

    logger.info("[Urgent] 신규 배치 %d건 생성", len(newly_created))

    # ── 4. 연선 헤더 배치 중복 처리 ──────────────────────────────────────────
    # create_batches가 긴급 수주만의 수량으로 새 ST- 헤더를 생성했을 수 있음.
    # 동일 batch_group의 기존 비동결 헤더가 있으면 수량 합산 후 신규 헤더 삭제.
    merged_groups: set[str] = set()
    new_groups: set[str] = set()

    new_headers = [b for b in newly_created if b.batch_seq == -1]
    for new_hdr in new_headers:
        bg = new_hdr.batch_group
        if not bg:
            continue
        existing_hdr = _find_existing_header(run_label, bg, new_hdr.batch_id, db)
        if existing_hdr is not None and existing_hdr.status not in _FROZEN_STATUSES:
            # 합산: 기존 헤더에 신규 수량 더하기
            _merge_header(existing_hdr, new_hdr)
            db.delete(new_hdr)
            merged_groups.add(bg)
            logger.info("[Urgent] 헤더 합산: %s (drum_count=%d→%d)",
                        bg, existing_hdr.drum_count,
                        existing_hdr.drum_count)
        else:
            # 기존 헤더 없거나 frozen → 신규 그룹
            new_groups.add(bg)

    db.flush()

    # ── 5. 절연·시스 배치가 추가된 기존 그룹 수집 ────────────────────────────
    # create_batches는 절연·시스 배치를 기존과 동일한 batch_group 키에 배치함.
    # 헤더가 없으므로 중복 처리 불필요 — 단순히 affected 목록에 추가.
    non_header_new = [b for b in newly_created if b.batch_seq != -1]
    for b in non_header_new:
        bg = b.batch_group
        if not bg:
            continue
        if bg in merged_groups or bg in new_groups:
            continue
        # 이 batch_group에 기존 배치가 있으면 merge (기존 그룹에 추가된 것)
        existing_count = (
            db.query(ProductionBatch)
            .filter(
                ProductionBatch.run_label == run_label,
                ProductionBatch.batch_group == bg,
                ProductionBatch.batch_id != b.batch_id,
            )
            .count()
        )
        if existing_count > 0:
            merged_groups.add(bg)
        else:
            new_groups.add(bg)

    result["merged_groups"] = sorted(merged_groups)
    result["new_groups"] = sorted(new_groups)
    logger.info("[Urgent] 합산 그룹: %s | 신규 그룹: %s", merged_groups, new_groups)

    # ── 6. 분할 후보 검토 ────────────────────────────────────────────────────
    # 헤더 합산 후 drum_count가 커졌을 경우 자동 분할
    try:
        auto_split_result = execute_auto_splits(run_label, db, gap_days=gap_days)
        result["split_count"] = auto_split_result.get("auto_split_count", 0)
    except Exception as exc:
        result["warnings"].append(f"자동 분할 실패 (계속 진행): {exc}")
        logger.warning("[Urgent] 자동 분할 실패: %s", exc)
    db.flush()

    # 분할로 인해 새로 생긴 batch_group (_B, _C 등)도 affected에 포함
    affected_groups = merged_groups | new_groups
    affected_groups |= _collect_split_children(run_label, affected_groups, db)

    # ── 7. 영향 그룹만 부분 재스케줄 ────────────────────────────────────────
    if not affected_groups:
        result["warnings"].append("재스케줄 대상 batch_group이 없습니다.")
        return result

    result["rescheduled_groups"] = sorted(affected_groups)
    from app.services.schedule_optimizer import reschedule_affected_groups
    try:
        sched_result = reschedule_affected_groups(run_label, db, affected_groups)
        result["warnings"].extend(sched_result.get("warnings", []))
        logger.info("[Urgent] 부분 재스케줄 완료: %d 그룹", len(affected_groups))
    except Exception as exc:
        result["warnings"].append(f"재스케줄 실패: {exc}")
        logger.error("[Urgent] 재스케줄 오류: %s", exc, exc_info=True)

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


def _find_existing_header(
    run_label: str,
    batch_group: str,
    exclude_batch_id: int,
    db: Session,
) -> ProductionBatch | None:
    """동일 batch_group의 기존 헤더 배치(batch_seq=-1) 조회. 신규 헤더 자신은 제외."""
    return (
        db.query(ProductionBatch)
        .filter(
            ProductionBatch.run_label == run_label,
            ProductionBatch.batch_group == batch_group,
            ProductionBatch.batch_seq == -1,
            ProductionBatch.batch_id != exclude_batch_id,
        )
        .first()
    )


def _merge_header(existing: ProductionBatch, new_hdr: ProductionBatch) -> None:
    """신규 헤더의 수량을 기존 헤더에 합산하고 drum_count / estimated_duration_min 재계산."""
    add_qty = float(new_hdr.total_length_m or 0)
    existing.total_length_m = (float(existing.total_length_m or 0)) + add_qty

    lot_size = float(existing.drum_length_m or 0)
    if lot_size > 0:
        existing.drum_count = max(math.ceil(existing.total_length_m / lot_size), 1)

    line_speed = float(existing.line_speed_mpm or 0)
    if line_speed > 0:
        existing.estimated_duration_min = existing.total_length_m / line_speed

    # EDD를 더 이른 쪽으로 업데이트
    if new_hdr.due_date and (
        existing.due_date is None or new_hdr.due_date < existing.due_date
    ):
        existing.due_date = new_hdr.due_date


def _collect_split_children(
    run_label: str,
    parent_groups: set[str],
    db: Session,
) -> set[str]:
    """분할로 생성된 자식 batch_group(_B, _C, ...)을 수집한다."""
    if not parent_groups:
        return set()
    children: set[str] = set()
    for pg in parent_groups:
        rows = (
            db.query(ProductionBatch.batch_group)
            .filter(
                ProductionBatch.run_label == run_label,
                ProductionBatch.batch_group.like(f"{pg}_%"),
            )
            .distinct()
            .all()
        )
        for r in rows:
            if r.batch_group and r.batch_group != pg:
                children.add(r.batch_group)
    return children
