"""설비별 no_overlap hard constraint — 같은 설비에서 두 작업 동시 진행 금지.

ConstraintConfig 매핑: implicit (도메인 invariant — physical equipment exclusivity).
원래 ``model_builder.build_model`` §6-c 블록.

핵심:
  - 그룹 × 설비별로 ``optional_interval_var`` 생성 — 해당 설비 bool=1 일 때만 active.
  - per_eq_dur_enabled 그룹은 설비별 dur 가 다르므로 interval size 도 설비별.
    (Round 2 HIGH #5: 빠른 설비를 인식해 overlap 회피 학습)
  - 같은 설비에 active interval 이 2개 이상이면 ``add_no_overlap`` 강제.

Why pure helper module:
  ``build_model`` §6-c 의 ~50 LOC 인라인 블록을 분리. itv_vars dict 는 BuiltModel
  필드로도 노출되어야 하므로 caller (build_model) 가 반환값을 받는다.
"""

from __future__ import annotations

from typing import Any

from ortools.sat.python import cp_model


def add_equipment_no_overlap(
    *,
    model: cp_model.CpModel,
    group_meta: dict[str, Any],
    all_eq_codes: list[str],
    start_vars: dict[str, cp_model.IntVar],
    end_vars: dict[str, cp_model.IntVar],
    equip_vars: dict[str, dict[str, cp_model.IntVar]],
    max_horizon_min: int,
) -> dict[tuple[str, str], Any]:
    """§6-c. 설비 충돌 방지 (no_overlap) + per_eq_dur 인터벌 정합.

    Returns:
        ``itv_vars``: ``{(group_key, equipment_code): IntervalVar}`` 딕셔너리.
        BuiltModel.itv_vars 로 노출되며 trace_writer / decision_aggregator 가
        consume.
    """
    itv_vars: dict[tuple[str, str], Any] = {}
    for gk in group_meta.keys():
        meta = group_meta[gk]
        dur_scalar = meta["cpsat_dur"]
        per_eq_enabled = meta.get("per_eq_dur_enabled", False)
        dur_by_eq = meta.get("cpsat_dur_by_eq") or {}
        for eq_code, bv in equip_vars[gk].items():
            _itv_size = (
                int(dur_by_eq.get(eq_code, dur_scalar))
                if per_eq_enabled
                else dur_scalar
            )
            if per_eq_enabled:
                # optional interval 은 size=상수 일 때 자체 end IntVar 를 요구.
                # start 는 공통 start_vars[gk] 사용, end 는 helper 생성.
                _e_eq = model.new_int_var(
                    _itv_size, max_horizon_min, f"e_{gk}_{eq_code}"
                )
                # bv=1 일 때만 (s + size == _e_eq AND _e_eq == end_vars[gk]) 강제.
                model.add(_e_eq == start_vars[gk] + _itv_size).only_enforce_if(bv)
                model.add(_e_eq == end_vars[gk]).only_enforce_if(bv)
                itv = model.new_optional_interval_var(
                    start_vars[gk], _itv_size, _e_eq, bv, f"itv_{gk}_{eq_code}"
                )
            else:
                itv = model.new_optional_interval_var(
                    start_vars[gk],
                    dur_scalar,
                    end_vars[gk],
                    bv,
                    f"itv_{gk}_{eq_code}",
                )
            itv_vars[(gk, eq_code)] = itv

    for eq_code in all_eq_codes:
        itvs = [
            itv_vars[(gk, eq_code)]
            for gk in group_meta.keys()
            if eq_code in equip_vars.get(gk, {})
        ]
        if len(itvs) >= 2:
            model.add_no_overlap(itvs)

    return itv_vars
