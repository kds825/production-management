"""연선 SQ-transition setup penalty — Round 2 HIGH #6.

ConstraintConfig 매핑: 4-1 spec_change_setup (연선 SQ 전환).
원래 ``model_builder.build_model`` Round-2-transition 블록.

핵심:
  - 연선 (rep.process_name == "연선") 그룹쌍 중 SQ 가 다른 경우만 처리.
  - 두 그룹 같은 설비에 배치 → ``trans=1`` bool 생성, ``TRANSITION_WEIGHT`` 곱해
    objective 에 더한다.
  - 납기 ordinal 14일 초과 차이는 sequence 영향 미미 → skip (성능 + 의미).

Why proxy 모델:
  adjacency 단위 sequence-dependent setup 의 정확 모델링은 AddCircuit 필요 →
  현재 group=single-interval 구조에서는 동일-설비-bool 의 합산으로 근사. PoC
  목적엔 충분.
"""

from __future__ import annotations

from typing import Any

from ortools.sat.python import cp_model


def collect_transition_terms(
    *,
    model: cp_model.CpModel,
    group_meta: dict[str, Any],
    equip_vars: dict[str, dict[str, cp_model.IntVar]],
) -> list[cp_model.IntVar]:
    """Round-2-transition. Returns ``transition_terms``."""
    transition_terms: list[cp_model.IntVar] = []
    stranding_gks = [
        g for g, m in group_meta.items() if m["rep"].process_name == "연선"
    ]
    for i in range(len(stranding_gks)):
        for j in range(i + 1, len(stranding_gks)):
            gk_a = stranding_gks[i]
            gk_b = stranding_gks[j]
            meta_a = group_meta[gk_a]
            meta_b = group_meta[gk_b]
            if meta_a["sq"] == meta_b["sq"]:
                continue
            shared = set(equip_vars[gk_a].keys()) & set(equip_vars[gk_b].keys())
            if not shared:
                continue
            due_a = meta_a.get("due_date_ord")
            due_b = meta_b.get("due_date_ord")
            if due_a is not None and due_b is not None and abs(due_b - due_a) > 14:
                continue
            same_eq_bools: list[Any] = []
            for ec in shared:
                both = model.new_bool_var(f"both_{gk_a}_{gk_b}_{ec}")
                model.add_bool_and(
                    [equip_vars[gk_a][ec], equip_vars[gk_b][ec]]
                ).only_enforce_if(both)
                model.add_bool_or(
                    [equip_vars[gk_a][ec].Not(), equip_vars[gk_b][ec].Not()]
                ).only_enforce_if(both.Not())
                same_eq_bools.append(both)
            trans = model.new_bool_var(f"trans_{gk_a}_{gk_b}")
            model.add(trans == sum(same_eq_bools))
            transition_terms.append(trans)
    return transition_terms
