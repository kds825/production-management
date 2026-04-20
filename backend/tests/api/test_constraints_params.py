"""ConstraintConfig 확장 API — history / drift-status / preview-impact 테스트."""

from fastapi.testclient import TestClient

from app.infrastructure.models.constraint_config_history import (
    ConstraintConfigHistory,
)
from app.main import app

client = TestClient(app)


def test_patch_records_history(db) -> None:
    """PATCH 시 old/new params_json 을 constraint_config_history 에 기록."""
    before = db.query(ConstraintConfigHistory).count()

    resp = client.patch(
        "/api/constraints/4-1",
        json={"params_json": {"stranding_min": 200}},
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
    assert latest.new_params_json.get("stranding_min") == 200
    assert latest.old_params_json.get("stranding_min") == 210

    # cleanup — 시드값 복원
    client.patch(
        "/api/constraints/4-1",
        json={
            "params_json": {
                "stranding_min": 210,
                "insulation_min": 60,
                "sheath_min": 30,
                "cv_min": 300,
            }
        },
    )


def test_patch_merges_partial_params(db) -> None:
    """부분 patch 는 기존 키를 보존하고 해당 키만 덮어써야 한다.

    Regression: 이전엔 row.params_json = new_params 로 전체 교체라서
    {stranding_min: 30} patch 시 insulation_min/sheath_min/cv_min 이 사라졌다.
    """
    resp = client.patch(
        "/api/constraints/4-1",
        json={"params_json": {"stranding_min": 30}},
    )
    assert resp.status_code == 200

    got = client.get("/api/constraints").json()
    row = next(c for c in got["constraints"] if c["constraint_id"] == "4-1")
    assert row["params_json"]["stranding_min"] == 30
    assert row["params_json"]["insulation_min"] == 60
    assert row["params_json"]["sheath_min"] == 30
    assert row["params_json"]["cv_min"] == 300

    # cleanup — 시드값 복원
    client.patch(
        "/api/constraints/4-1",
        json={
            "params_json": {
                "stranding_min": 210,
                "insulation_min": 60,
                "sheath_min": 30,
                "cv_min": 300,
            }
        },
    )


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
