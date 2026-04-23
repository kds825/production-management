"""긴급 수주 증분 반영 — CP-SAT 전역 재최적화로 연결 (P4 이후)

설계 원칙
─────────
1. 보호 대상 최소화:
   - status in {in_progress, completed, wip_complete} 배치는 hard frozen.
   - base_date 이전 start_datetime 의 'scheduled' 배치도 hard frozen
     (이미 생산 시작 가능성이 있으므로).
   - 그 외 나머지 배치는 전부 CP-SAT 자유변수 → 납기 초과 최소화 목적으로
     전역 재배치.

2. Merged 그룹 처리:
   - 기존 배치 헤더에 긴급 수주 수량을 합산 (total_length_m, drum_count,
     estimated_duration_min, due_date 갱신) 하고 신규 임시 헤더는 삭제.
   - 위치/시간 조정 트릭은 사용하지 않는다. CP-SAT 가 run 전체를 다시 풀어
     merged 그룹도 자유변수로 재배치한다. 색상 체인은 sheath_color_hard 로
     강제 (infeasible 시 soft 로 강등 후 재시도, 그래도 실패 시 greedy 폴백).

3. 연선 헤더 중복:
   - create_batches 가 긴급 수주만의 수량으로 새 ST- 헤더를 생성한다.
   - 기존 비-frozen 헤더에 수량 합산 후 신규 임시 헤더 삭제.

4. 자동 분할:
   - drum_count 증가 시 분할이 필요할 수 있음 — CP-SAT 입력 전에
     execute_auto_splits 수행하여 자식 배치를 선생성.

5. 스냅샷 (P6):
   - 파싱 직전 snapshot_before, 재최적화 후 snapshot_after 를 캡처하고
     ScheduleChangeSet(kind='urgent') 로 저장한다. revert / diff API 의 근거.
   - INSERT 실패 시 snapshot_persisted=False 로 호출자에게 보고
     (apply 자체는 성공 유지).

공개 API
────────
apply_urgent_incremental(erp_content, run_label, db, *, gap_days=3) → dict
"""

from __future__ import annotations

import logging
import math
import uuid

from sqlalchemy.orm import Session

from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.sales_order import SalesOrder
from app.infrastructure.models.schedule_change_set import ScheduleChangeSet
from app.infrastructure.models.schedule_task import ScheduleTask

logger = logging.getLogger(__name__)

_FROZEN_STATUSES = frozenset({"in_progress", "completed", "wip_complete"})


def _build_snapshot(run_label: str, db: Session) -> dict:
    """run_label 에 속한 모든 ScheduleTask 를 JSONB 저장용 dict 로 직렬화.

    구조: {task_id_str: {start, end, equipment_code}}
    batch_group / process / color 같은 메타는 diff API 에서 join 으로 보강.

    P6: urgent apply 의 before/after 스냅샷 캡처용. read-only 쿼리로 트랜잭션
    오염 없음 — CP-SAT 실패/DB 커밋 실패에도 스냅샷 구축은 안전.
    """
    tasks = db.query(ScheduleTask).filter(ScheduleTask.run_label == run_label).all()
    return {
        str(t.task_id): {
            "start": t.start_datetime.isoformat() if t.start_datetime else None,
            "end": t.end_datetime.isoformat() if t.end_datetime else None,
            "equipment_code": t.equipment_code,
        }
        for t in tasks
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
    """긴급 수주를 기존 run_label 에 증분 반영한다.

    동작 (P4 이후):
      1. snapshot_before 캡처 → ERP 파싱 → 신규 배치 생성.
      2. 기존 비-frozen 헤더에 수량 합산 + 임시 헤더 삭제 (merged).
      3. drum_count 증가 시 execute_auto_splits 로 분할 배치 선생성.
      4. reschedule_affected_groups(use_cpsat=True) 로 CP-SAT 전역 재최적화.
         frozen 은 진행중/완료 + base_date 이전 scheduled 만 유지되고 나머지는
         모두 자유변수로 재배치된다.
      5. snapshot_after 캡처 + ScheduleChangeSet(kind='urgent') INSERT.
         (INSERT 실패 시 snapshot_persisted=False, 나머지는 성공으로 보고.)

    Returns:
        {
            "new_orders": int,
            "merged_groups": list[str],
            "new_groups": list[str],
            "extended_tasks": int,       # 항상 0 — 하위 호환 유지용 shape.
            "split_count": int,
            "rescheduled_groups": list[str],
            "warnings": list[str],
            "change_set_id": str | None,       # INSERT 성공 시 uuid, 실패/변화없음 None.
            "snapshot_count_before": int,
            "snapshot_count_after": int,
            "snapshot_persisted": bool,        # ScheduleChangeSet INSERT 성공 여부.
        }
    """
    result: dict = {
        "new_orders": 0,
        "merged_groups": [],
        "new_groups": [],
        "extended_tasks": 0,
        "split_count": 0,
        "rescheduled_groups": [],
        "warnings": [],
        # P6: before/after 스냅샷 캡처 + ScheduleChangeSet INSERT 결과.
        # 실질적 변경 없음(new_orders=0) 또는 snapshot INSERT 실패 시 None.
        "change_set_id": None,
        "snapshot_count_before": 0,
        "snapshot_count_after": 0,
        # 스냅샷 INSERT 성공 여부를 호출자가 명시적으로 감지할 수 있는 플래그.
        # 왜 분리? change_set_id 는 '실질 변경 없음' 에도 None → 실패와 구분 불가.
        # snapshot_persisted=False + new_orders>0 조합이 '스케줄은 바뀌었는데
        # revert/diff 는 불가' 한 경고 상태.
        "snapshot_persisted": False,
    }

    # ── 0. ERP 파싱 직전 snapshot_before 캡처 (P6) ──────────────────────────
    # 파싱/배치생성/CP-SAT 는 모두 ScheduleTask 를 수정할 수 있으므로 가장 이른
    # 시점에 read-only 로 현 상태를 저장한다. 트랜잭션 오염 없음.
    snapshot_before = _build_snapshot(run_label, db)
    result["snapshot_count_before"] = len(snapshot_before)

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
        # P6: 실질적 변경이 없으므로 ScheduleChangeSet 도 만들지 않는다.
        # change_set_id 는 기본값 None 유지.
        result["warnings"].append("새로 추가된 수주가 없습니다 (중복 또는 파일 이상).")
        return result

    logger.info("[Urgent] 신규 수주 %d건 파싱 완료", len(new_order_keys))

    # ── 2. 긴급 수주만 배치 생성 ─────────────────────────────────────────────
    from app.services.batch_grouping import create_batches, execute_auto_splits

    batch_result = create_batches(
        run_label,
        db,
        frozen_order_keys=before_keys if before_keys else None,
    )
    db.flush()
    result["warnings"].extend(batch_result.get("warnings", []))

    # ── 3. 신규 배치 수집 ────────────────────────────────────────────────────
    new_order_ids = {k[0] for k in new_order_keys}
    newly_created: list[ProductionBatch] = (
        db.query(ProductionBatch)
        .filter(
            ProductionBatch.run_label == run_label,
            ProductionBatch.sales_order_id.in_(new_order_ids),
        )
        .all()
    )
    newly_created = [
        b
        for b in newly_created
        if (b.sales_order_id, b.sales_order_line) in new_order_keys
    ]

    if not newly_created:
        result["warnings"].append("신규 배치 생성 결과가 없습니다.")
        return result

    # ── 4. 연선 헤더 배치 중복 처리 (기존 헤더에 합산 후 신규 헤더 삭제) ─────
    merged_groups: set[str] = set()  # 기존 그룹에 합산된 batch_group
    new_groups: set[str] = set()  # 새로 생성된 batch_group

    new_headers = [b for b in newly_created if b.batch_seq == -1]
    for new_hdr in new_headers:
        bg = new_hdr.batch_group
        if not bg:
            continue
        existing_hdr = _find_existing_header(run_label, bg, new_hdr.batch_id, db)
        if existing_hdr is not None and existing_hdr.status not in _FROZEN_STATUSES:
            _merge_header(existing_hdr, new_hdr)
            db.delete(new_hdr)
            merged_groups.add(bg)
        else:
            new_groups.add(bg)

    db.flush()

    # ── 5. 절연·시스 등 비헤더 배치의 그룹 분류 ─────────────────────────────
    non_header_new = [b for b in newly_created if b.batch_seq != -1]
    for b in non_header_new:
        bg = b.batch_group
        if not bg or bg in merged_groups or bg in new_groups:
            continue
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
    logger.info(
        "[Urgent] 합산 그룹: %d개 | 신규 그룹: %d개",
        len(merged_groups),
        len(new_groups),
    )

    # ── 6. Merged 그룹 처리 (P4 변경) ────────────────────────────────────────
    # 과거: ScheduleTask.start_datetime 유지 + end_datetime 만 연장 + cascade
    #       push 로 후속 작업 밀어냄.
    # 현재: 해당 트릭 제거. CP-SAT 가 전체 run 을 자유변수로 다시 풀어 merged
    #       그룹도 재배치. 따라서 여기서는 신규 merged 배치의 status 만 리셋
    #       (현재 planned 상태 유지 → 8단계의 CP-SAT 재최적화 대상).
    # extended_tasks 는 항상 0 (API shape 하위 호환 목적 유지).
    #
    # 7단계의 execute_auto_splits 가 분할을 만들고, 8단계의 CP-SAT 전역 재최적화
    # 가 merged + new + 분할 자식 + 기존 non-frozen 을 모두 자유변수로 푼다.
    result["extended_tasks"] = 0
    unscheduled_merged: set[str] = set()  # merged 전체를 CP-SAT 재최적화 대상으로

    # ── 7. 자동 분할 검토 ────────────────────────────────────────────────────
    # merged 그룹의 drum_count 증가 시 분할이 필요할 수 있음
    try:
        auto_split_result = execute_auto_splits(run_label, db, gap_days=gap_days)
        result["split_count"] = auto_split_result.get("auto_split_count", 0)
    except Exception as exc:
        result["warnings"].append(f"자동 분할 실패 (계속 진행): {exc}")
        logger.warning("[Urgent] 자동 분할 실패: %s", exc)
    db.flush()

    # ── 8. New 그룹 + 미스케줄 Merged 그룹: EDD 기반 빈 슬롯 삽입 ────────────
    to_reschedule = new_groups | unscheduled_merged
    # 분할로 생성된 자식 그룹도 포함
    to_reschedule |= _collect_split_children(
        run_label, to_reschedule | merged_groups, db
    )

    if to_reschedule:
        result["rescheduled_groups"] = sorted(to_reschedule)
        from app.services.schedule_optimizer import reschedule_affected_groups

        try:
            # P4: use_cpsat=True → CP-SAT 전역 재최적화. merged 그룹의 start 유지
            # 트릭을 제거했으므로 단순 slot insertion 으로는 merged 를 옮길 수
            # 없다. frozen 보호 (진행중/완료 + base_date 이전 scheduled) 하에서
            # 전체를 자유변수로 재최적화.
            sched_result = reschedule_affected_groups(
                run_label, db, to_reschedule, use_cpsat=True
            )
            result["warnings"].extend(sched_result.get("warnings", []))
            logger.info("[Urgent] 신규 그룹 스케줄 완료: %d 그룹", len(to_reschedule))
        except Exception as exc:
            result["warnings"].append(f"신규 그룹 스케줄 실패: {exc}")
            logger.error("[Urgent] 신규 그룹 스케줄 오류: %s", exc, exc_info=True)

    db.flush()

    # ── 9. 재최적화 후 snapshot_after 캡처 + ScheduleChangeSet INSERT (P6) ──
    # snapshot INSERT 실패가 urgent apply 자체를 롤백시키지 않도록 방어적 처리.
    # 스케줄 변경은 이미 db.flush() 로 세션에 반영된 상태 — INSERT 실패 시
    # ERROR 로깅 + snapshot_persisted=False 플래그로 호출자에게 전달.
    # exception 은 여전히 swallow (urgent apply 자체는 성공 유지) 하되,
    # 호출자가 감지할 수 있도록 result 필드로 노출.
    try:
        snapshot_after = _build_snapshot(run_label, db)
        result["snapshot_count_after"] = len(snapshot_after)

        change_set_id = str(uuid.uuid4())
        cs = ScheduleChangeSet(
            change_set_id=change_set_id,
            kind="urgent",
            snapshot_before=snapshot_before,
            snapshot_after=snapshot_after,
        )
        db.add(cs)
        db.flush()
        result["change_set_id"] = change_set_id
        result["snapshot_persisted"] = True
        logger.info(
            "[Urgent] ScheduleChangeSet INSERT 완료: %s (before=%d, after=%d)",
            change_set_id,
            len(snapshot_before),
            len(snapshot_after),
        )
    except Exception as exc:
        # snapshot INSERT 실패 — 스케줄은 이미 변경됐지만 revert/diff 기록이 없다.
        # ERROR 레벨로 로깅하여 운영에서 즉각 감지 가능하도록 한다.
        # change_set_id=None + snapshot_persisted=False 로 호출자 (route) 가
        # "이 apply 는 롤백 불가" 임을 인지하고 UI 경고를 낼 수 있다.
        logger.error(
            "[Urgent] ScheduleChangeSet INSERT 실패 — revert/diff 불가: %s",
            exc,
            exc_info=True,
        )
        result["change_set_id"] = None
        result["snapshot_persisted"] = False
        result["warnings"].append(
            f"스냅샷 저장 실패: {type(exc).__name__} — "
            "롤백/diff 기능이 이 apply 에는 적용 안 됨"
        )

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
    existing.total_length_m = float(existing.total_length_m or 0) + add_qty

    lot_size = float(existing.drum_length_m or 0)
    if lot_size > 0:
        existing.drum_count = max(math.ceil(existing.total_length_m / lot_size), 1)

    line_speed = float(existing.line_speed_mpm or 0)
    if line_speed > 0:
        existing.estimated_duration_min = existing.total_length_m / line_speed

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
