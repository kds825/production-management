"""시스 색상 묶음(cluster) 스케줄링 헬퍼.

'같은 설비 카테고리(A100/A120/저압시스/고압시스) + 같은 주차 반버킷 + 같은 색상'
인 그룹들을 하나의 묶음으로 취급해 latest_start 역산 + 연속 배치를 지원한다.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any


@dataclass
class SheathCluster:
    """시스 색상 묶음. 같은 설비/주차/색상 그룹들의 메타 집계."""

    cluster_id: str
    equipment_category: str  # "A100" | "A120" | "저압시스" | "고압시스"
    color: str
    due_week_int: int  # schedule_optimizer._sheath_group_due_week_int 규격
    group_keys: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 내부 유틸
# ---------------------------------------------------------------------------


def _derive_equipment_category(group_key: str, process_name: str) -> str | None:
    if group_key.startswith("A100_"):
        return "A100"
    if group_key.startswith("A120_"):
        return "A120"
    if process_name == "고압시스":
        return "고압시스"
    if process_name == "저압시스":
        return "저압시스"
    return None


def _due_week_int(due: date | None) -> int:
    if not due:
        return 999999
    yr, wk, wday = due.isocalendar()
    half = 0 if wday <= 3 else 1
    return yr * 200 + wk * 2 + half


def _due_week_token(due: date | None) -> str:
    if not due:
        return "9999W99X"
    yr, wk, wday = due.isocalendar()
    half = "H1" if wday <= 3 else "H2"
    return f"{yr}W{wk:02d}{half}"


# ---------------------------------------------------------------------------
# 빌더
# ---------------------------------------------------------------------------


def build_sheath_clusters(groups_meta: dict) -> list[SheathCluster]:
    """groups_meta 에서 시스 그룹만 추려 (설비 카테고리, 주차, 색상) 별로 묶는다.

    Args:
        groups_meta: {group_key: {"batches": [...], ...}} — cp_sat_optimizer 의
            group_meta 와 동일 형식

    Returns:
        묶음 리스트. 비결정적 순서 — 호출자가 정렬한다.
    """
    bucket: dict[tuple[str, int, str], list[str]] = defaultdict(list)
    rep_of: dict[tuple[str, int, str], tuple[object, str]] = {}
    for gk, meta in groups_meta.items():
        batches = meta.get("batches") or []
        if not batches:
            continue
        rep = batches[0]
        if rep.process_name not in ("저압시스", "고압시스"):
            continue
        cat = _derive_equipment_category(gk, rep.process_name)
        if cat is None:
            continue
        color = (rep.sheath_color or "").strip() or "기타"
        due_wk_int = _due_week_int(rep.due_date)
        due_wk_tok = _due_week_token(rep.due_date)
        key = (cat, due_wk_int, color)
        bucket[key].append(gk)
        rep_of.setdefault(key, (rep, due_wk_tok))

    clusters: list[SheathCluster] = []
    for (cat, due_wk, color), gks in bucket.items():
        _, due_wk_tok = rep_of[(cat, due_wk, color)]
        color_safe = color.replace("/", "_")
        cluster_id = f"{cat}_{color_safe}_{due_wk_tok}"
        clusters.append(
            SheathCluster(
                cluster_id=cluster_id,
                equipment_category=cat,
                color=color,
                due_week_int=due_wk,
                group_keys=sorted(gks),
            )
        )
    return clusters


# ---------------------------------------------------------------------------
# 메타 집계
# ---------------------------------------------------------------------------


def compute_cluster_meta(cluster: SheathCluster, groups_meta: dict) -> dict[str, Any]:
    """묶음 메타(earliest, latest_due, total_duration_min, latest_start_hint) 계산.

    - earliest: 묶음 내 모든 그룹 pred_ready 의 최소값
    - latest_due: 묶음 내 가장 급한 납기
    - total_duration_min: 묶음 내 모든 그룹 cpsat_dur 의 합
    - latest_start_hint: latest_due 기반 역산 hint (정렬 tiebreaker 용)
    """
    pred_readys = [
        groups_meta[gk].get("pred_ready")
        for gk in cluster.group_keys
        if groups_meta[gk].get("pred_ready") is not None
    ]
    dues = [
        groups_meta[gk].get("earliest_due")
        for gk in cluster.group_keys
        if groups_meta[gk].get("earliest_due") is not None
    ]
    durs = [int(groups_meta[gk].get("cpsat_dur") or 0) for gk in cluster.group_keys]
    total_dur = sum(durs)

    earliest = min(pred_readys) if pred_readys else None
    latest_due = min(dues) if dues else None
    latest_start_hint = None
    if latest_due is not None:
        latest_start_hint = datetime.combine(latest_due, datetime.min.time()).replace(
            hour=8
        )
    return {
        "earliest": earliest,
        "latest_due": latest_due,
        "total_duration_min": total_dur,
        "latest_start_hint": latest_start_hint,
    }


# ---------------------------------------------------------------------------
# 정렬 키
# ---------------------------------------------------------------------------


def cluster_sort_key(cluster: SheathCluster, groups_meta: dict) -> tuple:
    """묶음 정렬 키: (latest_due, color_rank, due_week_int, cluster_id).

    1차: latest_due — 납기 임박 묶음 먼저
    2차: color_rank — 같은 납기면 흑→갈→회→... 순
    3차: due_week_int — 안전장치
    4차: cluster_id — 결정적 tiebreak
    """
    from app.services.batch_grouping import _SHEATH_COLOR_RANK

    meta = compute_cluster_meta(cluster, groups_meta)
    latest_due = meta["latest_due"] or date.max
    color_rank = _SHEATH_COLOR_RANK.get(cluster.color, 99)
    return (latest_due, color_rank, cluster.due_week_int, cluster.cluster_id)
