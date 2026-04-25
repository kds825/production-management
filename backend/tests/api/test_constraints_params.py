"""ConstraintConfig 확장 API — history / drift-status / preview-impact 테스트."""

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.infrastructure.database import get_db
from app.infrastructure.models.constraint_config_history import (
    ConstraintConfigHistory,
)
from app.main import app


# Why: TestClient 라우트는 `Depends(get_db)` 로 자체 세션을 만들고 그 안에서
# commit 한다. `db` 픽스처의 rollback 만으로는 라우트 commit 을 되돌릴 수 없어
# Supabase 공유 DB 에 사용자 데이터가 영구 변형된다.
#
# 해결: `get_db` 를 테스트 세션으로 override + outer transaction + savepoint
# 자동 재시작 이벤트. 라우트 내부 `db.commit()` 은 savepoint release 만 일으키고,
# 픽스처 종료 시 outer transaction.rollback() 으로 모두 폐기된다.
@pytest.fixture
def client(db: Session) -> Generator[TestClient, None, None]:
    """`get_db` 를 테스트 트랜잭션 세션으로 묶은 TestClient.

    SQLAlchemy "join a session into an external transaction" 패턴:
    초기 SAVEPOINT 를 열어두고 라우트 commit 으로 종료될 때마다
    `after_transaction_end` 이벤트로 즉시 새 SAVEPOINT 를 재오픈한다.
    `db` 픽스처의 마지막 `session.rollback()` 이 outer 트랜잭션 전체를
    되돌리므로 Supabase 에 어떤 변경도 영구화되지 않는다.
    """
    db.begin_nested()

    @event.listens_for(db, "after_transaction_end")
    def _restart_savepoint(session: Session, transaction) -> None:
        if transaction.nested and not transaction._parent.nested:
            session.begin_nested()

    def _override_get_db() -> Generator[Session, None, None]:
        # FastAPI Depends 는 generator 의 첫 yield 값을 주입한다.
        # commit/close 는 라우트가 호출하지만, 여기서는 단일 테스트 세션을
        # 그대로 공유해야 하므로 finally 에서 닫지 않는다.
        yield db

    app.dependency_overrides[get_db] = _override_get_db

    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)
        event.remove(db, "after_transaction_end", _restart_savepoint)


def test_patch_records_history(db: Session, client: TestClient) -> None:
    """PATCH 시 old/new params_json 을 constraint_config_history 에 기록."""
    pre = client.get("/api/constraints").json()
    pre_4_1 = next(c for c in pre["constraints"] if c["constraint_id"] == "4-1")[
        "params_json"
    ]
    pre_stranding = pre_4_1.get("stranding_min")
    new_stranding = 200 if pre_stranding != 200 else 201

    before = db.query(ConstraintConfigHistory).count()

    resp = client.patch(
        "/api/constraints/4-1",
        json={"params_json": {"stranding_min": new_stranding}},
    )
    assert resp.status_code == 200

    after = db.query(ConstraintConfigHistory).count()
    assert after == before + 1

    latest = (
        db.query(ConstraintConfigHistory)
        .order_by(ConstraintConfigHistory.history_id.desc())
        .first()
    )
    assert latest.constraint_id == "4-1"
    assert latest.new_params_json.get("stranding_min") == new_stranding
    assert latest.old_params_json.get("stranding_min") == pre_stranding


def test_patch_merges_partial_params(db: Session, client: TestClient) -> None:
    """부분 patch 는 기존 키를 보존하고 해당 키만 덮어써야 한다.

    Regression: 이전엔 row.params_json = new_params 로 전체 교체라서
    {stranding_min: 30} patch 시 insulation_min/sheath_min/cv_min 이 사라졌다.
    """
    pre = client.get("/api/constraints").json()
    pre_4_1 = next(c for c in pre["constraints"] if c["constraint_id"] == "4-1")[
        "params_json"
    ]
    new_stranding = 30 if pre_4_1.get("stranding_min") != 30 else 31

    resp = client.patch(
        "/api/constraints/4-1",
        json={"params_json": {"stranding_min": new_stranding}},
    )
    assert resp.status_code == 200

    got = client.get("/api/constraints").json()
    row = next(c for c in got["constraints"] if c["constraint_id"] == "4-1")
    assert row["params_json"]["stranding_min"] == new_stranding
    for k in ("insulation_min", "sheath_min", "cv_min"):
        assert row["params_json"][k] == pre_4_1[k]


def test_get_history(db: Session, client: TestClient) -> None:
    resp = client.get("/api/constraints/4-1/history")
    assert resp.status_code == 200
    body = resp.json()
    assert "history" in body
    assert isinstance(body["history"], list)


def test_drift_status_returns_dirty_flag(db: Session, client: TestClient) -> None:
    resp = client.get("/api/constraints/drift-status")
    assert resp.status_code == 200
    body = resp.json()
    assert "dirty" in body
    assert isinstance(body["dirty"], bool)


def test_preview_impact_counts_planned_batches(db: Session, client: TestClient) -> None:
    """4-1 stranding_min 변경 시 영향 배치 수 + Δ 총 리드타임."""
    resp = client.post(
        "/api/constraints/4-1/preview-impact",
        json={"new_params_json": {"stranding_min": 0}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "affected_batch_count" in body
    assert "total_delta_min" in body
    assert body["affected_batch_count"] >= 0
