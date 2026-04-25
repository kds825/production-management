"""Frozen group pinning — 긴급수주 재최적화 시 진행/완료 배치 고정.

ConstraintConfig 매핑: 1-3 urgent_change (재최적화 시 frozen 정책).
원래 ``model_builder.build_model`` §6-b-2 블록.

핵심:
  - 호출자가 ``frozen_group_keys`` 로 고정 대상 그룹키 집합을 명시하면,
    각 그룹의 ``start_var`` 를 DB snapshot 의 ``start_wmin`` 으로 박고
    설비 bool 도 고정. ``end_var`` 는 ``e == s + dur`` 로 묶여있어 자동 고정.
  - 방어:
      * group_meta 에 없는 키 → warning + skip
      * snapshot 미존재 → warning + skip
      * start 가 horizon 초과 → warning + skip (clamp 대신 skip 으로 INFEASIBLE 방지)
      * 설비 코드가 eligible 밖 → warning + 시간만 고정 (설비 고정 skip)

Why infrastructure 미접촉:
  caller (cp_sat_schedule) 가 미리 ScheduleTask 를 dict snapshot 으로 변환해
  ``frozen_tasks_snapshot`` 에 주입. 본 모듈은 pure dict lookup 만 수행 →
  services/solver/* 의 infrastructure-import 금지 경계 준수.
"""

from __future__ import annotations

from typing import Any

from ortools.sat.python import cp_model


def apply_frozen_pins(
    *,
    model: cp_model.CpModel,
    group_meta: dict[str, Any],
    start_vars: dict[str, cp_model.IntVar],
    equip_vars: dict[str, dict[str, cp_model.IntVar]],
    frozen_group_keys: set[str] | None,
    frozen_tasks_snapshot: dict[str, dict[str, Any]] | None,
    max_horizon_min: int,
    warnings: list[str],
) -> None:
    """§6-b-2. frozen_group_keys 의 각 그룹을 DB snapshot 으로 고정."""
    if not frozen_group_keys:
        return
    snapshot = frozen_tasks_snapshot or {}
    for gk in frozen_group_keys:
        if gk not in group_meta:
            warnings.append(
                f"frozen_group_keys: '{gk}' 은(는) group_meta 에 없어 고정 불가 (skip)"
            )
            continue
        snap = snapshot.get(gk)
        if snap is None:
            warnings.append(
                f"frozen_group_keys: '{gk}' 에 해당하는 ScheduleTask 없음 (skip)"
            )
            continue

        fixed_start_wmin = int(snap["start_wmin"])
        fixed_eq_code = snap["equipment_code"]

        dur = group_meta[gk]["cpsat_dur"]
        if fixed_start_wmin > max_horizon_min - dur:
            warnings.append(
                f"frozen_group_keys: '{gk}' start 가 horizon 초과 → 고정 skip"
            )
            continue

        model.add(start_vars[gk] == fixed_start_wmin)

        # 설비 고정: eligible 에 있으면 박고, 없으면 warning 만 (시간만 고정).
        if fixed_eq_code in equip_vars[gk]:
            for ec, bv in equip_vars[gk].items():
                if ec == fixed_eq_code:
                    model.add(bv == 1)
                else:
                    model.add(bv == 0)
        else:
            warnings.append(
                f"frozen_group_keys: '{gk}' 의 고정 설비 '{fixed_eq_code}' 가 "
                f"eligible 에 없음 → 설비 고정 skip (시간만 고정)"
            )
