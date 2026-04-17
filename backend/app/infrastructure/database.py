"""SQLAlchemy 엔진 + 세션 팩토리 — FastAPI 의존성 주입용"""

from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

# Why:
# Supabase role-level `statement_timeout=2min` 이 default — Stage2 auto_schedule 같은
# 장시간 트랜잭션이 2분에 QueryCanceled 됨. Session Pooler(5432) 전제 하에서
# connect 시점에 startup option 과 SET 으로 세션 timeout 을 10분까지 확장한다.
# `run_stage2_direct.py` 의 엔진 구성과 일관성 유지 (keepalives 포함).
engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=1800,  # 30min — Supabase idle timeout 대비
    connect_args={
        "options": "-c statement_timeout=600000",  # 10min (ms)
        "keepalives": 1,
        "keepalives_idle": 10,
        "keepalives_interval": 5,
        "keepalives_count": 3,
    },
)


@event.listens_for(engine, "connect")
def _set_session_timeout_on_connect(dbapi_conn, conn_record):
    """connect 시점에 1회 SET."""
    cur = dbapi_conn.cursor()
    cur.execute("SET statement_timeout = '10min'")
    cur.close()


@event.listens_for(engine, "checkout")
def _set_session_timeout_on_checkout(dbapi_conn, conn_record, conn_proxy):
    """Why:
    Supabase Session Pooler 가 pool return 시 server-side session state 를
    reset 하는 케이스가 관측됨 (FRESH connect 은 10min, 풀 재사용은 2min).
    매 checkout 마다 SET 을 재적용해 2분 role-default 로 되돌아가는 것을 방지.
    """
    cur = dbapi_conn.cursor()
    cur.execute("SET statement_timeout = '10min'")
    cur.close()


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
