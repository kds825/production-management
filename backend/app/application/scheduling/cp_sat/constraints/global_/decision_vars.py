"""Decision variable creation — start / end / equip_bool / tardiness / dur.

ConstraintConfig 매핑: implicit (모델 기반 변수, ``ConstraintConfig`` row 직접
대응 없음). 단 1-1 tardiness 의 hard 분기가 본 모듈에서 ``e ≤ due_wmin`` 으로
posting 되므로 ``tardiness_hard`` flag 가 동작 분기를 결정.

원래 ``model_builder.build_model`` §6-b 블록.

핵심:
  - 그룹별 (start, end, dur) IntVar 생성. ``per_eq_dur_enabled`` 그룹은 dur 가
    설비별 상이 → ``dur_var = sum(eq_bool_i * dur_i)`` 선형합 (Round 2 HIGH #5).
  - tardiness_hard 모드:
      * earliest_due 있고 due_wmin ≥ 0 → ``e ≤ due_wmin`` hard.
      * earliest_due 있고 due_wmin < 0 → past-due → soft tardiness 변수 생성
        (objective 에서 weight × tardiness 로 압박, 솔버가 앞으로 끌도록).
      * earliest_due 없음 → tardiness 미생성 (no-due 그룹).
  - tardiness_hard=False (legacy soft 모드): 모든 그룹에 ``tard = max(0, e - due_wmin)``.
  - 설비 결정 변수: ``eq_bools[ec]`` per group, ``add_exactly_one``.

Why pure helper module:
  ``build_model`` §6-b 의 ~95 LOC 인라인. tardiness hard/soft 분기와 per_eq_dur
  분기가 한 함수에 섞여 있어 분리 시 의미가 더 명확해진다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ortools.sat.python import cp_model


@dataclass
class DecisionVars:
    """``add_decision_vars`` 의 반환 묶음 — caller (build_model) 가 BuiltModel
    필드로 그대로 노출."""

    start_vars: dict[str, cp_model.IntVar]
    end_vars: dict[str, cp_model.IntVar]
    equip_vars: dict[str, dict[str, cp_model.IntVar]]
    tardiness_vars: dict[str, cp_model.IntVar]
    dur_vars: dict[str, Any]


def add_decision_vars(
    *,
    model: cp_model.CpModel,
    group_meta: dict[str, Any],
    max_horizon_min: int,
    tardiness_hard: bool,
    warnings: list[str],
) -> DecisionVars:
    """§6-b. 그룹별 결정 변수 생성 + 1-1 tardiness hard/soft posting."""
    start_vars: dict[str, cp_model.IntVar] = {}
    end_vars: dict[str, cp_model.IntVar] = {}
    equip_vars: dict[str, dict[str, cp_model.IntVar]] = {}
    tardiness_vars: dict[str, cp_model.IntVar] = {}
    dur_vars: dict[str, Any] = {}

    for gk in group_meta.keys():
        meta = group_meta[gk]
        dur = meta["cpsat_dur"]
        per_eq = meta.get("per_eq_dur_enabled", False)
        dur_by_eq = meta.get("cpsat_dur_by_eq") or {}

        if per_eq and dur_by_eq:
            min_dur = min(dur_by_eq.values())
            max_dur = max(dur_by_eq.values())
            dur_var = model.new_int_var(min_dur, max_dur, f"dur_{gk}")
            s = model.new_int_var(0, max_horizon_min - min_dur, f"s_{gk}")
            e = model.new_int_var(min_dur, max_horizon_min, f"e_{gk}")
            model.add(e == s + dur_var)
            dur_vars[gk] = dur_var
        else:
            s = model.new_int_var(0, max_horizon_min - dur, f"s_{gk}")
            e = model.new_int_var(dur, max_horizon_min, f"e_{gk}")
            model.add(e == s + dur)
            dur_vars[gk] = None

        start_vars[gk] = s
        end_vars[gk] = e

        if tardiness_hard:
            if meta.get("earliest_due") is not None:
                if meta["due_wmin"] < 0:
                    warnings.append(
                        f"그룹 {gk}: 납기 {meta['earliest_due']} 이미 "
                        f"{abs(meta['due_wmin'])}min 지남 — tardiness hard 강제 skip, "
                        f"soft penalty 로 전환"
                    )
                    tard = model.new_int_var(0, 2 * max_horizon_min, f"t_past_{gk}")
                    model.add_max_equality(
                        tard, [e - meta["due_wmin"], model.new_constant(0)]
                    )
                    tardiness_vars[gk] = tard
                else:
                    model.add(e <= meta["due_wmin"])
        else:
            tard = model.new_int_var(0, 2 * max_horizon_min, f"t_{gk}")
            model.add_max_equality(tard, [e - meta["due_wmin"], model.new_constant(0)])
            tardiness_vars[gk] = tard

        eq_bools: dict[str, cp_model.IntVar] = {}
        for eq in meta["eligible"]:
            eq_bools[eq.equipment_code] = model.new_bool_var(
                f"eq_{gk}_{eq.equipment_code}"
            )
        equip_vars[gk] = eq_bools
        model.add_exactly_one(eq_bools.values())

        if dur_vars.get(gk) is not None:
            dur_by_eq_local = meta["cpsat_dur_by_eq"]
            model.add(
                dur_vars[gk]
                == sum(
                    eq_bools[ec] * int(dur_by_eq_local[ec]) for ec in eq_bools.keys()
                )
            )

    return DecisionVars(
        start_vars=start_vars,
        end_vars=end_vars,
        equip_vars=equip_vars,
        tardiness_vars=tardiness_vars,
        dur_vars=dur_vars,
    )
