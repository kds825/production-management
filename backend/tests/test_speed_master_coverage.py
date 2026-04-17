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
    "ST-54BO2": 2,  # 기존 DB 기준 (A2' 은 UPDATE 하지 않음)
    "ST-54BO3": 3,
    "ST-30BO": 2,
    "ST-44BO": 4,
    "ST-1150BC": 2,
    "ST-T6B0": 2,
    "ST-AL6BO": 2,
    "CA-12BO": 2,
    "CA-4BO": 3,
    "CA-LU": 4,
}


# 연선 설비의 (equipment, cross_section) 최소 mpm — **fallback(10) 탈출** 확인이
# 목적. A2 라운드에서 25mpm 공격 기대 → PDF 해석 오류 판명 → A2′ 에서 기존 DB
# 값(11.7~13.3) 기준으로 완화.
STRANDING_MIN_SPEED = {
    ("ST-54BO1", 120): 11,  # 기존 DB 11.7
    ("ST-54BO2", 380): 9,  # 기존 DB 10
    ("ST-54BO3", 107): 11,  # 기존 DB 11.7
    ("ST-T6B0", 50): 12,  # 기존 DB 13.3
    ("ST-AL6BO", 25): 11,  # A2′ 신규 13mpm
    ("ST-30BO", 120): 9,
    ("ST-44BO", 240): 7,
}

# 비-연선 설비도 fallback(10) 탈출만 확인. 빈 설비에 대한 보수 값 회귀 방지.
NON_STRANDING_MIN_SPEED = {
    ("CA-12BO", 4): 13,  # A2′ 신규 15mpm
    ("CA-4BO", 50): 10,  # A2′ 신규 12mpm
    ("CA-LU", 95): 10,  # A2′ 신규 11mpm
    ("TP-1", 50): 18,  # A2′ 신규 20mpm (보수)
    ("TP-GD", 50): 18,
    ("SH-B100", 25): 5,  # 고압시스 저속 정상
}

# TP-2 는 운영 DB 에 역사적으로 EX-B100 값이 복제된 상태였음. A2.1 에서 정상화.
# 값 회귀 방지를 위해 TP-1 과 동일값 기대.
TP2_SANITY_MIN_MPM = {16: 20, 50: 25, 120: 15, 400: 9}


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


def _check_min_speed(db_session: Session, table: dict, label: str) -> list[str]:
    failures: list[str] = []
    for (eq, sq), min_mpm in table.items():
        row = (
            db_session.query(SpeedMaster)
            .filter(
                SpeedMaster.equipment_code == eq,
                SpeedMaster.cross_section == sq,
            )
            .first()
        )
        if row is None:
            failures.append(f"[{label}] {eq} SQ={sq}: row missing")
            continue
        actual = float(row.line_speed_mpm or 0)
        if actual < min_mpm:
            failures.append(
                f"[{label}] {eq} SQ={sq}: mpm={actual} < required {min_mpm}"
            )
    return failures


def test_stranding_equipment_speeds_not_near_fallback(db_session: Session):
    """연선 병목 설비의 line_speed_mpm 이 fallback(10) 근처 값이면 FAIL."""
    failures = _check_min_speed(db_session, STRANDING_MIN_SPEED, "stranding")
    assert not failures, "연선 설비 속도 비정상(fallback 근처):\n  " + "\n  ".join(
        failures
    )


def test_non_stranding_speeds_not_near_fallback(db_session: Session):
    """연합(CA-*)/T/P(TP-1/TP-GD)/고압시스(SH-B100) 도 합리적 속도 유지."""
    failures = _check_min_speed(db_session, NON_STRANDING_MIN_SPEED, "non-stranding")
    assert not failures, "비-연선 설비 속도 비정상:\n  " + "\n  ".join(failures)


def test_tp2_values_are_not_ex_b100_duplicate(db_session: Session):
    """TP-2 의 SpeedMaster 값이 EX-B100 값 복제(16→55 등)가 아닌지 검증.

    과거 운영 DB 에 TP-2 가 EX-B100 값으로 잘못 채워진 이력 있음 — 회귀 방지.
    """
    failures: list[str] = []
    for sq, min_mpm in TP2_SANITY_MIN_MPM.items():
        row = (
            db_session.query(SpeedMaster)
            .filter(
                SpeedMaster.equipment_code == "TP-2",
                SpeedMaster.cross_section == sq,
            )
            .first()
        )
        if row is None:
            failures.append(f"TP-2 SQ={sq}: row missing")
            continue
        mpm = float(row.line_speed_mpm or 0)
        # EX-B100 SQ=16 은 55mpm — T/P 로는 너무 빠름. 40 이하면 OK.
        if mpm > 45:
            failures.append(
                f"TP-2 SQ={sq}: mpm={mpm} too high (likely EX-B100 복제 회귀)"
            )
        if mpm < min_mpm:
            failures.append(f"TP-2 SQ={sq}: mpm={mpm} < required {min_mpm}")
    assert not failures, "TP-2 값 이상:\n  " + "\n  ".join(failures)
