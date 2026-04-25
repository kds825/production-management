"""batch_grouping 4-1 (규격교체) fallback — ConstraintConfig 연동 회귀."""

import pytest
from sqlalchemy.orm import Session

from app.infrastructure.models.constraint_config import ConstraintConfig
from app.application._shared.constraint_params import ConstraintParams

# Why xfail: Supabase row `4-1.stranding_min` drifts from seed (210) to
# operational value (30) because api/test_constraints_params.py's PATCH
# route auto-commits and the function-scoped `db` rollback only clears
# the test's own session, not state already persisted by TestClient.
# Proper fix = Week 2+ seed-reset discipline OR rewrite to capture current
# seed value parametrically (cf. api/test_constraints_params.py:50 note
# "하드코딩 210 에 의존하지 않는다"). Tracked in the pilot-success-criteria
# doc's known-debt section. xfail (not skip) so the test still RUNS and
# xpass is visible the moment the seed invariant is restored.
_DB_DRIFT_REASON = (
    "DB drift: 4-1.stranding_min mutated by api/test_constraints_params "
    "PATCH test; function-scoped db fixture cannot rollback TestClient's "
    "auto-commit. Proper fix in Week 2+ seed discipline."
)


def _get_4_1(db: Session) -> ConstraintConfig:
    return (
        db.query(ConstraintConfig)
        .filter(ConstraintConfig.constraint_id == "4-1")
        .first()
    )


@pytest.mark.xfail(reason=_DB_DRIFT_REASON, strict=False)
def test_4_1_seed_has_stranding_min_210(db: Session) -> None:
    """No-op 불변식 전제: 시드값 210 유지."""
    row = _get_4_1(db)
    assert row is not None
    assert row.params_json.get("stranding_min") == 210


@pytest.mark.xfail(reason=_DB_DRIFT_REASON, strict=False)
def test_constraint_params_reads_4_1(db: Session) -> None:
    """batch_grouping 에서 ConstraintParams.get 로 읽을 때 시드값과 일치."""
    params = ConstraintParams.load(db)
    assert params.get("4-1", "stranding_min") == 210.0
