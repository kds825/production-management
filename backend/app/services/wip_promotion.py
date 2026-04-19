"""T2 승격 공통 헬퍼 — 배치 completed 전환 시 WIP 예상 → 실적_추정 승격.

설계서: docs/specs/2026-04-18-wip-lifecycle-design.md §10.
Task 9. 3 endpoint 통합은 Task 10 에서.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.infrastructure.models.wip_inventory import WipInventory


def _promote_expected_to_estimated(batch_id: int, new_status: str, db: Session) -> bool:
    """배치 status 가 completed 로 전환될 때 WIP 를 예상 → 실적_추정 승격.

    Returns:
        True: 승격 실행됨
        False: no-op (completed 아님 / 연결 WIP 없음 / 이미 승격 후 상태)

    규칙:
      - `completed` 로의 전환에만 반응 (wip_complete, in_progress, scheduled, planned 무시)
      - 멱등: 이미 실적_추정 / 실사_확정 / 사용완료 이면 no-op
      - MES 없음 가정 → actual_length_m = expected_length_m
    """
    if new_status != "completed":
        return False

    wip = (
        db.query(WipInventory)
        .filter(
            WipInventory.source_batch_id == batch_id,
            WipInventory.status == "예상",
        )
        .first()
    )
    if not wip:
        return False

    wip.status = "실적_추정"
    if wip.actual_length_m is None:
        wip.actual_length_m = wip.expected_length_m
    # autoflush=False 세션에서도 멱등성 보장: flush 로 DB 반영 후 반환.
    # 연속 호출 시 두 번째 query 가 DB 에서 "예상" 행을 찾지 못해 no-op 됨.
    db.flush()
    return True
