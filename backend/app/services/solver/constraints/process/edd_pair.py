"""EDD pair penalty — 같은 공정 + 공유 설비 후보 그룹쌍의 납기 순서 위반.

ConstraintConfig 매핑: 1-1 tardiness (EDD 강화 분기).
원래 ``model_builder.build_model`` §6-h 블록.

핵심:
  - 같은 공정 + 공통 eligible 설비가 있는 그룹쌍에서 납기 빠른 쪽이 늦게 시작
    하면 ``wrong=1`` bool 변수 생성. objective 합성 시 ``EDD_PAIR_WEIGHT``
    (혹은 mixed pastdue 인 경우 ``EDD_MIXED_PASTDUE_WEIGHT``) 와 곱해 minimize.
  - past-due ↔ on-time 혼합 쌍은 별도 리스트로 분류해 heavy weight (1e9) 적용.
    "past-due 가 무조건 먼저" 도메인 룰 강제.

Why 분리:
  ~60 LOC 의 O(n²) loop. process 카테고리이지만 구현이 process_name 분기보다는
  같은 공정 분류 dict 기반이라 ``process/`` 디렉토리에 둠.
"""

from __future__ import annotations

from typing import Any

from ortools.sat.python import cp_model


def collect_edd_pair_terms(
    *,
    model: cp_model.CpModel,
    group_meta: dict[str, Any],
    start_vars: dict[str, cp_model.IntVar],
    equip_vars: dict[str, dict[str, cp_model.IntVar]],
    max_horizon_min: int,
) -> tuple[list[cp_model.IntVar], list[cp_model.IntVar]]:
    """§6-h. Returns ``(edd_pair_terms, edd_mixed_pastdue_terms)``."""
    edd_pair_terms: list[cp_model.IntVar] = []
    edd_mixed_pastdue_terms: list[cp_model.IntVar] = []
    process_gks: dict[str, list[str]] = {}
    for gk, meta in group_meta.items():
        process_gks.setdefault(meta["rep"].process_name, []).append(gk)
    for _proc, gks_proc in process_gks.items():
        for i in range(len(gks_proc)):
            for j in range(i + 1, len(gks_proc)):
                gk_a = gks_proc[i]
                gk_b = gks_proc[j]
                due_a = group_meta[gk_a]["due_wmin"]
                due_b = group_meta[gk_b]["due_wmin"]
                if due_a == max_horizon_min or due_b == max_horizon_min:
                    continue
                if due_a == due_b:
                    continue
                if not (set(equip_vars[gk_a].keys()) & set(equip_vars[gk_b].keys())):
                    continue
                if due_a < due_b:
                    earlier, later = gk_a, gk_b
                else:
                    earlier, later = gk_b, gk_a
                wrong = model.new_bool_var(f"edd_wrong_{earlier}__{later}")
                model.add(start_vars[earlier] > start_vars[later]).only_enforce_if(
                    wrong
                )
                model.add(start_vars[earlier] <= start_vars[later]).only_enforce_if(
                    wrong.Not()
                )
                earlier_due = group_meta[earlier]["due_wmin"]
                later_due = group_meta[later]["due_wmin"]
                is_mixed_pastdue = earlier_due < 0 <= later_due
                if is_mixed_pastdue:
                    edd_mixed_pastdue_terms.append(wrong)
                else:
                    edd_pair_terms.append(wrong)
    return edd_pair_terms, edd_mixed_pastdue_terms
