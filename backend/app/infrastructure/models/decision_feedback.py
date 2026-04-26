"""decision_feedback 테이블 — 운영자가 결정 카드에 남긴 의견.

Phase 6 (decision_card) 신설. 운영자(공장 생산관리담당자)가 [⚠️ 이상한
것 같아요] 클릭 시 dialog 로 제출한 의견을 저장한다. 개발자가 admin 큐
(`/admin/decision-feedback`) 에서 처리하며, status 가 `fixed` / `wontfix`
로 변경되면 운영자에게 알림 (§7.4 bell icon polling).

핵심 컬럼:
- `payload_snapshot JSONB NOT NULL` — DecisionCard 응답 전체 스냅샷.
  6개월 retention. batch 가 사라져도 admin 큐에서 운영자가 본 그대로
  재생할 수 있게 한다 (2nd opinion review blocker).
- `attachment_path` — 안정화 trial 동안 로컬 디스크
  `/var/kbi/feedback_attachments/{id}.{ext}`. S3 마이그레이션은 후속 spec.
- `line_anchor` — DecisionLine.anchor (`why_line_3` 등) 와 매칭 키.
  `constraint_id_hint` 와 함께 admin 큐 clustering 의 1차 grouping 키.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB

from app.infrastructure.database import Base


class DecisionFeedback(Base):
    __tablename__ = "decision_feedback"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # 컨텍스트 — 어느 run / 어느 batch / 어느 task 에 대한 의견인지
    run_label = Column(String(50), nullable=False)
    batch_id = Column(Integer, ForeignKey("production_batch.batch_id"), nullable=False)
    task_id = Column(Integer, ForeignKey("schedule_task.task_id"), nullable=True)

    # 위치 — 카드 어느 섹션 / 어느 자연어 줄에 대한 의견
    section = Column(
        String(20), nullable=False
    )  # 'why' | 'impact' | 'handoff' | 'equipment_day' | 'bundle' | 'alternatives' | 'general'
    line_anchor = Column(String(50), nullable=False)  # 'why_line_3' 등
    constraint_id_hint = Column(
        String(10), nullable=True
    )  # '#4-2' — phrasing 이 line_anchor 와 함께 회신한 추정 ID

    # 본문 + 첨부
    free_text = Column(Text, nullable=False)
    attachment_path = Column(
        String(255), nullable=True
    )  # /var/kbi/feedback_attachments/{id}.{ext} (안정화 trial 로컬), S3 마이그는 후속 spec

    # 작성자 + 처리 상태
    operator_id = Column(String(50), nullable=False)
    status = Column(
        String(20), nullable=False, default="open"
    )  # 'open' | 'investigating' | 'fixed' | 'wontfix'
    dev_notes = Column(Text, nullable=True)
    linked_pr_url = Column(String(255), nullable=True)

    # DecisionCard 전체 스냅샷 — batch 삭제 후에도 admin 큐 재생 가능 (6개월 retention)
    payload_snapshot = Column(JSONB, nullable=False)
