"""CP-SAT objective composition — pure function extracted from `cp_sat_schedule`.

Task 2A.2 (Production Handoff Refactor, Week 2): `cp_sat_schedule` 의 §6-f ~
§6-h 에서 누적되는 penalty / slack / EDD / transition term 들을
`model.minimize(...)` 로 합성하는 로직을 pure 함수 `compose_objective` 로 분리.

## Why separate from `model_builder.py`

Week 5 에서 weights 는 `ConstraintSpec.weight` 로 이동할 예정이다. 그 때
objective 합성 로직만 바뀌고 variable 생성 / hard constraint 포스팅은
그대로 유지되도록 **두 모듈을 지금부터 분리**한다. 현재는 parity 보존을
위해 `cp_sat_optimizer.py` 의 module constants 를 `ModelWeights` 번들로
받아 그대로 사용한다.

## Boundary invariant (Spec §7)

`app.infrastructure` 를 import 하지 않는다. model / IntVar 객체만 다룬다.
"""

from __future__ import annotations

from typing import Any


from app.services.solver.model_builder import BuiltModel, ModelWeights


def compose_objective(
    built: BuiltModel,
    *,
    weights: ModelWeights,
    group_meta: dict[str, Any],
    tardiness_hard: bool,
) -> None:
    """`built.model.minimize(...)` 를 호출해 objective 를 확정.

    이 함수는 `built` 의 IntVar 리스트들을 읽어 가중합을 계산하고
    `model.minimize(_objective)` 한 번을 호출한다. 반환 값 없음 — 모델 자체가
    mutate 된다.

    Args:
        built: `build_model` 반환 값. idle_terms / transition_terms /
            sheath_end_terms / slack_terms / tardiness_vars / edd_pair_terms /
            edd_mixed_pastdue_terms / model 을 읽는다.
        weights: `ModelWeights` 번들. IDLE_WEIGHT / EDD_PAIR_WEIGHT /
            EDD_MIXED_PASTDUE_WEIGHT / TRANSITION_WEIGHT 등 사용.
        group_meta: `SolverInput.group_meta` — tardiness_vars 에 곱해질
            `meta["weight"]` 조회용.
        tardiness_hard: True 면 tardiness term 은 past-due 그룹만 (soft),
            False 면 전체 그룹의 `meta["weight"] * tard` 합.
    """
    raise NotImplementedError("Task 2A.2 sub-commit 2 에서 구현")
