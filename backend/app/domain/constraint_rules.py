"""제약(spec/color) 분기 결정 — pure dispatch 룰.

Why: schedule_optimizer / cp_sat_optimizer / batch_grouping 등 다수 use-case 가
ConstraintConfig 4-1 (규격교체) / 4-2 (색상교체) 시간 결정에 동일 우선순위
규칙을 사용한다. 각 호출부가 직접 분기하면 드리프트 위험이 있어 본 모듈로
중앙집중화한다.

본 모듈은 pure function 만 포함한다 — DB / I/O / 외부 SDK 의존 0.
ConstraintParams 자체의 적재(`load`) 와 dataclass 정의는 application layer
(`application/_shared/constraint_params.py`) 에 위치한다. 본 모듈은
`ConstraintParams` 를 TYPE_CHECKING 으로만 참조해 도메인 → 어플리케이션
역방향 런타임 의존을 피한다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.application._shared.constraint_params import ConstraintParams


# 공정명 → ConstraintConfig 4-1 params_json 키 매핑.
# 이 매핑에 들어있는 공정은 ConstraintConfig 4-1 이 authoritative:
# SpeedMaster.setup_spec_min 값이 있어도 ConstraintConfig 를 우선 사용한다.
# 이유: UI(/master/constraints) 에서 한 번 편집으로 전체 공정에 일관 반영되게
# 하기 위함. 장비·SQ별 예외 처리는 현재 도메인 요구가 없음.
_PROCESS_TO_SPEC_KEY: dict[str, str] = {
    "연선": "stranding_min",
    "저압절연": "insulation_min",
    "저압시스": "sheath_min",
    "고압절연": "cv_min",
}


def resolve_spec_setup_min(
    process_name: str | None,
    sm_spec_min: float | None,
    params: "ConstraintParams",
) -> float:
    """규격교체(setup_spec_min) 시간 결정 — ConstraintConfig 4-1 우선.

    Why: 사용자가 /master/constraints 4-1 에서 편집한 값이 즉시 전체 공정에
    반영되어야 함. 이전엔 SpeedMaster.setup_spec_min 이 우선이라 4-1 편집이
    무력했음.

    우선순위:
    1. process_name 이 4-1 매핑에 있으면 → ConstraintConfig 4-1 의 해당 키 값
    2. 매핑 없는 공정(고압시스/연합/T/P 등) → SpeedMaster.setup_spec_min 사용
    3. 둘 다 없으면 → 0.0
    """
    key = _PROCESS_TO_SPEC_KEY.get(process_name or "")
    if key:
        return params.get("4-1", key, default=0.0)
    if sm_spec_min is not None:
        return float(sm_spec_min)
    return 0.0


def resolve_color_change_min(
    sm_color_min: float | None,
    params: "ConstraintParams",
) -> float:
    """색상교체 시간 결정.

    Why: schedule_optimizer 와 cp_sat_optimizer 양쪽에서 동일 규칙을 쓰기 위해
    이 모듈에 둠. 두 스케줄러가 각자 구현하면 드리프트 위험.

    - SpeedMaster.setup_color_min 이 None 이면 ConstraintConfig 4-2 fallback.
    - 0.0 은 '값 없음' 이 아닌 '0분 허용' 으로 처리 (시맨틱 교정).
    """
    if sm_color_min is None:
        return params.get("4-2", "sheath_color_min")
    return float(sm_color_min)
