"""decision_card_telemetry 테이블 — 결정 카드 진입/검토 시간 KPI source.

Phase 6 (decision_card) 신설. 운영자가 카드를 펼치는 시점(opened_at), 결정
[✅ 맞습니다] 또는 [⚠️ 이상해요] 누른 시점(decided_at), 닫기 시점
(dismissed_at) 을 기록한다. CEO §3 KPI 4종 중 (3) "운영자 카드 평균 검토
시간" + (4) "카드 진입률 (DAU/배치수)" 의 데이터 출처.

`/admin/kpi-dashboard` (Step 7) 가 본 테이블을 집계해 weekly retro 자료를
생성. 운영자별 telemetry 라 PII 취급 — 안정화 후 1년 retention 후 폐기.
"""

from sqlalchemy import BigInteger, Column, DateTime, Integer, String

from app.infrastructure.database import Base


class DecisionCardTelemetry(Base):
    __tablename__ = "decision_card_telemetry"

    telemetry_id = Column(BigInteger, primary_key=True, autoincrement=True)

    run_label = Column(String(50), nullable=False)
    batch_id = Column(Integer, nullable=False)  # FK 미설정 — batch 삭제 무관 KPI 보존
    operator_id = Column(String(50), nullable=False)

    opened_at = Column(DateTime(timezone=True), nullable=False)
    decided_at = Column(
        DateTime(timezone=True), nullable=True
    )  # [✅맞습니다] 또는 [⚠️이상해요] 누른 시점
    dismissed_at = Column(
        DateTime(timezone=True), nullable=True
    )  # 닫기 시점 (decided_at 없이 닫힌 경우)
