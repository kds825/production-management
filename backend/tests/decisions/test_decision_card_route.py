"""Phase 6 Step 3c-1 — decision_card route role-based debug omit + 404 contract.

핵심 invariant:
1. operator role (X-User-Role: operator) + ?debug=1 → response.debug == None
2. admin role + ?debug=1 → response.debug 채움
3. admin role + ?debug=0 → response.debug == None
4. 헤더 미지정 (default admin) + ?debug=1 → debug 채움
5. batch.run_label != path run_label → 404
6. batch_id 미존재 → 404
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.application.decisions.equipment_day_gantt import (
    DefaultGanttBuilder,
    InsulationGanttBuilder,
    OutsourceGanttBuilder,
    SheathGanttBuilder,
    StrandingGanttBuilder,
    register_gantt_builder,
    reset_gantt_builders,
)
from app.application.decisions.phrasing import (
    register_phrasing_provider,
    reset_registry,
)
from app.application.decisions.phrasing_providers import (
    DefaultPhrasingProvider,
    InsulationPhrasingProvider,
    OutsourcePhrasingProvider,
    SheathPhrasingProvider,
    StrandingPhrasingProvider,
)
from app.application.decisions.section_builder import (
    DefaultSectionBuilder,
    InsulationSectionBuilder,
    OutsourceSectionBuilder,
    SheathSectionBuilder,
    StrandingSectionBuilder,
    register_section_builder,
    reset_section_builders,
)
from app.config import settings
from app.infrastructure.database import get_db
from app.infrastructure.models.production_batch import ProductionBatch
from app.main import app


@pytest.fixture(autouse=True)
def _register_providers():
    """TestClient 가 context-manager 로 안 쓰이면 on_event('startup') 미발화
    → 수동 register. 다른 test 파일의 autouse reset 영향도 본 fixture 가 복구."""
    reset_registry()
    reset_section_builders()
    reset_gantt_builders()
    for p in (
        DefaultPhrasingProvider(),
        SheathPhrasingProvider(),
        StrandingPhrasingProvider(),
        InsulationPhrasingProvider(),
        OutsourcePhrasingProvider(),
    ):
        register_phrasing_provider(p)
    for sb in (
        DefaultSectionBuilder(),
        SheathSectionBuilder(),
        StrandingSectionBuilder(),
        InsulationSectionBuilder(),
        OutsourceSectionBuilder(),
    ):
        register_section_builder(sb)
    for gb in (
        DefaultGanttBuilder(),
        SheathGanttBuilder(),
        StrandingGanttBuilder(),
        InsulationGanttBuilder(),
        OutsourceGanttBuilder(),
    ):
        register_gantt_builder(gb)
    yield
    reset_registry()
    reset_section_builders()
    reset_gantt_builders()


@pytest.fixture(scope="module")
def _engine():
    return create_engine(settings.DATABASE_URL)


@pytest.fixture
def _db_with_batch(_engine):
    """fixtures 격리 — savepoint 패턴. 신규 batch 1건 INSERT 후 rollback."""
    Session = sessionmaker(bind=_engine, autoflush=False)
    db = Session()
    db.begin_nested()  # SAVEPOINT
    try:
        # 테스트용 batch — run_label 'TEST-RUN-3C1'.
        # 기존 컬럼 채우는 최소 set (NOT NULL 만족).
        b = ProductionBatch(
            run_label="TEST-RUN-3C1",
            sales_order_id="TEST-ORDER-3C1",
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
            pass  # rollback 은 fixture 가 담당

    app.dependency_overrides[get_db] = _override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


# ─────────────────────────────────────────────────────────────────────────
# 404 contract
# ─────────────────────────────────────────────────────────────────────────


def test_404_when_batch_not_found(client):
    resp = client.get("/api/scheduler/TEST-RUN-3C1/decision-card/999999999")
    assert resp.status_code == 404
    assert "999999999" in resp.json()["detail"]


def test_404_when_run_label_mismatch(client, _db_with_batch):
    _, batch_id = _db_with_batch
    resp = client.get(f"/api/scheduler/WRONG-LABEL/decision-card/{batch_id}")
    assert resp.status_code == 404
    assert "불일치" in resp.json()["detail"]


# ─────────────────────────────────────────────────────────────────────────
# Role-based debug omit (2nd opinion blocker)
# ─────────────────────────────────────────────────────────────────────────


def test_operator_role_debug_omitted_even_if_requested(client, _db_with_batch):
    _, batch_id = _db_with_batch
    resp = client.get(
        f"/api/scheduler/TEST-RUN-3C1/decision-card/{batch_id}?debug=1",
        headers={"X-User-Role": "operator"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["debug"] is None, "운영자 role 은 debug 강제 omit"


def test_admin_role_debug_populated_when_requested(client, _db_with_batch):
    _, batch_id = _db_with_batch
    resp = client.get(
        f"/api/scheduler/TEST-RUN-3C1/decision-card/{batch_id}?debug=1",
        headers={"X-User-Role": "admin"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["debug"] is not None, "admin + ?debug=1 면 debug 채워야"
    assert "engine" in body["debug"]
    assert "schedule_task_row" in body["debug"]


def test_admin_role_debug_omitted_when_not_requested(client, _db_with_batch):
    _, batch_id = _db_with_batch
    resp = client.get(
        f"/api/scheduler/TEST-RUN-3C1/decision-card/{batch_id}",
        headers={"X-User-Role": "admin"},
    )
    assert resp.status_code == 200
    assert resp.json()["debug"] is None


def test_no_role_header_defaults_to_admin(client, _db_with_batch):
    """헤더 미지정 = admin (PoC 단계 — 인증 미설정). debug=1 효력 발동."""
    _, batch_id = _db_with_batch
    resp = client.get(f"/api/scheduler/TEST-RUN-3C1/decision-card/{batch_id}?debug=1")
    assert resp.status_code == 200
    assert resp.json()["debug"] is not None


def test_alternative_admin_role_aliases(client, _db_with_batch):
    """'developer', 'dev' 도 admin 으로 취급."""
    _, batch_id = _db_with_batch
    for alias in ("developer", "dev", "Admin", "ADMIN"):
        resp = client.get(
            f"/api/scheduler/TEST-RUN-3C1/decision-card/{batch_id}?debug=1",
            headers={"X-User-Role": alias},
        )
        assert resp.json()["debug"] is not None, f"{alias!r} 은 admin 으로 취급되어야"


# ─────────────────────────────────────────────────────────────────────────
# 응답 shape (skeleton — Step 3c-2 wire-up 후 본격 검증)
# ─────────────────────────────────────────────────────────────────────────


def test_response_has_all_required_fields(client, _db_with_batch):
    _, batch_id = _db_with_batch
    resp = client.get(
        f"/api/scheduler/TEST-RUN-3C1/decision-card/{batch_id}",
        headers={"X-User-Role": "operator"},
    )
    body = resp.json()
    for key in (
        "batch_id",
        "run_label",
        "process_key",
        "process_label",
        "placement_text",
        "verdict_summary",
        "why",
        "impact",
        "equipment_day",
        "equipment_day_sort_label",
        "bundle_compare",
        "alternatives",
        "section_default_expanded",
        "provenance",
        "source",
    ):
        assert key in body, f"missing key: {key}"


def test_process_key_correctly_resolved_for_sheath(client, _db_with_batch):
    _, batch_id = _db_with_batch
    resp = client.get(
        f"/api/scheduler/TEST-RUN-3C1/decision-card/{batch_id}",
        headers={"X-User-Role": "operator"},
    )
    assert resp.json()["process_key"] == "sheath"


def test_equipment_day_sort_label_matches_memory_for_sheath(client, _db_with_batch):
    """sheath 카드의 ❹ 정렬 라벨이 memory 3차 iteration 과 일치."""
    _, batch_id = _db_with_batch
    resp = client.get(
        f"/api/scheduler/TEST-RUN-3C1/decision-card/{batch_id}",
        headers={"X-User-Role": "operator"},
    )
    assert (
        resp.json()["equipment_day_sort_label"]
        == "① 납기 가까운 순 → ② 전공정 ready 시각 → ③ 색상 인접 순"
    )
