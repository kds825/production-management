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
