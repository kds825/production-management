"""pytest 공통 픽스처.

`db` 픽스처: 실제 Supabase DB 세션을 트랜잭션 경계로 감싸 테스트 후 롤백.
- 이유: 테스트 데이터를 커밋 남기지 않고 정리하기 위함. auto_schedule 등은
  내부적으로 db.flush()/db.commit()을 하지 않고 호출자(라우트)가 commit하므로
  rollback만으로 충분히 세션 내 변경을 제거할 수 있다.
"""

import os
from collections.abc import Generator

import pytest
from sqlalchemy.orm import Session

# CP-SAT 워커 수를 1로 강제 — 테스트 결정론 보장.
# 왜 모듈 최상단: cp_sat_optimizer 가 import 시점에 env 를 한번 읽지 않고, solve()
# 호출 시마다 `_resolve_num_workers()` 로 재조회하므로 프로세스 전체에서 유효.
# autouse fixture 로 해도 되지만, session scope 이전에 import 된 모듈 상수에
# 영향을 주지 않도록 import 이전 단계에 세팅. 기존 값이 있으면 보존하지 않음
# (테스트 의도: 결정론 > 사용자 편의).
os.environ["CPSAT_WORKERS"] = "1"

from app.infrastructure.database import SessionLocal  # noqa: E402


def pytest_addoption(parser: pytest.Parser) -> None:
    """--parity-quick: run parity harness on 3-scenario subset (~3 min).

    Selection (01/05/08) per design spec §Task 1.4 — one nominal, one
    complex (sheath color chain), one edge (capacity overflow). Gives
    coverage of the three archetypes that have historically caught the
    most regressions while keeping pre-push turnaround under 3 minutes.
    """
    parser.addoption(
        "--parity-quick",
        action="store_true",
        default=False,
        help=(
            "Run parity harness on 3-scenario subset "
            "(01_nominal, 05_sheath_color_chain, 08_capacity_overflow)."
        ),
    )


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
