"""Task 3B.2 — 베이스라인 diff / list 엔드포인트 테스트.

Why savepoint TestClient: routes 가 자체 Depends(get_db) 세션으로 commit 한다.
db 픽스처 단독 rollback 만으로는 라우트 commit 을 되돌릴 수 없어 Supabase 공유
DB 가 영구 변형된다 (참조: tests/api/test_constraints_params.py 의 동일 패턴).
outer transaction + 자동 재시작 SAVEPOINT 로 라우트 commit 을 격리한다.
"""

from collections.abc import Generator
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.infrastructure.database import get_db
from app.infrastructure.models.constraint_config_history import (
    ConstraintConfigHistory,
)
from app.main import app


@pytest.fixture
def client(db: Session) -> Generator[TestClient, None, None]:
    """savepoint 기반 격리 TestClient — test_constraints_params.py 패턴 그대로."""
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


@pytest.fixture
def two_baselines(db: Session) -> tuple[str, str]:
    """4-1, 4-2 두 제약에 대한 두 개의 베이스라인 history 행을 시드.

    BASELINE_<tag>_<iso> 패턴을 따라 changed_by 를 구성. 두 베이스라인이
    서로 다른 시각에 존재하도록 changed_at 을 명시적으로 분리.
    """
    base_iso_a = "20260101T000000Z"
    base_iso_b = "20260201T000000Z"
    tag_a = f"BASELINE_test-baseA_{base_iso_a}"
    tag_b = f"BASELINE_test-baseB_{base_iso_b}"

    now = datetime.now(timezone.utc)

    # baseline A: 4-1 stranding_min=210, 4-2 sheath_color_min=120
    db.add(
        ConstraintConfigHistory(
            constraint_id="4-1",
            old_params_json={},
            new_params_json={"stranding_min": 210, "insulation_min": 60},
            changed_by=tag_a,
            changed_at=now - timedelta(days=10),
        )
    )
    db.add(
        ConstraintConfigHistory(
            constraint_id="4-2",
            old_params_json={},
            new_params_json={"sheath_color_min": 120},
            changed_by=tag_a,
            changed_at=now - timedelta(days=10),
        )
    )

    # baseline B: 4-1 stranding_min=180 (변경), insulation_min 동일,
    # 4-2 는 베이스라인에 미포함 (한쪽에만 존재하는 케이스 검증)
    # + 4-4 신규 추가 (반대쪽에만 존재)
    db.add(
        ConstraintConfigHistory(
            constraint_id="4-1",
            old_params_json={},
            new_params_json={"stranding_min": 180, "insulation_min": 60},
            changed_by=tag_b,
            changed_at=now - timedelta(days=5),
        )
    )
    db.add(
        ConstraintConfigHistory(
            constraint_id="4-4",
            old_params_json={},
            new_params_json={"welding_min": 45},
            changed_by=tag_b,
            changed_at=now - timedelta(days=5),
        )
    )
    db.flush()  # routes 가 보도록 visible 하게 — commit 은 outer SAVEPOINT 에 흡수

    return tag_a, tag_b


def test_diff_two_baselines(
    db: Session, client: TestClient, two_baselines: tuple[str, str]
) -> None:
    """두 베이스라인의 params_json 차이를 정확히 잡아낸다."""
    tag_a, tag_b = two_baselines

    resp = client.get(f"/api/constraints/versions/{tag_a}/diff/{tag_b}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["version_a"] == tag_a
    assert body["version_b"] == tag_b
    assert isinstance(body["diff"], list)

    diff_index = {(r["constraint_id"], r["field"]): r for r in body["diff"]}

    # 4-1 stranding_min: 210 vs 180 — 차이 있음
    assert ("4-1", "stranding_min") in diff_index
    row = diff_index[("4-1", "stranding_min")]
    assert row["value_a"] == 210
    assert row["value_b"] == 180

    # 4-1 insulation_min: 60 vs 60 — 동일, diff 에 포함되지 않음
    assert ("4-1", "insulation_min") not in diff_index

    # 4-2 sheath_color_min: A 에만 존재 → value_b is None
    assert ("4-2", "sheath_color_min") in diff_index
    row = diff_index[("4-2", "sheath_color_min")]
    assert row["value_a"] == 120
    assert row["value_b"] is None

    # 4-4 welding_min: B 에만 존재 → value_a is None
    assert ("4-4", "welding_min") in diff_index
    row = diff_index[("4-4", "welding_min")]
    assert row["value_a"] is None
    assert row["value_b"] == 45


def test_baselines_list_returns_seeded_baselines(
    db: Session, client: TestClient, two_baselines: tuple[str, str]
) -> None:
    """list_baselines 가 시드된 두 베이스라인을 정확히 노출."""
    tag_a, tag_b = two_baselines

    resp = client.get("/api/constraints/baselines")
    assert resp.status_code == 200
    body = resp.json()
    assert "baselines" in body

    by_changed_by = {b["changed_by"]: b for b in body["baselines"]}
    assert tag_a in by_changed_by
    assert tag_b in by_changed_by

    # tag 파싱: BASELINE_test-baseA_20260101T000000Z → "test-baseA"
    assert by_changed_by[tag_a]["tag"] == "test-baseA"
    assert by_changed_by[tag_b]["tag"] == "test-baseB"

    # row_count: A 는 4-1, 4-2 → 2; B 는 4-1, 4-4 → 2
    assert by_changed_by[tag_a]["row_count"] == 2
    assert by_changed_by[tag_b]["row_count"] == 2

    # created_at 은 ISO 문자열
    assert isinstance(by_changed_by[tag_a]["created_at"], str)


def test_diff_404_when_baseline_missing(db: Session, client: TestClient) -> None:
    """존재하지 않는 changed_by 태그는 404."""
    resp = client.get(
        "/api/constraints/versions/BASELINE_does-not-exist_20990101T000000Z"
        "/diff/BASELINE_also-missing_20990101T000000Z"
    )
    assert resp.status_code == 404
    assert "찾을 수 없" in resp.json()["detail"]


def test_diff_404_when_only_one_side_missing(
    db: Session, client: TestClient, two_baselines: tuple[str, str]
) -> None:
    """한쪽 베이스라인만 존재해도 404 — 비교 불가."""
    tag_a, _tag_b = two_baselines
    resp = client.get(
        f"/api/constraints/versions/{tag_a}"
        "/diff/BASELINE_does-not-exist_20990101T000000Z"
    )
    assert resp.status_code == 404
