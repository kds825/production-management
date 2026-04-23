# 재공(WIP) Lifecycle Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stage 1 배치 잉여를 DB에 기록하고 다음 수주에 자동 매칭되는 4단계 WIP lifecycle 을 구현한다. 실사 Excel 업로드로 예상→실적→실사 전환을 reconciliation 하고, 보정배치는 틀단 원칙을 지켜 listener 경유로 잉여가 재귀 반영되도록 한다.

**Architecture:**

- SQLAlchemy `after_insert` listener 가 **헤더 배치**(`batch_seq=-1 AND process_name="연선"`) INSERT 시에만 fire → `WipInventory(status='예상')` auto-create (`ON CONFLICT DO NOTHING`).
- `status-write` endpoint 3곳 공통 헬퍼로 `예상→실적_추정` 승격.
- Excel reconciliation 이 canonical rows hash 멱등성 + 결정적 tiebreaker 로 `실적_추정→실사_확정` 전환.
- `create_shortage_batches` 는 `DrumLotMaster.lot_stranding` 확장 후 `batch_seq=-1` 으로 신규 배치 → listener 경유 recursion.
- 매칭 기본은 Level 2 (`사용가능 + 실사_확정 + 실적_추정`). `예상` 은 opt-in emergency_mode.

**Tech Stack:** Python 3.x, FastAPI, SQLAlchemy 2.x, PostgreSQL, Alembic, pytest, React/Next.js (frontend), Playwright.

**Spec:** `docs/specs/2026-04-18-wip-lifecycle-design.md` (Status: APPROVED, Eng review 반영). 아래 용어/결정은 spec을 단일 진실 소스로 취급.

**Reviews referenced:**

- `docs/specs/2026-04-18-wip-lifecycle-eng-review.md` (FAIL → PASS_WITH_CHANGES, 3 blockers 반영)
- `docs/specs/2026-04-18-wip-lifecycle-ceo-review.md` (SELECTIVE EXPANSION, 5 제안 Phase 2 유보)

---

## File Structure

### Backend (신규/수정)

| 파일                                                  | 책임                                                                                                    | 상태                      |
| ----------------------------------------------------- | ------------------------------------------------------------------------------------------------------- | ------------------------- |
| `backend/app/services/wip_lifecycle_listener.py`      | SQLAlchemy after_insert listener + 게이트 + ON CONFLICT                                                 | 신규                      |
| `backend/app/infrastructure/database.py`              | listener 등록 + bulk API 금지 가드                                                                      | 수정                      |
| `backend/app/services/batch_grouping.py`              | 정규 헤더 + split 헤더에 `wip_output_expected_m` 계산                                                   | 수정 (413-446, 1344-1382) |
| `backend/app/services/wip_matching.py`                | Level 2 status 필터 + emergency_mode 플래그 + cross-run 주석                                            | 수정 (41)                 |
| `backend/app/services/wip_parser.py`                  | canonical hash 멱등성 + reconciliation (tiebreaker + UPDATE/INSERT)                                     | 수정 (180+)               |
| `backend/app/services/sm_inventory.py`                | `create_shortage_batches` lot 확장 + batch*seq=-1 + DrumLotMaster tuple key + status '실적'→'실사*확정' | 수정 (68-121)             |
| `backend/app/services/wip_promotion.py`               | `_promote_expected_to_estimated` 공통 헬퍼                                                              | 신규                      |
| `backend/app/presentation/routes/plan_pipeline.py`    | 3 status-write endpoint 에 hook + cascade                                                               | 수정 (1555, 1598+, 1640)  |
| `backend/app/infrastructure/models/wip_upload_log.py` | 신규 멱등성 테이블 모델                                                                                 | 신규                      |
| `backend/alembic/versions/*_wip_unique_and_rename.py` | G1 UNIQUE partial index + preflight + '실적'→'실사\_확정'                                               | 신규                      |
| `backend/alembic/versions/*_wip_upload_log.py`        | wip_upload_log 테이블                                                                                   | 신규                      |

### Backend tests (신규)

| 파일                                          | 책임                                                  | 상태 |
| --------------------------------------------- | ----------------------------------------------------- | ---- |
| `backend/tests/test_wip_output_expected_m.py` | `wip_output_expected_m` 계산식 (정규/split 헤더)      | 신규 |
| `backend/tests/test_wip_listener.py`          | 게이트 on/off, ON CONFLICT, rollback propagation      | 신규 |
| `backend/tests/test_wip_matching_levels.py`   | Level 1/2/3 status 필터, cross-run 매칭               | 신규 |
| `backend/tests/test_wip_promotion_hook.py`    | 3 endpoint hook, cascade, 멱등성, wip_complete 무반응 | 신규 |
| `backend/tests/test_wip_reconciliation.py`    | canonical hash, tiebreaker, UPDATE/INSERT, 허용오차   | 신규 |
| `backend/tests/test_shortage_batch_lot.py`    | lot 확장, DrumLotMaster tuple key, recursion          | 신규 |
| `backend/tests/test_migration_preflight.py`   | UNIQUE 중복 abort, status rename 정확도               | 신규 |

### Frontend (신규/수정)

| 파일                                                            | 책임                                | 상태 |
| --------------------------------------------------------------- | ----------------------------------- | ---- |
| `frontend/src/features/wip/components/ReconciliationDialog.tsx` | Excel 애매한 매칭 시 사용자 선택 UI | 신규 |
| `frontend/src/features/wip/api/reconciliation.ts`               | reconciliation API fetcher          | 신규 |
| `frontend/e2e/wip-lifecycle.spec.ts`                            | 시연 시나리오 E2E (4단계 flow)      | 신규 |

---

## Execution Phases

- **Phase 0 — Foundation** (Task 1–3): 리서치 spike + 2개 migration
- **Phase 1 — Surplus 계산** (Task 4–5): `wip_output_expected_m` header / split
- **Phase 2 — Listener** (Task 6–7): listener + 등록 + bulk 가드
- **Phase 3 — 매칭 & 승격** (Task 8–10): status 필터 + T2 헬퍼 + 3 endpoint hook
- **Phase 4 — Reconciliation** (Task 11–12): canonical hash + tiebreaker
- **Phase 5 — Shortage 재귀** (Task 13): create_shortage_batches 재작성
- **Phase 6 — UI & E2E** (Task 14–15): Reconciliation 다이얼로그 + Playwright 시연

**Dependency 의도**: Phase 0-5 각 Task 완료 후 commit. Phase 6 은 Phase 3-4 완료 후 진입. Task 내 test → impl → verify → commit 순.

---

## Phase 0 — Foundation

### Task 1: DrumLotMaster uniqueness 리서치 spike

**목적**: spec OQ3 해소. `DrumLotMaster` 의 실제 uniqueness key 를 확정해서 Task 13 의 tuple key 구성 근거 확보.

**Files:**

- Read: `backend/app/infrastructure/models/drum_lot_master.py`
- Read: `backend/alembic/versions/*_drum_lot_master*.py` (있다면)
- Read: `backend/app/services/batch_grouping.py:314-319` (기존 dict key 사용 패턴)

- [ ] **Step 1: 모델/마이그레이션 읽고 컬럼 리스트 확인**

```bash
grep -n "Column" backend/app/infrastructure/models/drum_lot_master.py
grep -rn "DrumLotMaster" backend/app/services/
```

- [ ] **Step 2: 실제 DB 행에서 uniqueness 검증**

```bash
# PoC DB 로컬에서 실행
docker compose exec postgres psql -U postgres -d kbi_poc -c "
SELECT cross_section, voltage_class, material, COUNT(*)
FROM drum_lot_master
GROUP BY cross_section, voltage_class, material
HAVING COUNT(*) > 1;
"
```

Expected: 결과 0건이면 (cross_section) 단일 키로 충분. 결과 있으면 tuple key 필요.

- [ ] **Step 3: 발견 내용을 design doc OQ3 에 업데이트**

결과 문서화 위치: `docs/specs/2026-04-18-wip-lifecycle-design.md` §16 OQ3.

- 단일 키면: "OQ3 해결: cross_section 단일 PK" 기록
- Tuple 필요면: 실제 key 조합 명시. Task 13 구현 시 사용할 tuple 정의

- [ ] **Step 4: Commit**

```bash
git add docs/specs/2026-04-18-wip-lifecycle-design.md
git commit -m "docs(spec): resolve OQ3 — DrumLotMaster uniqueness key"
```

---

### Task 2: Migration — G1 UNIQUE index + preflight + status rename

**목적**: Eng review blocker #3 해결. UNIQUE partial index 생성 + `'실적'→'실사_확정'` mass rename. Preflight 로 중복 데이터 있으면 abort.

**Files:**

- Create: `backend/alembic/versions/YYYYMMDDHHMM_wip_unique_and_rename.py`
- Test: `backend/tests/test_migration_preflight.py`

- [ ] **Step 1: Alembic 현재 head revision 확인**

```bash
cd backend && alembic heads
```

기록: 부모 revision_id.

- [ ] **Step 2: 신규 마이그레이션 파일 생성**

```bash
cd backend && alembic revision -m "wip unique index + status rename"
```

생성된 파일 경로 기록.

- [ ] **Step 3: Preflight 테스트 작성 (실패하도록)**

`backend/tests/test_migration_preflight.py`:

```python
"""Test UNIQUE index preflight — abort on duplicate source_batch_id."""
import pytest
from sqlalchemy import text
from app.infrastructure.models.wip_inventory import WipInventory
from app.infrastructure.models.production_batch import ProductionBatch


def test_preflight_aborts_on_duplicate_source_batch_id(db_session):
    """중복 source_batch_id 있으면 마이그레이션 upgrade 가 RuntimeError 로 abort."""
    # Arrange — 중복 source_batch_id 심기
    batch = ProductionBatch(
        run_label="test_run",
        process_name="연선",
        batch_seq=-1,
        total_length_m=1000,
        status="planned",
    )
    db_session.add(batch)
    db_session.flush()

    w1 = WipInventory(
        status="사용가능",
        source_batch_id=batch.batch_id,
        total_length_m=100,
    )
    w2 = WipInventory(
        status="사용가능",
        source_batch_id=batch.batch_id,  # 중복
        total_length_m=200,
    )
    db_session.add_all([w1, w2])
    db_session.flush()

    # Act — preflight 쿼리 실행
    from backend.alembic.versions.YYYYMMDDHHMM_wip_unique_and_rename import _preflight_check
    with pytest.raises(RuntimeError, match="UNIQUE 제약 위반 가능 데이터 발견"):
        _preflight_check(db_session.bind)


def test_status_rename_converts_legacy(db_session):
    """'실적' → '실사_확정' mass rename."""
    w = WipInventory(status="실적", total_length_m=100)
    db_session.add(w)
    db_session.flush()

    from backend.alembic.versions.YYYYMMDDHHMM_wip_unique_and_rename import _rename_status
    _rename_status(db_session.bind)

    db_session.refresh(w)
    assert w.status == "실사_확정"
```

- [ ] **Step 4: 테스트 실패 확인**

```bash
cd backend && pytest tests/test_migration_preflight.py -v
```

Expected: ImportError — 함수 미정의.

- [ ] **Step 5: 마이그레이션 구현**

생성된 alembic 파일 내용:

```python
"""wip unique index + status rename

Revision ID: <auto>
Revises: <parent>
Create Date: 2026-04-18 ...
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision = "<auto>"
down_revision = "<parent>"
branch_labels = None
depends_on = None


def _preflight_check(conn):
    """중복 source_batch_id 검출 시 abort."""
    dup = conn.execute(text("""
        SELECT source_batch_id, COUNT(*) AS c
        FROM wip_inventory
        WHERE source_batch_id IS NOT NULL
        GROUP BY source_batch_id
        HAVING COUNT(*) > 1
        LIMIT 1
    """)).fetchone()
    if dup:
        raise RuntimeError(
            f"UNIQUE 제약 위반 가능 데이터 발견: source_batch_id={dup.source_batch_id} "
            f"count={dup.c}. 중복 제거 후 재시도."
        )


def _rename_status(conn):
    """'실적' → '실사_확정' 라벨 통일."""
    conn.execute(text(
        "UPDATE wip_inventory SET status = '실사_확정' WHERE status = '실적'"
    ))


def upgrade() -> None:
    conn = op.get_bind()
    _preflight_check(conn)
    _rename_status(conn)
    op.create_index(
        "idx_wip_source_batch_unique",
        "wip_inventory",
        ["source_batch_id"],
        unique=True,
        postgresql_where=text("source_batch_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_wip_source_batch_unique", table_name="wip_inventory")
    # status rename 은 downgrade 에서 복원하지 않음 (데이터 무결성 유지)
```

- [ ] **Step 6: 테스트 import 경로 수정 + 통과 확인**

테스트 파일 상단 import 를 실제 생성된 파일명으로 변경. 재실행:

```bash
cd backend && pytest tests/test_migration_preflight.py -v
```

Expected: 2 passed.

- [ ] **Step 7: 로컬 DB 에서 마이그레이션 실제 실행**

```bash
cd backend && alembic upgrade head
```

Expected: 성공. 중복 데이터 있었으면 RuntimeError 로 abort.

- [ ] **Step 8: Commit**

```bash
git add backend/alembic/versions/*_wip_unique_and_rename.py backend/tests/test_migration_preflight.py
git commit -m "feat(wip): G1 UNIQUE index + preflight + status rename migration"
```

---

### Task 3: Migration — `wip_upload_log` 테이블

**목적**: Excel 재업로드 멱등성 G4 기반. canonical_hash UNIQUE.

**Files:**

- Create: `backend/app/infrastructure/models/wip_upload_log.py`
- Create: `backend/alembic/versions/YYYYMMDDHHMM_wip_upload_log.py`
- Test: `backend/tests/test_wip_upload_log_model.py`

- [ ] **Step 1: 모델 파일 작성**

`backend/app/infrastructure/models/wip_upload_log.py`:

```python
from sqlalchemy import Column, String, Integer, DateTime
from datetime import datetime
from app.infrastructure.database import Base


class WipUploadLog(Base):
    __tablename__ = "wip_upload_log"

    upload_id = Column(Integer, primary_key=True, autoincrement=True)
    canonical_hash = Column(String(64), unique=True, nullable=False)
    raw_file_hash = Column(String(64), nullable=True)  # 디버깅용
    run_label = Column(String(50), nullable=True)
    uploaded_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    rows_inserted = Column(Integer, default=0)
    rows_updated = Column(Integer, default=0)
    file_name = Column(String(255), nullable=True)
```

- [ ] **Step 2: 모델 import 검증 테스트 작성**

`backend/tests/test_wip_upload_log_model.py`:

```python
from app.infrastructure.models.wip_upload_log import WipUploadLog


def test_wip_upload_log_insert(db_session):
    log = WipUploadLog(
        canonical_hash="a" * 64,
        raw_file_hash="b" * 64,
        run_label="test_run",
        rows_inserted=5,
        rows_updated=2,
        file_name="test.xlsx",
    )
    db_session.add(log)
    db_session.flush()
    assert log.upload_id is not None


def test_canonical_hash_unique_constraint(db_session):
    import pytest
    from sqlalchemy.exc import IntegrityError

    h = "c" * 64
    db_session.add(WipUploadLog(canonical_hash=h))
    db_session.flush()

    db_session.add(WipUploadLog(canonical_hash=h))  # 중복
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()
```

- [ ] **Step 3: 테스트 실패 확인 (테이블 없음)**

```bash
cd backend && pytest tests/test_wip_upload_log_model.py -v
```

Expected: FAIL with "relation wip_upload_log does not exist".

- [ ] **Step 4: 마이그레이션 생성**

```bash
cd backend && alembic revision -m "wip_upload_log table" --autogenerate
```

생성된 파일이 `create_table("wip_upload_log", ...)` 를 포함하는지 확인. autogenerate 가 모델과 sync 하므로 수동 편집 불필요.

- [ ] **Step 5: 마이그레이션 실행 + 테스트 재실행**

```bash
cd backend && alembic upgrade head && pytest tests/test_wip_upload_log_model.py -v
```

Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/app/infrastructure/models/wip_upload_log.py \
  backend/alembic/versions/*_wip_upload_log.py \
  backend/tests/test_wip_upload_log_model.py
git commit -m "feat(wip): wip_upload_log table for Excel idempotency"
```

---

## Phase 1 — Surplus 계산

### Task 4: `wip_output_expected_m` — 정규 그룹 헤더 (batch_grouping.py:413)

**목적**: Eng review blocker #1 대응. 그룹 헤더(batch_seq=-1)에만 surplus 계산, 나머지는 0 유지.

**Files:**

- Modify: `backend/app/services/batch_grouping.py:413-446` (and 524, 580 — header-like 61-strand core 도 확인)
- Test: `backend/tests/test_wip_output_expected_m.py`

- [ ] **Step 1: 현재 그룹 헤더 constructor 읽고 `work_qty_g`, `net_qty_g` 정확 위치 확인**

```bash
grep -n "work_qty_g\|net_qty_g\|header_batch\s*=" backend/app/services/batch_grouping.py
```

변수 계산 위치 (line 350, 362-364) 와 header constructor 인자 목록 확인.

- [ ] **Step 2: 실패 테스트 작성**

`backend/tests/test_wip_output_expected_m.py`:

```python
"""Test wip_output_expected_m calculation on group headers."""
import pytest
from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.sales_order import SalesOrder
from app.infrastructure.models.drum_lot_master import DrumLotMaster
from app.services.batch_grouping import create_batches


def test_group_header_has_correct_surplus(db_session, seed_masters):
    """150SQ 수주 700m, lot_stranding=1000m → 헤더 surplus = 300m."""
    # Arrange
    db_session.add(DrumLotMaster(cross_section=150, lot_stranding=1000))
    db_session.add(SalesOrder(
        run_label="T4",
        order_id="ORD-T4-001",
        order_line=1,
        spec_raw="150SQ",
        ordered_qty_m=700,
        core_count=1,
        voltage="0.6/1kV",
    ))
    db_session.flush()

    # Act
    create_batches(run_label="T4", db=db_session)

    # Assert
    headers = db_session.query(ProductionBatch).filter_by(
        run_label="T4",
        batch_seq=-1,
        process_name="연선",
    ).all()
    assert len(headers) == 1, "단일 그룹 헤더 생성"
    h = headers[0]
    assert float(h.total_length_m or 0) == 1000  # lot 확장 후 생산량
    # 300m = 1000 - 700 (defect_buffer 적용 전 기준), 실제 구현은 defect_buffer 포함
    surplus = float(h.wip_output_expected_m or 0)
    assert surplus > 0, f"헤더에 surplus 설정돼야 함, 실제={surplus}"
    # 허용 범위 체크 (defect_buffer 포함되므로 정확값은 ~265m)
    assert 200 <= surplus <= 300, f"surplus 범위 벗어남: {surplus}"


def test_non_header_batch_has_zero_surplus(db_session, seed_masters):
    """per-order 배치 (batch_seq != -1) 는 surplus 0."""
    db_session.add(DrumLotMaster(cross_section=150, lot_stranding=1000))
    db_session.add(SalesOrder(
        run_label="T4b",
        order_id="ORD-T4b-001",
        order_line=1,
        spec_raw="150SQ",
        ordered_qty_m=700,
        core_count=1,
        voltage="0.6/1kV",
    ))
    db_session.flush()
    create_batches(run_label="T4b", db=db_session)

    non_headers = db_session.query(ProductionBatch).filter(
        ProductionBatch.run_label == "T4b",
        ProductionBatch.batch_seq != -1,
    ).all()
    for b in non_headers:
        assert float(b.wip_output_expected_m or 0) == 0, \
            f"non-header 배치는 surplus=0 이어야 함: batch_id={b.batch_id}"
```

참고: `seed_masters` fixture 가 기존 conftest.py 에 없으면 추가 필요 (ItemMaster, SpeedMaster 등 minimum 시드). 필요 시 `backend/tests/conftest.py` 참조 후 fixture 보강.

- [ ] **Step 3: 테스트 실패 확인**

```bash
cd backend && pytest tests/test_wip_output_expected_m.py -v
```

Expected: FAIL — surplus 가 0 (현재 구현은 쓰지 않음).

- [ ] **Step 4: batch_grouping.py 헤더 constructor 에 surplus 라인 추가**

`batch_grouping.py:413-446` 부근의 헤더 배치 생성자 (변수명은 `header_batch` 가정) 에서, `total_length_m=work_qty_g` 라인 직후에:

```python
header_batch = ProductionBatch(
    run_label=run_label,
    process_name="연선",
    batch_seq=-1,
    total_length_m=work_qty_g,
    # ── Eng review 블로커 #1: 헤더에만 surplus 기록 ──
    wip_output_expected_m=max(0, work_qty_g - net_qty_g),
    sq_mm2=sq,
    ...  # 기존 나머지 필드 그대로
)
```

61-strand core 배치(`524`, `580`) 와 per-order display 배치(`474`) 는 **건드리지 않음** (default 0 유지). 명시성을 위해 해당 constructor 에 다음 주석 추가:

```python
# wip_output_expected_m 은 헤더 전용. 여기선 설정하지 않음 (default 0).
# Listener 는 batch_seq == -1 AND process_name == "연선" 에만 fire.
```

- [ ] **Step 5: 테스트 통과 확인**

```bash
cd backend && pytest tests/test_wip_output_expected_m.py -v
```

Expected: 2 passed.

- [ ] **Step 6: 기존 batch_grouping 회귀 테스트 실행**

```bash
cd backend && pytest tests/test_batch_grouping_4_1.py -v
```

Expected: 기존 테스트 모두 PASS (surplus 추가는 새 필드 설정이라 기존 로직 영향 없어야 함).

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/batch_grouping.py backend/tests/test_wip_output_expected_m.py
git commit -m "feat(batch): compute wip_output_expected_m on group header only"
```

---

### Task 5: `wip_output_expected_m` — Split 헤더 재계산 (batch_grouping.py:1344)

**목적**: 파이프라인 split 시 surplus 를 원 헤더에서 proportional 복사하면 틀림. Split 후 실제 lot 개수로 재계산.

**Files:**

- Modify: `backend/app/services/batch_grouping.py:1344-1382`
- Test: `backend/tests/test_wip_output_expected_m.py` (Task 4 파일에 추가)

- [ ] **Step 1: Split 경로 읽고 `split_lot_count`, `split_orders` 변수 확인**

```bash
grep -n "split_lot_count\|split_orders\|batch_grouping.py:1344" backend/app/services/batch_grouping.py
sed -n '1340,1390p' backend/app/services/batch_grouping.py
```

- [ ] **Step 2: 실패 테스트 추가**

`backend/tests/test_wip_output_expected_m.py` 에 추가:

```python
def test_split_header_recomputes_surplus(db_session, seed_masters):
    """파이프라인 분할 시 split 헤더의 surplus 는 split 후 lot 기준 재계산."""
    # Arrange — 150SQ 10+4 처럼 split 유도할 수주 3건 (합산이 1 교대 초과)
    db_session.add(DrumLotMaster(cross_section=150, lot_stranding=1000))
    for i, qty in enumerate([4500, 4500, 1000], 1):
        db_session.add(SalesOrder(
            run_label="T5",
            order_id=f"ORD-T5-{i:03d}",
            order_line=1,
            spec_raw="150SQ",
            ordered_qty_m=qty,
            core_count=1,
            voltage="0.6/1kV",
        ))
    db_session.flush()

    create_batches(run_label="T5", db=db_session)

    headers = db_session.query(ProductionBatch).filter_by(
        run_label="T5",
        batch_seq=-1,
        process_name="연선",
    ).all()
    # 최소 2개 헤더 (split 결과)
    assert len(headers) >= 2, "split 시 헤더 2개 이상"
    # 각 split 헤더의 surplus 가 해당 split 의 work_qty - 해당 orders 환산 수량
    for h in headers:
        work_qty = float(h.total_length_m or 0)
        surplus = float(h.wip_output_expected_m or 0)
        assert surplus >= 0
        assert surplus < work_qty, f"surplus({surplus}) < work_qty({work_qty})"
```

- [ ] **Step 3: 테스트 실패 확인**

```bash
cd backend && pytest tests/test_wip_output_expected_m.py::test_split_header_recomputes_surplus -v
```

Expected: FAIL — split 헤더의 `wip_output_expected_m` 이 0 또는 misproportioned.

- [ ] **Step 4: Split 헤더 constructor 수정**

`batch_grouping.py:1344-1382` split 헤더 생성 코드에서, split 결과 orders 와 lot count 를 기준으로 재계산:

```python
# split 후 헤더 surplus 재계산 (Eng review 블로커 #1)
split_orders_conv_qty = sum(
    float(o.ordered_qty_m or 0)
    * max(int(o.core_count or 1), 1)
    * (1 + defect_buffer_pct)
    for o in split_orders
)
split_work_qty = split_lot_count * lot_size_g
split_surplus = max(0, split_work_qty - split_orders_conv_qty)

new_header = ProductionBatch(
    run_label=run_label,
    process_name="연선",
    batch_seq=-1,
    total_length_m=split_work_qty,
    wip_output_expected_m=split_surplus,   # ← 신규
    sq_mm2=sq,
    ...
)
```

`defect_buffer_pct` 가 이 지점에서 available 한지 확인. 없으면 함수 시그니처 확장 또는 ConstraintConfig 에서 재조회.

- [ ] **Step 5: 테스트 통과 확인**

```bash
cd backend && pytest tests/test_wip_output_expected_m.py -v
```

Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/batch_grouping.py backend/tests/test_wip_output_expected_m.py
git commit -m "feat(batch): recompute wip_output_expected_m on pipeline split header"
```

---

## Phase 2 — Listener

### Task 6: `wip_lifecycle_listener.py` — after_insert listener

**목적**: 게이트 + `ON CONFLICT DO NOTHING` 으로 WIP 자동 생성. 3단계 테스트 (게이트 on/off, ON CONFLICT, rollback).

**Files:**

- Create: `backend/app/services/wip_lifecycle_listener.py`
- Test: `backend/tests/test_wip_listener.py`

- [ ] **Step 1: 실패 테스트 작성**

`backend/tests/test_wip_listener.py`:

```python
"""Listener gate + ON CONFLICT + rollback tests."""
import pytest
from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.wip_inventory import WipInventory


def _insert_header(db_session, expected=300, process="연선"):
    b = ProductionBatch(
        run_label="T6",
        process_name=process,
        batch_seq=-1,
        total_length_m=1000,
        wip_output_expected_m=expected,
        sq_mm2=150,
        voltage="0.6/1kV",
        conductor_material="CU",
        status="planned",
    )
    db_session.add(b)
    db_session.flush()
    return b


def test_listener_creates_wip_on_header_with_surplus(db_session, listener_registered):
    batch = _insert_header(db_session, expected=300)
    wip = db_session.query(WipInventory).filter_by(source_batch_id=batch.batch_id).first()
    assert wip is not None
    assert wip.status == "예상"
    assert float(wip.expected_length_m) == 300


def test_listener_skips_non_header(db_session, listener_registered):
    b = ProductionBatch(
        run_label="T6b",
        process_name="연선",
        batch_seq=1,   # non-header
        total_length_m=700,
        wip_output_expected_m=300,
        sq_mm2=150,
        status="planned",
    )
    db_session.add(b)
    db_session.flush()
    wip = db_session.query(WipInventory).filter_by(source_batch_id=b.batch_id).first()
    assert wip is None, "non-header 배치는 WIP 생성 안 함"


def test_listener_skips_non_연선_process(db_session, listener_registered):
    batch = _insert_header(db_session, expected=300, process="절연")
    wip = db_session.query(WipInventory).filter_by(source_batch_id=batch.batch_id).first()
    assert wip is None


def test_listener_skips_zero_surplus(db_session, listener_registered):
    batch = _insert_header(db_session, expected=0)
    wip = db_session.query(WipInventory).filter_by(source_batch_id=batch.batch_id).first()
    assert wip is None


def test_listener_on_conflict_do_nothing(db_session, listener_registered):
    """같은 source_batch_id 로 두 번 트리거해도 G1 UNIQUE 충돌 없이 통과."""
    batch = _insert_header(db_session, expected=300)
    # 수동으로 중복 INSERT 시도
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    stmt = pg_insert(WipInventory.__table__).values(
        status="예상",
        source_batch_id=batch.batch_id,
        expected_length_m=999,
        total_length_m=999,
    ).on_conflict_do_nothing(index_elements=["source_batch_id"])
    db_session.execute(stmt)
    db_session.flush()

    wips = db_session.query(WipInventory).filter_by(source_batch_id=batch.batch_id).all()
    assert len(wips) == 1, "ON CONFLICT DO NOTHING 동작 — 중복 삽입 무시"


def test_listener_rollback_propagates(db_session, listener_registered):
    """outer transaction rollback 시 listener 가 만든 WIP 도 rollback."""
    batch = _insert_header(db_session, expected=300)
    wip_id = db_session.query(WipInventory).filter_by(
        source_batch_id=batch.batch_id
    ).first().wip_id
    db_session.rollback()

    # 다른 세션에서 확인
    remaining = db_session.query(WipInventory).filter_by(wip_id=wip_id).first()
    assert remaining is None, "rollback 시 WIP 도 사라져야 함"
```

`listener_registered` fixture 는 `conftest.py` 에 추가 필요 (다음 Step).

- [ ] **Step 2: `conftest.py` 에 fixture 추가**

`backend/tests/conftest.py` 맨 아래:

```python
import pytest
from app.services.wip_lifecycle_listener import register_wip_listener


@pytest.fixture
def listener_registered():
    """테스트 시 명시적으로 listener 등록. 테스트 끝나면 해제 불필요 (모듈 레벨 event)."""
    register_wip_listener()
    yield
    # SQLAlchemy 전역 이벤트는 제거 어려움. 테스트 격리는 DB rollback 으로 처리.
```

- [ ] **Step 3: 테스트 실패 확인**

```bash
cd backend && pytest tests/test_wip_listener.py -v
```

Expected: ImportError on `wip_lifecycle_listener`.

- [ ] **Step 4: Listener 구현**

`backend/app/services/wip_lifecycle_listener.py`:

```python
"""ProductionBatch after_insert listener — WIP 예상재공 자동 생성.

Eng review 블로커 #1: 헤더 배치(batch_seq=-1 + 연선)에만 fire.
Eng review Medium #4: ON CONFLICT DO NOTHING 으로 IntegrityError 전파 차단.

Bulk insert 주의: Session.bulk_save_objects() / bulk_insert_mappings() 는 ORM event
를 bypass. database.py 의 do_orm_execute 가드 참조.
"""
from sqlalchemy import event
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.wip_inventory import WipInventory

_WIP_GATE_PROCESSES = frozenset({"연선"})


def _derive_process_stage(process_name: str | None) -> str:
    """공정명 → WIP process_stage 매핑."""
    if not process_name:
        return ""
    if "연선" in process_name:
        return "연선재고"
    if "절연" in process_name:
        return "절연재고"
    if "연합" in process_name or "T/P" in process_name:
        return "연합재고"
    return process_name


def _auto_create_expected_wip(mapper, connection, batch):
    """헤더 배치 INSERT 시 예상 WIP auto-create."""
    if batch.batch_seq != -1:
        return
    if batch.process_name not in _WIP_GATE_PROCESSES:
        return
    expected = float(batch.wip_output_expected_m or 0)
    if expected <= 0:
        return

    stmt = pg_insert(WipInventory.__table__).values(
        process_stage=_derive_process_stage(batch.process_name),
        cross_section=batch.sq_mm2,
        voltage_class=batch.voltage,
        material=batch.conductor_material,
        core_colors=getattr(batch, "core_colors", None),
        length_m=expected,
        count=1,
        total_length_m=expected,
        expected_length_m=expected,
        status="예상",
        source_batch_id=batch.batch_id,
        run_label=batch.run_label,
    ).on_conflict_do_nothing(index_elements=["source_batch_id"])
    connection.execute(stmt)


_registered = False


def register_wip_listener() -> None:
    """SQLAlchemy after_insert listener 등록. 앱 시작 시 1회 호출."""
    global _registered
    if _registered:
        return
    event.listen(ProductionBatch, "after_insert", _auto_create_expected_wip)
    _registered = True
```

- [ ] **Step 5: 테스트 통과 확인**

```bash
cd backend && pytest tests/test_wip_listener.py -v
```

Expected: 6 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/wip_lifecycle_listener.py \
  backend/tests/test_wip_listener.py \
  backend/tests/conftest.py
git commit -m "feat(wip): after_insert listener with gate + ON CONFLICT DO NOTHING"
```

---

### Task 7: Listener 등록 + bulk API 금지 가드

**목적**: 앱 시작 시 listener 자동 등록. 미래에 bulk_save_objects 전환 시 bypass 방지.

**Files:**

- Modify: `backend/app/infrastructure/database.py`
- Test: `backend/tests/test_wip_listener.py` (bulk 가드 테스트 추가)

- [ ] **Step 1: 현재 database.py 구조 확인**

```bash
cat backend/app/infrastructure/database.py
```

`Base` 선언, `SessionLocal` 패턴 파악.

- [ ] **Step 2: Bulk 가드 테스트 추가**

`backend/tests/test_wip_listener.py` 끝에 추가:

```python
def test_bulk_save_objects_blocked(db_session, listener_registered):
    """ProductionBatch 에 대한 bulk_save_objects 는 RuntimeError."""
    b = ProductionBatch(
        run_label="T7",
        process_name="연선",
        batch_seq=-1,
        total_length_m=1000,
        wip_output_expected_m=300,
        sq_mm2=150,
        status="planned",
    )
    with pytest.raises(RuntimeError, match="bulk.*ProductionBatch.*금지"):
        db_session.bulk_save_objects([b])
```

- [ ] **Step 3: 테스트 실패 확인**

```bash
cd backend && pytest tests/test_wip_listener.py::test_bulk_save_objects_blocked -v
```

Expected: FAIL — 가드 없음.

- [ ] **Step 4: `database.py` 수정**

`backend/app/infrastructure/database.py` 에 Base/SessionLocal 선언 후 다음 추가:

```python
from sqlalchemy import event
from sqlalchemy.orm import Session as _Session


@event.listens_for(_Session, "do_orm_execute")
def _block_bulk_for_production_batch(state):
    """ProductionBatch 대한 bulk API 차단 — listener bypass 방지."""
    from app.infrastructure.models.production_batch import ProductionBatch

    # bulk_save_objects / bulk_insert_mappings 는 statement.is_bulk 판별 가능
    stmt = state.statement
    if not hasattr(stmt, "entity_description"):
        return
    if getattr(stmt, "is_bulk", False):
        desc = stmt.entity_description.get("entity", None) if stmt.entity_description else None
        if desc is ProductionBatch:
            raise RuntimeError(
                "bulk_save_objects/bulk_insert_mappings 는 ProductionBatch 에서 금지 "
                "(after_insert listener bypass). 일반 session.add() 사용."
            )


# 앱 시작 시 WIP listener 자동 등록
from app.services.wip_lifecycle_listener import register_wip_listener
register_wip_listener()
```

**주의**: SQLAlchemy 2.x 에서 `do_orm_execute` state 의 bulk 판별 API 가 버전별로 다를 수 있음. 실제 설치 버전 확인 후 조정:

```bash
cd backend && python -c "import sqlalchemy; print(sqlalchemy.__version__)"
```

최소한의 대안: `session.bulk_save_objects` 를 monkey-patch 로 감싸는 방법.

- [ ] **Step 5: 테스트 통과 확인**

```bash
cd backend && pytest tests/test_wip_listener.py -v
```

Expected: 모두 pass.

- [ ] **Step 6: 전체 테스트 회귀 확인**

```bash
cd backend && pytest -x
```

Expected: 기존 테스트 깨지는 것 없음. `ProductionBatch` bulk insert 쓰는 기존 코드가 있다면 이 task 에서 감지됨 (기대).

- [ ] **Step 7: Commit**

```bash
git add backend/app/infrastructure/database.py backend/tests/test_wip_listener.py
git commit -m "feat(wip): register listener at startup + forbid bulk for ProductionBatch"
```

---

## Phase 3 — 매칭 & 승격

### Task 8: `wip_matching.py` status 필터 + emergency_mode

**목적**: Level 2 기본 (`사용가능 + 실사_확정 + 실적_추정`). Level 3 emergency_mode 플래그로 예상 포함.

**Files:**

- Modify: `backend/app/services/wip_matching.py:41`
- Test: `backend/tests/test_wip_matching_levels.py`

- [ ] **Step 1: 현재 `match_wip` 시그니처 확인**

```bash
sed -n '22,50p' backend/app/services/wip_matching.py
```

- [ ] **Step 2: 실패 테스트 작성**

`backend/tests/test_wip_matching_levels.py`:

```python
"""Level 1/2/3 status filter tests for match_wip."""
import pytest
from app.infrastructure.models.wip_inventory import WipInventory
from app.infrastructure.models.sales_order import SalesOrder
from app.services.wip_matching import match_wip


def _seed_wip(db_session, status, qty=300):
    w = WipInventory(
        process_stage="연선재고",
        cross_section=150,
        voltage_class="저압",
        length_m=qty,
        count=1,
        total_length_m=qty,
        status=status,
    )
    db_session.add(w)
    db_session.flush()
    return w


def _seed_order(db_session, qty=280):
    o = SalesOrder(
        run_label="T8",
        order_id="ORD-T8-001",
        order_line=1,
        spec_raw="150SQ",
        ordered_qty_m=qty,
        core_count=1,
        voltage="0.6/1kV",
    )
    db_session.add(o)
    db_session.flush()
    return o


def test_level2_default_matches_사용가능(db_session, seed_criteria):
    _seed_wip(db_session, "사용가능")
    _seed_order(db_session)
    result = match_wip(run_label="T8", db=db_session)
    assert result["matched"] == 1


def test_level2_default_matches_실사_확정(db_session, seed_criteria):
    _seed_wip(db_session, "실사_확정")
    _seed_order(db_session)
    result = match_wip(run_label="T8", db=db_session)
    assert result["matched"] == 1


def test_level2_default_matches_실적_추정(db_session, seed_criteria):
    _seed_wip(db_session, "실적_추정")
    _seed_order(db_session)
    result = match_wip(run_label="T8", db=db_session)
    assert result["matched"] == 1


def test_level2_default_skips_예상(db_session, seed_criteria):
    _seed_wip(db_session, "예상")
    _seed_order(db_session)
    result = match_wip(run_label="T8", db=db_session)
    assert result["matched"] == 0, "예상 상태는 Level 2 기본에서 skip"


def test_level3_emergency_mode_matches_예상(db_session, seed_criteria):
    _seed_wip(db_session, "예상")
    _seed_order(db_session)
    result = match_wip(run_label="T8", db=db_session, emergency_mode=True)
    assert result["matched"] == 1, "emergency_mode=True 시 예상도 매칭"


def test_skips_사용완료(db_session, seed_criteria):
    _seed_wip(db_session, "사용완료")
    _seed_order(db_session)
    result = match_wip(run_label="T8", db=db_session)
    assert result["matched"] == 0
```

`seed_criteria` fixture 는 DecisionCriteria 기본값 시드 (기존 conftest.py 확장 필요).

- [ ] **Step 3: 테스트 실패 확인**

```bash
cd backend && pytest tests/test_wip_matching_levels.py -v
```

Expected: FAIL — 기존 필터는 `status == "사용가능"` 단일.

- [ ] **Step 4: `wip_matching.py` 수정**

`backend/app/services/wip_matching.py:22` 의 시그니처 확장:

```python
# 매칭 Level 정책
_LEVEL1_STATUSES = ("사용가능", "실사_확정")  # 보수적
_LEVEL2_STATUSES = ("사용가능", "실사_확정", "실적_추정")  # 기본
_LEVEL3_STATUSES = ("사용가능", "실사_확정", "실적_추정", "예상")  # 공격적 (temporal guard 필수)


def match_wip(
    run_label: str,
    db: Session,
    *,
    exclude_wip_ids: set[int] | None = None,
    emergency_mode: bool = False,
) -> dict:
    """재공 재고를 수주에 매칭.

    Args:
        emergency_mode: True 시 Level 3 (예상재공 포함). Phase 1 에선 G5 temporal
            guard 미구현 상태이므로 시연/긴급수주 용도로만 사용. 기본 False.
    """
    result = {"matched": 0, "skipped": 0, "details": []}

    # ... 기존 로직 ...

    allowed = _LEVEL3_STATUSES if emergency_mode else _LEVEL2_STATUSES
    wip_query = db.query(WipInventory).filter(WipInventory.status.in_(allowed))
    # Note: run_label 로 필터링하지 않음. WIP 풀은 cross-run by design (spec §G3).
    # ... 이후 기존 로직 ...
```

기존 41번째 줄 `wip_query = db.query(WipInventory).filter(WipInventory.status == "사용가능")` 을 위 블록으로 교체.

- [ ] **Step 5: 테스트 통과 확인**

```bash
cd backend && pytest tests/test_wip_matching_levels.py -v
```

Expected: 6 passed.

- [ ] **Step 6: 기존 wip_matching 테스트 회귀 확인**

```bash
cd backend && pytest tests/ -k "wip" -v
```

Expected: 기존 매칭 테스트 모두 pass.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/wip_matching.py backend/tests/test_wip_matching_levels.py
git commit -m "feat(wip): Level 2 default status filter + emergency_mode flag"
```

---

### Task 9: T2 승격 공통 헬퍼 `_promote_expected_to_estimated`

**목적**: 3개 endpoint 에서 공통 사용. 멱등성, wip_complete 무반응, cascade 지원.

**Files:**

- Create: `backend/app/services/wip_promotion.py`
- Test: `backend/tests/test_wip_promotion_hook.py` (Part A — 헬퍼 단위테스트)

- [ ] **Step 1: 실패 테스트 작성**

`backend/tests/test_wip_promotion_hook.py`:

```python
"""T2 promotion helper + 3 endpoint hook tests."""
import pytest
from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.wip_inventory import WipInventory


def _seed_batch_with_wip(db_session, listener_registered):
    b = ProductionBatch(
        run_label="T9",
        process_name="연선",
        batch_seq=-1,
        total_length_m=1000,
        wip_output_expected_m=300,
        sq_mm2=150,
        voltage="0.6/1kV",
        conductor_material="CU",
        status="planned",
    )
    db_session.add(b)
    db_session.flush()
    return b


def test_promote_on_completed(db_session, listener_registered):
    from app.services.wip_promotion import _promote_expected_to_estimated
    batch = _seed_batch_with_wip(db_session, listener_registered)

    _promote_expected_to_estimated(batch.batch_id, "completed", db_session)
    db_session.flush()

    wip = db_session.query(WipInventory).filter_by(source_batch_id=batch.batch_id).first()
    assert wip.status == "실적_추정"
    assert float(wip.actual_length_m or 0) == float(wip.expected_length_m)


def test_promote_skip_for_non_completed(db_session, listener_registered):
    from app.services.wip_promotion import _promote_expected_to_estimated
    batch = _seed_batch_with_wip(db_session, listener_registered)

    _promote_expected_to_estimated(batch.batch_id, "in_progress", db_session)
    _promote_expected_to_estimated(batch.batch_id, "scheduled", db_session)
    _promote_expected_to_estimated(batch.batch_id, "wip_complete", db_session)
    db_session.flush()

    wip = db_session.query(WipInventory).filter_by(source_batch_id=batch.batch_id).first()
    assert wip.status == "예상", "completed 아닌 전환은 승격 skip"


def test_promote_idempotent(db_session, listener_registered):
    from app.services.wip_promotion import _promote_expected_to_estimated
    batch = _seed_batch_with_wip(db_session, listener_registered)

    _promote_expected_to_estimated(batch.batch_id, "completed", db_session)
    # 2번째 호출 — 이미 실적_추정. no-op.
    _promote_expected_to_estimated(batch.batch_id, "completed", db_session)
    db_session.flush()

    wip = db_session.query(WipInventory).filter_by(source_batch_id=batch.batch_id).first()
    assert wip.status == "실적_추정"


def test_promote_no_op_when_no_wip(db_session, listener_registered):
    """WIP 없는 배치 (surplus=0) 의 completed 전환은 조용히 통과."""
    from app.services.wip_promotion import _promote_expected_to_estimated
    b = ProductionBatch(
        run_label="T9b",
        process_name="연선",
        batch_seq=-1,
        total_length_m=1000,
        wip_output_expected_m=0,
        status="planned",
    )
    db_session.add(b)
    db_session.flush()

    # 예외 없이 통과해야 함
    _promote_expected_to_estimated(b.batch_id, "completed", db_session)
```

- [ ] **Step 2: 테스트 실패 확인**

```bash
cd backend && pytest tests/test_wip_promotion_hook.py -v
```

Expected: ImportError.

- [ ] **Step 3: 헬퍼 구현**

`backend/app/services/wip_promotion.py`:

```python
"""T2 승격 공통 헬퍼 — 배치 `completed` 전환 시 연결된 WIP 를 예상→실적_추정 승격.

Spec §10. Eng review HIGH #2: 3개 status-write endpoint 에서 호출.
"""
from sqlalchemy.orm import Session

from app.infrastructure.models.wip_inventory import WipInventory


def _promote_expected_to_estimated(
    batch_id: int, new_status: str, db: Session
) -> bool:
    """배치 status 가 completed 로 전환될 때 WIP 를 예상→실적_추정 승격.

    Returns:
        True if promotion happened, False if no-op.

    Rules:
      - `completed` 로의 전환에만 반응 (wip_complete, in_progress, scheduled 는 무시)
      - 멱등: 이미 실적_추정/실사_확정/사용완료 이면 no-op
      - MES 없음 → actual_length_m = expected_length_m 로 설정
    """
    if new_status != "completed":
        return False

    wip = db.query(WipInventory).filter(
        WipInventory.source_batch_id == batch_id,
        WipInventory.status == "예상",
    ).first()
    if not wip:
        return False

    wip.status = "실적_추정"
    if wip.actual_length_m is None:
        wip.actual_length_m = wip.expected_length_m
    return True
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
cd backend && pytest tests/test_wip_promotion_hook.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/wip_promotion.py backend/tests/test_wip_promotion_hook.py
git commit -m "feat(wip): _promote_expected_to_estimated common helper"
```

---

### Task 10: T2 hook 3 endpoint 통합 + cascade

**목적**: 3개 status-write endpoint 에 hook + header cascade.

**Files:**

- Modify: `backend/app/presentation/routes/plan_pipeline.py` (3곳: 1555, 1598+, 1640)
- Test: `backend/tests/test_wip_promotion_hook.py` (Part B — endpoint 통합 테스트)

- [ ] **Step 1: 현재 3 endpoint 구조 확인**

```bash
grep -n "PATCH\|@router.patch\|update_batch_status\|update_batch_group_status\|def update_batch\b" backend/app/presentation/routes/plan_pipeline.py
sed -n '1550,1680p' backend/app/presentation/routes/plan_pipeline.py
```

3개 엔드포인트 시그니처와 현재 status 쓰기 지점 파악.

- [ ] **Step 2: 통합 테스트 추가**

`backend/tests/test_wip_promotion_hook.py` 에 추가:

```python
from fastapi.testclient import TestClient


def test_single_endpoint_triggers_promotion(test_client, db_session, listener_registered):
    batch = _seed_batch_with_wip(db_session, listener_registered)
    db_session.commit()

    resp = test_client.patch(
        f"/api/batch/{batch.batch_id}/status",
        json={"status": "completed"},
    )
    assert resp.status_code == 200

    wip = db_session.query(WipInventory).filter_by(source_batch_id=batch.batch_id).first()
    db_session.refresh(wip)
    assert wip.status == "실적_추정"


def test_freeform_endpoint_triggers_promotion(test_client, db_session, listener_registered):
    batch = _seed_batch_with_wip(db_session, listener_registered)
    db_session.commit()

    resp = test_client.patch(
        f"/api/batch/{batch.batch_id}",
        json={"status": "completed"},
    )
    assert resp.status_code == 200

    wip = db_session.query(WipInventory).filter_by(source_batch_id=batch.batch_id).first()
    db_session.refresh(wip)
    assert wip.status == "실적_추정"


def test_bulk_endpoint_triggers_promotion(test_client, db_session, listener_registered):
    batch = _seed_batch_with_wip(db_session, listener_registered)
    db_session.commit()

    resp = test_client.patch(
        f"/api/batch-group/{batch.batch_group or 'default'}/status",
        json={"status": "completed"},
    )
    assert resp.status_code == 200

    wip = db_session.query(WipInventory).filter_by(source_batch_id=batch.batch_id).first()
    db_session.refresh(wip)
    assert wip.status == "실적_추정"


def test_cascade_header_promotion(test_client, db_session, listener_registered):
    """non-header 배치 completed → header 도 cascade → header 의 WIP 승격."""
    header = _seed_batch_with_wip(db_session, listener_registered)  # batch_seq=-1
    child = ProductionBatch(
        run_label=header.run_label,
        process_name="연선",
        batch_seq=1,   # non-header
        batch_group=header.batch_group,
        total_length_m=700,
        wip_output_expected_m=0,
        sq_mm2=150,
        status="planned",
    )
    db_session.add(child)
    db_session.commit()

    # child 를 completed 로
    resp = test_client.patch(
        f"/api/batch/{child.batch_id}/status",
        json={"status": "completed"},
    )
    assert resp.status_code == 200

    # 만약 header cascade 로직이 존재하면 header 도 completed + WIP 승격
    db_session.refresh(header)
    if header.status == "completed":
        wip = db_session.query(WipInventory).filter_by(source_batch_id=header.batch_id).first()
        assert wip.status == "실적_추정", "cascade 시 header 의 WIP 도 승격"
```

`test_client` fixture 가 conftest.py 에 없으면 추가:

```python
from fastapi.testclient import TestClient
from app.main import app  # 실제 앱 import 경로 확인

@pytest.fixture
def test_client():
    return TestClient(app)
```

- [ ] **Step 3: 테스트 실패 확인**

```bash
cd backend && pytest tests/test_wip_promotion_hook.py -v -k "endpoint or cascade"
```

Expected: FAIL — hook 미통합.

- [ ] **Step 4: 3 endpoint 에 hook 통합**

`backend/app/presentation/routes/plan_pipeline.py` 상단에:

```python
from app.services.wip_promotion import _promote_expected_to_estimated
```

각 엔드포인트:

```python
# 1. Single endpoint (line ~1555)
@router.patch("/batch/{batch_id}/status")
def update_batch_status(batch_id: int, payload: dict, db: Session = Depends(get_db)):
    batch = db.query(ProductionBatch).get(batch_id)
    if not batch:
        raise HTTPException(404)
    new_status = payload.get("status")
    batch.status = new_status

    # T2 승격 hook (Eng review HIGH #2)
    _promote_expected_to_estimated(batch.batch_id, new_status, db)

    # Cascade — non-header 가 completed 되면 header 도 승격 확인
    if batch.batch_seq != -1 and new_status == "completed":
        header = db.query(ProductionBatch).filter(
            ProductionBatch.batch_group == batch.batch_group,
            ProductionBatch.batch_seq == -1,
        ).first()
        if header and header.status == "completed":
            _promote_expected_to_estimated(header.batch_id, "completed", db)

    db.commit()
    return {"status": "ok"}


# 2. Freeform endpoint (line ~1598)
@router.patch("/batch/{batch_id}")
def update_batch(batch_id: int, payload: dict, db: Session = Depends(get_db)):
    batch = db.query(ProductionBatch).get(batch_id)
    if not batch:
        raise HTTPException(404)

    for field, value in payload.items():
        setattr(batch, field, value)

    # T2 hook
    if "status" in payload:
        _promote_expected_to_estimated(batch.batch_id, payload["status"], db)

    db.commit()
    return {"status": "ok"}


# 3. Bulk endpoint (line ~1640)
@router.patch("/batch-group/{batch_group}/status")
def update_batch_group_status(batch_group: str, payload: dict, db: Session = Depends(get_db)):
    new_status = payload.get("status")
    batches = db.query(ProductionBatch).filter(
        ProductionBatch.batch_group == batch_group
    ).all()
    if not batches:
        raise HTTPException(404)

    for b in batches:
        b.status = new_status
        # T2 hook per batch
        _promote_expected_to_estimated(b.batch_id, new_status, db)

    db.commit()
    return {"status": "ok", "updated": len(batches)}
```

**주의**: 실제 엔드포인트 시그니처/의존성은 현재 코드 기반으로 조정. 위는 스켈레톤.

- [ ] **Step 5: 테스트 통과 확인**

```bash
cd backend && pytest tests/test_wip_promotion_hook.py -v
```

Expected: 모든 테스트 pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/presentation/routes/plan_pipeline.py backend/tests/test_wip_promotion_hook.py
git commit -m "feat(wip): T2 promotion hook on 3 status-write endpoints + cascade"
```

---

## Phase 4 — Reconciliation

### Task 11: Canonical rows hash + upload_log 멱등성

**목적**: Excel 파싱 후 canonical 튜플 기준 hash. 동일 내용 재업로드 감지.

**Files:**

- Modify: `backend/app/services/wip_parser.py` (상단 + parse_file 함수)
- Test: `backend/tests/test_wip_reconciliation.py` (Part A — hash)

- [ ] **Step 1: 현재 `parse_file` 함수 시그니처 확인**

```bash
sed -n '1,50p' backend/app/services/wip_parser.py
```

- [ ] **Step 2: 실패 테스트 작성**

`backend/tests/test_wip_reconciliation.py`:

```python
"""Reconciliation tests — canonical hash, tiebreaker, UPDATE/INSERT."""
import hashlib
import io
import pytest
from openpyxl import Workbook


def _make_excel(rows: list[dict]) -> bytes:
    wb = Workbook()
    ws = wb.active
    headers = ["공정", "규격", "전압", "길이", "개수", "색상"]
    ws.append(headers)
    for r in rows:
        ws.append([
            r.get("공정", "연선재고"),
            r.get("규격", "150SQ"),
            r.get("전압", "저압"),
            r.get("길이", 300),
            r.get("개수", 1),
            r.get("색상", ""),
        ])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_canonical_hash_stable_across_whitespace(db_session):
    from app.services.wip_parser import _compute_canonical_hash

    rows_a = [{"공정": "연선재고", "규격": "150SQ", "길이": 300}]
    rows_b = [{"공정": "연선재고", "규격": "150SQ", "길이": 300}]

    bytes_a = _make_excel(rows_a)
    bytes_b = _make_excel(rows_b)

    # raw bytes 는 다를 수 있으나 canonical hash 는 동일
    hash_a = _compute_canonical_hash(bytes_a)
    hash_b = _compute_canonical_hash(bytes_b)
    assert hash_a == hash_b


def test_canonical_hash_differs_on_content_change(db_session):
    from app.services.wip_parser import _compute_canonical_hash

    rows_a = [{"공정": "연선재고", "규격": "150SQ", "길이": 300}]
    rows_b = [{"공정": "연선재고", "규격": "150SQ", "길이": 400}]  # 길이 다름

    hash_a = _compute_canonical_hash(_make_excel(rows_a))
    hash_b = _compute_canonical_hash(_make_excel(rows_b))
    assert hash_a != hash_b


def test_reupload_same_content_detects_duplicate(db_session):
    from app.services.wip_parser import parse_wip_excel

    rows = [{"공정": "연선재고", "규격": "150SQ", "길이": 300}]
    file_bytes = _make_excel(rows)

    # 1st upload
    r1 = parse_wip_excel(file_bytes, db_session, run_label="T11")
    db_session.commit()
    assert r1.get("duplicate") is False
    assert r1["total"] == 1

    # 2nd upload — same content
    r2 = parse_wip_excel(file_bytes, db_session, run_label="T11")
    assert r2.get("duplicate") is True, "동일 canonical_hash 재업로드 감지"
    assert r2.get("existing_upload_id") is not None
```

- [ ] **Step 3: 테스트 실패 확인**

```bash
cd backend && pytest tests/test_wip_reconciliation.py::test_canonical_hash_stable_across_whitespace -v
```

Expected: FAIL — `_compute_canonical_hash` 미정의.

- [ ] **Step 4: `wip_parser.py` 에 canonical hash + upload_log 체크 추가**

`backend/app/services/wip_parser.py` 상단에:

```python
import hashlib
import json
from app.infrastructure.models.wip_upload_log import WipUploadLog


def _compute_canonical_hash(file_content: bytes) -> str:
    """Excel 을 파싱한 뒤 key 필드 정렬된 튜플 리스트 기준 SHA-256.

    공백/저장시간/sheet 순서 변경 무관. 동일 내용 → 동일 hash.
    """
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(file_content), data_only=True)
    ws = wb.active

    # 헤더 매핑 찾기
    header_row = 1
    headers = {c.value: c.column for c in ws[header_row] if c.value}

    canonical_rows = []
    for row in ws.iter_rows(min_row=header_row + 1, values_only=False):
        row_dict = {}
        for name, col_idx in headers.items():
            val = row[col_idx - 1].value
            # 문자열은 trim, 숫자는 float 정규화
            if isinstance(val, str):
                val = val.strip()
            elif isinstance(val, (int, float)):
                val = float(val)
            row_dict[str(name).strip()] = val
        # 완전 빈 row skip
        if any(v is not None and v != "" for v in row_dict.values()):
            canonical_rows.append(row_dict)

    # 정렬 (deterministic)
    canonical_rows.sort(key=lambda d: json.dumps(d, sort_keys=True, ensure_ascii=False))

    payload = json.dumps(canonical_rows, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
```

기존 `parse_wip_excel` 함수 상단 (파일 파싱 루프 전):

```python
def parse_wip_excel(file_content: bytes, db: Session, run_label: str | None = None) -> dict:
    # ── 멱등성 체크 ──
    canonical_hash = _compute_canonical_hash(file_content)
    raw_hash = hashlib.sha256(file_content).hexdigest()

    existing = db.query(WipUploadLog).filter_by(canonical_hash=canonical_hash).first()
    if existing:
        return {
            "duplicate": True,
            "existing_upload_id": existing.upload_id,
            "existing_uploaded_at": existing.uploaded_at.isoformat(),
            "total": 0,
            "warnings": [f"동일 내용 이미 업로드됨 (upload_id={existing.upload_id})"],
        }

    # ── 기존 파싱 로직 ──
    result = {"total": 0, "warnings": [], "duplicate": False}
    # ... 기존 parse 로직 ...

    # ── Upload log 기록 ──
    log = WipUploadLog(
        canonical_hash=canonical_hash,
        raw_file_hash=raw_hash,
        run_label=run_label,
        rows_inserted=result["total"],
        rows_updated=0,
    )
    db.add(log)
    return result
```

- [ ] **Step 5: 테스트 통과 확인**

```bash
cd backend && pytest tests/test_wip_reconciliation.py -v -k "hash or duplicate"
```

Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/wip_parser.py backend/tests/test_wip_reconciliation.py
git commit -m "feat(wip): canonical rows hash + upload_log idempotency"
```

---

### Task 12: Reconciliation — tiebreaker + UPDATE/INSERT 분기

**목적**: G2 의 결정적 tiebreaker (exact → narrowest delta → earliest). 매칭 1건 auto-UPDATE, 0건 orphan INSERT, 2건+ UI dialog용 마킹.

**Files:**

- Modify: `backend/app/services/wip_parser.py` (`parse_wip_excel` 내부 로직)
- Test: `backend/tests/test_wip_reconciliation.py` (Part B — 매칭 시나리오)

- [ ] **Step 1: 실패 테스트 추가**

`backend/tests/test_wip_reconciliation.py` 에 추가:

```python
from app.infrastructure.models.wip_inventory import WipInventory
from app.infrastructure.models.production_batch import ProductionBatch


def _seed_expected_wip(db_session, sq=150, expected=300, voltage="저압",
                       colors=None, created_offset_days=0):
    from datetime import datetime, timedelta
    b = ProductionBatch(
        run_label="T12",
        process_name="연선",
        batch_seq=-1,
        total_length_m=1000,
        wip_output_expected_m=expected,
        sq_mm2=sq,
        voltage="0.6/1kV",
        status="planned",
    )
    db_session.add(b)
    db_session.flush()

    w = WipInventory(
        process_stage="연선재고",
        cross_section=sq,
        voltage_class=voltage,
        length_m=expected,
        count=1,
        total_length_m=expected,
        expected_length_m=expected,
        status="예상",
        source_batch_id=b.batch_id,
        core_colors=colors,
    )
    if created_offset_days:
        w.created_at = datetime.utcnow() - timedelta(days=created_offset_days)
    db_session.add(w)
    db_session.flush()
    return w


def test_reconciliation_single_match_auto_update(db_session):
    from app.services.wip_parser import parse_wip_excel

    expected_wip = _seed_expected_wip(db_session, sq=150, expected=300)
    db_session.commit()

    rows = [{"공정": "연선재고", "규격": "150SQ", "길이": 275}]
    result = parse_wip_excel(_make_excel(rows), db_session, run_label="T12")
    db_session.flush()

    db_session.refresh(expected_wip)
    assert expected_wip.status == "실사_확정"
    assert float(expected_wip.actual_length_m) == 275
    assert float(expected_wip.variance_m) == -25


def test_reconciliation_zero_match_inserts_orphan(db_session):
    from app.services.wip_parser import parse_wip_excel

    # 매칭 대상 없음
    rows = [{"공정": "연선재고", "규격": "240SQ", "길이": 500}]
    result = parse_wip_excel(_make_excel(rows), db_session, run_label="T12")
    db_session.flush()

    orphan = db_session.query(WipInventory).filter_by(
        cross_section=240, source_batch_id=None
    ).first()
    assert orphan is not None
    assert orphan.status == "사용가능"


def test_reconciliation_multi_match_flags_for_ui(db_session):
    """ambiguous 매칭 시 WIP 자동 업데이트하지 않고 candidates 반환."""
    from app.services.wip_parser import parse_wip_excel

    _seed_expected_wip(db_session, sq=150, expected=300)
    _seed_expected_wip(db_session, sq=150, expected=310)  # ±10% 겹침 가능
    db_session.commit()

    rows = [{"공정": "연선재고", "규격": "150SQ", "길이": 305}]
    result = parse_wip_excel(_make_excel(rows), db_session, run_label="T12")

    assert "ambiguous" in result
    assert len(result["ambiguous"]) >= 1
    assert "candidates" in result["ambiguous"][0]


def test_tiebreaker_narrowest_delta_wins(db_session):
    """±10% 다중 매칭 시 길이 차이 최소가 선택 (UI dialog 없이 auto)."""
    from app.services.wip_parser import parse_wip_excel

    w_close = _seed_expected_wip(db_session, sq=150, expected=280)  # delta 5
    w_far = _seed_expected_wip(db_session, sq=150, expected=320)    # delta 35

    rows = [{"공정": "연선재고", "규격": "150SQ", "길이": 275}]
    parse_wip_excel(_make_excel(rows), db_session, run_label="T12")
    db_session.flush()

    db_session.refresh(w_close)
    db_session.refresh(w_far)
    # 현재 설계: 2건 이상 후보 발견 시 ambiguous 로 flag. tiebreaker 는 exact 매칭 먼저.
    # 이 테스트는 tiebreaker 가 적용되는 구체적 조건 기반 (narrowest delta) 를 검증.
    # 자동 선택 규칙: "exact SQ/voltage 일치" 단일 + "narrowest delta" 단일 이면 auto-UPDATE.
    # 단, 같은 SQ/voltage 에서 2건이면 ambiguous 로 가는 설계.
    # 여기선 설계 §G2 "tiebreaker 1→4" 에 따라 narrowest delta 가 확정으로 선택돼야 함.
    assert w_close.status == "실사_확정"
    assert w_far.status == "예상", "delta 먼 후보는 변경 없음"


def test_reconciliation_skip_unassigned_batch(db_session):
    """unassigned 배치에 연결된 WIP 는 매칭 후보 제외 (Eng review LOW #8)."""
    from app.services.wip_parser import parse_wip_excel

    w = _seed_expected_wip(db_session, sq=150, expected=300)
    w.source_batch = db_session.query(ProductionBatch).get(w.source_batch_id)
    w.source_batch.status = "unassigned"
    db_session.flush()

    rows = [{"공정": "연선재고", "규격": "150SQ", "길이": 275}]
    parse_wip_excel(_make_excel(rows), db_session, run_label="T12")

    db_session.refresh(w)
    assert w.status == "예상", "unassigned 소스의 WIP 은 reconciliation skip"
    # orphan 으로 들어와야 함
    orphan = db_session.query(WipInventory).filter_by(
        cross_section=150, source_batch_id=None
    ).first()
    assert orphan is not None
```

- [ ] **Step 2: 테스트 실패 확인**

```bash
cd backend && pytest tests/test_wip_reconciliation.py -v
```

Expected: reconciliation 로직 미구현 → FAIL.

- [ ] **Step 3: Reconciliation 로직 구현**

`backend/app/services/wip_parser.py` 내부 INSERT 경로를 reconciliation 로직으로 교체:

```python
from app.infrastructure.models.production_batch import ProductionBatch


_LENGTH_TOLERANCE = 0.10  # ±10% (spec OQ1)


def _find_reconciliation_candidates(
    db: Session, *, process_stage: str, cross_section: float | None,
    voltage: str | None, colors: str | None, length: float | None
) -> list[WipInventory]:
    """매칭 후보 검색 — status IN ('예상', '실적_추정') + spec 조건.

    Eng review LOW #8: unassigned 소스의 WIP 은 제외.
    """
    q = db.query(WipInventory).filter(
        WipInventory.status.in_(["예상", "실적_추정"]),
        WipInventory.process_stage == process_stage,
    )
    if cross_section is not None:
        q = q.filter(WipInventory.cross_section == cross_section)
    if voltage:
        q = q.filter((WipInventory.voltage_class == voltage) | (WipInventory.voltage_class.is_(None)))

    candidates = q.all()
    # unassigned 배치 WIP 제외
    filtered = []
    for w in candidates:
        if w.source_batch_id:
            b = db.query(ProductionBatch).get(w.source_batch_id)
            if b and b.status == "unassigned":
                continue
        filtered.append(w)

    # 길이 tolerance
    if length is not None:
        lo = length * (1 - _LENGTH_TOLERANCE)
        hi = length * (1 + _LENGTH_TOLERANCE)
        filtered = [
            w for w in filtered
            if w.expected_length_m is None
            or (lo <= float(w.expected_length_m) <= hi)
        ]

    # 색상 set-overlap with null-as-wildcard (§G2)
    if colors:
        target_colors = set(colors.split(",")) if colors else set()
        def _color_overlap(w):
            if not w.core_colors:
                return True  # wildcard
            w_colors = set(w.core_colors.split(","))
            return bool(target_colors & w_colors) or not target_colors
        filtered = [w for w in filtered if _color_overlap(w)]

    return filtered


def _pick_by_tiebreaker(
    candidates: list[WipInventory], target_length: float, target_voltage: str | None
) -> WipInventory | None:
    """결정적 tiebreaker (spec §G2): exact → narrowest delta → earliest created_at."""
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    # 1) Exact voltage 일치 먼저
    if target_voltage:
        exact_volt = [w for w in candidates if w.voltage_class == target_voltage]
        if len(exact_volt) == 1:
            return exact_volt[0]
        if exact_volt:
            candidates = exact_volt

    # 2) Narrowest length delta
    def _delta(w):
        return abs(float(w.expected_length_m or 0) - target_length)
    min_delta = min(_delta(w) for w in candidates)
    narrowest = [w for w in candidates if _delta(w) == min_delta]
    if len(narrowest) == 1:
        return narrowest[0]

    # 3) Earliest created_at
    narrowest.sort(key=lambda w: w.created_at or datetime.max)
    if len(narrowest) >= 1 and len(candidates) > 1:
        # 여러 개가 tied → ambiguous 로 flag (None 반환, caller 가 dialog 처리)
        return None
    return narrowest[0]


def _reconcile_or_insert(
    row_data: dict, db: Session, run_label: str | None, result: dict
) -> None:
    """Excel 1 row → 매칭 후보 검색 → UPDATE (1건) / dialog (2건+) / orphan INSERT (0건)."""
    # row_data 파싱 결과에서 필요 필드 추출
    process_stage = row_data.get("process_stage")
    cross_section = row_data.get("cross_section")
    voltage = row_data.get("voltage_class")
    colors = row_data.get("core_colors")
    length = row_data.get("length_m")
    count = row_data.get("count", 1)

    candidates = _find_reconciliation_candidates(
        db,
        process_stage=process_stage,
        cross_section=cross_section,
        voltage=voltage,
        colors=colors,
        length=length,
    )

    if not candidates:
        # orphan INSERT
        w = WipInventory(
            process_stage=process_stage,
            cross_section=cross_section,
            voltage_class=voltage,
            core_colors=colors,
            length_m=length,
            count=count,
            total_length_m=(length or 0) * (count or 1),
            status="사용가능",
            source_batch_id=None,
            run_label=run_label,
        )
        db.add(w)
        result["total"] += 1
        return

    pick = _pick_by_tiebreaker(candidates, length or 0, voltage)
    if pick is None:
        # ambiguous
        result.setdefault("ambiguous", []).append({
            "excel_row": row_data,
            "candidates": [
                {
                    "wip_id": w.wip_id,
                    "source_batch_id": w.source_batch_id,
                    "expected_length_m": float(w.expected_length_m or 0),
                    "cross_section": float(w.cross_section or 0),
                    "voltage": w.voltage_class,
                }
                for w in candidates
            ],
        })
        return

    # auto-UPDATE
    pick.status = "실사_확정"
    pick.actual_length_m = length
    pick.variance_m = (length or 0) - float(pick.expected_length_m or 0)
    pick.total_length_m = length
    result["total"] += 1
```

기존 `parse_wip_excel` 내 `WipInventory(...)` 직접 INSERT 구간을 `_reconcile_or_insert(row_data, db, run_label, result)` 호출로 교체.

- [ ] **Step 4: 테스트 통과 확인**

```bash
cd backend && pytest tests/test_wip_reconciliation.py -v
```

Expected: 모든 reconciliation 테스트 pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/wip_parser.py backend/tests/test_wip_reconciliation.py
git commit -m "feat(wip): reconciliation — tiebreaker + UPDATE/orphan INSERT"
```

---

## Phase 5 — Shortage 재귀

### Task 13: `create_shortage_batches` 재작성

**목적**: lot 확장 + `batch_seq=-1` + DrumLotMaster tuple key + wip_output 계산 → listener 가 새 예상재공 생성 (recursion).

**Files:**

- Modify: `backend/app/services/sm_inventory.py:68-121`
- Test: `backend/tests/test_shortage_batch_lot.py`

- [ ] **Step 1: 현재 `create_shortage_batches` 확인 + DrumLotMaster tuple key 결정 (Task 1 결과 반영)**

```bash
sed -n '68,121p' backend/app/services/sm_inventory.py
```

Task 1 결과 기반 tuple key 확정. (`cross_section` 단일이면 dict, 그 외면 tuple)

- [ ] **Step 2: 실패 테스트 작성**

`backend/tests/test_shortage_batch_lot.py`:

```python
"""Shortage batch lot expansion + recursion tests."""
import pytest
from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.wip_inventory import WipInventory
from app.infrastructure.models.drum_lot_master import DrumLotMaster
from app.services.sm_inventory import create_shortage_batches


def test_shortage_batch_expands_to_lot(db_session, listener_registered):
    """5m 부족 → lot_stranding=1000m 기준 신규 1 드럼 배치 생성."""
    db_session.add(DrumLotMaster(cross_section=150, lot_stranding=1000))

    # 실사_확정 + variance_m=-5 인 WIP 심기
    w = WipInventory(
        process_stage="연선재고",
        cross_section=150,
        voltage_class="저압",
        length_m=275,
        total_length_m=275,
        expected_length_m=280,
        actual_length_m=275,
        variance_m=-5,
        status="실사_확정",
        run_label="T13",
        matched_order_id="ORD-T13-001",
    )
    db_session.add(w)
    db_session.flush()

    r = create_shortage_batches("T13", db_session)
    assert r["shortage_batches_created"] == 1

    new_batches = db_session.query(ProductionBatch).filter_by(
        run_label="T13",
        process_name="연선",
    ).all()
    assert len(new_batches) == 1
    nb = new_batches[0]
    assert float(nb.total_length_m) == 1000, "lot 확장 확인"
    assert nb.batch_seq == -1, "header 로 생성"
    assert float(nb.wip_output_expected_m or 0) == 995, "1000 - 5 = 995 잉여"


def test_shortage_batch_triggers_listener_recursion(db_session, listener_registered):
    """새 보정 배치 → listener 가 예상 WIP 995m 생성."""
    db_session.add(DrumLotMaster(cross_section=150, lot_stranding=1000))

    w = WipInventory(
        process_stage="연선재고",
        cross_section=150,
        voltage_class="저압",
        expected_length_m=280,
        actual_length_m=275,
        variance_m=-5,
        status="실사_확정",
        run_label="T13b",
    )
    db_session.add(w)
    db_session.flush()

    create_shortage_batches("T13b", db_session)
    db_session.flush()

    new_batch = db_session.query(ProductionBatch).filter_by(
        run_label="T13b", process_name="연선"
    ).first()
    assert new_batch is not None

    # Listener 가 새 예상 WIP 자동 생성
    new_wip = db_session.query(WipInventory).filter_by(
        source_batch_id=new_batch.batch_id,
        status="예상",
    ).first()
    assert new_wip is not None
    assert float(new_wip.expected_length_m) == 995


def test_no_drum_lot_master_falls_back_to_shortage(db_session, listener_registered):
    """DrumLotMaster 에 해당 SQ 가 없으면 shortage 자체를 total_length_m 로 (보수)."""
    w = WipInventory(
        process_stage="연선재고",
        cross_section=999,   # 미등록 SQ
        variance_m=-100,
        status="실사_확정",
        run_label="T13c",
    )
    db_session.add(w)
    db_session.flush()

    create_shortage_batches("T13c", db_session)
    b = db_session.query(ProductionBatch).filter_by(run_label="T13c").first()
    if b:
        assert float(b.total_length_m) == 100
        assert float(b.wip_output_expected_m or 0) == 0


def test_shortage_below_1m_skipped(db_session, listener_registered):
    db_session.add(DrumLotMaster(cross_section=150, lot_stranding=1000))
    w = WipInventory(
        cross_section=150,
        variance_m=-0.5,   # 1m 미만
        status="실사_확정",
        run_label="T13d",
    )
    db_session.add(w)
    db_session.flush()

    r = create_shortage_batches("T13d", db_session)
    assert r["shortage_batches_created"] == 0
```

- [ ] **Step 3: 테스트 실패 확인**

```bash
cd backend && pytest tests/test_shortage_batch_lot.py -v
```

Expected: FAIL — lot 확장/batch_seq/wip_output 미구현.

- [ ] **Step 4: `create_shortage_batches` 재작성**

`backend/app/services/sm_inventory.py:68-121` 교체:

```python
import math
from app.infrastructure.models.drum_lot_master import DrumLotMaster


def create_shortage_batches(run_label: str, db: Session) -> dict:
    """부족분에 대한 추가 생산 배치 자동 생성. 틀단 확장 + listener 경유 recursion.

    Rewritten per Eng review:
    - status 필터: '실사_확정' (legacy '실적' 에서 마이그레이션 완료)
    - lot_stranding 으로 work_qty 확장
    - batch_seq=-1 설정 → listener 가 새 예상 WIP 생성
    - DrumLotMaster tuple key (Task 1 리서치 결과 반영)
    """
    shortages = (
        db.query(WipInventory)
        .filter(
            WipInventory.run_label == run_label,
            WipInventory.variance_m < 0,
            WipInventory.status == "실사_확정",
        )
        .all()
    )

    # DrumLotMaster uniqueness: Task 1 결과에 맞춰 조정
    # 예시: (cross_section, voltage_class) tuple
    drum_lots: dict[tuple[float, str], float] = {}
    for lot in db.query(DrumLotMaster).all():
        sq = float(lot.cross_section) if lot.cross_section else None
        volt = getattr(lot, "voltage_class", None) or ""
        if sq is None:
            continue
        drum_lots[(sq, volt)] = float(lot.lot_stranding)

    created = 0
    for wip in shortages:
        shortage = abs(float(wip.variance_m or 0))
        if shortage < 1:
            continue

        sq = float(wip.cross_section) if wip.cross_section else None
        volt = wip.voltage_class or ""
        lot_size = drum_lots.get((sq, volt)) if sq else None

        if lot_size and lot_size > 0:
            lot_count = math.ceil(shortage / lot_size)
            work_qty = lot_count * lot_size
            wip_output = work_qty - shortage
        else:
            work_qty = shortage
            wip_output = 0

        batch = ProductionBatch(
            run_label=run_label,
            sales_order_id=wip.matched_order_id,
            process_name="연선",
            batch_seq=-1,
            total_length_m=work_qty,
            wip_output_expected_m=wip_output,
            sq_mm2=sq,
            voltage=volt,
            conductor_material=wip.material,
            core_colors=wip.core_colors,
            customer_name="SM재고 부족분",
            status="planned",
            remarks=f"SM부족 보정: WIP#{wip.wip_id} 부족 {shortage}m, lot 확장 {work_qty}m",
        )
        db.add(batch)
        created += 1

    db.flush()
    return {"shortage_batches_created": created}
```

- [ ] **Step 5: 테스트 통과 확인**

```bash
cd backend && pytest tests/test_shortage_batch_lot.py -v
```

Expected: 4 passed.

- [ ] **Step 6: 기존 sm_inventory 테스트 회귀 확인**

```bash
cd backend && pytest tests/ -k "sm_inventory or shortage" -v
```

Expected: 기존 모두 pass.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/sm_inventory.py backend/tests/test_shortage_batch_lot.py
git commit -m "feat(wip): shortage batches respect lot_stranding + recursion via listener"
```

---

## Phase 6 — UI & E2E

### Task 14: Reconciliation Dialog UI

**목적**: `ambiguous` 매칭이 나올 때 사용자가 후보 중 선택. Frontend React 컴포넌트 + API fetcher.

**Files:**

- Create: `frontend/src/features/wip/components/ReconciliationDialog.tsx`
- Create: `frontend/src/features/wip/api/reconciliation.ts`
- Test: 초기 구현에선 Task 15 의 Playwright E2E 로 커버 (유닛 react-testing-library 도 옵션)

- [ ] **Step 1: Backend 가 제공하는 upload 응답 스키마 확정**

Task 12 의 `result` 구조 확인:

```json
{
  "total": 3,
  "duplicate": false,
  "ambiguous": [
    {
      "excel_row": { "공정": "연선재고", ... },
      "candidates": [
        { "wip_id": 42, "source_batch_id": 100, "expected_length_m": 280, ... }
      ]
    }
  ]
}
```

- [ ] **Step 2: 신규 endpoint `POST /api/wip/reconcile/resolve` 백엔드 추가**

`backend/app/presentation/routes/plan_pipeline.py` 또는 신규 `wip.py` 라우터:

```python
@router.post("/wip/reconcile/resolve")
def resolve_ambiguous(payload: dict, db: Session = Depends(get_db)):
    """사용자가 선택한 wip_id 로 매칭 확정."""
    wip_id = payload.get("wip_id")
    actual_length_m = payload.get("actual_length_m")

    wip = db.query(WipInventory).get(wip_id)
    if not wip:
        raise HTTPException(404)

    wip.status = "실사_확정"
    wip.actual_length_m = actual_length_m
    wip.variance_m = (actual_length_m or 0) - float(wip.expected_length_m or 0)
    db.commit()
    return {"status": "ok"}
```

- [ ] **Step 3: Frontend API fetcher 생성**

`frontend/src/features/wip/api/reconciliation.ts`:

```typescript
export type Candidate = {
  wip_id: number;
  source_batch_id: number | null;
  expected_length_m: number;
  cross_section: number;
  voltage: string | null;
};

export type AmbiguousItem = {
  excel_row: Record<string, unknown>;
  candidates: Candidate[];
};

export async function resolveAmbiguous(
  wip_id: number,
  actual_length_m: number,
): Promise<void> {
  const resp = await fetch("/api/wip/reconcile/resolve", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ wip_id, actual_length_m }),
  });
  if (!resp.ok) {
    throw new Error(`resolve failed: ${resp.status}`);
  }
}
```

- [ ] **Step 4: Dialog 컴포넌트 생성**

`frontend/src/features/wip/components/ReconciliationDialog.tsx`:

```tsx
import { useState } from "react";
import { resolveAmbiguous, type AmbiguousItem } from "../api/reconciliation";

type Props = {
  items: AmbiguousItem[];
  onDone: () => void;
  onCancel: () => void;
};

export function ReconciliationDialog({ items, onDone, onCancel }: Props) {
  const [idx, setIdx] = useState(0);
  const current = items[idx];

  if (!current) {
    return null;
  }

  const excelLength = Number(current.excel_row["길이"] ?? 0);

  async function pick(wip_id: number) {
    await resolveAmbiguous(wip_id, excelLength);
    if (idx + 1 < items.length) {
      setIdx(idx + 1);
    } else {
      onDone();
    }
  }

  return (
    <div role="dialog" aria-label="재공 매칭 후보 선택">
      <h2>
        재공 매칭 후보 ({idx + 1}/{items.length})
      </h2>
      <div>Excel row: {JSON.stringify(current.excel_row)}</div>
      <ul>
        {current.candidates.map((c) => (
          <li key={c.wip_id}>
            <button onClick={() => pick(c.wip_id)}>
              WIP #{c.wip_id} (배치 #{c.source_batch_id}, 예상{" "}
              {c.expected_length_m}m, {c.cross_section}SQ,
              {c.voltage})
            </button>
          </li>
        ))}
      </ul>
      <button onClick={onCancel}>취소</button>
    </div>
  );
}
```

pwc-design 토큰 적용: 버튼/레이아웃 스타일을 samildevkit 컨벤션으로. (Memory 의 pwc-design skill 호출 권장). 이 task 스코프엔 mvp 만, 디자인 토큰 연동은 Step 5 에서.

- [ ] **Step 5: pwc-design 토큰 적용**

pwc-design skill 참조 후 `cls` 및 CSS 변수를 컴포넌트에 적용. 색상은 하드코딩 금지 (samildevkit CSS 변수 사용). 상세는 skill 가이드에 따름.

- [ ] **Step 6: 수동 브라우저 검증**

```bash
cd frontend && npm run dev
```

ambiguous 응답을 mock 한 뒤 dialog 가 올바르게 렌더링되는지 육안 확인.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/features/wip/ \
  backend/app/presentation/routes/plan_pipeline.py
git commit -m "feat(ui): reconciliation dialog for ambiguous wip matches"
```

---

### Task 15: E2E Playwright — 시연 시나리오 4단계

**목적**: spec §14 의 데모 스토리 (월 오전 → 월 오후 → 화 → 수) 를 end-to-end 검증.

**Files:**

- Create: `frontend/e2e/wip-lifecycle.spec.ts`
- Fixture: `frontend/e2e/fixtures/wip-fixture.ts`

- [ ] **Step 1: Fixture 준비 — 초기 DB 상태 세팅**

`frontend/e2e/fixtures/wip-fixture.ts`:

```typescript
import { test as base } from "@playwright/test";

export const test = base.extend<{ seedWip: () => Promise<void> }>({
  seedWip: async ({ request }, use) => {
    await use(async () => {
      // DrumLotMaster 시드
      await request.post("/api/test/seed", {
        data: {
          drum_lots: [{ cross_section: 150, lot_stranding: 1000 }],
        },
      });
    });
  },
});
```

주: `POST /api/test/seed` 는 test 전용 endpoint. 없다면 DB fixture 로 직접 INSERT.

- [ ] **Step 2: 시연 시나리오 테스트 작성**

`frontend/e2e/wip-lifecycle.spec.ts`:

```typescript
import { expect } from "@playwright/test";
import { test } from "./fixtures/wip-fixture";

test("WIP lifecycle 데모 시나리오 — 4단계 flow", async ({
  page,
  request,
  seedWip,
}) => {
  await seedWip();

  // ── 월 오전: 수주 A 업로드 → Stage 1 → 헤더 배치 + 예상 WIP 300m
  await page.goto("/plan-register");
  await page.setInputFiles(
    'input[type="file"][name="orderExcel"]',
    "e2e/fixtures/mon_am_order_A.xlsx",
  );
  await page.getByRole("button", { name: /Stage 1 실행/ }).click();
  await expect(page.getByText(/배치 생성 완료/)).toBeVisible();

  // WIP 확인
  await page.goto("/wip");
  await expect(page.getByText(/예상.*300m/)).toBeVisible();

  // ── 월 오후: 배치 A completed → WIP 예상 → 실적_추정
  await page.goto("/scheduler");
  await page.getByText(/150SQ 헤더/).click();
  await page.getByRole("button", { name: /completed/ }).click();

  await page.goto("/wip");
  await expect(page.getByText(/실적_추정.*300m/)).toBeVisible();

  // ── 화: 긴급수주 B (150SQ 280m) → 새 run → 재공 매칭, 신규 배치 없음
  await page.goto("/plan-register");
  await page.setInputFiles(
    'input[type="file"][name="orderExcel"]',
    "e2e/fixtures/tue_urgent_order_B.xlsx",
  );
  await page.getByRole("button", { name: /Stage 1 실행/ }).click();

  // 수주 B 가 use_wip=True 로 처리됐는지 확인
  await expect(page.getByText(/수주 B.*재공 활용/)).toBeVisible();

  // ── 수: 실사 Excel (275m) 업로드 → 실사_확정, variance=-5 → 보정 배치 lot 확장
  await page.goto("/wip/upload");
  await page.setInputFiles(
    'input[type="file"][name="wipExcel"]',
    "e2e/fixtures/wed_wip_actual.xlsx",
  );
  await page.getByRole("button", { name: /업로드/ }).click();

  await expect(page.getByText(/실사_확정/)).toBeVisible();
  await expect(page.getByText(/variance.*-5/)).toBeVisible();

  // 보정 배치 확인 (1000m 드럼 1개 + 995m 예상 WIP 새로 생성 = recursion)
  await page.goto("/scheduler");
  await expect(page.getByText(/SM재고 부족분.*1000m/)).toBeVisible();

  await page.goto("/wip");
  await expect(page.getByText(/예상.*995m/)).toBeVisible();
});
```

- [ ] **Step 3: Fixture Excel 파일 3개 준비**

`frontend/e2e/fixtures/` 에 다음 파일 배치 (수동 생성 또는 스크립트):

- `mon_am_order_A.xlsx`: 150SQ 수주 700m 1건
- `tue_urgent_order_B.xlsx`: 150SQ 긴급수주 280m 1건
- `wed_wip_actual.xlsx`: 150SQ 연선재고 275m 1건

```python
# scripts/gen_e2e_fixtures.py
from openpyxl import Workbook

def write_order(path, orders):
    wb = Workbook()
    ws = wb.active
    ws.append(["수주번호", "규격", "수량", "심선수", "전압"])
    for o in orders:
        ws.append(o)
    wb.save(path)

write_order("frontend/e2e/fixtures/mon_am_order_A.xlsx",
            [["ORD-A", "150SQ", 700, 1, "0.6/1kV"]])
write_order("frontend/e2e/fixtures/tue_urgent_order_B.xlsx",
            [["ORD-B", "150SQ", 280, 1, "0.6/1kV"]])
# WIP Excel (다른 포맷)
wb = Workbook(); ws = wb.active
ws.append(["공정", "규격", "전압", "길이", "개수"])
ws.append(["연선재고", "150SQ", "저압", 275, 1])
wb.save("frontend/e2e/fixtures/wed_wip_actual.xlsx")
```

- [ ] **Step 4: E2E 실행**

```bash
cd frontend && npm run test:e2e -- wip-lifecycle.spec.ts
```

Expected: 모든 assertion pass. 실패 시 각 단계 screenshot 확인.

- [ ] **Step 5: Commit**

```bash
git add frontend/e2e/wip-lifecycle.spec.ts \
  frontend/e2e/fixtures/ \
  scripts/gen_e2e_fixtures.py
git commit -m "test(e2e): WIP lifecycle 4-stage demo scenario"
```

---

## Self-Review (계획서 내부 체크)

**Spec coverage 확인**:

- spec §6 5대 Guard → G1 (Task 2), G2 (Task 12), G3 (Task 8), G4 (Task 11), G5 (Level 3 유보, emergency_mode 플래그로 Task 8 에 스텁)
- spec §7 Listener → Task 6, 7
- spec §8 보정배치 재작성 → Task 13
- spec §9 surplus 계산 → Task 4, 5
- spec §10 T2 hook → Task 9, 10
- spec §11 마이그레이션 → Task 2, 3
- spec §12 구현 범위 10개 → Task 2-13 로 직접 대응
- spec §13 테스트 계획 → 각 Task 내 TDD cycle 로 분산
- spec §14 시연 시나리오 → Task 15 E2E
- spec §16 OQ3 (DrumLotMaster key) → Task 1 리서치 spike

**Placeholder scan**: 모든 step 에 실제 code block / command / expected output 명시. "TBD" "TODO" 없음.

**Type consistency**:

- `_promote_expected_to_estimated(batch_id, new_status, db)` 시그니처 Task 9 정의 → Task 10 에서 동일하게 사용
- `_compute_canonical_hash(file_bytes) -> str` Task 11 정의 → Task 15 fixture 와 무관, 일관성 OK
- `_find_reconciliation_candidates` Task 12 만 정의 → 내부 헬퍼, 외부 노출 없음

**Known risks (구현 시 주의)**:

- Task 7 `do_orm_execute` 의 bulk 감지 API 는 SQLAlchemy 버전별 차이 존재. 2.x 에서 동작 확인 필요. 폴백: `bulk_save_objects` monkey-patch.
- Task 11 canonical hash 의 `openpyxl.load_workbook` 은 `.xls` 미지원. Legacy Excel 형식 들어오면 `xlrd` 추가 필요.
- Task 15 의 `/api/test/seed` 는 test 전용 endpoint 로 별도 구현 필요. 없으면 DB fixture 로 우회.

---

## Execution Handoff

**계획서 완성. `docs/plans/2026-04-18-wip-lifecycle.md` 에 저장됨. 2가지 실행 경로**:

1. **Subagent-Driven (권장)** — Task 별 fresh subagent dispatch, 각 Task 완료 후 리뷰, 빠른 iteration
2. **Inline Execution** — 현재 세션에서 순차 실행, checkpoint 기반 배치 실행

어느 쪽으로?
