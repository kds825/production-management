"""_find_speed 가 연합 공정에 대해 CA-* 설비 속도를 반환하는지 검증.

과거 equipment_map['연합']=['AS-A100'] 오타로 연합 batches 전수가 line_speed=None
으로 저장되어 Stage1 단계 duration 계산이 누락되던 이슈의 회귀 방지.

Unit test 중심 — SpeedMaster mock 으로 _find_speed 함수 자체의 동작만 검증.
DB 의존성 없이 pure-function 검증으로 run_label 이 바뀌어도 유지.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.batch_grouping import _find_speed


@dataclass
class _FakeSpeed:
    equipment_code: str
    cross_section: float
    line_speed_mpm: float


@dataclass
class _FakeOrder:
    product_group: str = ""
    core_count: int = 1
    voltage: str = "0.6/1kV"
    conductor_material: str = "CU"


def test_find_speed_returns_ca_speed_for_coupling_process():
    """연합 process → CA-* 설비 속도 lookup 성공해야 한다.

    이전 버그: equipment_map['연합']=['AS-A100'] 라서 CA-12BO SpeedMaster 행이
    있어도 연합 공정 batch 의 _find_speed 가 None 반환.

    _find_speed lookup 키는 (equipment_code, product_type, sq) 3-tuple.
    연합 공정 product_type 은 elif chain 통과 못해 None 이 됨.
    """
    speed_lookup: dict[tuple, _FakeSpeed] = {
        ("CA-12BO", None, 4.0): _FakeSpeed("CA-12BO", 4.0, 15.0),
        ("CA-4BO", None, 95.0): _FakeSpeed("CA-4BO", 95.0, 10.0),
    }

    order = _FakeOrder(product_group="TFR-CV")

    result = _find_speed(speed_lookup, "연합", order, 4.0)
    assert result is not None, "연합 공정에서 CA-12BO 설비 속도를 찾지 못함"
    assert result.equipment_code == "CA-12BO"
    assert result.line_speed_mpm == 15.0

    result = _find_speed(speed_lookup, "연합", order, 95.0)
    assert result is not None, "연합 공정에서 CA-4BO 설비 속도를 찾지 못함"
    assert result.equipment_code == "CA-4BO"


def test_find_speed_coupling_falls_through_to_any_ca_equipment():
    """CA-LU 도 equipment_map 에 포함되어 다른 CA-* 누락 시 fallback."""
    speed_lookup: dict[tuple, _FakeSpeed] = {
        ("CA-LU", None, 150.0): _FakeSpeed("CA-LU", 150.0, 9.0),
    }
    order = _FakeOrder()
    result = _find_speed(speed_lookup, "연합", order, 150.0)
    assert result is not None, "CA-LU 만 있어도 lookup 성공해야 함"
    assert result.equipment_code == "CA-LU"


def test_find_speed_coupling_returns_none_when_no_ca_row():
    """CA-* SpeedMaster 행이 전혀 없으면 None 반환 (정상 동작)."""
    speed_lookup: dict[tuple, _FakeSpeed] = {
        ("ST-54BO1", "연선", 95.0): _FakeSpeed("ST-54BO1", 95.0, 11.7),
    }
    order = _FakeOrder()
    result = _find_speed(speed_lookup, "연합", order, 95.0)
    assert result is None, "CA-* 매칭 없으면 None 이어야 (ST-* 잘못 집지 않음)"
