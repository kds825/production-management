"""SpeedMaster 커버리지·값 타당성 회귀 방지.

이 테스트가 실패하면:
- 커버리지 부족(설비에 행 부재) → fallback 10mpm 경로로 회귀
- 값 이상(연선 설비가 fallback 근처값) → duration 2.5~3배 과다 회귀
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.infrastructure.database import SessionLocal
from app.infrastructure.models import EquipmentMaster, SpeedMaster


# (equipment_code, min_rows_expected) — 각 설비 최소 행 수
REQUIRED_EQUIPMENT_WITH_SPEED = {
    "EX-B100": 10,
    "EX-CV1": 4,
    "EX-CV2": 3,
    "SH-A100": 10,
    "SH-A120": 2,
    "SH-A150": 4,
    "SH-B100": 4,
    "TP-1": 10,
    "TP-GD": 10,
    "ST-54BO1": 8,
    "ST-54BO2": 8,
    "ST-54BO3": 8,
    "ST-30BO": 2,
    "ST-44BO": 4,
    "ST-1150BC": 2,
    "ST-T6B0": 2,
    "ST-AL6BO": 2,
    "CA-12BO": 2,
    "CA-4BO": 3,
    "CA-LU": 4,
}


# 연선 설비의 (equipment, cross_section) 최소 mpm 기대값 — fallback(10) 근처면 FAIL.
# PoC PDF 레퍼런스 기준, 20% 완충.
STRANDING_MIN_SPEED = {
    ("ST-54BO1", 70): 20,
    ("ST-54BO1", 95): 20,
    ("ST-54BO1", 120): 20,
    ("ST-54BO1", 150): 18,
    ("ST-54BO1", 240): 15,
    ("ST-54BO2", 380): 12,
    ("ST-54BO2", 507): 10,
    ("ST-54BO3", 380): 12,
    ("ST-T6B0", 50): 15,  # 기존 13.3 불연속 교정
}


@pytest.fixture(scope="module")
def db_session():
    s = SessionLocal()
    yield s
    s.close()


def test_speed_master_required_equipment_coverage(db_session: Session):
    counts: dict[str, int] = {}
    for sm in db_session.query(SpeedMaster).all():
        counts[sm.equipment_code] = counts.get(sm.equipment_code, 0) + 1
    missing = [
        f"{eq}: got {counts.get(eq, 0)}, need >= {need}"
        for eq, need in REQUIRED_EQUIPMENT_WITH_SPEED.items()
        if counts.get(eq, 0) < need
    ]
    assert not missing, "SpeedMaster 커버리지 부족:\n  " + "\n  ".join(missing)


def test_all_non_drawing_equipment_has_speed(db_session: Session):
    """신선 제외 모든 equipment_master 행이 SpeedMaster 에 최소 1건 이상."""
    sm_eqs = {sm.equipment_code for sm in db_session.query(SpeedMaster).all()}
    missing = [
        f"{e.equipment_code} ({e.process_name})"
        for e in db_session.query(EquipmentMaster).all()
        if e.process_name != "신선" and e.equipment_code not in sm_eqs
    ]
    assert not missing, "SpeedMaster 에 없는 설비:\n  " + "\n  ".join(missing)


def test_stranding_equipment_speeds_not_near_fallback(db_session: Session):
    """연선 병목 설비의 line_speed_mpm 이 fallback(10) 근처 값이면 FAIL.

    PoC PDF 레퍼런스 기준 20% 완충한 최소값(STRANDING_MIN_SPEED)보다 낮으면
    fallback 과 실질적 차이가 없는 것으로 간주하고 회귀 판정.
    """
    failures: list[str] = []
    for (eq, sq), min_mpm in STRANDING_MIN_SPEED.items():
        row = (
            db_session.query(SpeedMaster)
            .filter(
                SpeedMaster.equipment_code == eq,
                SpeedMaster.cross_section == sq,
            )
            .first()
        )
        if row is None:
            failures.append(f"{eq} SQ={sq}: row missing")
            continue
        actual = float(row.line_speed_mpm or 0)
        if actual < min_mpm:
            failures.append(f"{eq} SQ={sq}: mpm={actual} < required {min_mpm}")
    assert not failures, "연선 설비 속도 비정상(fallback 근처):\n  " + "\n  ".join(
        failures
    )
