"""Week 5 Task 5B.1 — promote-baseline / reset-to-baseline 라이트 엔드포인트.

Why savepoint TestClient: routes 는 자체 Depends(get_db) 세션으로 commit 한다.
db 픽스처 단독 rollback 만으로는 라우트 commit 을 되돌릴 수 없어 Supabase 공유
DB 가 영구 변형된다. test_constraints_params.py / test_constraints_diff.py 와
동일한 outer transaction + 자동 재시작 SAVEPOINT 패턴을 사용한다.

Why also explicit cleanup: SAVEPOINT 패턴은 단일 commit 시나리오에선 충분하지만,
promote-baseline 처럼 하나의 라우트가 N개 행을 add 후 commit 하면 outer transaction
까지 흘러갈 수 있다(SQLAlchemy 의 Session.commit 동작상). 안전을 위해 테스트
픽스처에서 `BASELINE_test-*` / `BASELINE_blocked-*` 태그로 시작하는 history rows
를 finalize 단계에서 직접 삭제하고, finished_at IS NULL 인 SolverRun 도 동일하게
정리한다.
"""

from collections.abc import Generator
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.infrastructure.database import SessionLocal, get_db
from app.infrastructure.models.constraint_config import ConstraintConfig
from app.infrastructure.models.constraint_config_history import (
    ConstraintConfigHistory,
)
from app.infrastructure.models.solver_run import SolverRun
from app.main import app

# Why: 테스트 데이터 마커 — fixture cleanup 이 production baselines 와 충돌하지
# 않도록 모든 테스트는 이 prefix 로 시작하는 tag 를 사용한다.
TEST_BASELINE_PREFIXES: tuple[str, ...] = (
    "BASELINE_test-",
    "BASELINE_blocked-by-solver_",
)
TEST_SOLVER_RUN_LABEL = "test-active-run-baseline-writes"


@pytest.fixture
def client(db: Session) -> Generator[TestClient, None, None]:
    """savepoint 기반 격리 TestClient — 기존 패턴 그대로 복제 + 강제 cleanup.

    SAVEPOINT 만으로는 막을 수 없는 누수가 있어 finalize 단계에서 별도 세션으로
    test prefix 매칭 행을 직접 DELETE — 운영 baselines 는 prefix 가 다르므로
    영향 없음.
    """
    db.begin_nested()

    @event.listens_for(db, "after_transaction_end")
    def _restart_savepoint(session: Session, transaction) -> None:
        if transaction.nested and not transaction._parent.nested:
            session.begin_nested()

    def _override_get_db() -> Generator[Session, None, None]:
        yield db

    app.dependency_overrides[get_db] = _override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)
        event.remove(db, "after_transaction_end", _restart_savepoint)
        # 강제 cleanup — 별도 세션에서 별도 트랜잭션으로 실행해 픽스처 상태와
        # 분리. 운영 데이터(BASELINE_phase*) 와 충돌하지 않도록 prefix 화이트리스트.
        cleanup = SessionLocal()
        try:
            for prefix in TEST_BASELINE_PREFIXES:
                cleanup.query(ConstraintConfigHistory).filter(
                    ConstraintConfigHistory.changed_by.like(f"{prefix}%")
                ).delete(synchronize_session=False)
            cleanup.query(SolverRun).filter(
                SolverRun.run_label == TEST_SOLVER_RUN_LABEL
            ).delete(synchronize_session=False)
            cleanup.commit()
        finally:
            cleanup.close()


def test_promote_baseline_creates_history_rows(db: Session, client: TestClient) -> None:
    """promote-baseline 호출 시 모든 ConstraintConfig 에 1:1 history 행 생성.

    검증:
      - changed_by 형식이 BASELINE_<tag>_<iso> 패턴
      - row_count == 현재 ConstraintConfig 행 수
      - new_params_json == 현재 row.params_json
    """
    config_count = db.query(ConstraintConfig).count()
    assert config_count > 0, "ConstraintConfig 시드가 비어 있어 테스트 의미 없음"

    resp = client.post(
        "/api/constraints/promote-baseline",
        json={
            "tag": "test-promote-fixture",
            "created_by": "JK",
            "approval_note": "단위 테스트 — promote 동작 검증",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["tag"] == "test-promote-fixture"
    assert body["row_count"] == config_count
    changed_by = body["changed_by"]
    assert changed_by.startswith("BASELINE_test-promote-fixture_")

    # ISO suffix 가 14자 (YYYYMMDDTHHMMSSZ — 16 char) 인지 검증
    iso_suffix = changed_by.rsplit("_", 1)[-1]
    assert len(iso_suffix) == 16, f"unexpected iso suffix: {iso_suffix}"
    assert iso_suffix.endswith("Z")

    # DB 검증: 같은 changed_by 그룹에 N개 행이 들어갔는지
    rows = (
        db.query(ConstraintConfigHistory)
        .filter(ConstraintConfigHistory.changed_by == changed_by)
        .all()
    )
    assert len(rows) == config_count

    # 각 history row 의 new_params_json 이 그 시점 ConstraintConfig.params_json 과 일치
    config_by_id = {
        c.constraint_id: dict(c.params_json or {})
        for c in db.query(ConstraintConfig).all()
    }
    for h in rows:
        assert h.new_params_json == config_by_id[h.constraint_id]


def test_reset_to_baseline_restores_params(db: Session, client: TestClient) -> None:
    """reset-to-baseline 은 베이스라인 시점 params_json 을 그대로 복원."""
    # 1) 임의의 ConstraintConfig 한 행을 골라 baseline history 를 시드.
    target = db.query(ConstraintConfig).first()
    assert target is not None
    cid = target.constraint_id

    # 시드용 베이스라인: 명확히 다른 값 (현재 params 와 충돌하지 않는 marker key)
    baseline_changed_by = "BASELINE_test-reset_20260101T000000Z"
    seeded_params = {"_test_marker": 999, "stranding_min": 12345}
    db.add(
        ConstraintConfigHistory(
            constraint_id=cid,
            changed_by=baseline_changed_by,
            changed_at=datetime.now(timezone.utc) - timedelta(days=1),
            old_params_json={},
            new_params_json=seeded_params,
        )
    )
    db.flush()

    # 2) 현재 row.params_json 을 다른 값으로 변경 (reset 이 의미 있어지도록).
    target.params_json = {"current_value": 42}
    db.flush()

    # 3) reset 호출 → 베이스라인 시점 params 로 되돌림
    resp = client.post(
        "/api/constraints/reset-to-baseline",
        json={"changed_by": baseline_changed_by},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["changed_by"] == baseline_changed_by
    assert body["reset_count"] == 1

    # 4) DB 재확인
    refreshed = (
        db.query(ConstraintConfig).filter(ConstraintConfig.constraint_id == cid).first()
    )
    assert refreshed is not None
    assert refreshed.params_json == seeded_params


def test_reset_to_baseline_404_when_missing(db: Session, client: TestClient) -> None:
    """존재하지 않는 changed_by 는 404."""
    resp = client.post(
        "/api/constraints/reset-to-baseline",
        json={"changed_by": "BASELINE_does-not-exist_20990101T000000Z"},
    )
    assert resp.status_code == 404
    assert "찾을 수 없" in resp.json()["detail"]


def test_promote_baseline_400_when_missing_fields(
    db: Session, client: TestClient
) -> None:
    """tag/created_by/approval_note 누락 시 400."""
    resp = client.post(
        "/api/constraints/promote-baseline",
        json={"tag": "only-tag"},
    )
    assert resp.status_code == 400


def _seed_active_solver_run(db: Session) -> SolverRun:
    """finished_at IS NULL 인 SolverRun 한 행을 직접 insert.

    Why: 423 응답 검증용 — trace_writer 정상 흐름은 finished_at 을 즉시 채우므로
    "진행 중" 상태를 자연스럽게 만들 수 없다. 강제로 NULL 행을 insert 한다.

    run_id 는 uuid4 로 매번 새로 — 동일 픽스처가 여러 테스트에서 호출돼도
    primary key 충돌이 발생하지 않도록.
    """
    import uuid

    run = SolverRun(
        run_id=str(uuid.uuid4()),
        run_label=TEST_SOLVER_RUN_LABEL,
        started_at=datetime.now(timezone.utc),
        finished_at=None,
        solver_status="UNKNOWN",
        objective_value=None,
        input_hash="sha256:" + "0" * 64,
        output_hash=None,
        constraint_config_version=None,
        solver_params={},
    )
    db.add(run)
    db.flush()
    return run


def test_promote_baseline_423_when_solver_active(
    db: Session, client: TestClient
) -> None:
    """솔버 실행 중(finished_at IS NULL)에는 promote 가 423 으로 거부."""
    _seed_active_solver_run(db)

    resp = client.post(
        "/api/constraints/promote-baseline",
        json={
            "tag": "blocked-by-solver",
            "created_by": "JK",
            "approval_note": "should not pass",
        },
    )
    assert resp.status_code == 423
    assert "솔버" in resp.json()["detail"]


def test_reset_to_baseline_423_when_solver_active(
    db: Session, client: TestClient
) -> None:
    """솔버 실행 중에는 reset 도 423."""
    _seed_active_solver_run(db)

    # 가짜 changed_by — 어차피 423 이 먼저 나오므로 존재 여부 무관.
    resp = client.post(
        "/api/constraints/reset-to-baseline",
        json={"changed_by": "BASELINE_anything_20260101T000000Z"},
    )
    assert resp.status_code == 423
    assert "솔버" in resp.json()["detail"]
