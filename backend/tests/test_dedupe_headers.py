"""배치 중복 정리 — `deduplicate_group_headers` 테스트.

Why: create_batches / stage1 incremental / auto-split 복합 경로에서 같은
(batch_group, sales_order_id, sales_order_line, batch_seq) 튜플이 다중 insert
되는 현상이 관측됨. 본 테스트는 post-hoc 정리 함수가:
  1) sub-batch 중복 제거 (같은 키 튜플은 최소 batch_id 만 유지)
  2) 헤더(batch_seq=-1) 중복 제거 (한 batch_group 에 1개만 유지)
  3) 재집계 (total_length_m / drum_count / due_date / priority / duration 을
     남은 sub-batch 기준으로 재계산)
가 모두 올바로 수행되는지 검증한다.
"""

from datetime import date

from app.infrastructure.models.production_batch import ProductionBatch
from app.application.ingest import deduplicate_group_headers

RUN = "test-dedupe"


def _mk_header(
    batch_group: str,
    due: date,
    drum_count: int,
    total_len: float,
    drum_length_m: float = 9500.0,
    priority: int = 5,
    line_speed_mpm: float = 10.0,
) -> ProductionBatch:
    return ProductionBatch(
        run_label=RUN,
        batch_seq=-1,
        batch_group=batch_group,
        process_name="연선",
        sq_mm2=300,
        drum_count=drum_count,
        drum_length_m=drum_length_m,
        total_length_m=total_len,
        due_date=due,
        customer_priority=priority,
        line_speed_mpm=line_speed_mpm,
        estimated_duration_min=total_len / line_speed_mpm,
        voltage="0.6/1kV",
        conductor_material="CU",
        sales_order_id="SO-DUMMY",
        sales_order_line=0,
        status="planned",
    )


def _mk_sub(
    batch_group: str,
    so_id: str,
    line: int,
    due: date,
    length_m: float,
    seq: int = 1,
) -> ProductionBatch:
    return ProductionBatch(
        run_label=RUN,
        batch_seq=seq,
        batch_group=batch_group,
        process_name="연선",
        sq_mm2=300,
        drum_count=1,
        drum_length_m=length_m,
        total_length_m=length_m,
        due_date=due,
        customer_priority=5,
        voltage="0.6/1kV",
        conductor_material="CU",
        sales_order_id=so_id,
        sales_order_line=line,
        status="planned",
    )


def test_duplicate_sub_batches_are_removed(db):
    """동일 (batch_group, so, line, seq) 튜플 2회 insert → 1건만 유지."""
    bg = "ST-TEST-DEDUP-SUB"
    db.add(_mk_header(bg, date(2026, 5, 1), drum_count=1, total_len=9500.0))
    # 동일 sub 를 2번 삽입
    db.add(_mk_sub(bg, "SO-A", 1, date(2026, 5, 1), 500.0))
    db.add(_mk_sub(bg, "SO-A", 1, date(2026, 5, 1), 500.0))
    # 다른 sub 는 단일
    db.add(_mk_sub(bg, "SO-A", 2, date(2026, 5, 1), 700.0))
    db.flush()

    stats = deduplicate_group_headers(RUN, db)
    assert stats["non_header_deleted"] == 1, f"예상 sub 중복 1건 삭제: {stats}"

    remain = (
        db.query(ProductionBatch)
        .filter(
            ProductionBatch.run_label == RUN,
            ProductionBatch.batch_group == bg,
            ProductionBatch.batch_seq >= 1,
        )
        .all()
    )
    assert len(remain) == 2, "중복 제거 후 2건 남아야 함 (SO-A:1, SO-A:2)"
    keys = {(r.sales_order_id, r.sales_order_line) for r in remain}
    assert keys == {("SO-A", 1), ("SO-A", 2)}


def test_duplicate_headers_merged_and_recomputed(db):
    """같은 batch_group 에 헤더 2개 → canonical 1개만 남고 sub 기준으로 재집계."""
    bg = "ST-TEST-DEDUP-HDR"
    # 헤더 2개 (Canonical = earliest due)
    h_late = _mk_header(bg, date(2026, 5, 15), drum_count=1, total_len=9500.0)
    h_early = _mk_header(bg, date(2026, 5, 1), drum_count=3, total_len=28500.0)
    db.add(h_late)
    db.add(h_early)

    # 실제 수주 4건 (raw total 15,000m, lot 9500m 기준 ceil(15000/9500)=2틀, total=19000m)
    db.add(_mk_sub(bg, "SO-A", 1, date(2026, 5, 1), 5000.0))
    db.add(_mk_sub(bg, "SO-A", 2, date(2026, 5, 5), 4000.0))
    db.add(_mk_sub(bg, "SO-B", 1, date(2026, 5, 10), 3000.0))
    db.add(_mk_sub(bg, "SO-B", 2, date(2026, 5, 15), 3000.0))
    db.flush()

    stats = deduplicate_group_headers(RUN, db)
    assert stats["headers_deleted"] == 1
    assert stats["groups_rebalanced"] == 1

    remaining_headers = (
        db.query(ProductionBatch)
        .filter(
            ProductionBatch.run_label == RUN,
            ProductionBatch.batch_group == bg,
            ProductionBatch.batch_seq == -1,
        )
        .all()
    )
    assert len(remaining_headers) == 1, "헤더 1개만 남아야 함"

    canon = remaining_headers[0]
    # 재집계 확인: raw 15000m → ceil(15000/9500)=2틀 → total 19000m
    assert canon.drum_count == 2, f"drum_count 기대 2, 실제 {canon.drum_count}"
    assert abs(canon.total_length_m - 19000.0) < 0.5
    # 납기: 가장 이른 2026-05-01
    assert canon.due_date == date(2026, 5, 1)


def test_identical_duplicate_headers_merged_to_one(db):
    """완전 동일 헤더 2개 (같은 SO/drum/len/due) → 1개만 유지 + sub 재집계."""
    bg = "ST-TEST-DEDUP-IDENTICAL"
    h1 = _mk_header(bg, date(2026, 5, 1), drum_count=1, total_len=6800.0)
    h2 = _mk_header(bg, date(2026, 5, 1), drum_count=1, total_len=6800.0)
    db.add(h1)
    db.add(h2)
    db.add(_mk_sub(bg, "SO-X", 1, date(2026, 5, 1), 6800.0))
    db.flush()

    stats = deduplicate_group_headers(RUN, db)
    assert stats["headers_deleted"] == 1

    remain = (
        db.query(ProductionBatch)
        .filter(
            ProductionBatch.run_label == RUN,
            ProductionBatch.batch_group == bg,
            ProductionBatch.batch_seq == -1,
        )
        .all()
    )
    assert len(remain) == 1


def test_no_duplicates_is_noop(db):
    """중복 없는 정상 상태는 변경 없음 (통계 0, 헤더/서브 보존)."""
    bg = "ST-TEST-DEDUP-NOOP"
    db.add(_mk_header(bg, date(2026, 5, 1), drum_count=1, total_len=9500.0))
    db.add(_mk_sub(bg, "SO-A", 1, date(2026, 5, 1), 500.0))
    db.add(_mk_sub(bg, "SO-A", 2, date(2026, 5, 2), 700.0))
    db.flush()

    stats = deduplicate_group_headers(RUN, db)
    assert stats["non_header_deleted"] == 0
    assert stats["headers_deleted"] == 0
    # groups_rebalanced 는 재집계로 drum_count/total_len 변화 시 카운트됨.
    # 이 경우 raw=1200m → ceil/9500=1틀 → total=9500 (기존 헤더와 동일) → 0

    remain = db.query(ProductionBatch).filter(ProductionBatch.run_label == RUN).all()
    assert len(remain) == 3  # 헤더 1 + sub 2
