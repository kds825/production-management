"""S1 #4 — _group_sort_key 의 -sq tiebreak 회귀.

batch_group 이름이 "{proc}_{sq}SQ" 형식이라 stable sort 의 input 순서가
문자열 ('120SQ' < '300SQ' < '95SQ') 로 결정돼 같은 proc_order 내에서
SQ 역전이 발생했다. -sq 를 명시적 정렬 키로 추가한 뒤의 회귀.
"""

from __future__ import annotations

from app.application.scheduling.greedy._group_and_sort import _group_and_sort
from app.infrastructure.models.production_batch import ProductionBatch


def _make_batch(sq: int, batch_id: int) -> ProductionBatch:
    return ProductionBatch(
        batch_id=batch_id,
        run_label="test-s1-sort",
        sales_order_id=f"ORD-{batch_id}",
        process_name="저압절연",
        batch_seq=1,
        sq_mm2=sq,
        batch_group=f"저압절연_{sq}SQ",
        customer_priority=50,
    )


def test_group_processing_order_large_sq_first(db):
    """같은 process_order/EDD 내에서 큰 SQ 가 먼저 와야 함."""
    batches = [
        _make_batch(95, 1),
        _make_batch(120, 2),
        _make_batch(150, 3),
        _make_batch(300, 4),
    ]
    ordered, _ = _group_and_sort(batches, db)
    sqs = [int(gk.split("_")[1].rstrip("SQ")) for gk, _ in ordered]
    assert sqs == [300, 150, 120, 95], f"got {sqs}"


def test_reversed_input_same_order(db):
    """입력 순서에 무관하게 같은 결과 — stable sort 비의존성."""
    batches = [
        _make_batch(300, 4),
        _make_batch(150, 3),
        _make_batch(120, 2),
        _make_batch(95, 1),
    ]
    ordered, _ = _group_and_sort(batches, db)
    sqs = [int(gk.split("_")[1].rstrip("SQ")) for gk, _ in ordered]
    assert sqs == [300, 150, 120, 95], f"got {sqs}"
