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


# ── Bulk API 가드 ──────────────────────────────────────────────────────────────
# Why: Session.bulk_save_objects / bulk_insert_mappings 는 ORM after_insert 이벤트를
# bypass 하므로 wip_lifecycle_listener 가 발화하지 않아 WIP 자동생성이 누락된다.
# ProductionBatch 대상 bulk 경로를 전역 monkey-patch 로 차단하여
# 개발자가 실수로 bulk API 를 사용해 listener 를 우회하는 것을 방지한다.
# (전역 변경이지만 ProductionBatch 외 모델에는 원본을 그대로 통과시킨다.)

_original_bulk_save = Session.bulk_save_objects
_original_bulk_insert = Session.bulk_insert_mappings


def _guarded_bulk_save(self, objects, *args, **kwargs):
    from app.infrastructure.models.production_batch import ProductionBatch  # noqa: PLC0415

    for o in objects:
        if isinstance(o, ProductionBatch):
            raise RuntimeError(
                "bulk_save_objects 는 ProductionBatch 에서 금지 "
                "(after_insert listener bypass). 일반 session.add() 사용."
            )
    return _original_bulk_save(self, objects, *args, **kwargs)


def _guarded_bulk_insert(self, mapper, mappings, *args, **kwargs):
    from app.infrastructure.models.production_batch import ProductionBatch  # noqa: PLC0415

    # mapper 는 클래스 또는 Mapper 객체일 수 있음
    mapper_cls = mapper.class_ if hasattr(mapper, "class_") else mapper
    if mapper_cls is ProductionBatch:
        raise RuntimeError(
            "bulk_insert_mappings 는 ProductionBatch 에서 금지 "
            "(after_insert listener bypass). 일반 session.add() 사용."
        )
    return _original_bulk_insert(self, mapper, mappings, *args, **kwargs)


Session.bulk_save_objects = _guarded_bulk_save  # type: ignore[method-assign]
Session.bulk_insert_mappings = _guarded_bulk_insert  # type: ignore[method-assign]


# ── WIP listener 등록 (앱 시작 시 1회) ────────────────────────────────────────
# Why: database.py 는 앱 기동 시 가장 먼저 import 되는 인프라 모듈이므로,
# 여기서 등록하면 FastAPI app 생성 전에 listener 가 확실히 활성화된다.
# register_wip_listener() 는 내부적으로 _registered flag 로 중복 등록을 방지한다.
from app.services.wip_lifecycle_listener import register_wip_listener  # noqa: E402

register_wip_listener()
