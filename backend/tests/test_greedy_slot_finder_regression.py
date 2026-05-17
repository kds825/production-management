"""Phase 6 step 5 — greedy slot_finder 회귀 가드.

Step 6 에서 ``SchedulerState`` 에 ``calculate_end_datetime`` 결과를 캐싱할
예정. cache 가 stale 이거나 key 가 부정확하면 ``_find_available_slot`` 의
결과가 달라져 schedule overlap 이 재발할 위험.

본 test 는 cache 도입 **전후** 의 output 동일성 + 호출 결정론 invariant 를
freeze 한다. parity harness (cp_sat 만 검증) 가 greedy path 회귀를 잡지
못한다는 점을 보완 — Plan v2 §변경사항 5 의 "회귀 가드 우선" 원칙을 충족.

Invariant 6 가지:
1. db=None / equipment_code=None path: 빈 timeline → earliest 그대로
2. db=None: occupied 가 candidate 와 겹치면 그 다음으로 push
3. db=None: occupied 사이 빈 공간이 충분하면 fit
4. db != None: 같은 input 두 번 호출 → 동일 결과 (결정론)
5. occupied 정렬 무관 — sorted() 가 첫줄에 박혀 있음을 freeze
6. occupied 가 다르면 결과 달라짐 — cache key 에 occupied 가 포함되지 않더라도
   slot_finder 의 외부 contract 는 occupied 에 의존
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.application.scheduling.greedy.slot_finder import _find_available_slot


def test_find_slot_no_db_empty_timeline_returns_earliest() -> None:
    """db=None 경로 + 빈 timeline → earliest 그대로 반환."""
    start = datetime(2026, 4, 21, 8, 0)
    result = _find_available_slot(start, 60, [], db=None, equipment_code=None)
    assert result == start


def test_find_slot_no_db_pushes_after_overlap() -> None:
    """occupied 가 candidate 와 겹치면 그 다음으로 push."""
    start = datetime(2026, 4, 21, 8, 0)
    occupied = [(datetime(2026, 4, 21, 8, 0), datetime(2026, 4, 21, 10, 0))]
    result = _find_available_slot(start, 60, occupied, db=None, equipment_code=None)
    assert result == datetime(2026, 4, 21, 10, 0)


def test_find_slot_no_db_fits_in_gap_before_slot() -> None:
    """occupied 슬롯 시작 전 빈 공간이 충분하면 그 안에 fit (push 하지 않음)."""
    start = datetime(2026, 4, 21, 8, 0)
    occupied = [(datetime(2026, 4, 21, 10, 0), datetime(2026, 4, 21, 12, 0))]
    # candidate 8h, duration 60min → ends 9h. slot 10h 시작 전 fit.
    result = _find_available_slot(start, 60, occupied, db=None, equipment_code=None)
    assert result == start


def test_find_slot_with_db_is_deterministic(monkeypatch) -> None:
    """db != None 경로: 같은 input 두 번 호출 → 동일 결과.

    Step 6 cache 가 (equipment_code, candidate_start, duration_min) key 로
    저장되더라도 본 invariant 는 깨지면 안 된다. 깨지면 cache key 가
    output 에 영향을 주고 있다는 신호.
    """

    def fake_calc(start_dt, dur, _db, _eq):
        return start_dt + timedelta(minutes=dur)

    monkeypatch.setattr(
        "app.infrastructure.calendar_engine.calculate_end_datetime", fake_calc
    )

    start = datetime(2026, 4, 21, 8, 0)
    occupied = [(datetime(2026, 4, 21, 7, 0), datetime(2026, 4, 21, 7, 30))]
    r1 = _find_available_slot(start, 60, occupied, db="dummy", equipment_code="EX-B100")
    r2 = _find_available_slot(start, 60, occupied, db="dummy", equipment_code="EX-B100")
    assert r1 == r2, f"slot_finder 비결정성: {r1} vs {r2}"


def test_find_slot_occupied_order_does_not_affect_result() -> None:
    """occupied 리스트 정렬 여부에 관계없이 결과 동일.

    slot_finder.py line 30 의 ``sorted_slots = sorted(occupied_slots, key=...)``
    가 caller 의 정렬 책임을 흡수한다. cache 도입 시 이 sort 가 사라지면
    본 test 가 잡는다.
    """
    start = datetime(2026, 4, 21, 8, 0)
    occupied_sorted = [
        (datetime(2026, 4, 21, 8, 0), datetime(2026, 4, 21, 9, 0)),
        (datetime(2026, 4, 21, 10, 0), datetime(2026, 4, 21, 12, 0)),
    ]
    occupied_reversed = list(reversed(occupied_sorted))
    r_sorted = _find_available_slot(
        start, 60, occupied_sorted, db=None, equipment_code=None
    )
    r_reversed = _find_available_slot(
        start, 60, occupied_reversed, db=None, equipment_code=None
    )
    assert r_sorted == r_reversed, (
        f"정렬에 따른 비결정성: sorted={r_sorted} reversed={r_reversed}"
    )


def test_find_slot_different_occupied_yields_different_result() -> None:
    """occupied 가 다르면 결과 달라짐.

    cache 가 (eq, candidate, dur) 만 key 로 잡고 occupied 는 무시한다면
    cache hit 시에도 occupied 변화는 그대로 반영돼야 한다 (cache 는
    calculate_end_datetime 만 캐싱, slot 검색 자체는 매번 수행).
    """
    start = datetime(2026, 4, 21, 8, 0)
    occupied_a = [(datetime(2026, 4, 21, 8, 0), datetime(2026, 4, 21, 9, 0))]
    occupied_b = [(datetime(2026, 4, 21, 8, 0), datetime(2026, 4, 21, 12, 0))]
    r_a = _find_available_slot(start, 60, occupied_a, db=None, equipment_code=None)
    r_b = _find_available_slot(start, 60, occupied_b, db=None, equipment_code=None)
    assert r_a != r_b, (
        f"occupied 변화가 결과에 반영 안 됨: occ_a → {r_a}, occ_b → {r_b}"
    )
