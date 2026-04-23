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
from unittest.mock import MagicMock

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


def test_preflight_aborts_on_duplicate_source_batch_id():
    """중복 source_batch_id 가 있을 때 _preflight_check 가 RuntimeError 로 abort.

    Why mock:
    T6 에서 UNIQUE partial index(source_batch_id IS NOT NULL)가 이미 DB 에 적용됐으므로
    실제 DB 에 중복 행을 삽입하는 것 자체가 불가능하다(IntegrityError 발생).
    _preflight_check 는 "index 생성 전 기존 데이터에 중복이 있는가" 를 검사하는
    마이그레이션 선행 검사 로직이므로, 해당 로직만 단독으로 검증하는 것이 목적에 부합한다.
    mock conn 으로 "중복 존재" 시나리오를 재현해 RuntimeError 분기를 검증한다.
    """
    # Mock row — source_batch_id=999, count=2 (중복)
    mock_row = MagicMock()
    mock_row.source_batch_id = 999
    mock_row.c = 2

    mock_result = MagicMock()
    mock_result.fetchone.return_value = mock_row

    mock_conn = MagicMock()
    mock_conn.execute.return_value = mock_result

    with pytest.raises(RuntimeError, match="UNIQUE 제약 위반 가능 데이터 발견"):
        _preflight_check(mock_conn)


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
