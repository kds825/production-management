"""decision_card ❹ 단일 설비 일과 (mini-Gantt) — Protocol + 4 concrete.

Phase 6 (decision_card) Step 3b. ❹ 섹션은 본 batch 가 배치된 설비의 같은
날짜 row 묶음을 보여주고, **무엇을 기준으로 정렬됐는지** 라벨로 안내한다.

핵심 정정 (memory `feedback_sheath_sort_order` 3차 iteration, 2026-04-18):
sheath cluster 정렬은 (latest_due, pred_ready_wmin, color_rank, due_week_int,
cluster_id) 순. 1차 iteration 의 pred_ready 1차 안은 overdue 묶음이 뒤로
밀려 +9일 초과 regression 유발해 롤백됨.

본 모듈의 Builder 들은 `domain/sheath_cluster.py:188-204::cluster_sort_key`
를 미러한다. 코드 분기 출처 단일화 — 정렬 로직이 두 곳에 흩어지지 않게.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol, runtime_checkable


# ── Protocol ──────────────────────────────────────────────────────────────


@runtime_checkable
class EquipmentDayGanttBuilder(Protocol):
    """공정별 mini-Gantt 정렬 키 + 라벨 합성."""

    process_key: str

    def sort_key(self, row) -> tuple:
        """row 정렬에 쓸 tuple. (Stable; 결정적 tiebreak 포함)."""
        ...

    def sort_label(self) -> str:
        """운영자에게 보여줄 정렬 기준 라벨 1줄."""
        ...


# ── Sheath — memory 3차 iteration 미러 ───────────────────────────────────


class SheathGanttBuilder:
    """sheath_cluster.cluster_sort_key 미러. 납기 1차 (memory 3차 정정)."""

    process_key = "sheath"

    def sort_key(self, row) -> tuple:
        # row 는 build_card.py 가 채워주는 dict-like — getattr 으로 안전 추출.
        # tiebreak 위해 cluster_id 마지막에.
        latest_due = getattr(row, "latest_due", None) or date.max
        pred_ready = int(getattr(row, "pred_ready_wmin", 0) or 0)
        color_rank = int(getattr(row, "color_rank", 99))
        cluster_id = str(getattr(row, "cluster_id", "") or "")
        return (latest_due, pred_ready, color_rank, cluster_id)

    def sort_label(self) -> str:
        return "① 납기 가까운 순 → ② 전공정 ready 시각 → ③ 색상 인접 순"


# ── Stranding ─────────────────────────────────────────────────────────────


class StrandingGanttBuilder:
    """연선 — 연선방식 묶음 우선. (압축/원형/수밀/7연선코어/61연선)."""

    process_key = "stranding"

    def sort_key(self, row) -> tuple:
        stranding_type_rank = int(getattr(row, "stranding_type_rank", 99))
        latest_due = getattr(row, "latest_due", None) or date.max
        sq = float(getattr(row, "sq_mm2", 0) or 0)
        return (stranding_type_rank, latest_due, sq)

    def sort_label(self) -> str:
        return "① 연선방식 → ② 납기 → ③ SQ"


# ── Insulation ────────────────────────────────────────────────────────────


class InsulationGanttBuilder:
    """절연 — 색상그룹 묶음 우선."""

    process_key = "insulation"

    def sort_key(self, row) -> tuple:
        color_group_rank = int(getattr(row, "color_group_rank", 99))
        latest_due = getattr(row, "latest_due", None) or date.max
        compound_rank = int(getattr(row, "compound_rank", 99))
        return (color_group_rank, latest_due, compound_rank)

    def sort_label(self) -> str:
        return "① 색상그룹 → ② 납기 → ③ 컴파운드 종류"


# ── Outsource ─────────────────────────────────────────────────────────────


class OutsourceGanttBuilder:
    """외주 — Gantt 시간 축 = 일 단위. 협력사별 capacity 우선."""

    process_key = "outsource"

    def sort_key(self, row) -> tuple:
        order_at = getattr(row, "order_at", None) or date.max
        vendor_name = str(getattr(row, "vendor_name", "") or "")
        inbound_at = getattr(row, "inbound_at", None) or date.max
        return (order_at, vendor_name, inbound_at)

    def sort_label(self) -> str:
        return "① 외주 발주일 → ② 협력사별 capacity → ③ 입고일"


# ── Default fallback ──────────────────────────────────────────────────────


class DefaultGanttBuilder:
    process_key = "default"

    def sort_key(self, row) -> tuple:
        return (getattr(row, "start_datetime", None) or date.max,)

    def sort_label(self) -> str:
        return "ERP 적재 순서"


# ── Registry ──────────────────────────────────────────────────────────────


_GANTT_BUILDERS: dict[str, EquipmentDayGanttBuilder] = {}


def register_gantt_builder(builder: EquipmentDayGanttBuilder) -> None:
    _GANTT_BUILDERS[builder.process_key] = builder


def reset_gantt_builders() -> None:
    _GANTT_BUILDERS.clear()


def get_gantt_builder(process_key: str) -> EquipmentDayGanttBuilder:
    b = _GANTT_BUILDERS.get(process_key) or _GANTT_BUILDERS.get("default")
    if b is None:
        raise RuntimeError(
            "No gantt builder registered. "
            "FastAPI lifespan startup 에서 register_gantt_builder 를 호출했는지 확인."
        )
    return b
