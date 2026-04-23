"""ConstraintConfig 파라미터 프리페치 캐시.

Why: schedule_optimizer / batch_grouping 이 루프 내부에서 ConstraintConfig 를
조회하면 N+1 쿼리가 발생한다. 한 요청(create_batches 또는 auto_schedule)
진입 시 1회 프리페치하여 dict 스냅샷으로 전달한다.

전역 lru_cache 사용 금지 — PATCH 후 stale 위험.
"""

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.infrastructure.models.constraint_config import ConstraintConfig


@dataclass(frozen=True)
class ConstraintParams:
    """create_batches / auto_schedule 1회 실행 동안 재사용되는 스냅샷."""

    by_id: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def load(cls, db: Session) -> "ConstraintParams":
        rows = db.query(ConstraintConfig).all()
        return cls(
            by_id={r.constraint_id: dict(r.params_json or {}) for r in rows},
        )

    def get(
        self,
        constraint_id: str,
        key: str,
        default: float | None = None,
    ) -> float:
        """params_json 에서 숫자 파라미터 조회. Fail-fast 정책."""
        row = self.by_id.get(constraint_id)
        if row is None:
            if default is not None:
                return float(default)
            raise RuntimeError(
                f"ConstraintConfig '{constraint_id}' row not found. "
                "Run seed_db.py to initialize constraint parameters."
            )
        if key not in row:
            if default is not None:
                return float(default)
            raise RuntimeError(
                f"ConstraintConfig '{constraint_id}' params_json key '{key}' missing."
            )
        return float(row[key])


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
