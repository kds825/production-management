"""pytest 공통 픽스처.

`db` 픽스처: 실제 Supabase DB 세션을 트랜잭션 경계로 감싸 테스트 후 롤백.
- 이유: 테스트 데이터를 커밋 남기지 않고 정리하기 위함. auto_schedule 등은
  내부적으로 db.flush()/db.commit()을 하지 않고 호출자(라우트)가 commit하므로
  rollback만으로 충분히 세션 내 변경을 제거할 수 있다.
"""

from collections.abc import Generator

import pytest
from sqlalchemy.orm import Session

from app.infrastructure.database import SessionLocal


@pytest.fixture
def db() -> Generator[Session, None, None]:
    """테스트용 DB 세션. 종료 시 전체 rollback 으로 격리 보장."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def calendar_ctx() -> dict:
    """calendar_engine.reverse_advance / advance 테스트용 context.

    본 프로젝트 calendar_engine 은 `equipment_code` + optional `db` 를
    받아 공정별 작업창을 결정한다 (task 명세의 추상 ctx 를 실제 모델에
    매핑). EX-B100 (저압절연) 은 24h/12h 창 + 휴식 없음 → 역산 검증에
    가장 깔끔한 baseline.
    """
    return {"equipment_code": "EX-B100", "db": None}
