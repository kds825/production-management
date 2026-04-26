"""Phase 6 Step 7 — KPI dashboard endpoint smoke test.

데이터 부족한 환경에서도 200 응답 + 모든 metric key 존재.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.infrastructure.database import get_db
from app.main import app


@pytest.fixture(scope="module")
def _engine():
    return create_engine(settings.DATABASE_URL)


@pytest.fixture
def client(_engine):
    Session = sessionmaker(bind=_engine, autoflush=False)
    db = Session()
    db.begin_nested()

    def _override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()
    db.rollback()
    db.close()


def test_kpi_endpoint_returns_all_metrics(client):
    resp = client.get("/api/admin/kpi/decision-card?days=30")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["window_days"] == 30
    assert "eng" in body
    assert "product" in body
    assert "fixed_rate" in body["eng"]
    assert "fixed_rate" in body["eng"]["fixed_rate"]
    assert "line_anchor_top" in body["eng"]
    assert "fix_sla_p50" in body["eng"]
    assert "card_review_time_avg" in body["product"]
    assert "card_open_rate" in body["product"]


def test_kpi_default_days_is_30(client):
    resp = client.get("/api/admin/kpi/decision-card")
    assert resp.status_code == 200
    assert resp.json()["window_days"] == 30


def test_kpi_custom_window(client):
    resp = client.get("/api/admin/kpi/decision-card?days=7")
    assert resp.status_code == 200
    assert resp.json()["window_days"] == 7
    assert resp.json()["eng"]["fixed_rate"]["days"] == 7
