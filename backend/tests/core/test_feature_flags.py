import pytest
from app.core.feature_flags import is_cascade_v2_enabled


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("FEATURE_FLAG_CASCADE_V2", raising=False)
    assert is_cascade_v2_enabled() is False


def test_enabled_when_on(monkeypatch):
    monkeypatch.setenv("FEATURE_FLAG_CASCADE_V2", "on")
    assert is_cascade_v2_enabled() is True


def test_disabled_when_off(monkeypatch):
    monkeypatch.setenv("FEATURE_FLAG_CASCADE_V2", "off")
    assert is_cascade_v2_enabled() is False


@pytest.mark.parametrize("val", ["1", "true", "TRUE", "On"])
def test_truthy_variants(monkeypatch, val):
    monkeypatch.setenv("FEATURE_FLAG_CASCADE_V2", val)
    assert is_cascade_v2_enabled() is True
