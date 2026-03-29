"""SQLAlchemy 엔진 + 세션 팩토리 — FastAPI 의존성 주입용"""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    """FastAPI Depends()용 DB 세션 제너레이터"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
