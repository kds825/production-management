"""Track B (Plan Week 5A.4) — Admin UI priority 슬라이더 ↔ objective wiring.

이전 상태: `_spec_weight()` 가 `params.weight` 만 읽고 `priority` 컬럼 무시
         → /master/constraints 슬라이더 변경해도 objective 불변.
변경 후:  `_spec_weight_factory` 가 `weight × (priority / 50.0)` 로 스케일.
         priority=50 (DB default, 모든 W-* row) = factor 1.0 → parity 보존.
         priority=100 = 2.0× / priority=0 = effective off.

본 파일은 단위 테스트 (factory 행위 검증). e2e 는 test_priority_slider_e2e.py.
"""

from __future__ import annotations

import pytest

from app.application.scheduling.cp_sat.constraint_loader import ConstraintSpec


def _make_spec(cid: str, *, weight: int, priority: int) -> ConstraintSpec:
    """ConstraintSpec frozen dataclass fixture — 테스트용 최소 인자만 채움."""
    from datetime import datetime

    return ConstraintSpec(
        constraint_id=cid,
        constraint_name=cid,
        category="weight",
        is_enabled=True,
        priority=priority,
        impact_level=None,
        implementation_type="code_logic",
        params={"weight": weight},
        applicable_processes=(),
        notes=None,
        updated_at=datetime(2026, 4, 28),
    )


def test_factory_priority_50_preserves_baseline():
    """priority=50 = factor 1.0 = 기존 W-* row weight 그대로."""
    from app.application.scheduling.cp_sat.orchestrator import _spec_weight_factory

    fn = _spec_weight_factory({"X": _make_spec("X", weight=1000, priority=50)})
    assert fn("X", fallback=999) == 1000


def test_factory_priority_100_doubles_weight():
    from app.application.scheduling.cp_sat.orchestrator import _spec_weight_factory

    fn = _spec_weight_factory({"X": _make_spec("X", weight=1000, priority=100)})
    assert fn("X", fallback=999) == 2000


def test_factory_priority_0_effective_off():
    """priority=0 → factor 0 → effective ignore (constraint 비활성과 비슷한 효과)."""
    from app.application.scheduling.cp_sat.orchestrator import _spec_weight_factory

    fn = _spec_weight_factory({"X": _make_spec("X", weight=1000, priority=0)})
    assert fn("X", fallback=999) == 0


def test_factory_missing_spec_returns_fallback():
    """spec 없음 → 하드코딩 fallback (legacy DB / 테스트 환경 보호)."""
    from app.application.scheduling.cp_sat.orchestrator import _spec_weight_factory

    fn = _spec_weight_factory({})
    assert fn("MISSING", fallback=42) == 42


def test_factory_non_numeric_weight_returns_fallback():
    """params.weight 가 숫자 아님 → fallback (스키마 손상 방어)."""
    from app.application.scheduling.cp_sat.orchestrator import _spec_weight_factory

    spec = _make_spec("X", weight=0, priority=50)
    object.__setattr__(spec, "params", {"weight": "not-a-number"})
    fn = _spec_weight_factory({"X": spec})
    assert fn("X", fallback=42) == 42


def test_factory_priority_75_scales_proportionally():
    """비-경계 값도 정확히 비례."""
    from app.application.scheduling.cp_sat.orchestrator import _spec_weight_factory

    fn = _spec_weight_factory({"X": _make_spec("X", weight=1000, priority=75)})
    # 1000 * (75/50) = 1500
    assert fn("X", fallback=999) == 1500


@pytest.mark.parity
def test_seeded_w_rows_all_priority_50():
    """parity 보존 invariant — 모든 W-* row 가 priority=50 으로 시드되어 있어야
    factor 1.0 로 기존 11 fixture hash 가 유지된다.

    이 테스트가 실패하면 누군가 W-* priority 를 직접 갱신했다는 뜻.
    parity 깨질 가능성이 있으므로 즉시 조사.
    """
    from app.application.scheduling.cp_sat.constraint_loader import (
        load_active_constraints,
    )
    from app.infrastructure.database import SessionLocal

    db = SessionLocal()
    try:
        specs = load_active_constraints(db)
        spec_by_id = {s.constraint_id: s for s in specs}
        for cid in [
            "W-DHARD",
            "W-CHAIN",
            "W-IDLE",
            "W-SLACK",
            "W-PSEV",
            "W-EDDP",
            "W-EDDM",
            "W-TCRIT",
            "W-TURG",
            "W-TNORM",
            "W-TRANS",
        ]:
            assert cid in spec_by_id, f"{cid} not seeded in Supabase"
            assert spec_by_id[cid].priority == 50, (
                f"{cid} priority drift: {spec_by_id[cid].priority} (parity 위험)"
            )
    finally:
        db.close()
