"""시스 색상 체인 Hard Constraint — sheath_color_hard=True 일 때만.

ConstraintConfig 매핑: 3-2 sheath_color_grouping (hard 분기).
원래 ``model_builder.build_model`` §6-f-hard 블록.

핵심:
  - ``build_sheath_clusters`` 가 만든 클러스터 (설비 카테고리 + 주차 + 색상) 의
    인접 그룹쌍에 대해:
    1) ``start_b ≥ end_a + color_gap`` (시간 gap)
    2) 공통 eligible 설비에서 두 그룹이 같은 bool 활성화 (설비 일치)
  - 둘 다 frozen 인 쌍은 skip (start 이미 고정 → 추가 제약 모순 위험).

Why pure helper module:
  ``build_model`` §6-f-hard 의 ~50 LOC 인라인을 분리. 도메인 invariant 가
  강해 함수 분리만으로 의미가 명확해진다.
"""

from __future__ import annotations

from typing import Any

from ortools.sat.python import cp_model

from app.services.constraint_params import ConstraintParams, resolve_color_change_min
from app.services.sheath_cluster import build_sheath_clusters


def add_sheath_color_hard_chain(
    *,
    model: cp_model.CpModel,
    group_meta: dict[str, Any],
    start_vars: dict[str, cp_model.IntVar],
    end_vars: dict[str, cp_model.IntVar],
    equip_vars: dict[str, dict[str, cp_model.IntVar]],
    constraint_params: ConstraintParams,
    frozen_group_keys: set[str] | None,
) -> None:
    """§6-f-hard. 시스 색상 hard chain. ``sheath_color_hard=True`` caller branch."""
    clusters_hard = build_sheath_clusters(group_meta)
    color_gap_min = int(
        round(
            resolve_color_change_min(
                sm_color_min=None,
                params=constraint_params,
            )
        )
    )
    frozen_set = frozen_group_keys or set()

    for cluster in clusters_hard:
        gks_ord = cluster.group_keys
        if len(gks_ord) < 2:
            continue
        for i in range(len(gks_ord) - 1):
            gk_a = gks_ord[i]
            gk_b = gks_ord[i + 1]
            if gk_a not in start_vars or gk_b not in start_vars:
                continue
            if gk_a not in equip_vars or gk_b not in equip_vars:
                continue
            if gk_a in frozen_set and gk_b in frozen_set:
                continue
            model.add(start_vars[gk_b] >= end_vars[gk_a] + color_gap_min)
            shared_eqs = equip_vars[gk_a].keys() & equip_vars[gk_b].keys()
            for eq in shared_eqs:
                model.add(equip_vars[gk_a][eq] == equip_vars[gk_b][eq])
