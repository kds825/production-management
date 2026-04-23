"""ConstraintConfig 확장 API — history / drift-status / preview-impact 테스트."""

import pytest
from fastapi.testclient import TestClient

from app.infrastructure.database import SessionLocal
from app.infrastructure.models.constraint_config import ConstraintConfig
from app.infrastructure.models.constraint_config_history import (
    ConstraintConfigHistory,
)
from app.main import app

client = TestClient(app)


# Why: TestClient 라우트는 자체 세션으로 commit 하므로 `db` 픽스처의 rollback 으로
# 정리되지 않는다. 실 DB(Supabase)를 공유하는 개발 환경에서 이 파일의 테스트가
# 사용자가 UI 로 편집한 값을 덮어쓰지 않도록, 각 테스트 시작 시점의 params_json
# 을 스냅샷해두고 테스트 종료 시 그대로 복원한다. (하드코딩 210 복원 금지)
@pytest.fixture(autouse=True)
def _preserve_constraint_params():
    session = SessionLocal()
    try:
        snapshot = {
            r.constraint_id: dict(r.params_json or {})
            for r in session.query(ConstraintConfig).all()
        }
    finally:
        session.close()

    yield

    session = SessionLocal()
    try:
        for cid, params in snapshot.items():
            row = (
                session.query(ConstraintConfig)
                .filter(ConstraintConfig.constraint_id == cid)
                .first()
            )
            if row is not None and row.params_json != params:
                row.params_json = params
        session.commit()
    finally:
        session.close()


def test_patch_records_history(db) -> None:
    """PATCH 시 old/new params_json 을 constraint_config_history 에 기록."""
    # 시작 시점의 stranding_min 캡처 — 하드코딩 210 에 의존하지 않는다.
    pre = client.get("/api/constraints").json()
    pre_4_1 = next(c for c in pre["constraints"] if c["constraint_id"] == "4-1")[
        "params_json"
    ]
    pre_stranding = pre_4_1.get("stranding_min")
    # pre 와 동일하면 history 가 생성되지 않으므로 반드시 다른 값 사용
    new_stranding = 200 if pre_stranding != 200 else 201

    before = db.query(ConstraintConfigHistory).count()

    resp = client.patch(
        "/api/constraints/4-1",
        json={"params_json": {"stranding_min": new_stranding}},
    )
    assert resp.status_code == 200

    db.commit()  # route가 commit한 걸 세션 refresh 용 — rollback 전에 확인
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


def test_patch_merges_partial_params(db) -> None:
    """부분 patch 는 기존 키를 보존하고 해당 키만 덮어써야 한다.

    Regression: 이전엔 row.params_json = new_params 로 전체 교체라서
    {stranding_min: 30} patch 시 insulation_min/sheath_min/cv_min 이 사라졌다.
    """
    # 병합 검증용 pre-스냅샷 — 편집 안 한 키는 이 값 그대로 유지되어야 한다.
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


def test_get_history(db) -> None:
    resp = client.get("/api/constraints/4-1/history")
    assert resp.status_code == 200
    body = resp.json()
    assert "history" in body
    assert isinstance(body["history"], list)


def test_drift_status_returns_dirty_flag(db) -> None:
    resp = client.get("/api/constraints/drift-status")
    assert resp.status_code == 200
    body = resp.json()
    assert "dirty" in body
    assert isinstance(body["dirty"], bool)


def test_preview_impact_counts_planned_batches(db) -> None:
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
