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

    Parity 보존 원칙: `cp_sat_schedule` 의 §6-f/-g-tiebreak/-g-slack/-h 와
    §6-Round2-transition 의 합성 순서와 연산자 사용을 **한 글자도 바꾸지
    않는다**. `+=` 가 아닌 `_objective = _objective + ...` 형태를 유지하는
    것도 solver 가 받는 LinearExpr 구조의 안정성을 위해서.

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
    model = built.model
    idle_terms = built.idle_terms
    tardiness_vars = built.tardiness_vars
    sheath_end_terms = built.sheath_end_terms
    slack_terms = built.slack_terms
    edd_pair_terms = built.edd_pair_terms
    edd_mixed_pastdue_terms = built.edd_mixed_pastdue_terms
    transition_terms = built.transition_terms

    # objective 초기화:
    #   tardiness_hard=True  → idle + past-due tardiness (on-time 은 hard 제약)
    #   tardiness_hard=False → sum(weight*tardiness) + idle (기존 soft 유지)
    # 시스 색상 교체 비용은 §6-g 에서 sequence-dependent gap 으로 duration 에 직접
    # 반영 (soft penalty 가 아닌 hard interval gap). Solver 가 실제 wall-clock
    # 을 정확히 인식 → tardiness 와의 tradeoff 를 모든 시스 그룹 쌍 단위로 평가.
    #
    # Past-due 가 tardiness_hard=True 에서도 tardiness_vars 에 포함됨 (build_model
    # 의 past-due 분기 참조). objective 에서도 해당 항을 반영해야 solver 가
    # past-due 그룹을 앞으로 배치.
    if tardiness_hard:
        _objective = weights.IDLE_WEIGHT * sum(idle_terms) if idle_terms else 0
        # Past-due: tardiness_vars 에 담긴 그룹만 weight-soft penalty (hard 는 못 검)
        if tardiness_vars:
            _objective = _objective + sum(
                group_meta[gk]["weight"] * tardiness_vars[gk] for gk in tardiness_vars
            )
    else:
        _objective = sum(
            meta["weight"] * tardiness_vars[gk] for gk, meta in group_meta.items()
        )
        if idle_terms:
            _objective = _objective + weights.IDLE_WEIGHT * sum(idle_terms)

    # §6-g-tiebreak. 시스 그룹 makespan bias — weight=1 (암묵).
    if sheath_end_terms:
        _objective = _objective + sum(sheath_end_terms)

    # §6-g-slack. On-time 그룹 slack-weighted completion.
    # slack_terms 는 이미 (w × end_var) 형태로 build_model 이 준비.
    if slack_terms:
        _objective = _objective + sum(slack_terms)

    # §6-h. EDD pair penalty (normal + mixed past-due).
    if edd_pair_terms:
        _objective = _objective + weights.EDD_PAIR_WEIGHT * sum(edd_pair_terms)
    if edd_mixed_pastdue_terms:
        _objective = _objective + weights.EDD_MIXED_PASTDUE_WEIGHT * sum(
            edd_mixed_pastdue_terms
        )

    # Round 2 HIGH #6 transition penalty.
    if transition_terms:
        _objective = _objective + weights.TRANSITION_WEIGHT * sum(transition_terms)

    model.minimize(_objective)
