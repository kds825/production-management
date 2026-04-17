"""T/P 공정이 TP-2 에 우선 배정되는지 검증.

PDF 1안은 T/P#2 (TP-2) 만 사용. _find_speed.equipment_map["T/P"]=["TP-2"] 도
TP-2 기준 속도만 lookup 하므로, scheduler 의 실제 설비 배정도 TP-2 우선이어야
duration 계산과 실제 배정이 일관됨.

이 테스트는 schedule_optimizer 의 T/P preferred-equipment narrowing 블록 동작을
최소 가짜 객체로 검증 (실제 스케줄링 전체 파이프라인 실행 없이).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class _FakeEquip:
    equipment_code: str


def _apply_tp_preferred(
    eligible: list[_FakeEquip], process_name: str
) -> list[_FakeEquip]:
    """schedule_optimizer.py 의 T/P preferred 블록 로직 재현 (검증용).

    실제 코드와 동일한 시그니처/의도로 동작해야 하며, 이 함수가 깨지면 테스트가
    먼저 실패하도록 구성.
    """
    if process_name == "T/P":
        tp2_match = [e for e in eligible if e.equipment_code == "TP-2"]
        if tp2_match:
            return tp2_match
    return eligible


def test_tp_process_narrows_to_tp2_when_both_eligible():
    """TP-1, TP-2, TP-GD 모두 eligible 하면 TP-2 만 남아야 한다."""
    eligible = [_FakeEquip("TP-1"), _FakeEquip("TP-2"), _FakeEquip("TP-GD")]
    result = _apply_tp_preferred(eligible, "T/P")
    assert len(result) == 1 and result[0].equipment_code == "TP-2", (
        f"T/P preferred 블록이 TP-2 만 남기지 않음: {[e.equipment_code for e in result]}"
    )


def test_tp_process_falls_back_when_tp2_excluded():
    """TP-2 가 (color/range 등으로) 제외되면 기존 eligible 유지 (fallback)."""
    eligible = [_FakeEquip("TP-1"), _FakeEquip("TP-GD")]
    result = _apply_tp_preferred(eligible, "T/P")
    # TP-2 없으니 원래 list 그대로 반환 — scheduling skip 방지
    assert [e.equipment_code for e in result] == ["TP-1", "TP-GD"]


def test_non_tp_process_unaffected():
    """연선/절연/시스 등은 T/P preferred 블록 영향 없음."""
    eligible = [_FakeEquip("ST-54BO1"), _FakeEquip("ST-T6B0")]
    result = _apply_tp_preferred(eligible, "연선")
    assert [e.equipment_code for e in result] == ["ST-54BO1", "ST-T6B0"]


def test_live_code_has_tp2_preferred_block():
    """schedule_optimizer.py 에 실제 TP-2 preferred 블록이 존재하는지 source 확인."""
    from pathlib import Path

    src = (
        Path(__file__).parent.parent / "app" / "services" / "schedule_optimizer.py"
    ).read_text(encoding="utf-8")
    # 핵심 블록의 특징 문자열 존재 확인
    assert "T/P 공정 preferred 설비" in src, (
        "schedule_optimizer.py 에 T/P preferred 블록 주석이 사라짐 — 회귀 의심"
    )
    assert 'equipment_code == "TP-2"' in src, (
        "schedule_optimizer.py 에 TP-2 narrowing 로직 누락"
    )
