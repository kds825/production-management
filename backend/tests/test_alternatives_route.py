"""GET /api/decisions/{batch_id}/alternatives tests.

대안 설비 시뮬레이터는 solver 재실행 없이 schedule_task + equipment_master 만
조회해 "이 배치를 다른 설비로 옮기면" 시나리오를 비교한다. PoC 의사결정
지원 패널 (DecisionConstraintsModal P2(i)) 의 backend.

테스트 케이스:
  1. 404 — 존재하지 않는 batch
  2. 호환 설비 후보 산정 — SQ range / 재질 / 색상그룹 룰
  3. 충돌 카운트 + 가용 시점 — schedule_task 시간 겹침 평가
  4. 지연일 계산 — due_date 와의 차이

테스트 셋업은 ``test_decisions_route.py`` 의 savepoint TestClient 패턴을
동일하게 사용한다.
"""

from __future__ import annotations

import uuid
from collections.abc import Generator
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.infrastructure.database import get_db
from app.infrastructure.models.equipment_master import EquipmentMaster
from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.schedule_task import ScheduleTask
from app.main import app


# ────────────────────────────────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────────────────────────────────


@pytest.fixture
def client(db: Session) -> Generator[TestClient, None, None]:
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


def _unique_code(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:6].upper()}"


def _unique_process() -> str:
    """테스트별 unique process_name — 운영 Supabase 에 같은 process_name 의
    seeded equipment 가 존재하면 호환 필터 단계에서 후보로 섞여 들어 단언이
    깨진다. 매 테스트 새 process 명을 발급해 격리."""
    return f"alt-test-{uuid.uuid4().hex[:8]}"


def _make_equipment(
    db: Session,
    *,
    process_name: str,
    range_min: float | None = None,
    range_max: float | None = None,
    material_limit: str | None = None,
    color_group: str | None = None,
    name_suffix: str = "",
) -> EquipmentMaster:
    eq = EquipmentMaster(
        equipment_code=_unique_code("EQ"),
        equipment_name=f"테스트설비{name_suffix}",
        process_name=process_name,
        range_min=range_min,
        range_max=range_max,
        material_limit=material_limit,
        color_group=color_group,
    )
    db.add(eq)
    db.flush()
    return eq


def _make_batch(
    db: Session,
    *,
    process_name: str,
    sq_mm2: float = 25.0,
    conductor_material: str | None = None,
    sheath_color: str | None = None,
    due_date: date | None = None,
    estimated_duration_min: float | None = 240.0,
) -> ProductionBatch:
    batch = ProductionBatch(
        run_label=f"alt-test-{uuid.uuid4().hex[:8]}",
        process_name=process_name,
        sq_mm2=sq_mm2,
        conductor_material=conductor_material,
        sheath_color=sheath_color,
        due_date=due_date,
        estimated_duration_min=estimated_duration_min,
    )
    db.add(batch)
    db.flush()
    return batch


def _make_task(
    db: Session,
    *,
    batch_id: int,
    equipment_code: str,
    start: datetime,
    end: datetime,
) -> ScheduleTask:
    task = ScheduleTask(
        batch_id=batch_id,
        equipment_code=equipment_code,
        start_datetime=start,
        end_datetime=end,
    )
    db.add(task)
    db.flush()
    return task


# ────────────────────────────────────────────────────────────────────────
# Test 1: 404 on missing batch
# ────────────────────────────────────────────────────────────────────────


def test_alternatives_returns_404_for_missing_batch(client: TestClient) -> None:
    resp = client.get("/api/decisions/999999999/alternatives")
    assert resp.status_code == 404


def test_alternatives_returns_404_for_non_numeric_id(client: TestClient) -> None:
    resp = client.get("/api/decisions/not-a-number/alternatives")
    assert resp.status_code == 404


# ────────────────────────────────────────────────────────────────────────
# Test 2: Compatibility filter — SQ range, material, color group
# ────────────────────────────────────────────────────────────────────────


def test_alternatives_filters_by_sq_range(db: Session, client: TestClient) -> None:
    """SQ 25mm² batch 는 range_min=10/range_max=50 설비만 후보."""
    proc = _unique_process()
    other_proc = _unique_process()
    batch = _make_batch(db, process_name=proc, sq_mm2=25.0)

    # 호환: 25 ∈ [10, 50]
    eq_ok = _make_equipment(
        db, process_name=proc, range_min=10, range_max=50, name_suffix="OK"
    )
    # 비호환: 25 < range_min=30
    _make_equipment(
        db, process_name=proc, range_min=30, range_max=100, name_suffix="LO"
    )
    # 비호환: 25 > range_max=20
    _make_equipment(db, process_name=proc, range_min=1, range_max=20, name_suffix="HI")
    # 비호환: 다른 공정
    _make_equipment(
        db, process_name=other_proc, range_min=10, range_max=50, name_suffix="P2"
    )

    resp = client.get(f"/api/decisions/{batch.batch_id}/alternatives")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    codes = [a["equipment_code"] for a in body["alternatives"]]
    assert codes == [eq_ok.equipment_code]


def test_alternatives_filters_by_material(db: Session, client: TestClient) -> None:
    """CU batch 는 material_limit=CU 설비만 — AL 설비 제외."""
    proc = _unique_process()
    batch = _make_batch(db, process_name=proc, conductor_material="CU")

    eq_cu = _make_equipment(
        db, process_name=proc, material_limit="CU", name_suffix="CU"
    )
    _make_equipment(db, process_name=proc, material_limit="AL", name_suffix="AL")
    eq_all = _make_equipment(
        db, process_name=proc, material_limit="ALL", name_suffix="A"
    )

    resp = client.get(f"/api/decisions/{batch.batch_id}/alternatives")
    body = resp.json()
    codes = {a["equipment_code"] for a in body["alternatives"]}
    assert codes == {eq_cu.equipment_code, eq_all.equipment_code}


# ────────────────────────────────────────────────────────────────────────
# Test 3: Conflict count + earliest_available
# ────────────────────────────────────────────────────────────────────────


def test_alternatives_counts_conflicts_and_computes_earliest(
    db: Session, client: TestClient
) -> None:
    """다른 설비에 4시간 충돌이 있으면 earliest_available 가 그 종료 직후로 시프트.

    구성:
      - batch.due_date 는 멀리 (지연 없음 확인)
      - 현재 task: EQ-A 08:00-12:00
      - EQ-B 에는 09:00-13:00 다른 batch 가 이미 차지 → 충돌
      - EQ-C 는 비어있음 → 충돌 0
    """
    today = datetime(2026, 6, 1)
    proc = _unique_process()
    batch = _make_batch(db, process_name=proc, due_date=date(2026, 12, 31))

    eq_a = _make_equipment(db, process_name=proc, name_suffix="A")
    eq_b = _make_equipment(db, process_name=proc, name_suffix="B")
    eq_c = _make_equipment(db, process_name=proc, name_suffix="C")

    # 현재 배정 — EQ-A 08:00-12:00
    _make_task(
        db,
        batch_id=batch.batch_id,
        equipment_code=eq_a.equipment_code,
        start=today.replace(hour=8),
        end=today.replace(hour=12),
    )

    # EQ-B 에 다른 batch 가 09:00-13:00 점유
    other_batch = _make_batch(db, process_name=proc, due_date=date(2026, 12, 31))
    _make_task(
        db,
        batch_id=other_batch.batch_id,
        equipment_code=eq_b.equipment_code,
        start=today.replace(hour=9),
        end=today.replace(hour=13),
    )

    resp = client.get(f"/api/decisions/{batch.batch_id}/alternatives")
    body = resp.json()

    by_code = {a["equipment_code"]: a for a in body["alternatives"]}
    assert by_code[eq_a.equipment_code]["is_current"] is True
    assert by_code[eq_a.equipment_code]["conflict_count"] == 0
    assert by_code[eq_b.equipment_code]["conflict_count"] == 1
    # EQ-B 의 earliest_available 은 충돌 종료 (13:00) 이후
    eq_b_avail = body["alternatives"]
    eq_b_iso = next(
        a["earliest_available"]
        for a in eq_b_avail
        if a["equipment_code"] == eq_b.equipment_code
    )
    assert "T13:00:00" in eq_b_iso  # 13:00 으로 시프트
    assert by_code[eq_c.equipment_code]["conflict_count"] == 0


# ────────────────────────────────────────────────────────────────────────
# Test 4: Delay days vs due_date
# ────────────────────────────────────────────────────────────────────────


def test_alternatives_computes_delay_days(db: Session, client: TestClient) -> None:
    """충돌로 종료가 due_date 를 넘기면 delay_days 양수."""
    today = datetime(2026, 6, 1)
    proc = _unique_process()
    # batch duration 4h, due_date = 2026-06-01 (오늘)
    batch = _make_batch(
        db,
        process_name=proc,
        due_date=date(2026, 6, 1),
        estimated_duration_min=240.0,
    )

    eq_a = _make_equipment(db, process_name=proc, name_suffix="A")
    eq_b = _make_equipment(db, process_name=proc, name_suffix="B")

    # 현재 배정 — EQ-A 08:00-12:00 (당일 완료)
    _make_task(
        db,
        batch_id=batch.batch_id,
        equipment_code=eq_a.equipment_code,
        start=today.replace(hour=8),
        end=today.replace(hour=12),
    )

    # EQ-B 는 다음날 24시간 점유 — 옮기면 +1일 지연
    other = _make_batch(db, process_name=proc)
    _make_task(
        db,
        batch_id=other.batch_id,
        equipment_code=eq_b.equipment_code,
        start=today.replace(hour=8),
        end=(today + timedelta(days=1)).replace(hour=8),
    )

    resp = client.get(f"/api/decisions/{batch.batch_id}/alternatives")
    body = resp.json()
    by_code = {a["equipment_code"]: a for a in body["alternatives"]}

    assert by_code[eq_a.equipment_code]["delay_days"] == 0
    # EQ-B 로 옮기면 6/2 08:00 시작 → 12:00 종료 → due_date 2026-06-01 대비 +1
    assert by_code[eq_b.equipment_code]["delay_days"] == 1


# ────────────────────────────────────────────────────────────────────────
# Test 5: Sort order — current first, then by delay ascending
# ────────────────────────────────────────────────────────────────────────


def test_alternatives_sort_current_first_then_delay(
    db: Session, client: TestClient
) -> None:
    today = datetime(2026, 6, 1)
    proc = _unique_process()
    batch = _make_batch(
        db,
        process_name=proc,
        due_date=date(2026, 6, 1),
        estimated_duration_min=240.0,
    )

    eq_delayed = _make_equipment(db, process_name=proc, name_suffix="D")
    eq_current = _make_equipment(db, process_name=proc, name_suffix="C")
    eq_free = _make_equipment(db, process_name=proc, name_suffix="F")

    _make_task(
        db,
        batch_id=batch.batch_id,
        equipment_code=eq_current.equipment_code,
        start=today.replace(hour=8),
        end=today.replace(hour=12),
    )

    other = _make_batch(db, process_name=proc)
    _make_task(
        db,
        batch_id=other.batch_id,
        equipment_code=eq_delayed.equipment_code,
        start=today.replace(hour=8),
        end=(today + timedelta(days=1)).replace(hour=8),
    )

    resp = client.get(f"/api/decisions/{batch.batch_id}/alternatives")
    body = resp.json()

    codes = [a["equipment_code"] for a in body["alternatives"]]
    # 현재 → 충돌없음(지연 0) → 지연
    assert codes[0] == eq_current.equipment_code
    assert codes[1] == eq_free.equipment_code
    assert codes[2] == eq_delayed.equipment_code
