from sqlalchemy import Column, String, Integer, DateTime
from datetime import datetime
from app.infrastructure.database import Base


class WipUploadLog(Base):
    __tablename__ = "wip_upload_log"

    upload_id = Column(Integer, primary_key=True, autoincrement=True)
    canonical_hash = Column(
        String(64), unique=True, nullable=False
    )  # 파싱된 row 정렬 튜플의 SHA-256
    raw_file_hash = Column(String(64), nullable=True)  # 원본 바이트 SHA-256 (디버깅용)
    run_label = Column(String(50), nullable=True)  # 업로드가 속한 run
    uploaded_at = Column(
        DateTime, default=datetime.utcnow, nullable=False
    )  # 업로드 시각 (UTC)
    rows_inserted = Column(
        Integer, default=0, nullable=False
    )  # orphan 신규 INSERT 건수
    rows_updated = Column(
        Integer, default=0, nullable=False
    )  # reconciliation UPDATE 건수
    file_name = Column(String(255), nullable=True)  # 원본 파일명 (참고용)
