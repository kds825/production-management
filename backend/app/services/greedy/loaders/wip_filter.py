"""WIP 재고로 대체 가능한 공정의 배치 스킵.

원래 ``optimization_loop._run_optimization_once`` 의 lines 127-150 블록.

핵심:
  - ``batch.wip_matched_id`` 가 있으면 ``WipInventory.process_stage`` 조회.
  - ``_WIP_SKIP_PROCESSES[process_stage]`` 의 set 에 batch.process_name 이 있으면
    해당 배치는 스킵 (재고로 충당) — ``status='wip_complete'`` 마킹.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.domain.constants import _WIP_SKIP_PROCESSES
from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.wip_inventory import WipInventory


def filter_wip_skippable(
    batches: list[ProductionBatch],
    db: Session,
) -> tuple[list[ProductionBatch], int]:
    """Returns ``(schedulable_batches, wip_skipped_count)``.

    ``status='wip_complete'`` 로 마킹된 배치는 schedulable 에서 제외된다 (in-place).
    """
    wip_ids = {b.wip_matched_id for b in batches if b.wip_matched_id is not None}
    wip_stage_map: dict[int, str] = {}
    if wip_ids:
        wips = db.query(WipInventory).filter(WipInventory.wip_id.in_(wip_ids)).all()
        wip_stage_map = {w.wip_id: w.process_stage or "" for w in wips}

    schedulable: list[ProductionBatch] = []
    wip_skipped = 0
    for batch in batches:
        if batch.wip_matched_id and batch.wip_matched_id in wip_stage_map:
            wip_stage = wip_stage_map[batch.wip_matched_id]
            skip_set = _WIP_SKIP_PROCESSES.get(wip_stage, set())
            if batch.process_name in skip_set:
                batch.status = "wip_complete"
                wip_skipped += 1
                continue
        schedulable.append(batch)
    return schedulable, wip_skipped
