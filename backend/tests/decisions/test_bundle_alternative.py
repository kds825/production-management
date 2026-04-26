"""Phase 6 Step 3b — bundle_alternative.py 단위 테스트.

핵심 invariant:
1. compare_bundles 는 _resolve_key 결과로 4 함수 dispatch
2. 본 묶음 (is_chosen=True) 은 항상 결과 첫 행
3. score 오름차순 정렬 (chosen 먼저 + alt score 정렬)
4. top_n 한도 — 결과 길이 ≤ top_n
5. dict / dataclass-like metric 둘 다 지원
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from app.application.decisions.bundle_alternative import (
    BundleAlternativeRow,
    compare_bundles,
)


@dataclass
class FakeBatch:
    process_name: str = "저압시스"
    sq_mm2: float = 150.0
    product_group: str = ""
    customer_name: str = ""


# ─────────────────────────────────────────────────────────────────────────
# 기본 dispatch
# ─────────────────────────────────────────────────────────────────────────


def test_compare_bundles_chosen_first():
    chosen = {
        "label": "본 묶음",
        "color_change_min": 0,
        "spec_change_min": 0,
        "duration_min": 510,
        "score": 100.0,
    }
    alts = [
        {
            "label": "대안 1",
            "color_change_min": 120,
            "spec_change_min": 0,
            "duration_min": 630,
            "score": 80.0,  # chosen 보다 점수 좋음
        },
    ]
    rows = compare_bundles(FakeBatch(), chosen, alts)
    assert rows[0].is_chosen is True
    assert rows[0].label == "본 묶음"
    # alt score 가 더 좋아도 chosen 이 첫 행
    assert rows[1].is_chosen is False


def test_compare_bundles_alts_sorted_by_score():
    chosen = {"label": "C", "score": 0.0}
    alts = [
        {"label": "alt-high", "score": 200.0},
        {"label": "alt-low", "score": 50.0},
        {"label": "alt-mid", "score": 120.0},
    ]
    rows = compare_bundles(FakeBatch(), chosen, alts, top_n=4)
    labels = [r.label for r in rows]
    assert labels == ["C", "alt-low", "alt-mid", "alt-high"]


def test_compare_bundles_top_n_limit():
    chosen = {"label": "C", "score": 0.0}
    alts = [{"label": f"alt-{i}", "score": float(i)} for i in range(10)]
    rows = compare_bundles(FakeBatch(), chosen, alts, top_n=5)
    assert len(rows) == 5  # 1 chosen + 4 alts


def test_compare_bundles_no_alternatives():
    chosen = {"label": "C", "score": 0.0}
    rows = compare_bundles(FakeBatch(), chosen, [], top_n=5)
    assert len(rows) == 1
    assert rows[0].is_chosen


def test_compare_bundles_no_chosen_returns_just_alts():
    alts = [{"label": "alt", "score": 10.0}]
    rows = compare_bundles(FakeBatch(), None, alts)
    assert len(rows) == 1
    assert rows[0].is_chosen is False


# ─────────────────────────────────────────────────────────────────────────
# 공정별 dispatch
# ─────────────────────────────────────────────────────────────────────────


def test_dispatch_outsource_for_sq_le_10():
    """SQ ≤ 10 batch 는 _resolve_key 가 outsource 반환 → _compare_outsource dispatch."""
    rows = compare_bundles(
        FakeBatch(sq_mm2=10),
        chosen={"label": "외주 본 묶음", "score": 0.0},
        alternatives=[],
    )
    assert rows[0].label == "외주 본 묶음"


def test_dispatch_stranding():
    rows = compare_bundles(
        FakeBatch(process_name="연선"),
        chosen={"label": "연선 본 묶음", "score": 0.0},
        alternatives=[],
    )
    assert rows[0].label == "연선 본 묶음"


def test_dispatch_insulation():
    rows = compare_bundles(
        FakeBatch(process_name="저압절연"),
        chosen={"label": "절연 본 묶음", "score": 0.0},
        alternatives=[],
    )
    assert rows[0].label == "절연 본 묶음"


def test_dispatch_default_for_unknown_process():
    rows = compare_bundles(
        FakeBatch(process_name="신선"),
        chosen={"label": "신선 본 묶음", "score": 0.0},
        alternatives=[],
    )
    assert rows[0].label == "신선 본 묶음"


# ─────────────────────────────────────────────────────────────────────────
# Metric source — dict / dataclass-like
# ─────────────────────────────────────────────────────────────────────────


def test_metric_source_dict():
    rows = compare_bundles(
        FakeBatch(),
        chosen={
            "label": "L",
            "color_change_min": 120,
            "spec_change_min": 60,
            "duration_min": 720,
            "score": 50.0,
            "rationale": "테스트",
        },
        alternatives=[],
    )
    assert rows[0].color_change_min == 120
    assert rows[0].rationale == "테스트"


def test_metric_source_dataclass_like():
    chosen = SimpleNamespace(
        label="ns",
        color_change_min=10,
        spec_change_min=20,
        duration_min=300,
        score=5.0,
        rationale="ns rationale",
    )
    rows = compare_bundles(FakeBatch(), chosen, [])
    assert rows[0].label == "ns"
    assert rows[0].color_change_min == 10


def test_metric_missing_fields_default_zero():
    rows = compare_bundles(FakeBatch(), {"label": "x"}, [])
    assert rows[0].color_change_min == 0
    assert rows[0].score == 0.0
    assert rows[0].rationale == ""


# ─────────────────────────────────────────────────────────────────────────
# 성능 — phrasing-only rebuild < 50ms (DB 1 round trip)
# ─────────────────────────────────────────────────────────────────────────


def test_compare_bundles_under_50ms():
    chosen = {"label": "C", "score": 0.0}
    alts = [{"label": f"alt-{i}", "score": float(i)} for i in range(50)]
    start = time.perf_counter()
    for _ in range(100):
        compare_bundles(FakeBatch(), chosen, alts, top_n=5)
    elapsed_ms = (time.perf_counter() - start) * 1000 / 100
    assert elapsed_ms < 50, f"compare_bundles took {elapsed_ms:.2f}ms (>50ms)"


# ─────────────────────────────────────────────────────────────────────────
# Output — BundleAlternativeRow frozen dataclass
# ─────────────────────────────────────────────────────────────────────────


def test_bundle_alternative_row_frozen():
    row = BundleAlternativeRow(
        label="x",
        color_change_min=0,
        spec_change_min=0,
        duration_min=0,
        score=0.0,
        is_chosen=True,
    )
    with pytest.raises(Exception):  # dataclasses.FrozenInstanceError
        row.score = 100.0  # type: ignore[misc]
