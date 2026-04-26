"""decision_card 운영자 카드 endpoint — Phase 6 Step 3c-1.

`GET /api/scheduler/{run_label}/decision-card/{batch_id}?debug=...`

핵심 (2nd opinion blocker — role-based debug omit):
1. `debug=1` 쿼리 + admin role 헤더 (`X-User-Role: admin`) 둘 다 만족하면
   응답에 `debug` 필드 채움. 그 외에는 응답에서 `None` (omit).
2. 운영자 role 은 `?debug=1` 을 명시해도 `response.debug == None`. 백엔드
   1차 게이트 — frontend 분기 렌더링에 의존하지 않는다.
3. role 결정은 PoC 단계라 헤더 기반. 정식 JWT 는 Step 5 / 별도 spec.

기존 `/api/decisions/{batch_id}/latest` (audit-style) 는 보존 — 본 라우트
는 별도 신설.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.application.decisions.build_card import build_decision_card
from app.infrastructure.database import get_db
from app.infrastructure.models.production_batch import ProductionBatch
from app.presentation.schemas.decision_card import DecisionCard


router = APIRouter(prefix="/scheduler", tags=["decision_card"])

# 운영자 role 은 debug 응답에서 omit. PoC 단계라 헤더 기반.
# Step 5 / 별도 spec 에서 JWT 로 교체될 때 본 함수만 갈아끼우면 라우트 영향 0.
_ADMIN_ROLES = frozenset({"admin", "developer", "dev"})


def _resolve_role(x_user_role: str | None) -> Literal["operator", "admin"]:
    """X-User-Role 헤더 → 'operator' | 'admin'.

    헤더 미지정 시 default 'admin' (PoC — 인증 미설정). 운영 배포 시 nginx
    또는 frontend 가 헤더 항상 채우는 방식.
    """
    if not x_user_role:
        return "admin"
    return "admin" if x_user_role.lower() in _ADMIN_ROLES else "operator"


@router.get("/{run_label}/decision-card/{batch_id}", response_model=DecisionCard)
def get_decision_card(
    run_label: str,
    batch_id: int,
    debug: int = 0,
    x_user_role: str | None = Header(default=None, alias="X-User-Role"),
    db: Session = Depends(get_db),
) -> DecisionCard:
    """운영자 결정 카드 페이로드 합성.

    Args:
      run_label: 스케줄 run 라벨. URL path 의 batch_id 와 일관성 검증용.
      batch_id: 카드 주체 batch.
      debug: 1 이면 DebugBlock 채움. admin role 만 효력.
      x_user_role: 'admin' / 'operator' / 기타. 운영자는 debug 자동 omit.
    """
    # 일관성 검증 — batch.run_label != path run_label 이면 404 (URL 변조 방지)
    batch = (
        db.query(ProductionBatch)
        .filter(ProductionBatch.batch_id == batch_id)
        .one_or_none()
    )
    if batch is None:
        raise HTTPException(
            status_code=404,
            detail=f"배치 '{batch_id}' 를 찾을 수 없습니다.",
        )
    if batch.run_label and batch.run_label != run_label:
        raise HTTPException(
            status_code=404,
            detail=(
                f"배치 '{batch_id}' 는 run_label='{batch.run_label}' 소속 — "
                f"'{run_label}' 과 불일치"
            ),
        )

    role = _resolve_role(x_user_role)
    debug_requested = bool(debug) and role == "admin"

    card = build_decision_card(batch_id=batch_id, db=db, debug=debug_requested)

    # 백엔드 1차 게이트 — operator role 은 debug 강제 None.
    # build_decision_card 가 잘못 채웠어도 본 라우트에서 한 번 더 필터링.
    if role == "operator":
        card.debug = None

    return card
