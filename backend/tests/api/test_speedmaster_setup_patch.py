"""PATCH /master/speed_master/{id}/setup-params — 화이트리스트 4컬럼 편집."""

from fastapi.testclient import TestClient

from app.main import app
from app.infrastructure.models.speed_master import SpeedMaster


client = TestClient(app)


def _pick_one_id(db) -> int:
    row = db.query(SpeedMaster).first()
    assert row is not None, "speed_master seed required"
    return int(row.speed_id)


def test_patch_rejects_structural_field(db) -> None:
    """equipment_code 같은 구조 필드는 거부한다 (extra='forbid')."""
    sid = _pick_one_id(db)
    resp = client.patch(
        f"/api/master/speed_master/{sid}/setup-params",
        json={"equipment_code": "FOO"},
    )
    assert resp.status_code == 422


def test_patch_rejects_negative(db) -> None:
    sid = _pick_one_id(db)
    resp = client.patch(
        f"/api/master/speed_master/{sid}/setup-params",
        json={"setup_spec_min": -1},
    )
    assert resp.status_code == 422


def test_patch_updates_single_column(db) -> None:
    """4컬럼 중 하나만 바꾸면 나머지는 유지된다."""
    sid = _pick_one_id(db)
    before = db.query(SpeedMaster).filter(SpeedMaster.speed_id == sid).first()
    before_color = float(before.setup_color_min or 0)

    resp = client.patch(
        f"/api/master/speed_master/{sid}/setup-params",
        json={"setup_spec_min": 999},
    )
    assert resp.status_code == 200
    db.commit()

    after = db.query(SpeedMaster).filter(SpeedMaster.speed_id == sid).first()
    assert float(after.setup_spec_min) == 999
    assert float(after.setup_color_min or 0) == before_color

    # cleanup
    client.patch(
        f"/api/master/speed_master/{sid}/setup-params",
        json={"setup_spec_min": float(before.setup_spec_min or 0)},
    )


def test_patch_updates_updated_at(db) -> None:
    """PATCH 시 updated_at 이 갱신된다 (onupdate hook)."""
    sid = _pick_one_id(db)
    row = db.query(SpeedMaster).filter(SpeedMaster.speed_id == sid).first()
    original_updated_at = row.updated_at

    new_val = float(row.setup_start_min or 0) + 1
    resp = client.patch(
        f"/api/master/speed_master/{sid}/setup-params",
        json={"setup_start_min": new_val},
    )
    assert resp.status_code == 200
    db.commit()

    db.refresh(row)
    assert row.updated_at > original_updated_at

    # cleanup
    client.patch(
        f"/api/master/speed_master/{sid}/setup-params",
        json={"setup_start_min": new_val - 1},
    )


def test_patch_returns_404_when_missing() -> None:
    resp = client.patch(
        "/api/master/speed_master/999999/setup-params",
        json={"setup_spec_min": 100},
    )
    assert resp.status_code == 404
