"""batch_grouping 4-1 (규격교체) fallback — ConstraintConfig 연동 회귀."""

from sqlalchemy.orm import Session

from app.infrastructure.models.constraint_config import ConstraintConfig
from app.services.constraint_params import ConstraintParams


def _get_4_1(db: Session) -> ConstraintConfig:
    return (
        db.query(ConstraintConfig)
        .filter(ConstraintConfig.constraint_id == "4-1")
        .first()
    )


def test_4_1_seed_has_stranding_min_210(db: Session) -> None:
    """No-op 불변식 전제: 시드값 210 유지."""
    row = _get_4_1(db)
    assert row is not None
    assert row.params_json.get("stranding_min") == 210


def test_constraint_params_reads_4_1(db: Session) -> None:
    """batch_grouping 에서 ConstraintParams.get 로 읽을 때 시드값과 일치."""
    params = ConstraintParams.load(db)
    assert params.get("4-1", "stranding_min") == 210.0
