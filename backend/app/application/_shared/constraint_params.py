"""ConstraintConfig 파라미터 프리페치 캐시.

Why: schedule_optimizer / batch_grouping 이 루프 내부에서 ConstraintConfig 를
조회하면 N+1 쿼리가 발생한다. 한 요청(create_batches 또는 auto_schedule)
진입 시 1회 프리페치하여 dict 스냅샷으로 전달한다.

전역 lru_cache 사용 금지 — PATCH 후 stale 위험.

분기 룰 (resolve_spec_setup_min / resolve_color_change_min) 은
`app.domain.constraint_rules` 로 분리되었다 (Phase 1 step 3 / target.md §4 P4).
본 모듈은 적재 + 데이터 클래스만 보유한다.
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
