"""Planned ``ProductionBatch`` rows 로딩 + 공정 순서 정렬.

원래 ``optimization_loop._run_optimization_once`` 의 lines 97-121 블록.

핵심:
  - ``run_label`` + ``status='planned'`` 필터링.
  - 1차 정렬 (DB-side): due → priority → batch_seq.
  - 2차 정렬 (Python-side): PROCESS_ORDER 우선, batch_seq, due, priority, sq desc.
    "공정 순서가 최우선" 도메인 룰 (연선→절연→시스 파이프라인).
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.domain.constants import PROCESS_ORDER
from app.infrastructure.models.production_batch import ProductionBatch


def load_planned_batches(run_label: str, db: Session) -> list[ProductionBatch]:
    """run_label 의 planned 배치를 공정-우선 순으로 정렬해 반환."""
    batches = (
        db.query(ProductionBatch)
        .filter(
            ProductionBatch.run_label == run_label,
            ProductionBatch.status == "planned",
        )
        .order_by(
            ProductionBatch.due_date.asc(),
            ProductionBatch.customer_priority.asc(),
            ProductionBatch.batch_seq.asc(),
        )
        .all()
    )
    # Python 레벨 재정렬: batch_seq 는 라우팅 내 공정 순서이지만, 같은 그룹의
    # 연선이 절연보다 먼저 스케줄링되어야 predecessor_map 이 올바르게 동작.
    batches.sort(
        key=lambda b: (
            PROCESS_ORDER.get(b.process_name, 50),
            b.batch_seq or 0,  # 61연선 코어(seq=0)가 메인(seq=1)보다 먼저
            b.due_date or date.max,
            b.customer_priority or 99,
            -(float(b.sq_mm2 or 0)),
        )
    )
    return batches
