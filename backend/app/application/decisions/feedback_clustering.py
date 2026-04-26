"""decision_feedback admin 큐 clustering — Phase 6 Step 6-admin.

CEO review §8: 같은 (process_name, line_anchor) 의견 cluster + impact_score
정렬. admin 이 "동일 line 에 김선임/박과장/이대리 모두 의견 보냈다" 를 즉시
파악 → 단일 fix 가 운영자 N명 만족.

impact_score:
  frequency × distinct_operators × log(cards_affected + 1)
  - frequency: 본 (line_anchor) 의견 총 건수
  - distinct_operators: 본 의견 보낸 운영자 수 (중복 제거)
  - cards_affected: 본 line_anchor 가 등장하는 distinct (run_label, batch_id) 수
"""

from __future__ import annotations

import math
from typing import Optional

from sqlalchemy import distinct, func
from sqlalchemy.orm import Session

from app.infrastructure.models.decision_feedback import DecisionFeedback
from app.infrastructure.models.production_batch import ProductionBatch


def list_clusters(
    db: Session,
    *,
    status_filter: Optional[str] = "open",
    limit: int = 50,
) -> list[dict]:
    """(process_name, line_anchor) 기준 group + impact_score 정렬.

    Returns:
      [
        {
          "process_name": "저압시스",
          "line_anchor": "why_line_3",
          "constraint_id_hint": "4-2",
          "frequency": 5,
          "distinct_operators": 3,
          "cards_affected": 2,
          "impact_score": 16.47,
          "sample_free_text": "이 색상교체 추정이 ...",
          "feedback_ids": [12, 17, 23, 34, 41],
        }, ...
      ]
    """
    q = (
        db.query(
            ProductionBatch.process_name.label("process_name"),
            DecisionFeedback.line_anchor,
            DecisionFeedback.constraint_id_hint,
            func.count(DecisionFeedback.id).label("frequency"),
            func.count(distinct(DecisionFeedback.operator_id)).label(
                "distinct_operators"
            ),
            func.count(
                distinct(
                    func.concat(
                        DecisionFeedback.run_label,
                        ":",
                        DecisionFeedback.batch_id,
                    )
                )
            ).label("cards_affected"),
            func.array_agg(DecisionFeedback.id).label("feedback_ids"),
            func.min(DecisionFeedback.free_text).label("sample_free_text"),
        )
        .join(
            ProductionBatch,
            DecisionFeedback.batch_id == ProductionBatch.batch_id,
        )
        .group_by(
            ProductionBatch.process_name,
            DecisionFeedback.line_anchor,
            DecisionFeedback.constraint_id_hint,
        )
    )
    if status_filter:
        q = q.filter(DecisionFeedback.status == status_filter)
    rows = q.all()

    out: list[dict] = []
    for r in rows:
        freq = int(r.frequency or 0)
        distinct_ops = int(r.distinct_operators or 0)
        cards = int(r.cards_affected or 0)
        impact = freq * distinct_ops * math.log(cards + 1)
        ids = list(r.feedback_ids or [])
        out.append(
            {
                "process_name": r.process_name or "",
                "line_anchor": r.line_anchor,
                "constraint_id_hint": r.constraint_id_hint,
                "frequency": freq,
                "distinct_operators": distinct_ops,
                "cards_affected": cards,
                "impact_score": round(impact, 2),
                "sample_free_text": (r.sample_free_text or "")[:200],
                "feedback_ids": ids,
            }
        )
    out.sort(key=lambda d: d["impact_score"], reverse=True)
    return out[:limit]
