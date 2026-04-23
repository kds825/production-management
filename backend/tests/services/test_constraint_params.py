"""ConstraintParams 프리페치 캐시 헬퍼 테스트."""

import pytest
from sqlalchemy.orm import Session

from app.services.constraint_params import ConstraintParams


def test_load_builds_dict_of_params(db: Session) -> None:
    """DB 모든 ConstraintConfig row를 constraint_id 키 dict로 프리페치한다."""
    params = ConstraintParams.load(db)
    # 시드된 4-1 / 4-2 / 4-4 가 존재해야 함
    assert "4-1" in params.by_id
    assert params.by_id["4-1"].get("stranding_min") == 210
    assert params.by_id["4-2"].get("sheath_color_min") == 120
    assert params.by_id["4-4"].get("welding_min") == 30


def test_get_returns_value(db: Session) -> None:
    params = ConstraintParams.load(db)
    assert params.get("4-1", "stranding_min") == 210.0
    assert isinstance(params.get("4-1", "stranding_min"), float)


def test_get_uses_default_when_key_missing(db: Session) -> None:
    params = ConstraintParams.load(db)
    assert params.get("4-1", "nonexistent_key", default=99.0) == 99.0


def test_get_raises_when_row_missing_and_no_default(db: Session) -> None:
    params = ConstraintParams(by_id={})
    with pytest.raises(RuntimeError, match="ConstraintConfig '4-1' row not found"):
        params.get("4-1", "stranding_min")


def test_get_raises_when_key_missing_and_no_default(db: Session) -> None:
    params = ConstraintParams(by_id={"4-1": {}})
    with pytest.raises(RuntimeError, match="key 'stranding_min' missing"):
        params.get("4-1", "stranding_min")


def test_frozen_dataclass_prevents_mutation() -> None:
    params = ConstraintParams(by_id={"4-1": {"stranding_min": 210}})
    with pytest.raises(Exception):  # dataclasses.FrozenInstanceError
        params.by_id = {}  # type: ignore
