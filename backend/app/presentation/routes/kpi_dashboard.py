"""KPI dashboard endpoint — Phase 6 Step 7 (Stabilization).

`GET /api/admin/kpi/decision-card?days=30` — Product + Eng triage KPI 합산.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.application.decisions.kpi_metrics import kpi_summary
from app.infrastructure.database import get_db


router = APIRouter(prefix="/admin/kpi", tags=["kpi"])


@router.get("/decision-card")
def get_decision_card_kpi(
    days: int = 30,
    db: Session = Depends(get_db),
) -> dict:
    """7 metric 합산 — frontend dashboard 단일 호출."""
    return kpi_summary(db, days=days)
