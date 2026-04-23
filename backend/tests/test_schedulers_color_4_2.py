"""4-2 색상교체 fallback — sm_color NULL vs 0 시맨틱 교정 + 동등성."""

from app.services.constraint_params import ConstraintParams, resolve_color_change_min


def _make_params_with(sheath_color_min: float) -> ConstraintParams:
    return ConstraintParams(
        by_id={"4-2": {"sheath_color_min": sheath_color_min}},
    )


def test_color_change_falls_back_to_constraint_params_when_sm_color_none() -> None:
    """SpeedMaster.setup_color_min IS NULL → ConstraintConfig 4-2 읽음."""
    params = _make_params_with(120.0)
    assert resolve_color_change_min(sm_color_min=None, params=params) == 120.0


def test_color_change_uses_sm_color_zero_as_valid_value() -> None:
    """시맨틱 교정: sm_color_min == 0 을 '값 없음' 이 아닌 '0분 허용' 으로 처리.

    이전 로직 `float(sm_color[0] or 120.0)` 은 0 을 120 으로 치환하는 버그였음.
    """
    params = _make_params_with(120.0)  # fallback 이 호출되면 120 이 나올 것
    assert resolve_color_change_min(sm_color_min=0.0, params=params) == 0.0


def test_color_change_uses_sm_color_nonzero() -> None:
    params = _make_params_with(120.0)
    assert resolve_color_change_min(sm_color_min=90.0, params=params) == 90.0


def test_color_change_accepts_int_as_sm_value() -> None:
    """SQLAlchemy 에서 int 반환 가능성 → float 로 정규화."""
    params = _make_params_with(120.0)
    result = resolve_color_change_min(sm_color_min=60, params=params)  # type: ignore[arg-type]
    assert result == 60.0
    assert isinstance(result, float)


def test_welding_min_resolution_uses_seed() -> None:
    """4-4 welding — ConstraintParams.get 로 통일되었는지 (Task 4 회귀 guard)."""
    from app.services.constraint_params import ConstraintParams

    params = ConstraintParams(by_id={"4-4": {"welding_min": 30}})
    assert params.get("4-4", "welding_min", default=30) == 30.0


def test_welding_min_uses_default_when_key_missing() -> None:
    """4-4 row 있지만 welding_min 누락 → default (_DEFAULT_WELDING_MIN) 반환."""
    from app.services.constraint_params import ConstraintParams

    params = ConstraintParams(by_id={"4-4": {}})
    assert params.get("4-4", "welding_min", default=30) == 30.0


def test_welding_min_uses_default_when_row_missing() -> None:
    """4-4 row 아예 없음 → default 반환 (하위 호환 보장)."""
    from app.services.constraint_params import ConstraintParams

    params = ConstraintParams(by_id={})
    assert params.get("4-4", "welding_min", default=30) == 30.0
