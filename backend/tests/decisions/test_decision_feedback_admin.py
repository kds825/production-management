"""Phase 6 Step 6-admin — admin 큐 GET/PATCH/bulk + clustering 검증.

invariant:
1. GET /api/admin/decision-feedback?status=open → status='open' 만
2. PATCH /api/admin/decision-feedback/{id} {status: 'fixed'} → 200
3. PATCH wontfix without dev_notes → 422 (운영자 reason 필수)
4. PATCH bulk → IDs 일괄 status 변경
5. GET /clusters — (process_name, line_anchor) group + impact_score DESC
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
def _db_with_data(_engine):
    Session = sessionmaker(bind=_engine, autoflush=False)
    db = Session()
    db.begin_nested()
    try:
        b = ProductionBatch(
            run_label="TEST-FB-ADMIN-1",
            sales_order_id="TEST-FB-ADMIN-ORDER-1",
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

        # 같은 line_anchor 에 운영자 2명 의견 — clustering source
        for op_id, anchor in [
            ("kim.s", "why_line_3"),
            ("park.j", "why_line_3"),
            ("lee.h", "why_line_5"),
        ]:
            db.add(
                DecisionFeedback(
                    run_label="TEST-FB-ADMIN-1",
                    batch_id=b.batch_id,
                    task_id=None,
                    section="why",
                    line_anchor=anchor,
                    constraint_id_hint="4-2" if anchor == "why_line_3" else "5-1",
                    free_text=f"의견 from {op_id} on {anchor}",
                    operator_id=op_id,
                    status="open",
                    payload_snapshot={"verdict_summary": "⚠ 검토"},
                )
            )
        db.flush()
        yield db, b.batch_id
    finally:
        db.rollback()
        db.close()


@pytest.fixture
def client(_db_with_data):
    db, _ = _db_with_data

    def _override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_admin_list_filters_by_status(client):
    resp = client.get("/api/admin/decision-feedback?status=open")
    assert resp.status_code == 200
    body = resp.json()
    assert all(r["status"] == "open" for r in body)
    assert len(body) >= 3


def test_admin_patch_single_status_change(client, _db_with_data):
    db, _ = _db_with_data
    fb_id = (
        db.query(DecisionFeedback.id)
        .filter(DecisionFeedback.run_label == "TEST-FB-ADMIN-1")
        .filter(DecisionFeedback.line_anchor == "why_line_3")
        .first()[0]
    )
    resp = client.patch(
        f"/api/admin/decision-feedback/{fb_id}",
        json={"status": "fixed", "linked_pr_url": "https://github.com/x/y/pull/1"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "fixed"


def test_admin_patch_wontfix_requires_dev_notes(client, _db_with_data):
    db, _ = _db_with_data
    # 본 fixture 가 만든 fresh row 만 대상 — 과거 dev run / 이전 테스트의 잔존 row 회피
    fb = (
        db.query(DecisionFeedback)
        .filter(DecisionFeedback.run_label == "TEST-FB-ADMIN-1")
        .filter(DecisionFeedback.line_anchor == "why_line_5")
        .first()
    )
    assert fb is not None
    fb.dev_notes = None  # ensure fresh state
    db.flush()
    fb_id = fb.id
    resp = client.patch(
        f"/api/admin/decision-feedback/{fb_id}", json={"status": "wontfix"}
    )
    assert resp.status_code == 422
    assert "dev_notes" in resp.json()["detail"] or "사유" in resp.json()["detail"]

    # dev_notes 채우면 OK
    resp_ok = client.patch(
        f"/api/admin/decision-feedback/{fb_id}",
        json={"status": "wontfix", "dev_notes": "현장에서 검토 후 의도된 동작 확인"},
    )
    assert resp_ok.status_code == 200


def test_admin_bulk_patch_changes_all_ids(client, _db_with_data):
    db, _ = _db_with_data
    ids = [
        row[0]
        for row in db.query(DecisionFeedback.id)
        .filter(DecisionFeedback.run_label == "TEST-FB-ADMIN-1")
        .filter(DecisionFeedback.line_anchor == "why_line_3")
        .all()
    ]
    resp = client.patch(
        "/api/admin/decision-feedback/bulk",
        json={"ids": ids, "status": "investigating"},
    )
    assert resp.status_code == 200
    assert resp.json()["updated"] == len(ids)


def test_admin_clusters_groups_by_anchor_and_orders_by_impact(client):
    """Cluster endpoint: 본 fixture 가 등록한 (저압시스, why_line_3) 그룹이
    distinct_operators=2 (kim.s + park.j) 로 집계된다.

    impact_score 절대값 비교는 다른 테스트 / 과거 dev run 의 잔존 데이터
    영향으로 불안정 — distinct_operators count 만 deterministic 으로 검증.
    rows 목록은 impact_score DESC 정렬됨 (helper 가 보장)."""
    resp = client.get("/api/admin/decision-feedback/clusters?status=open")
    assert resp.status_code == 200
    rows = resp.json()
    matching = [
        r
        for r in rows
        if r["process_name"] == "저압시스" and r["line_anchor"] == "why_line_3"
    ]
    assert matching, "fixture 가 만든 (저압시스, why_line_3) cluster 가 응답에 있어야"
    assert matching[0]["distinct_operators"] >= 2
    # 정렬 invariant — impact_score 가 DESC
    scores = [r["impact_score"] for r in rows]
    assert scores == sorted(scores, reverse=True)
