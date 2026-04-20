"""Schedule change set — bulk-update 원본 스냅샷 저장 (Undo 용).

Task 12: change_set 1건 = 한 번의 cascade apply 트랜잭션 결과. snapshot_before /
snapshot_after 를 JSON 으로 보관해 Task 14 revert 엔드포인트가 최신 change_set 을
역적용할 수 있게 한다.

PoC 단계에서는 최신 1건만 Undo 스코프로 삼고, older rows 의 GC 는 수행하지 않음
(조회 부담 없으므로 운영 안정성 목적이 아닌 한 보존).

주의:
- snapshot_* 에는 민감정보(가격 등) 가 없어 JSON 직렬화로 충분.
- datetime 직렬화는 서비스 레이어에서 ISO8601 (naive KST) 문자열로 수행.
"""

from datetime import datetime

from sqlalchemy import Column, DateTime, String
from sqlalchemy.dialects.postgresql import JSONB

from app.infrastructure.database import Base


class ScheduleChangeSet(Base):
    __tablename__ = "schedule_change_sets"

    # uuid 문자열. 서비스 레이어가 uuid.uuid4() 로 생성해 주입.
    change_set_id = Column(String, primary_key=True)
    # index — Task 14 revert 가 created_at DESC 로 최신 1건을 조회.
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    # cascade-preview 응답의 request_id 와 연결 (감사 추적용). None 허용 — 테스트 등에서
    # preview 없이 직접 bulk-update 를 호출한 경우.
    preview_request_id = Column(String, nullable=True)
    # 변경 대상 task 들의 before/after 스냅샷 (task_id → {start, end, equipment_code}).
    # JSONB 선택 이유: PostgreSQL 에서 인덱싱/쿼리 효율 + SQLAlchemy dict 자동 변환.
    snapshot_before = Column(JSONB, nullable=False)
    snapshot_after = Column(JSONB, nullable=False)
    # PoC 에선 인증이 없어 빈 값. 향후 auth 연결 시 user_id 를 기록.
    applied_by = Column(String, nullable=True)
    # change_set 종류 구분용. 가능한 값: "cascade" | "urgent" | "manual".
    # - cascade: bulk-update v2 의 선후공정 cascade apply 결과 (Task 12 기본값)
    # - urgent : 긴급수주 반영 시 before/after 스냅샷
    # - manual : 수동 편집(향후 확장)
    # default="cascade" 는 기존 데이터 호환 (alembic upgrade 시 NOT NULL DEFAULT 'cascade').
    # diff API 에서 종류별 필터링 빈도가 높아 index=True.
    kind = Column(String(20), nullable=False, default="cascade", index=True)
