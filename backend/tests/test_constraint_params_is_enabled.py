"""Track A Task A.0 — ConstraintParams.is_rule_enabled toggle 게이트 helper.

Stage 1 batch_grouper 의 하드코딩 룰 (2-2 외주, 2-4 61연선, 5-5 TFR-GV) 을
DB-toggle 가능하게 만들기 위한 인프라. 기존 ConstraintParams.load 가
params_json 만 캐시하고 is_enabled 는 무시하던 것을 확장.
"""

from __future__ import annotations

from app.application._shared.constraint_params import ConstraintParams
from app.infrastructure.database import SessionLocal


def test_is_rule_enabled_returns_true_for_enabled_row():
    """기본 상태: 2-2 외주 룰은 is_enabled=True 로 시드되어 있음."""
    db = SessionLocal()
    try:
        params = ConstraintParams.load(db)
        assert params.is_rule_enabled("2-2") is True
    finally:
        db.close()


def test_is_rule_enabled_returns_false_for_disabled_row():
    """8-1 등은 시드 시점에 is_enabled=False (확인된 상태)."""
    db = SessionLocal()
    try:
        params = ConstraintParams.load(db)
        # 8-1, 8-2, 8-3, 7-2, 3-4 가 기본 disabled (DB 직접 확인)
        assert params.is_rule_enabled("8-1") is False
    finally:
        db.close()


def test_is_rule_enabled_returns_default_for_missing_id():
    db = SessionLocal()
    try:
        params = ConstraintParams.load(db)
        assert params.is_rule_enabled("NONEXISTENT-999", default=True) is True
        assert params.is_rule_enabled("NONEXISTENT-999", default=False) is False
    finally:
        db.close()


def test_is_rule_enabled_default_when_default_arg_omitted():
    """default 인자 미지정 시 기본값 = True (보수적: row 없으면 룰 적용 유지)."""
    db = SessionLocal()
    try:
        params = ConstraintParams.load(db)
        assert params.is_rule_enabled("NONEXISTENT-999") is True
    finally:
        db.close()
