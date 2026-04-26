"""Phase 6 Step 6-MVP — POST /api/decision-feedback route 검증.

invariant:
1. happy path → 201 + DB INSERT (status='open')
2. batch_id 미존재 → 404
3. run_label 불일치 → 404
4. payload_snapshot JSONB 보존 (admin 큐 재생용)
5. free_text empty → 422
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.infrastructure.database import get_db
from app.infrastructure.models.decision_feedback import DecisionFeedback
from app.infrastructure.models.production_batch import ProductionBatch
from app.main import app


@pytest.fixture(scope="module")
def _engine():
    return create_engine(settings.DATABASE_URL)


@pytest.fixture
def _db_with_batch(_engine):
    Session = sessionmaker(bind=_engine, autoflush=False)
    db = Session()
    db.begin_nested()
    try:
        b = ProductionBatch(
            run_label="TEST-FB-1",
            sales_order_id="TEST-FB-ORDER-1",
            sales_order_line=1,
            product_group="LV-150",
            sq_mm2=150,
            voltage="600",
            core_count=1,
            customer_name="테스트거래처",
            customer_priority=2,
            process_name="저압시스",
            sheath_color="흑",
            conductor_material="CU",
            stranding_type="압축연선",
            total_length_m=3850,
            extra_length_m=0,
            line_speed_mpm=20,
            estimated_duration_min=510,
            status="planned",
            batch_seq=1,
        )
        db.add(b)
        db.flush()
        yield db, b.batch_id
    finally:
        db.rollback()
        db.close()


@pytest.fixture
def client(_db_with_batch):
    db, _ = _db_with_batch

    def _override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def _payload(batch_id: int, **over) -> dict:
    base = {
        "run_label": "TEST-FB-1",
        "batch_id": batch_id,
        "task_id": None,
        "section": "why",
        "line_anchor": "why_line_3",
        "constraint_id_hint": "4-2",
        "free_text": "이 색상교체 추정이 어색합니다 — 직전 묶음과 동일색인데 180분 잡힘",
        "operator_id": "kim.s",
        "payload_snapshot": {"verdict_summary": "⚠ 검토 — 색상교체 180분"},
    }
    base.update(over)
    return base


def test_post_happy_path_201(client, _db_with_batch):
    db, batch_id = _db_with_batch
    resp = client.post("/api/decision-feedback", json=_payload(batch_id))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "open"
    assert body["section"] == "why"
    assert body["constraint_id_hint"] == "4-2"
    assert body["operator_id"] == "kim.s"
    # DB 행 확인
    row = db.query(DecisionFeedback).filter(DecisionFeedback.id == body["id"]).first()
    assert row is not None
    assert row.payload_snapshot == {"verdict_summary": "⚠ 검토 — 색상교체 180분"}


def test_post_batch_not_found_404(client):
    resp = client.post("/api/decision-feedback", json=_payload(999_999_999))
    assert resp.status_code == 404
    assert "999999999" in resp.json()["detail"]


def test_post_run_label_mismatch_404(client, _db_with_batch):
    _, batch_id = _db_with_batch
    resp = client.post(
        "/api/decision-feedback",
        json=_payload(batch_id, run_label="WRONG-LABEL"),
    )
    assert resp.status_code == 404
    assert "불일치" in resp.json()["detail"]


def test_post_empty_free_text_422(client, _db_with_batch):
    _, batch_id = _db_with_batch
    resp = client.post("/api/decision-feedback", json=_payload(batch_id, free_text=""))
    assert resp.status_code == 422


def test_post_invalid_section_422(client, _db_with_batch):
    _, batch_id = _db_with_batch
    resp = client.post(
        "/api/decision-feedback", json=_payload(batch_id, section="unknown")
    )
    assert resp.status_code == 422
