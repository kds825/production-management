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

    def _gk_edd(gk: str) -> date:
        """그룹의 earliest_due — 우선 meta 저장값, 없으면 batches 에서 계산."""
        m = groups_meta.get(gk) or {}
        ed = m.get("earliest_due")
        if ed is not None:
            return ed
        dues = [b.due_date for b in (m.get("batches") or []) if b.due_date]
        return min(dues) if dues else date.max

    clusters: list[SheathCluster] = []
    for (cat, due_wk, color), gks in bucket.items():
        _, due_wk_tok = rep_of[(cat, due_wk, color)]
        color_safe = color.replace("/", "_")
        cluster_id = f"{cat}_{color_safe}_{due_wk_tok}"
        # Why EDD primary within cluster: 같은 week-bucket·색상 클러스터 내부의 그룹들
        # 은 due 가 다를 수 있음(예: 4/17 Friday 와 4/19 Sunday 모두 W16H2).
        # 알파벳 순(150SQ < 300SQ < 400SQ) 으로 배치하면 due 4/17 의 400SQ 가
        # due 4/19 의 300SQ 뒤로 밀려 납기 초과 + 긴 batch 가 주말 걸침 → 뒤따르는
        # cluster 전체 cascade. EDD 를 1차로 두면 같은 color chain 을 유지하면서
        # 납기 순서를 보장.
        clusters.append(
            SheathCluster(
                cluster_id=cluster_id,
                equipment_category=cat,
                color=color,
                due_week_int=due_wk,
                group_keys=sorted(gks, key=lambda g: (_gk_edd(g), g)),
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
    """묶음 정렬 키:
    (latest_due, pred_ready_wmin, color_rank, due_week_int, cluster_id).

    1차: latest_due — 납기 임박/오버듀 묶음을 앞으로 (납기 최우선)
    2차: pred_ready_wmin — 같은 납기 내에서 선행공정 빨리 끝난 묶음 먼저
         (설비 idle 최소화)
    3차: color_rank — 흑→갈→회→... (같은 납기·pred면 색상 체인 유도)
    4차: due_week_int — 안전장치
    5차: cluster_id — 결정적 tiebreak

    Why (2026-04-18 3rd iteration): 잠시 pred_ready_wmin 을 1차로 두었으나,
    solver 가 이미 초과된 납기(overdue)의 선행공정을 idle 최소화 차원에서
    뒤로 미루면서 pred_ready_wmin 이 매우 커지고, 결과적으로 긴급한 cluster
    가 sort 맨 뒤로 가서 +9 일 초과 등 심각한 regression 발생 (실측).
    → latest_due 를 다시 primary 로 두어 납기 순서 보장. pred_ready_wmin
       은 같은 납기 내 tiebreak 로만 사용 (idle 최소화 효과).
    """
    from app.domain.batch_sheath_keys import _SHEATH_COLOR_RANK

    meta = compute_cluster_meta(cluster, groups_meta)
    latest_due = meta["latest_due"] or date.max
    color_rank = _SHEATH_COLOR_RANK.get(cluster.color, 99)
    pred_ready_wmin = min(
        (int(groups_meta[gk].get("pred_ready_wmin") or 0) for gk in cluster.group_keys),
        default=0,
    )
    return (
        latest_due,
        pred_ready_wmin,
        color_rank,
        cluster.due_week_int,
        cluster.cluster_id,
    )


# ---------------------------------------------------------------------------
# Metric: 색상 전환 baseline (Phase 9-B 가중치 재조정 검증용)
# ---------------------------------------------------------------------------

# Why (intent): CP-SAT objective 에 색상 changeover penalty 를 추가/강화할 때
# "전후 비교" 가 필요. baseline 계산을 solver 로직과 분리해 metric 이 solver
# 변경의 영향을 받지 않도록 (관찰자 효과 격리). 120min 교체시간은 KBI 현장
# 표준 — 시스 공정 색상 교체 + 세척 + 시운전 총합.
_SHEATH_COLOR_CHANGEOVER_MIN: int = 120
_SHEATH_PROCESS_NAMES: frozenset[str] = frozenset({"저압시스", "고압시스"})


def count_color_transitions(run_label: str, db) -> dict:  # type: ignore[no-untyped-def]
    """시스 공정 ScheduleTask 를 설비별로 start_datetime 순 정렬해
    인접 쌍의 색상 전환 횟수를 계산.

    Args:
        run_label: 조회 대상 run 식별자.
        db: SQLAlchemy Session — 순환 임포트 피하려 lazy import 사용.

    Returns:
        {
            "total_transitions": int,   # 모든 시스 설비 전환 합
            "per_equipment": {
                equipment_code: {
                    "transitions": int,
                    "sequence": [color, ...],  # None 색 skip 후 리스트
                    "unique_colors": int,
                    "task_count": int,         # sequence 길이와 동일
                },
                ...
            },
            "categories": {
                "total_changeover_min": int,   # transitions * 120
            },
        }

    Rule:
        - 시스 판정: ProductionBatch.process_name ∈ {저압시스, 고압시스}
        - 색상 출처: ProductionBatch.sheath_color (컬럼이 이미 존재 —
          별도 파싱 불필요).
        - 인접 정의: 같은 equipment_code, start_datetime 오름차순.
        - 전환 정의: cur != prev (strip 공백 무시). None/빈문자열 색상은
          sequence 에서 skip — '미지정' 을 별도 색으로 세면 노이즈 전환 발생.
        - 설비 경계를 넘는 비교는 하지 않음 (독립 sequence).
    """
    # lazy import: 테스트에서 sheath_cluster 순수 유틸로도 쓰이므로 모델/ORM
    # 전역 import 부담을 피함.
    from app.infrastructure.models.production_batch import ProductionBatch
    from app.infrastructure.models.schedule_task import ScheduleTask

    # Fail-fast: run_label 누락/빈값이면 바로 empty 반환 (DB 호출 낭비 X)
    if not run_label:
        return {
            "total_transitions": 0,
            "per_equipment": {},
            "categories": {"total_changeover_min": 0},
        }

    # ScheduleTask + ProductionBatch join — 필요한 컬럼만 투영해 메모리 절약
    rows = (
        db.query(
            ScheduleTask.equipment_code,
            ScheduleTask.start_datetime,
            ProductionBatch.process_name,
            ProductionBatch.sheath_color,
        )
        .join(ProductionBatch, ScheduleTask.batch_id == ProductionBatch.batch_id)
        .filter(ScheduleTask.run_label == run_label)
        .filter(ProductionBatch.process_name.in_(list(_SHEATH_PROCESS_NAMES)))
        .order_by(ScheduleTask.equipment_code, ScheduleTask.start_datetime)
        .all()
    )

    per_equipment: dict[str, dict] = {}
    for eq_code, _start, _proc, color in rows:
        c = (color or "").strip()
        if not c:
            # None/"" → skip (spec: None 은 별도 색으로 취급 안 함)
            continue
        entry = per_equipment.setdefault(
            eq_code,
            {"transitions": 0, "sequence": [], "unique_colors": 0, "task_count": 0},
        )
        seq: list[str] = entry["sequence"]
        if seq and seq[-1] != c:
            entry["transitions"] += 1
        seq.append(c)

    # 파생값 — 후처리로 한 번에 계산 (루프 내 반복 계산 회피)
    total_transitions = 0
    for entry in per_equipment.values():
        seq = entry["sequence"]
        entry["task_count"] = len(seq)
        entry["unique_colors"] = len(set(seq))
        total_transitions += entry["transitions"]

    return {
        "total_transitions": total_transitions,
        "per_equipment": per_equipment,
        "categories": {
            "total_changeover_min": total_transitions * _SHEATH_COLOR_CHANGEOVER_MIN,
        },
    }
