"""Warm-start hint 주입 — 자유 변수 대상 탐색 시작점 제안.

ConstraintConfig 매핑: implicit (탐색 효율, ConstraintConfig row 없음).
원래 ``model_builder.build_model`` §6-b2 블록.

핵심:
  - 호출자가 ``warm_start_hints={gk: {start_wmin, equipment_code}}`` 를 주면
    ``model.add_hint`` 으로 해당 변수의 초기 값을 제안.
  - frozen_group_keys 에 이미 hard-pin 된 그룹은 redundant → skip.
  - 모델에 없는 (스테일) 키, dict 가 아닌 값 → skip + 카운트.
  - hint 실패 (silent failure 가 정상): 전체 optimize 를 깨면 안 되므로 try/except.

Why hint 가 hard 가 아닌가:
  hint 가 현 제약과 충돌하면 silent-fail (솔버는 죽지 않고 전역 탐색). 더 나은
  해 발견 시 자유 이동. 포트폴리오 워커 간 공유되어 병렬 탐색 가속.
"""

from __future__ import annotations

from typing import Any

from ortools.sat.python import cp_model


def apply_warm_start_hints(
    *,
    model: cp_model.CpModel,
    group_meta: dict[str, Any],
    start_vars: dict[str, cp_model.IntVar],
    equip_vars: dict[str, dict[str, cp_model.IntVar]],
    warm_start_hints: dict[str, dict[str, Any]] | None,
    frozen_group_keys: set[str] | None,
    max_horizon_min: int,
) -> tuple[int, int]:
    """§6-b2. Returns ``(applied, skipped)`` for caller stat tracking."""
    applied = 0
    skipped = 0
    if not warm_start_hints:
        return applied, skipped

    frozen_set = frozen_group_keys or set()
    for gk, snap in warm_start_hints.items():
        if gk in frozen_set:
            skipped += 1
            continue
        if gk not in start_vars:
            skipped += 1
            continue
        if not isinstance(snap, dict):
            skipped += 1
            continue
        hint_applied_one = False

        start_wmin = snap.get("start_wmin")
        if isinstance(start_wmin, int):
            dur = group_meta[gk]["cpsat_dur"]
            if 0 <= start_wmin <= max_horizon_min - dur:
                try:
                    model.add_hint(start_vars[gk], start_wmin)
                    hint_applied_one = True
                except Exception:
                    pass

        eq_code = snap.get("equipment_code")
        if eq_code and eq_code in equip_vars.get(gk, {}):
            try:
                for ec, bv in equip_vars[gk].items():
                    model.add_hint(bv, 1 if ec == eq_code else 0)
                hint_applied_one = True
            except Exception:
                pass

        if hint_applied_one:
            applied += 1
        else:
            skipped += 1

    return applied, skipped
