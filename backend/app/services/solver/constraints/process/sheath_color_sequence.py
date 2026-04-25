"""시스 색상 Sequence-Dependent Setup + makespan tiebreak.

ConstraintConfig 매핑: 4-2 sheath_color_changeover (sequence 분기) + 3-2 (tiebreak).
원래 ``model_builder.build_model`` §6-g + §6-g-tiebreak 블록.

핵심 (sequence):
  - 시스 공정 그룹쌍 (저압시스/고압시스) 중 색상 다르고 공통 eligible 설비 있는
    경우, conditional gap 제약을 부여:
      same_eq = OR_{ec ∈ shared}(equip_a[ec] ∧ equip_b[ec])
      a_before_b ∈ {0,1} (solver 자율)
      same_eq=1 ∧ a_before_b=1  ⇒  start_b ≥ end_a + color_gap
      same_eq=1 ∧ a_before_b=0  ⇒  start_a ≥ end_b + color_gap
  - 같은 색상 / 미지정 / 공통 설비 없음 → skip (no_overlap 으로 충분).

핵심 (tiebreak):
  - 시스 그룹의 end_var 합을 objective 에 더해 grouped 해 선호 (weight=1 암묵).

두 블록을 한 모듈에 두는 이유:
  ``_sheath_gks_all`` 리스트를 양쪽이 공유. 분리 시 두 번 walk 하게 됨.
"""

from __future__ import annotations

from typing import Any

from ortools.sat.python import cp_model

from app.services.constraint_params import ConstraintParams, resolve_color_change_min


def add_sheath_color_sequence_and_tiebreak(
    *,
    model: cp_model.CpModel,
    group_meta: dict[str, Any],
    start_vars: dict[str, cp_model.IntVar],
    end_vars: dict[str, cp_model.IntVar],
    equip_vars: dict[str, dict[str, cp_model.IntVar]],
    constraint_params: ConstraintParams,
) -> list[cp_model.IntVar]:
    """§6-g + §6-g-tiebreak. Returns ``sheath_end_terms`` for objective."""
    sheath_color_gap_min = int(
        round(
            resolve_color_change_min(
                sm_color_min=None,
                params=constraint_params,
            )
        )
    )
    sheath_gks_all = [
        g
        for g, m in group_meta.items()
        if m["rep"].process_name in ("저압시스", "고압시스")
    ]
    for i in range(len(sheath_gks_all)):
        for j in range(i + 1, len(sheath_gks_all)):
            gk_a = sheath_gks_all[i]
            gk_b = sheath_gks_all[j]
            color_a = (group_meta[gk_a]["rep"].sheath_color or "").strip()
            color_b = (group_meta[gk_b]["rep"].sheath_color or "").strip()
            if not color_a or not color_b or color_a == color_b:
                continue
            shared_eqs_color = set(equip_vars[gk_a].keys()) & set(
                equip_vars[gk_b].keys()
            )
            if not shared_eqs_color:
                continue
            both_bools = []
            for ec in shared_eqs_color:
                both = model.new_bool_var(f"sh_both_{gk_a}_{gk_b}_{ec}")
                model.add_bool_and(
                    [equip_vars[gk_a][ec], equip_vars[gk_b][ec]]
                ).only_enforce_if(both)
                model.add_bool_or(
                    [equip_vars[gk_a][ec].Not(), equip_vars[gk_b][ec].Not()]
                ).only_enforce_if(both.Not())
                both_bools.append(both)
            same_eq = model.new_bool_var(f"sh_same_eq_{gk_a}_{gk_b}")
            model.add(same_eq == sum(both_bools))
            a_before_b = model.new_bool_var(f"sh_order_{gk_a}_{gk_b}")
            model.add(
                start_vars[gk_b] >= end_vars[gk_a] + sheath_color_gap_min
            ).only_enforce_if([same_eq, a_before_b])
            model.add(
                start_vars[gk_a] >= end_vars[gk_b] + sheath_color_gap_min
            ).only_enforce_if([same_eq, a_before_b.Not()])

    # §6-g-tiebreak — sheath end_var 합 (weight=1)
    sheath_end_terms = [end_vars[g] for g in sheath_gks_all]
    return sheath_end_terms
