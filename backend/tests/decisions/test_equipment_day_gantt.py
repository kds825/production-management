"""Phase 6 Step 3b — equipment_day_gantt.py 단위 테스트.

Critical: SheathGanttBuilder.sort_key 가 memory `feedback_sheath_sort_order`
3차 iteration (납기 1차) 에 정확히 부합하는지 검증. 정렬 라벨 + 정렬 결과
모두 cluster_sort_key (domain/sheath_cluster.py:188-204) 와 동일해야 한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pytest

from app.application.decisions.equipment_day_gantt import (
    DefaultGanttBuilder,
    EquipmentDayGanttBuilder,
    InsulationGanttBuilder,
    OutsourceGanttBuilder,
    SheathGanttBuilder,
    StrandingGanttBuilder,
    get_gantt_builder,
    register_gantt_builder,
    reset_gantt_builders,
)


@dataclass
class FakeRow:
    latest_due: date | None = None
    pred_ready_wmin: int = 0
    color_rank: int = 0
    cluster_id: str = ""
    stranding_type_rank: int = 0
    sq_mm2: float = 0.0
    color_group_rank: int = 0
    compound_rank: int = 0
    order_at: date | None = None
    vendor_name: str = ""
    inbound_at: date | None = None


@pytest.fixture(autouse=True)
def _reset():
    reset_gantt_builders()
    yield
    reset_gantt_builders()


# ─────────────────────────────────────────────────────────────────────────
# Protocol / register / get
# ─────────────────────────────────────────────────────────────────────────


def test_protocol_runtime_check():
    for cls in (
        SheathGanttBuilder,
        StrandingGanttBuilder,
        InsulationGanttBuilder,
        OutsourceGanttBuilder,
        DefaultGanttBuilder,
    ):
        inst = cls()
        assert isinstance(inst, EquipmentDayGanttBuilder)


def test_register_get_dispatches():
    register_gantt_builder(DefaultGanttBuilder())
    register_gantt_builder(SheathGanttBuilder())
    assert get_gantt_builder("sheath").process_key == "sheath"
    assert get_gantt_builder("unknown").process_key == "default"


def test_no_registry_raises():
    with pytest.raises(RuntimeError, match="No gantt builder"):
        get_gantt_builder("anything")


# ─────────────────────────────────────────────────────────────────────────
# Sheath — memory 3차 정렬 정확성 (CRITICAL)
# ─────────────────────────────────────────────────────────────────────────


def test_sheath_sort_label_matches_memory():
    """memory feedback_sheath_sort_order 3차 — 납기 가까운 순 1차."""
    assert (
        SheathGanttBuilder().sort_label()
        == "① 납기 가까운 순 → ② 전공정 ready 시각 → ③ 색상 인접 순"
    )


def test_sheath_sort_key_due_date_primary():
    """납기가 다르면 납기가 기준. pred_ready 가 더 좋아도 납기 우선."""
    builder = SheathGanttBuilder()
    early_due_late_pred = FakeRow(
        latest_due=date(2026, 4, 30),
        pred_ready_wmin=2000,  # 늦은 pred_ready
        color_rank=5,
        cluster_id="C-A",
    )
    late_due_early_pred = FakeRow(
        latest_due=date(2026, 5, 5),
        pred_ready_wmin=100,  # 빠른 pred_ready
        color_rank=0,
        cluster_id="C-B",
    )
    rows = [late_due_early_pred, early_due_late_pred]
    rows.sort(key=builder.sort_key)
    # 납기 빠른 cluster 먼저 — pred_ready 가 늦어도
    assert rows[0].cluster_id == "C-A"
    assert rows[1].cluster_id == "C-B"


def test_sheath_sort_key_pred_ready_secondary_within_same_due():
    """같은 납기 내에서 pred_ready 빠른 cluster 먼저."""
    builder = SheathGanttBuilder()
    same_due = date(2026, 4, 30)
    early_pred = FakeRow(
        latest_due=same_due,
        pred_ready_wmin=100,
        color_rank=5,
        cluster_id="C-EARLY",
    )
    late_pred = FakeRow(
        latest_due=same_due,
        pred_ready_wmin=2000,
        color_rank=0,
        cluster_id="C-LATE",
    )
    rows = [late_pred, early_pred]
    rows.sort(key=builder.sort_key)
    assert rows[0].cluster_id == "C-EARLY"
    assert rows[1].cluster_id == "C-LATE"


def test_sheath_sort_key_color_tertiary_within_same_due_pred():
    """같은 납기 + 같은 pred_ready 면 color_rank 가 결정."""
    builder = SheathGanttBuilder()
    base = dict(latest_due=date(2026, 4, 30), pred_ready_wmin=500)
    rows = [
        FakeRow(**base, color_rank=5, cluster_id="C-COLOR-LATE"),
        FakeRow(**base, color_rank=0, cluster_id="C-COLOR-EARLY"),
    ]
    rows.sort(key=builder.sort_key)
    assert rows[0].cluster_id == "C-COLOR-EARLY"
    assert rows[1].cluster_id == "C-COLOR-LATE"


def test_sheath_sort_key_cluster_id_deterministic_tiebreak():
    """모든 1~3차가 동률이면 cluster_id 가 결정적 tiebreak."""
    builder = SheathGanttBuilder()
    base = dict(latest_due=date(2026, 4, 30), pred_ready_wmin=0, color_rank=0)
    rows = [
        FakeRow(**base, cluster_id="C-Z"),
        FakeRow(**base, cluster_id="C-A"),
    ]
    rows.sort(key=builder.sort_key)
    assert rows[0].cluster_id == "C-A"


def test_sheath_sort_key_handles_null_due():
    """latest_due 가 None 이면 date.max — 가장 마지막."""
    builder = SheathGanttBuilder()
    rows = [
        FakeRow(latest_due=None, cluster_id="C-NO-DUE"),
        FakeRow(latest_due=date(2026, 4, 30), cluster_id="C-WITH-DUE"),
    ]
    rows.sort(key=builder.sort_key)
    assert rows[0].cluster_id == "C-WITH-DUE"


# ─────────────────────────────────────────────────────────────────────────
# Stranding / Insulation / Outsource / Default — sort_label 만 검증
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "cls,expected",
    [
        (StrandingGanttBuilder, "① 연선방식 → ② 납기 → ③ SQ"),
        (InsulationGanttBuilder, "① 색상그룹 → ② 납기 → ③ 컴파운드 종류"),
        (OutsourceGanttBuilder, "① 외주 발주일 → ② 협력사별 capacity → ③ 입고일"),
        (DefaultGanttBuilder, "ERP 적재 순서"),
    ],
)
def test_other_sort_labels(cls, expected):
    assert cls().sort_label() == expected


def test_stranding_sort_groups_by_stranding_type():
    builder = StrandingGanttBuilder()
    # 다른 연선방식 — 같은 납기여도 stranding_type_rank 가 1차
    rows = [
        FakeRow(
            stranding_type_rank=2,
            latest_due=date(2026, 4, 30),
            cluster_id="ROUND",
        ),
        FakeRow(
            stranding_type_rank=0,
            latest_due=date(2026, 5, 5),
            cluster_id="COMPRESS",
        ),
    ]
    rows.sort(key=builder.sort_key)
    assert rows[0].cluster_id == "COMPRESS"  # rank 0 먼저
