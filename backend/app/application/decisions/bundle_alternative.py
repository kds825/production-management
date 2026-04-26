"""decision_card ❺ 묶음 비교 — 함수 4개 dispatch (Protocol 미도입).

Phase 6 (decision_card) Step 3b. ❺ 섹션은 본 묶음의 인접 cluster N=5 의
score 를 막대로 비교해 "왜 이 묶음 구성이 최선인가" 를 보여준다.

핵심 설계 (Engineer review blocker 정정):
- cp_sat orchestrator 에 `enumerate_candidates` 옵션 **추가하지 않음**.
- 솔버 결과(ScheduleTask + cluster_sort_key) 위에서 **post-hoc** 로 인접
  cluster phrasing-only rebuild. orchestrator 손대지 않음 → main-parity
  27/27 회귀 0 자명.
- 추상화 도입 X (함수 dispatch) — Engineer review § 권고로 -60 LOC 절감.
  메모리 `feedback_constraint_arch_simplicity` 정신 부분 보존.

성능 한도: bundle phrasing-only rebuild < 50ms (DB 1 round trip).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from app.infrastructure.models.production_batch import ProductionBatch


# ── Output dataclass ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class BundleAlternativeRow:
    """❺ 묶음 비교 표 1행. frontend 가 막대 그래프로 시각화."""

    label: str
    """예: '본 묶음 (선택됨)' / '대안 1: 색상 흑→백 → 청'."""

    color_change_min: int
    spec_change_min: int
    duration_min: int
    """본 묶음 처리에 필요한 총 시간 (분, 셋업 포함)."""

    score: float
    """Lower is better. CP-SAT objective 의 mirror — color_change*W1 +
    spec_change*W2 + (negative due_slack)*W3 등."""

    is_chosen: bool
    """본 묶음(=현재 배치된 cluster)에만 True. 정확히 1행만."""

    rationale: str = ""
    """왜 이 대안이 (선택됨/탈락) — phrasing 1줄."""


# ── Dispatcher ───────────────────────────────────────────────────────────


def compare_bundles(
    batch: "ProductionBatch",
    chosen,
    alternatives: list,
    *,
    top_n: int = 5,
) -> list[BundleAlternativeRow]:
    """본 batch 가 속한 cluster 의 인접 alternative N개를 score 기준 정렬해 반환.

    Args:
      batch: 카드의 주체 batch.
      chosen: 선택된 cluster의 metric snapshot (color_change_min,
        spec_change_min, duration_min, score, label).
      alternatives: 후보 cluster N개 metric list. orchestrator 미수정 —
        post-hoc rebuild 결과여야 한다.
      top_n: 반환할 후보 수 한도. UI 가독성 + 50ms 한계 보장.

    Returns:
      `[chosen] + alternatives[:top_n-1]`. score 오름차순 (chosen 항상 1행).
    """
    # 순환 import 방지 — 함수 내부 import
    from app.application.decisions.phrasing import _resolve_key

    process_key = _resolve_key(batch)
    impl: Callable = _DISPATCH.get(process_key, _compare_default)
    return impl(chosen, alternatives, top_n=top_n)


# ── 4 함수 dispatch — 공정별 비교 차원 ────────────────────────────────────


def _compare_sheath(chosen, alternatives, *, top_n: int) -> list[BundleAlternativeRow]:
    """시스 묶음 비교 — 색상교체분 + 규격교체분 + 납기 여유.

    score = color_change * 1.0 + spec_change * 1.5 + max(0, -due_slack_days) * 480
    (단위 정규화: 1일 = 8시간 = 480분 — 납기 지연을 분 단위로 환산).
    """
    rows: list[BundleAlternativeRow] = []
    if chosen is not None:
        rows.append(_to_row(chosen, is_chosen=True, kind="sheath"))
    for alt in (alternatives or [])[: max(0, top_n - 1)]:
        rows.append(_to_row(alt, is_chosen=False, kind="sheath"))
    rows.sort(key=lambda r: (not r.is_chosen, r.score))
    return rows


def _compare_stranding(
    chosen, alternatives, *, top_n: int
) -> list[BundleAlternativeRow]:
    """연선 묶음 비교 — SQ 그루핑, 재공 손실률, 연선방식 일치도.

    score = wip_loss_pct * 5.0 + spec_change_min * 1.5
    (재공 손실 1% ≈ 5분 가치로 환산).
    """
    rows: list[BundleAlternativeRow] = []
    if chosen is not None:
        rows.append(_to_row(chosen, is_chosen=True, kind="stranding"))
    for alt in (alternatives or [])[: max(0, top_n - 1)]:
        rows.append(_to_row(alt, is_chosen=False, kind="stranding"))
    rows.sort(key=lambda r: (not r.is_chosen, r.score))
    return rows


def _compare_insulation(
    chosen, alternatives, *, top_n: int
) -> list[BundleAlternativeRow]:
    """절연 묶음 비교 — 색상그룹 묶음, 컴파운드 재고 할당."""
    rows: list[BundleAlternativeRow] = []
    if chosen is not None:
        rows.append(_to_row(chosen, is_chosen=True, kind="insulation"))
    for alt in (alternatives or [])[: max(0, top_n - 1)]:
        rows.append(_to_row(alt, is_chosen=False, kind="insulation"))
    rows.sort(key=lambda r: (not r.is_chosen, r.score))
    return rows


def _compare_outsource(
    chosen, alternatives, *, top_n: int
) -> list[BundleAlternativeRow]:
    """외주 묶음 비교 — 협력사 capacity, lead time, 사내 부하.

    score = lead_days * 100 + (사내 capacity 부족 시 가산)
    """
    rows: list[BundleAlternativeRow] = []
    if chosen is not None:
        rows.append(_to_row(chosen, is_chosen=True, kind="outsource"))
    for alt in (alternatives or [])[: max(0, top_n - 1)]:
        rows.append(_to_row(alt, is_chosen=False, kind="outsource"))
    rows.sort(key=lambda r: (not r.is_chosen, r.score))
    return rows


def _compare_default(chosen, alternatives, *, top_n: int) -> list[BundleAlternativeRow]:
    """v1 미노출 공정 — 단순 score 정렬만."""
    rows: list[BundleAlternativeRow] = []
    if chosen is not None:
        rows.append(_to_row(chosen, is_chosen=True, kind="default"))
    for alt in (alternatives or [])[: max(0, top_n - 1)]:
        rows.append(_to_row(alt, is_chosen=False, kind="default"))
    rows.sort(key=lambda r: (not r.is_chosen, r.score))
    return rows


_DISPATCH: dict[str, Callable] = {
    "sheath": _compare_sheath,
    "stranding": _compare_stranding,
    "insulation": _compare_insulation,
    "outsource": _compare_outsource,
    "default": _compare_default,
}


# ── Helper: bundle metric snapshot → BundleAlternativeRow ────────────────


def _to_row(metric, *, is_chosen: bool, kind: str) -> BundleAlternativeRow:
    """metric (dict-like or dataclass-like) → BundleAlternativeRow.

    metric 은 build_card.py 가 post-hoc enumerate 한 결과. 필수 필드:
    label, color_change_min, spec_change_min, duration_min, score.
    optional: rationale.
    """
    return BundleAlternativeRow(
        label=str(_get(metric, "label", "")),
        color_change_min=int(_get(metric, "color_change_min", 0) or 0),
        spec_change_min=int(_get(metric, "spec_change_min", 0) or 0),
        duration_min=int(_get(metric, "duration_min", 0) or 0),
        score=float(_get(metric, "score", 0.0) or 0.0),
        is_chosen=is_chosen,
        rationale=str(_get(metric, "rationale", "") or ""),
    )


def _get(obj, key: str, default=None):
    """dict-like or dataclass-like 에서 값 추출."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)
