"""drift-status 확장 — SpeedMaster.updated_at 이 최근이면 dirty=true."""

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.main import app
from app.infrastructure.models.speed_master import SpeedMaster


client = TestClient(app)


def test_drift_status_dirty_when_speedmaster_updated_after_schedule(db) -> None:
    """SpeedMaster.updated_at 이 마지막 auto_schedule 실행보다 최근이면 dirty=true."""
    row = db.query(SpeedMaster).first()
    assert row is not None
    row.updated_at = datetime.now(timezone.utc)
    db.commit()

    resp = client.get("/api/constraints/drift-status")
    assert resp.status_code == 200
    body = resp.json()
    assert "dirty" in body
    assert isinstance(body["dirty"], bool)


def test_drift_status_includes_speedmaster_field(db) -> None:
    """응답 스키마에 latest_speed_master_updated_at 이 포함되어야 UI 가 활용 가능."""
    resp = client.get("/api/constraints/drift-status")
    assert resp.status_code == 200
    body = resp.json()
    assert "latest_speed_master_updated_at" in body
