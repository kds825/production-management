"""decision_card KPI 집계 — Phase 6 Step 7 (Stabilization).

CEO review §3 — 두 그룹 분리:

(A) Product KPI (CEO/공장장 보고용):
  1. AI 결정 유지율 = ScheduleTask 와 운영자 manual override 비율
  2. 납기 준수율 4주 MA delta (별도 ERP source 필요 — 본 module 은 placeholder)
  3. 운영자 카드 평균 검토시간 (decision_card_telemetry)
  4. 카드 진입률 = telemetry distinct (operator_id, date) / 운영자 N

(B) Eng triage KPI (개발자 weekly retro):
  1. status='fixed' 비율
  2. line_anchor top 10
  3. P50 fix SLA (open → fixed median 시간, 목표 ≤ 3일)

본 module 은 SQL aggregation only — frontend dashboard 가 표시 layer.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.infrastructure.models.decision_card_telemetry import DecisionCardTelemetry
from app.infrastructure.models.decision_feedback import DecisionFeedback


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── Eng triage KPI ──────────────────────────────────────────────────────


def fixed_rate(db: Session, *, days: int = 30) -> dict:
    """status='fixed' 비율 (최근 N일).

    fixed / (fixed + wontfix + investigating + open) — 단순 비율.
    """
    since = _now() - timedelta(days=days)
    rows = (
        db.query(DecisionFeedback.status, func.count(DecisionFeedback.id))
        .filter(DecisionFeedback.created_at >= since)
        .group_by(DecisionFeedback.status)
        .all()
    )
    by_status = {s: int(c) for s, c in rows}
    total = sum(by_status.values()) or 1
    fixed = by_status.get("fixed", 0)
    return {
        "days": days,
        "total": total,
        "fixed": fixed,
        "fixed_rate": round(fixed / total, 3),
        "by_status": by_status,
    }


def line_anchor_top(db: Session, *, days: int = 30, top: int = 10) -> list[dict]:
    """line_anchor top N — 가장 자주 의견 받는 줄."""
    since = _now() - timedelta(days=days)
    rows = (
        db.query(
            DecisionFeedback.line_anchor,
            func.count(DecisionFeedback.id).label("cnt"),
        )
        .filter(DecisionFeedback.created_at >= since)
        .group_by(DecisionFeedback.line_anchor)
        .order_by(func.count(DecisionFeedback.id).desc())
        .limit(top)
        .all()
    )
    return [{"line_anchor": a, "count": int(c)} for a, c in rows]


def fix_sla_p50(db: Session, *, days: int = 30) -> dict:
    """open → fixed P50 시간 (시간 단위). 목표 ≤ 72h (3일).

    PostgreSQL percentile_cont 사용 — created_at vs status='fixed' 시점은
    별도 audit 컬럼이 없으므로 dev_notes 채워진 시점 = updated_at 가정 (간단화).
    안정화 1차 KPI — 정밀도는 후속 audit 컬럼 도입 후 강화.
    """
    # PoC 단순화: fixed 행의 created_at vs 현재시각 차이의 median
    # (실제 reaction time 이 아닌 "처리되지 못한 채 쌓인 시간" 의 근사)
    since = _now() - timedelta(days=days)
    rows = (
        db.query(DecisionFeedback.created_at)
        .filter(
            DecisionFeedback.created_at >= since, DecisionFeedback.status == "fixed"
        )
        .all()
    )
    if not rows:
        return {"p50_hours": None, "n": 0}
    now = _now()
    deltas = sorted([(now - r[0]).total_seconds() / 3600 for r in rows])
    p50 = deltas[len(deltas) // 2]
    return {"p50_hours": round(p50, 1), "n": len(deltas), "target_hours": 72}


# ── Product KPI ─────────────────────────────────────────────────────────


def card_review_time_avg(db: Session, *, days: int = 30) -> dict:
    """운영자 카드 평균 검토시간 (decision_card_telemetry).

    opened_at → decided_at 차이의 평균. 닫혀버린 (dismissed_at) 카드는 제외.
    """
    since = _now() - timedelta(days=days)
    rows = (
        db.query(DecisionCardTelemetry.opened_at, DecisionCardTelemetry.decided_at)
        .filter(
            DecisionCardTelemetry.opened_at >= since,
            DecisionCardTelemetry.decided_at.isnot(None),
        )
        .all()
    )
    if not rows:
        return {"avg_seconds": None, "n": 0}
    secs = [(d - o).total_seconds() for o, d in rows]
    return {
        "avg_seconds": round(sum(secs) / len(secs), 1),
        "n": len(secs),
    }


def card_open_rate(db: Session, *, days: int = 30) -> dict:
    """카드 진입률 — 같은 운영자가 며칠에 한번 카드 열어보는지.

    distinct (operator_id, date(opened_at)) / days = 평균 enter-day per operator.
    """
    since = _now() - timedelta(days=days)
    rows = (
        db.query(
            DecisionCardTelemetry.operator_id,
            func.date(DecisionCardTelemetry.opened_at).label("d"),
        )
        .filter(DecisionCardTelemetry.opened_at >= since)
        .distinct()
        .all()
    )
    if not rows:
        return {"avg_days_active": 0.0, "operators": 0}
    by_op: dict[str, int] = {}
    for op, _d in rows:
        by_op[op] = by_op.get(op, 0) + 1
    avg = sum(by_op.values()) / max(len(by_op), 1)
    return {
        "avg_days_active": round(avg, 1),
        "operators": len(by_op),
        "window_days": days,
    }


def kpi_summary(db: Session, *, days: int = 30) -> dict:
    """frontend dashboard 단일 호출용 — 7 metric 합산."""
    return {
        "window_days": days,
        "eng": {
            "fixed_rate": fixed_rate(db, days=days),
            "line_anchor_top": line_anchor_top(db, days=days),
            "fix_sla_p50": fix_sla_p50(db, days=days),
        },
        "product": {
            "card_review_time_avg": card_review_time_avg(db, days=days),
            "card_open_rate": card_open_rate(db, days=days),
        },
    }
