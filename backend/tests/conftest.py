"""pytest 공통 픽스처.

`db` 픽스처: 실제 Supabase DB 위에서 명시적 outer transaction + savepoint 로
세션을 감싸 테스트 후 항상 rollback. 이전 버전은 `SessionLocal()` 만 만들고
`rollback()` 했으나, route 가 `db.commit()` 을 호출하면 그 시점에 outer
transaction 이 그대로 커밋되어 row 가 prod 에 영구 누수되었다 (2026-05-21
조사: schedule_change_sets 에 431건 누적). SQLAlchemy 2.0의
`join_transaction_mode="create_savepoint"` 를 쓰면 endpoint 의 commit() 이
savepoint commit 으로 강등되어 픽스처의 outer rollback 으로 모든 변경이
되돌려진다.
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

# Why: parity tests must never hit a real LLM (cost + nondeterminism). New Decision
# Card narrator (decision_narrator.py + llm_providers/) reads LLM_PROVIDER at call
# time, so set early. Force-overwrite (not setdefault) — 이전 setdefault 는 dev shell
# 의 LLM_PROVIDER=anthropic 을 보존해 parity 가 무음으로 실 LLM 을 호출할 위험이
# 있었다. 로컬에서 anthropic 호출이 필요하면 conftest 외부 e2e 스크립트로 분리.
os.environ["LLM_PROVIDER"] = "template"

from app.infrastructure.database import SessionLocal, engine, get_db  # noqa: E402
from app.main import app  # noqa: E402


def _sweep_test_rows() -> None:
    """`test-%` run_label 로 남은 모든 row 를 일괄 삭제.

    왜 필요한가:
      ``db`` 픽스처는 savepoint + outer rollback 으로 누수를 차단하지만,
      (1) ``SessionLocal()`` 을 직접 만드는 일부 테스트, (2) 새 픽스처
      추가 시 savepoint 패턴을 안 따른 경우, (3) outer transaction 이
      예기치 못한 경로로 commit 된 경우 등 leak 경로가 남아 있다. 실제로
      2026-05-21 조사에서 81 schedule_task / 344 solver_run 이 dev DB
      (Supabase) 에 누적돼 ERP 업로드 직후 stage2 전에 dummy 블록이
      그려졌다. 본 함수는 그 마지막 그물망이다.

    FK 의존성 (자식 → 부모) 순으로 삭제. ``solver_decision`` 은 run_label
    컬럼이 없어 run_id 서브쿼리로 매핑한다.
    """
    from sqlalchemy import text

    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM audit_log         WHERE run_label LIKE 'test-%'")
        )
        conn.execute(
            text("DELETE FROM decision_feedback WHERE run_label LIKE 'test-%'")
        )
        conn.execute(
            text(
                "DELETE FROM solver_decision "
                "WHERE run_id IN (SELECT run_id FROM solver_run WHERE run_label LIKE 'test-%')"
            )
        )
        conn.execute(
            text("DELETE FROM schedule_task     WHERE run_label LIKE 'test-%'")
        )
        conn.execute(
            text("DELETE FROM solver_run        WHERE run_label LIKE 'test-%'")
        )
        conn.execute(
            text("DELETE FROM production_batch  WHERE run_label LIKE 'test-%'")
        )


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """전체 pytest 세션 종료 시 ``test-%`` 잔재 청소.

    실패해도 테스트 결과에 영향을 주지 않도록 예외를 swallow 한다 (DB 미접속
    환경에서도 수트가 굴러가야 하므로). 청소 자체가 실패한 사실은 stderr 에
    출력된다.
    """
    try:
        _sweep_test_rows()
    except Exception as exc:  # noqa: BLE001
        print(f"[conftest] test-% sweep skipped: {exc}", flush=True)


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
    """테스트용 DB 세션 — explicit outer transaction + savepoint join.

    왜 이 패턴:
      route 함수가 `db.commit()` 을 호출하면 이전 구현에서는 outer transaction
      이 통째로 prod 에 커밋되어 row 가 누수됐다. `connection.begin()` 으로
      outer transaction 을 명시 시작하고, Session 을
      `join_transaction_mode="create_savepoint"` 로 묶으면 route 의 commit()
      이 SAVEPOINT commit 으로 강등된다. finally 의 `transaction.rollback()`
      이 outer 와 그 안의 모든 savepoint 를 한꺼번에 되돌리므로 prod 에는
      어떤 row 도 남지 않는다.
    """
    connection = engine.connect()
    transaction = connection.begin()
    session = SessionLocal(bind=connection, join_transaction_mode="create_savepoint")

    def _override_get_db() -> Generator[Session, None, None]:
        # FastAPI 의 `Depends(get_db)` 가 픽스처와 동일 세션을 받아야
        # client.get(...) 으로 호출한 route 가 픽스처의 INSERT 를 볼 수
        # 있다 (다른 connection 으로 가면 outer transaction 이 미커밋
        # 이라 row 가 안 보임). close 는 픽스처 finally 에서 처리.
        yield session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        yield session
    finally:
        app.dependency_overrides.pop(get_db, None)
        session.close()
        if transaction.is_active:
            transaction.rollback()
        connection.close()


@pytest.fixture
def calendar_ctx() -> dict:
    """calendar_engine.reverse_advance / advance 테스트용 context.

    본 프로젝트 calendar_engine 은 `equipment_code` + optional `db` 를
    받아 공정별 작업창을 결정한다 (task 명세의 추상 ctx 를 실제 모델에
    매핑). EX-B100 (저압절연) 은 24h/12h 창 + 휴식 없음 → 역산 검증에
    가장 깔끔한 baseline.
    """
    return {"equipment_code": "EX-B100", "db": None}
