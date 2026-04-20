"""긴급 수주 증분 반영 — CP-SAT 전역 재최적화로 연결 (P4)

설계 원칙
─────────
1. 진행중/완료 작업만 보호: status in {in_progress, completed, wip_complete}
   + base_date 이전 'scheduled' 배치만 frozen. 나머지는 CP-SAT 가 자유변수로
   전체를 다시 풀어 납기 초과 최소화.

2. Merged 그룹 처리 (P4 변경):
   - 과거: ScheduleTask.start_datetime 유지 + end_datetime 만 연장 + cascade
     push 로 후속 작업 밀어냄.
   - 현재: 해당 트릭 제거. CP-SAT 가 전체 run 을 다시 풀어 merged 그룹도
     자유변수로 재배치. 색상 체인은 sheath_color_hard 로 hard constraint
     강제 (infeasible 시 soft 로 폴백).

3. 연선 헤더 중복:
   - create_batches 가 긴급 수주만의 수량으로 새 ST- 헤더를 생성.
   - 기존 비동결 헤더에 수량 합산 후 신규 헤더 삭제 (유지).

4. 자동 분할:
   - drum_count 증가 시 분할 필요 — CP-SAT 입력 전에 execute_auto_splits 수행.

공개 API
────────
apply_urgent_incremental(erp_content, run_label, db, *, gap_days=3) → dict
"""

from __future__ import annotations

import logging
import math
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

    Merged 그룹: 기존 ScheduleTask의 end_datetime 만 연장 (위치 보존).
    New 그룹: EDD 기준으로 기존 ScheduleTask 사이 빈 슬롯에 삽입.

    Returns:
        {
            "new_orders": int,
            "merged_groups": list[str],
            "new_groups": list[str],
            "extended_tasks": int,       # end_datetime 연장된 ScheduleTask 수
            "split_count": int,
            "rescheduled_groups": list[str],
            "warnings": list[str],
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


def _find_group_task(
    run_label: str,
    batch_group: str,
    db: Session,
) -> ScheduleTask | None:
    """batch_group의 대표 배치에 연결된 ScheduleTask 조회.

    ScheduleTask는 스케줄링 시 group_batches[0] (batch_seq 오름차순 첫 번째)의
    batch_id를 사용한다. 연선 그룹은 seq=-1 헤더가 대표; 절연·시스는 seq>=1 중 최솟값.
    group 내 모든 batch_id로 ScheduleTask를 찾아 반환한다.
    """
    group_batch_ids = [
        r.batch_id
        for r in db.query(ProductionBatch.batch_id)
        .filter(
            ProductionBatch.run_label == run_label,
            ProductionBatch.batch_group == batch_group,
        )
        .all()
    ]
    if not group_batch_ids:
        return None
    return (
        db.query(ScheduleTask)
        .filter(
            ScheduleTask.run_label == run_label,
            ScheduleTask.batch_id.in_(group_batch_ids),
        )
        .first()
    )


def _cascade_push_tasks(
    run_label: str,
    equipment_code: str,
    from_task_start: "datetime",
    new_boundary: "datetime",
    db: Session,
    warnings: list,
) -> int:
    """from_task_start 이후 시작하는 같은 설비의 ScheduleTask를 겹치지 않게 밀어낸다.

    new_boundary(= 연장된 배치의 새 end_datetime) 이전에 시작하는 후속 배치부터
    도미노처럼 순서대로 밀어낸다. frozen 배치에서 멈추고 경고를 남긴다.

    Returns: 밀어낸 ScheduleTask 수
    """
    from app.services.calendar_engine import calculate_end_datetime

    current_boundary = new_boundary
    pushed = 0

    # from_task_start 이후 시작하는 배치들을 start_datetime 오름차순으로 조회
    # (한 번에 전체 로드 후 in-memory 처리 → 루프 내 start_datetime 변경에 영향 없음)
    tasks_after = (
        db.query(ScheduleTask)
        .filter(
            ScheduleTask.run_label == run_label,
            ScheduleTask.equipment_code == equipment_code,
            ScheduleTask.start_datetime > from_task_start,
        )
        .order_by(ScheduleTask.start_datetime.asc())
        .all()
    )

    for task in tasks_after:
        # 이미 current_boundary 이후에 시작하면 겹침 없음 → 종료
        if task.start_datetime >= current_boundary:
            break

        # frozen 배치는 밀어낼 수 없음
        batch = (
            db.query(ProductionBatch)
            .filter(ProductionBatch.batch_id == task.batch_id)
            .first()
        )
        if batch and batch.status in _FROZEN_STATUSES:
            warnings.append(
                f"[{equipment_code}] frozen 배치({batch.batch_group or '?'}) 겹침 — "
                "수동 조정 필요"
            )
            break

        # 총 작업시간(분) 계산 후 밀어냄
        total_min = _compute_task_total_min(task, batch, run_label, db)
        old_start = task.start_datetime
        task.start_datetime = current_boundary
        task.end_datetime = calculate_end_datetime(
            current_boundary, total_min, db, equipment_code
        )
        current_boundary = task.end_datetime
        pushed += 1

        bg_label = (batch.batch_group or "?") if batch else "?"
        logger.info(
            "[Urgent cascade] %s %s: %s → %s",
            equipment_code,
            bg_label,
            old_start.strftime("%m/%d %H:%M"),
            task.start_datetime.strftime("%m/%d %H:%M"),
        )

    return pushed


def _compute_task_total_min(
    task: ScheduleTask,
    batch: ProductionBatch | None,
    run_label: str,
    db: Session,
) -> float:
    """ScheduleTask의 총 작업시간(분) = setup + work. batch_group 데이터 기준 재산출."""
    setup_min = float(task.setup_time_min or 0)

    if batch and batch.batch_group:
        all_batches = (
            db.query(ProductionBatch)
            .filter(
                ProductionBatch.run_label == run_label,
                ProductionBatch.batch_group == batch.batch_group,
            )
            .all()
        )
        header = next((b for b in all_batches if b.batch_seq == -1), None)
        if header is not None:
            work_min = float(header.estimated_duration_min or 0)
        else:
            work_min = sum(
                float(b.estimated_duration_min or 0)
                for b in all_batches
                if b.batch_seq != -1
            )
    else:
        # 배치 정보 없음: wall-clock 길이로 근사 (setup 제외)
        delta_sec = (task.end_datetime - task.start_datetime).total_seconds()
        work_min = max(delta_sec / 60.0 - setup_min, 0)

    return setup_min + work_min


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
