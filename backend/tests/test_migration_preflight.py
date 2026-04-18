"""Test UNIQUE index preflight — abort on duplicate source_batch_id.

Why real-DB (not SQLite):
- PostgreSQL partial index (WHERE source_batch_id IS NOT NULL) 는 SQLite 미지원.
- conftest.py 의 db 픽스처가 Supabase SessionLocal 을 사용하므로 동일 전략 유지.
- 각 테스트는 트랜잭션 내에서 INSERT → 함수 호출 → rollback 으로 격리.

Why importlib.util:
- alembic/versions/ 에 __init__.py 가 없어 일반 패키지 import 불가.
- importlib 으로 파일 경로 직접 로드해 _preflight_check / _rename_status 를 추출.
"""

import importlib.util
import pathlib
import pytest

from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.wip_inventory import WipInventory

# alembic/versions/ 에 __init__.py 없으므로 importlib 으로 직접 로드
_MIG_PATH = (
    pathlib.Path(__file__).parent.parent
    / "alembic"
    / "versions"
    / "708591555e9b_wip_unique_index_status_rename.py"
)
_spec = importlib.util.spec_from_file_location("mig_708591555e9b", _MIG_PATH)
_mig = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mig)

_preflight_check = _mig._preflight_check
_rename_status = _mig._rename_status


def test_preflight_aborts_on_duplicate_source_batch_id(db):
    """중복 source_batch_id 있으면 _preflight_check 가 RuntimeError 로 abort."""
    batch = ProductionBatch(
        run_label="test_preflight_run",
        process_name="연선",
        batch_seq=-999,
        total_length_m=1000,
        status="planned",
    )
    db.add(batch)
    db.flush()

    w1 = WipInventory(
        status="사용가능",
        source_batch_id=batch.batch_id,
        total_length_m=100,
    )
    w2 = WipInventory(
        status="사용가능",
        source_batch_id=batch.batch_id,  # 의도적 중복
        total_length_m=200,
    )
    db.add_all([w1, w2])
    db.flush()

    # Why db.connection(): SA2 에서 Engine.execute() 제거됨.
    # db.connection() 은 세션의 기존 트랜잭션 내 Connection 을 반환하므로
    # flush() 로 작성된 미커밋 데이터를 같은 트랜잭션에서 조회 가능.
    with pytest.raises(RuntimeError, match="UNIQUE 제약 위반 가능 데이터 발견"):
        _preflight_check(db.connection())


def test_preflight_passes_when_no_duplicates(db):
    """중복 없으면 _preflight_check 가 조용히 통과한다."""
    batch1 = ProductionBatch(
        run_label="test_no_dup_run_1",
        process_name="연선",
        batch_seq=-998,
        total_length_m=1000,
        status="planned",
    )
    batch2 = ProductionBatch(
        run_label="test_no_dup_run_2",
        process_name="연선",
        batch_seq=-997,
        total_length_m=1000,
        status="planned",
    )
    db.add_all([batch1, batch2])
    db.flush()

    w1 = WipInventory(
        status="사용가능",
        source_batch_id=batch1.batch_id,
        total_length_m=100,
    )
    w2 = WipInventory(
        status="사용가능",
        source_batch_id=batch2.batch_id,  # 다른 batch — 중복 아님
        total_length_m=200,
    )
    db.add_all([w1, w2])
    db.flush()

    # 예외 없이 통과해야 함 (세션 내 트랜잭션 공유)
    _preflight_check(db.connection())


def test_status_rename_converts_legacy(db):
    """'실적' → '실사_확정' mass rename."""
    w = WipInventory(status="실적", total_length_m=100)
    db.add(w)
    db.flush()

    _rename_status(db.connection())

    db.refresh(w)
    assert w.status == "실사_확정"


def test_status_rename_leaves_other_statuses_intact(db):
    """'실적' 이외의 status 값은 rename 에 영향받지 않는다."""
    w_ok = WipInventory(status="사용가능", total_length_m=50)
    w_done = WipInventory(status="사용완료", total_length_m=50)
    db.add_all([w_ok, w_done])
    db.flush()

    _rename_status(db.connection())

    db.refresh(w_ok)
    db.refresh(w_done)
    assert w_ok.status == "사용가능"
    assert w_done.status == "사용완료"
