"""파이프라인 유휴 시간 soft penalty — 후공정 end - 선행공정 end.

ConstraintConfig 매핑: implicit (objective term, no row).
원래 ``model_builder.build_model`` §6-f idle_terms 블록.

핵심:
  - PREDECESSOR_PROCESS 가 정의한 사슬에서 같은 SQ 그룹쌍에 대해 idle = succ_end -
    pred_end 변수 생성. 6-d 의 hard constraint (succ_end ≥ pred_end) 가 양수성을
    보장하므로 idle ≥ 0.
  - objective 합성 시 ``IDLE_WEIGHT`` 와 곱해 minimize.

Why pure helper module:
  ``build_model`` §6-f 의 ~10 LOC 인라인. proc_groups_by_sq 를 predecessor 와
  공유하므로 caller 가 한 번 만들어 양쪽에 주입.
"""

from __future__ import annotations

from typing import Any

from ortools.sat.python import cp_model

from app.domain.constants import PREDECESSOR_PROCESS


def collect_idle_terms(
    *,
    model: cp_model.CpModel,
    group_meta: dict[str, Any],
    end_vars: dict[str, cp_model.IntVar],
    proc_groups_by_sq: dict[tuple[str, int], list[str]],
    max_horizon_min: int,
) -> list[cp_model.IntVar]:
    """§6-f. ``idle_<pred_gk>_<gk> == end[gk] - end[pred_gk]`` 변수 모음.

    objective 합성 단계가 ``IDLE_WEIGHT * sum(idle_terms)`` 로 minimize 한다.
    """
    idle_terms: list[cp_model.IntVar] = []
    for gk in group_meta.keys():
        pred_proc = PREDECESSOR_PROCESS.get(group_meta[gk]["rep"].process_name)
        if not pred_proc:
            continue
        for pred_gk in proc_groups_by_sq.get((pred_proc, group_meta[gk]["sq"]), []):
            idle = model.new_int_var(0, max_horizon_min, f"idle_{pred_gk}_{gk}")
            model.add(idle == end_vars[gk] - end_vars[pred_gk])
            idle_terms.append(idle)
    return idle_terms
