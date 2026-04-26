"""Phase 6 Step 3c-2 — 9 시나리오 snapshot + 도메인 정확성 invariant.

시나리오:
  A — 정상 시스 (베이스라인)
  B — 시스 색상교체 (warn)
  C — 시스 규격교체 (fail)
  D — 시스 D-day P1
  E — 외주 분기 (SQ ≤ 10)
  F — 연선 재공
  G — TFR-GV (절연 스킵)
  H — 묶음 비교 우위
  EMPTY — v1 미노출 공정 (신선/T·P/연합)

도메인 정확성 invariant (memory feedback_tfrg_color + UI review):
  - 시스 카드에는 '압축연선' 부재 (시스/연선 도메인 혼동 차단)
  - 연선 카드에는 '시스재질' 부재
  - 외주 카드는 '외주' 표기 + ❸ 외주 hand-off block

build 시간 < 100ms (DB 1 round trip 포함).
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.application.decisions.build_card import build_decision_card
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
from app.infrastructure.models.audit_log import AuditLog
from app.infrastructure.models.equipment_master import EquipmentMaster
from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.schedule_task import ScheduleTask


@pytest.fixture(scope="module")
def _engine():
    return create_engine(settings.DATABASE_URL)


@pytest.fixture(autouse=True)
def _register_providers():
    """모든 시나리오에 phrasing/section/gantt provider register."""
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


@pytest.fixture
def _db(_engine):
    Session = sessionmaker(bind=_engine, autoflush=False)
    db = Session()
    db.begin_nested()
    try:
        yield db
    finally:
        db.rollback()
        db.close()


# ── 시나리오 builder ──────────────────────────────────────────────────────


def _make_batch(db, **kwargs) -> ProductionBatch:
    """공통 batch 기본값 — 시스/연선/외주 각자 override."""
    defaults = dict(
        run_label=kwargs.pop("run_label", "TEST-3C2"),
        sales_order_id=kwargs.pop("sales_order_id", "ORDER-3C2"),
        sales_order_line=1,
        product_group="LV-150",
        sq_mm2=150,
        voltage="600",
        core_count=1,
        customer_name="한국전력",
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
        batch_group="시스_150SQ_G01",
    )
    defaults.update(kwargs)
    b = ProductionBatch(**defaults)
    db.add(b)
    db.flush()
    return b


def _ensure_equipment(db, equipment_code, **overrides) -> EquipmentMaster:
    """FK 충족 — equipment_master row 가 없으면 신규 INSERT (savepoint rollback)."""
    existing = (
        db.query(EquipmentMaster)
        .filter(EquipmentMaster.equipment_code == equipment_code)
        .one_or_none()
    )
    if existing is not None:
        return existing
    defaults = dict(
        equipment_code=equipment_code,
        equipment_name=f"테스트설비-{equipment_code}",
        process_name="저압시스",
        material_limit="ALL",
        range_min=100,
        range_max=300,
        range_unit="SQ",
        color_group="A120",
        base_working_hours=20,
        shift_type="2교대",
    )
    defaults.update(overrides)
    eq = EquipmentMaster(**defaults)
    db.add(eq)
    db.flush()
    return eq


def _make_task(db, batch, **kwargs) -> ScheduleTask:
    base = datetime(2026, 4, 30, 14, 0)
    equipment_code = kwargs.pop("equipment_code", "EQ-SHEATH-3")
    eq_overrides = kwargs.pop("equipment_overrides", {})
    _ensure_equipment(db, equipment_code, **eq_overrides)
    defaults = dict(
        batch_id=batch.batch_id,
        equipment_code=equipment_code,
        start_datetime=kwargs.pop("start_datetime", base),
        end_datetime=kwargs.pop("end_datetime", base + timedelta(hours=8, minutes=30)),
        run_label=batch.run_label,
        batch_group=batch.batch_group,
        status="scheduled",
    )
    defaults.update(kwargs)
    t = ScheduleTask(**defaults)
    db.add(t)
    db.flush()
    return t


def _make_audit(db, batch, action_type, constraints_applied=None, alternatives=None):
    a = AuditLog(
        run_label=batch.run_label,
        stage="stage1",
        batch_id=batch.batch_id,
        action_type=action_type,
        constraints_applied=constraints_applied or [],
        alternatives_considered=alternatives,
        decision_reason=f"test scenario {action_type}",
    )
    db.add(a)
    db.flush()
    return a


# ─────────────────────────────────────────────────────────────────────────
# §A — 정상 시스 (베이스라인)
# ─────────────────────────────────────────────────────────────────────────


def test_scenario_A_normal_sheath(_db):
    batch = _make_batch(_db)
    task = _make_task(_db, batch)
    _make_audit(
        _db,
        batch,
        action_type="constraint_checked",
        constraints_applied=[
            {"id": "5-1", "name": "SQ 적합", "result": "pass"},
            {
                "id": "4-2",
                "name": "색상교체",
                "result": "pass",
                "params": {"minutes": 0},
            },
        ],
    )

    card = build_decision_card(batch.batch_id, _db, debug=False)

    assert card.process_key == "sheath"
    assert card.process_label.startswith("저압시스")
    assert card.placement_text  # 빈 문자열 X
    # ❶ Why 줄에 4-2_save 자연어 — '색상교체 시간이 들지 않습니다'
    assert any("색상교체 시간이 들지 않습니다" in line.natural for line in card.why)
    # severity 'save' — 정상 케이스
    assert any(line.severity == "save" for line in card.why)
    # debug 미요청
    assert card.debug is None


# ─────────────────────────────────────────────────────────────────────────
# §B — 시스 색상교체 (warn)
# ─────────────────────────────────────────────────────────────────────────


def test_scenario_B_color_change_warn(_db):
    batch = _make_batch(_db, sheath_color="청")
    _make_task(_db, batch)
    _make_audit(
        _db,
        batch,
        action_type="constraint_checked",
        constraints_applied=[
            {
                "id": "4-2",
                "name": "색상교체",
                "result": "pass",
                "params": {"prev_color": "흑", "minutes": 120},
            }
        ],
    )

    card = build_decision_card(batch.batch_id, _db, debug=False)
    # ❶: '직전 묶음이 흑이라 색상교체 시간 120분이 발생합니다'
    assert any("색상교체 시간 120분이 발생합니다" in line.natural for line in card.why)
    # severity warn
    assert any(line.severity == "warn" for line in card.why)


# ─────────────────────────────────────────────────────────────────────────
# §C — 시스 규격교체 (fail kind fixed danger)
# ─────────────────────────────────────────────────────────────────────────


def test_scenario_C_spec_change_fail(_db):
    batch = _make_batch(_db)
    _make_task(_db, batch)
    _make_audit(
        _db,
        batch,
        action_type="constraint_checked",
        constraints_applied=[
            {
                "id": "spec-change",
                "name": "규격교체",
                "result": "pass",
                "params": {"minutes": 300},
            }
        ],
    )

    card = build_decision_card(batch.batch_id, _db, debug=False)
    # ❷ Impact 셀 — 규격교체 fail (kind fixed danger)
    spec_cells = [c for c in card.impact.cells if c.kind == "spec_change"]
    assert spec_cells
    assert spec_cells[0].severity == "fail"


# ─────────────────────────────────────────────────────────────────────────
# §D — 시스 D-day P1 (납기 임박)
# ─────────────────────────────────────────────────────────────────────────


def test_scenario_D_dday_p1(_db):
    batch = _make_batch(_db, customer_priority=1)
    _make_task(_db, batch)
    _make_audit(
        _db,
        batch,
        action_type="constraint_checked",
        constraints_applied=[
            {
                "id": "due_slack",
                "name": "납기 여유",
                "result": "pass",
                "params": {"days": 0.0},
            }
        ],
    )

    card = build_decision_card(batch.batch_id, _db, debug=False)
    assert card.customer_priority == 1
    due_cells = [c for c in card.impact.cells if c.kind == "due_slack_days"]
    assert due_cells
    # 0.0 = warn (-0.5~+0.5 범위)
    assert due_cells[0].severity == "warn"


# ─────────────────────────────────────────────────────────────────────────
# §E — 외주 분기 (SQ ≤ 10)
# ─────────────────────────────────────────────────────────────────────────


def test_scenario_E_outsource(_db):
    batch = _make_batch(_db, sq_mm2=10, batch_group="외주_10SQ_G01")
    _make_task(_db, batch, equipment_code="EQ-OUT-VH")
    _make_audit(
        _db,
        batch,
        action_type="outsource_handoff",
    )

    card = build_decision_card(batch.batch_id, _db, debug=False)
    # _resolve_key 외주 우선 (SQ ≤ 10)
    assert card.process_key == "outsource"
    # ❸ outsource_handoff 채워짐
    assert card.outsource_handoff is not None
    assert card.handoff is None
    assert card.wip_match is None


# ─────────────────────────────────────────────────────────────────────────
# §F — 연선 재공 (WipMatchBlock)
# ─────────────────────────────────────────────────────────────────────────


def test_scenario_F_stranding_wip_match(_db):
    batch = _make_batch(
        _db,
        process_name="연선",
        sheath_color="",
        product_group="LV-150",
        wip_matched_id=None,  # FK 무 — 테스트 격리
        batch_group="연선_150SQ_G01",
    )
    _make_task(_db, batch, equipment_code="EQ-STRAND-1")

    card = build_decision_card(batch.batch_id, _db, debug=False)
    assert card.process_key == "stranding"
    # ❸: WipMatchBlock 채워짐
    assert card.wip_match is not None
    assert card.handoff is None
    assert card.outsource_handoff is None


# ─────────────────────────────────────────────────────────────────────────
# §G — TFR-GV (절연 스킵)
# ─────────────────────────────────────────────────────────────────────────


def test_scenario_G_tfr_gv_skip_insulation(_db):
    batch = _make_batch(
        _db,
        product_group="TFR-GV",
        sq_mm2=50,  # > 25 (단선접지선 아님)
        batch_group="시스_TFR-GV_G01",
    )
    _make_task(_db, batch)

    card = build_decision_card(batch.batch_id, _db, debug=False)
    # 시스 카드 — TFR-GV ribbon
    assert card.process_key == "sheath"
    assert "TFR-GV" in card.process_label
    # ❸ HandoffBlock — predecessor_label 이 '연선 (절연 스킵 · TFR-GV)'
    assert card.handoff is not None
    assert "연선 (절연 스킵 · TFR-GV)" in card.handoff.predecessor_label


# ─────────────────────────────────────────────────────────────────────────
# §H — 묶음 비교 우위
# ─────────────────────────────────────────────────────────────────────────


def test_scenario_H_bundle_compare(_db):
    """본 batch + 같은 run 의 다른 batch_group 들 → bundle_compare 채워짐."""
    batch = _make_batch(_db, batch_group="시스_150SQ_G01")
    _make_task(_db, batch)

    # 같은 run_label 의 다른 batch_group 추가 — bundle_compare alternative source
    for i, group in enumerate(["시스_150SQ_G02", "시스_150SQ_G03", "시스_120SQ_G01"]):
        alt_batch = _make_batch(
            _db,
            sales_order_id=f"ORDER-3C2-ALT-{i}",
            batch_group=group,
            sq_mm2=120 if "120SQ" in group else 150,
        )
        _make_task(
            _db,
            alt_batch,
            start_datetime=datetime(2026, 5, 1 + i, 8, 0),
            end_datetime=datetime(2026, 5, 1 + i, 16, 0),
        )

    card = build_decision_card(batch.batch_id, _db, debug=False)
    # bundle_compare 첫 행은 chosen
    assert card.bundle_compare
    assert card.bundle_compare[0].is_chosen is True
    assert "본 묶음" in card.bundle_compare[0].label
    # alternative 들은 같은 run_label 에서 발견된 다른 batch_group
    alt_labels = [b.label for b in card.bundle_compare[1:]]
    assert any("시스_150SQ_G02" in lbl for lbl in alt_labels)


# ─────────────────────────────────────────────────────────────────────────
# §EMPTY — v1 미노출 공정 (신선/T·P/연합)
# ─────────────────────────────────────────────────────────────────────────


def test_scenario_EMPTY_unsupported_process(_db):
    batch = _make_batch(
        _db,
        process_name="연합",
        product_group="CV-300",
        sheath_color="",
        batch_group="연합_300SQ_G01",
    )
    # task 없이 — 미배정 상태

    card = build_decision_card(batch.batch_id, _db, debug=False)
    assert card.process_key == "default"
    assert card.placement_text == "미배정"
    assert card.equipment_day == []
    # ❸ default — handoff 빈 block
    assert card.handoff is not None
    assert card.handoff.predecessor_label == ""


# ─────────────────────────────────────────────────────────────────────────
# 도메인 정확성 invariant (memory feedback_tfrg_color + UI review)
# ─────────────────────────────────────────────────────────────────────────


def test_sheath_card_does_not_mention_stranding_terms(_db):
    """시스 카드의 자연어 어디에도 '압축연선' 등 연선 도메인 용어가 등장하지 않음."""
    batch = _make_batch(_db)
    _make_task(_db, batch)
    card = build_decision_card(batch.batch_id, _db, debug=False)

    forbidden = ("압축연선", "원형연선", "수밀연선", "7연선코어", "61연선")
    serialized = (
        " ".join(line.natural for line in card.why)
        + " ".join(c.value for c in card.impact.cells)
        + (card.handoff.predecessor_label if card.handoff else "")
        + card.verdict_summary
    )
    for term in forbidden:
        assert term not in serialized, (
            f"시스 카드에 연선 도메인 용어 '{term}' 등장 — domain leak"
        )


def test_stranding_card_does_not_mention_sheath_terms(_db):
    """연선 카드의 자연어 어디에도 '시스재질' 등 시스 도메인 용어가 등장하지 않음."""
    batch = _make_batch(
        _db,
        process_name="연선",
        sheath_color="",
        batch_group="연선_150SQ_G01",
    )
    _make_task(_db, batch, equipment_code="EQ-STRAND-1")
    card = build_decision_card(batch.batch_id, _db, debug=False)

    forbidden = ("시스재질", "PVC·CV·HFFR", "A120 묶음", "A100 묶음")
    serialized = (
        " ".join(line.natural for line in card.why)
        + " ".join(c.value for c in card.impact.cells)
        + card.verdict_summary
    )
    for term in forbidden:
        assert term not in serialized, (
            f"연선 카드에 시스 도메인 용어 '{term}' 등장 — domain leak"
        )


def test_outsource_card_has_outsource_block(_db):
    batch = _make_batch(_db, sq_mm2=10, batch_group="외주_10SQ_G01")
    _make_task(_db, batch, equipment_code="EQ-OUTSOURCE-1")
    card = build_decision_card(batch.batch_id, _db, debug=False)
    assert card.process_key == "outsource"
    assert card.outsource_handoff is not None


# ─────────────────────────────────────────────────────────────────────────
# memory feedback_sheath_sort_order — sort_label 정합
# ─────────────────────────────────────────────────────────────────────────


def test_sheath_card_sort_label_matches_memory(_db):
    batch = _make_batch(_db)
    _make_task(_db, batch)
    card = build_decision_card(batch.batch_id, _db, debug=False)
    assert (
        card.equipment_day_sort_label
        == "① 납기 가까운 순 → ② 전공정 ready 시각 → ③ 색상 인접 순"
    )


# ─────────────────────────────────────────────────────────────────────────
# 성능 — build < 100ms (DB 1 round trip 포함)
# ─────────────────────────────────────────────────────────────────────────


def test_build_decision_card_under_100ms(_db):
    batch = _make_batch(_db)
    _make_task(_db, batch)
    _make_audit(
        _db,
        batch,
        action_type="constraint_checked",
        constraints_applied=[
            {"id": "5-1", "name": "SQ 적합", "result": "pass"},
            {
                "id": "4-2",
                "name": "색상교체",
                "result": "pass",
                "params": {"minutes": 0},
            },
        ],
    )

    # warm-up
    build_decision_card(batch.batch_id, _db, debug=True)

    start = time.perf_counter()
    for _ in range(10):
        build_decision_card(batch.batch_id, _db, debug=True)
    elapsed_ms = (time.perf_counter() - start) * 1000 / 10
    assert elapsed_ms < 100, f"build_decision_card avg {elapsed_ms:.2f}ms > 100ms"


# ─────────────────────────────────────────────────────────────────────────
# Debug block — admin 응답에서 ScheduleTask + solver_decision raw 채워짐
# ─────────────────────────────────────────────────────────────────────────


def test_debug_block_has_schedule_task_row_not_solver_decision(_db):
    """S2/S3 — 시작/종료 시각 source 는 ScheduleTask, alt scores 는 details_json."""
    batch = _make_batch(_db)
    task = _make_task(_db, batch)
    card = build_decision_card(batch.batch_id, _db, debug=True)
    assert card.debug is not None
    assert card.debug.schedule_task_row["task_id"] == task.task_id
    # start_at 는 ScheduleTask source — 본 schema 컬럼명은 schedule_task_row["start_at"]
    assert "start_at" in card.debug.schedule_task_row
