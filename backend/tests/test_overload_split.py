"""Overload-driven auto-split 테스트 (2026-04-18 추가).

PDF "1안 수정 (가용용량 대응)" 의 "95SQ 5틀 → 3틀변경 + 2틀" 분할 패턴을
자동화한 trigger 검증. 단일 대형 배치가 설비 가용시간을 초과하여 납기가
불가능할 때 drum_count 절반 지점으로 분할 추천되는지 확인.

핵심 검증:
1. `detect_split_candidates` 가 overload 를 flag
2. `execute_auto_splits` 가 overload 케이스에 ceil(N/2) 지점 균형 분할 사용
3. 기존 "긴급 수주 후순위 드럼" 경로 (urgent only) 는 기존 1+rest 동작 유지
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.infrastructure.models.drum_lot_master import DrumLotMaster
from app.infrastructure.models.production_batch import ProductionBatch
from app.services.batch_grouping import (
    detect_split_candidates,
    execute_auto_splits,
)


_RUN_LABEL_OVERLOAD = "TEST_OVERLOAD_SPLIT_20260418"
_RUN_LABEL_URGENT = "TEST_URGENT_SPLIT_20260418"


def _cleanup_run_label(db: Session, run_label: str) -> None:
    """execute_auto_splits 내부 commit 때문에 rollback 으로 안 지워지는 행 정리.

    module 간 격리 보장용 — 이전 실행 잔여물이 어느 test 에도 영향 없도록.
    """
    db.query(ProductionBatch).filter(ProductionBatch.run_label == run_label).delete(
        synchronize_session=False
    )
    db.commit()


def _make_overload_fixture(db: Session, run_label: str) -> None:
    """ST-54BO1 95SQ 5틀 — 납기가 2일 후라 물리적으로 불가능한 fixture."""
    # drum_lot_master 가 없으면 생성
    existing = db.query(DrumLotMaster).filter(DrumLotMaster.cross_section == 95).first()
    if not existing:
        db.add(DrumLotMaster(cross_section=95, lot_stranding=9700, lot_sheath=9700))
        db.flush()

    today = date.today()
    # 3일 후 (월요일 포함) 납기 — available 실가동 ≤ 44h 로 72.6h 요구보다 작게.
    # calendar-aware 로직 하에서도 overload 판정 보장.
    due = today + timedelta(days=3)

    # 헤더 (batch_seq=-1)
    header = ProductionBatch(
        run_label=run_label,
        sales_order_id=None,
        sales_order_line=None,
        item_code=None,
        process_name="연선",
        batch_seq=-1,
        drum_count=5,
        drum_length_m=9700,
        total_length_m=48500,
        sq_mm2=95,
        core_count=1,
        equipment_code="ST-54BO1",
        due_date=due,
        customer_priority=5,
        line_speed_mpm=11.7,
        setup_time_min=210,
        estimated_duration_min=48500 / 11.7 + 210,  # ≈ 4355분 ≈ 72.6h
        status="planned",
        batch_group=f"ST-95-0.6/1kV-{run_label}",
    )
    db.add(header)

    # 수주별 배치 (batch_seq>=1) — 5개 드럼 각각 별도 수주로 가정
    for i in range(1, 6):
        db.add(
            ProductionBatch(
                run_label=run_label,
                sales_order_id=f"SO-OVL-{i:03d}",
                sales_order_line=1,
                item_code=None,
                process_name="연선",
                batch_seq=i,
                drum_count=1,
                drum_length_m=9700,
                total_length_m=9700,
                sq_mm2=95,
                core_count=1,
                equipment_code="ST-54BO1",
                # 납기는 첫 수주부터 순차적으로 (gap<3일)
                due_date=due,
                customer_priority=5,
                status="planned",
                batch_group=f"ST-95-0.6/1kV-{run_label}",
            )
        )
    db.flush()


def _make_urgent_only_fixture(db: Session, run_label: str) -> None:
    """3틀 그룹, 납기 여유 있고 (not overload), 2번째/3번째 드럼에 긴급 수주."""
    existing = (
        db.query(DrumLotMaster).filter(DrumLotMaster.cross_section == 120).first()
    )
    if not existing:
        db.add(DrumLotMaster(cross_section=120, lot_stranding=7100, lot_sheath=7100))
        db.flush()

    today = date.today()
    # 모든 드럼 납기가 충분히 여유 — overload 판정 안 됨
    # non-urgent 가 먼저 오고 (drum 1) urgent 는 gap_days>=3 뒤 (drum 2,3) 에 위치
    # sort 기준 (due asc) 유지하며 merge 후에도 later drum 에 urgent 가 남도록.
    non_urgent_due = today + timedelta(days=15)  # 우선, 긴급 임계 넘음
    urgent_due = today + timedelta(days=20)  # non-urgent 대비 +5일 gap

    header = ProductionBatch(
        run_label=run_label,
        item_code=None,
        process_name="연선",
        batch_seq=-1,
        drum_count=3,
        drum_length_m=7100,
        total_length_m=21300,
        sq_mm2=120,
        core_count=1,
        equipment_code="ST-54BO1",
        due_date=urgent_due,
        customer_priority=5,
        line_speed_mpm=11.7,
        setup_time_min=210,
        estimated_duration_min=21300 / 11.7 + 210,  # ≈ 32h, available ~400h
        status="planned",
        batch_group=f"ST-120-0.6/1kV-{run_label}",
    )
    db.add(header)

    # 3 수주: 1st non-urgent (priority 20, +15일), 2nd~3rd 우선순위 긴급 (priority 5, +20일)
    # → merge 후 drum[0]=non-urgent, drum[1]=urgent. has_urgent_in_later_drum=True.
    orders = [
        ("SO-URG-001", non_urgent_due, 20),
        ("SO-URG-002", urgent_due, 5),
        ("SO-URG-003", urgent_due, 5),
    ]
    for i, (oid, due, prio) in enumerate(orders, start=1):
        db.add(
            ProductionBatch(
                run_label=run_label,
                sales_order_id=oid,
                sales_order_line=1,
                item_code=None,
                process_name="연선",
                batch_seq=i,
                drum_count=1,
                drum_length_m=7100,
                total_length_m=7100,
                sq_mm2=120,
                core_count=1,
                equipment_code="ST-54BO1",
                due_date=due,
                customer_priority=prio,
                status="planned",
                batch_group=f"ST-120-0.6/1kV-{run_label}",
            )
        )
    db.flush()


def test_detect_overload_flag(db: Session) -> None:
    """단일 대형 배치 + 납기 불가능 조합이 is_overload=True 로 감지."""
    _cleanup_run_label(db, _RUN_LABEL_OVERLOAD)
    _make_overload_fixture(db, _RUN_LABEL_OVERLOAD)
    candidates = detect_split_candidates(_RUN_LABEL_OVERLOAD, db)
    assert len(candidates) == 1, f"overload 그룹 감지 실패: {candidates}"
    c = candidates[0]
    assert c["is_overload"] is True, f"is_overload 플래그 안 붙음: {c}"
    assert c["auto_split_recommended"] is True
    assert "과부하" in c["urgency_reason"]


def test_execute_overload_split_uses_balanced_boundary(db: Session) -> None:
    """overload 케이스: 5틀 → 3+2 (앞쪽이 더 많음). balanced 분할."""
    _cleanup_run_label(db, _RUN_LABEL_OVERLOAD)
    _make_overload_fixture(db, _RUN_LABEL_OVERLOAD)
    result = execute_auto_splits(_RUN_LABEL_OVERLOAD, db)

    assert result["auto_split_count"] == 1, f"분할 안 일어남: {result}"

    # 분할 후 동일 run_label 내 헤더들 drum_count 분포 확인
    headers = (
        db.query(ProductionBatch)
        .filter(
            ProductionBatch.run_label == _RUN_LABEL_OVERLOAD,
            ProductionBatch.batch_seq == -1,
        )
        .all()
    )
    drum_counts = sorted([h.drum_count or 0 for h in headers])
    # ceil(5/2)=3 → 앞 3틀, 뒤 2틀 이동
    assert drum_counts == [2, 3], f"예상 drum_count [2,3], 실제: {drum_counts}"


def test_execute_urgent_only_uses_legacy_boundary(db: Session) -> None:
    """기존 긴급-only 경로는 여전히 1+rest (균형 분할 변경 영향 없음)."""
    _cleanup_run_label(db, _RUN_LABEL_URGENT)
    _make_urgent_only_fixture(db, _RUN_LABEL_URGENT)

    # 먼저 overload 아님을 확인
    candidates = detect_split_candidates(_RUN_LABEL_URGENT, db)
    assert len(candidates) == 1
    c = candidates[0]
    assert c["is_overload"] is False, (
        f"urgent-only 픽스처가 overload 로 잘못 감지됨: {c}"
    )
    assert c["has_urgent_in_later_drum"] is True
    assert c["auto_split_recommended"] is True

    result = execute_auto_splits(_RUN_LABEL_URGENT, db)
    assert result["auto_split_count"] == 1

    headers = (
        db.query(ProductionBatch)
        .filter(
            ProductionBatch.run_label == _RUN_LABEL_URGENT,
            ProductionBatch.batch_seq == -1,
        )
        .all()
    )
    # 기존 동작: 앞 1틀 남고 뒤 2틀 이동 (3 drums → 1+2)
    drum_counts = sorted([h.drum_count or 0 for h in headers])
    assert drum_counts == [1, 2], f"기존 1+rest 경로 깨짐, drum_counts={drum_counts}"
