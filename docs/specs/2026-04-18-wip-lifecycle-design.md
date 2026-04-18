# Design: 재공(WIP) Lifecycle — 예상/실적*추정/실사*확정 4단계 + 중복방지 Guard

- **작성일**: 2026-04-18
- **브랜치**: dev_jaewoo
- **작성**: /office-hours 세션 (jaewoo)
- **Status**: APPROVED (Eng review 반영 완료, CEO 제안 Phase 2 유보)
- **Supersedes**: (없음 — 첫 WIP lifecycle 설계서)
- **Review Reports**:
  - `docs/specs/2026-04-18-wip-lifecycle-eng-review.md` (FAIL → PASS_WITH_CHANGES. 3 blockers 본 설계에 반영)
  - `docs/specs/2026-04-18-wip-lifecycle-ceo-review.md` (SELECTIVE EXPANSION. 5개 제안 모두 Phase 2 유보 — scope 우선순위 결정)
- **Related**:
  - `docs/specs/2026-04-17-batch-group-unassign-design.md` (unassign_reason — 본 설계가 status 전이 확장)
  - `backend/alembic/versions/90b349ba1c61_add_sm_inventory_lifecycle_columns.py` (이미 만들어진 컬럼 — 본 설계가 비로소 사용)

---

## 1. Problem Statement

Stage 1 배치 생산은 **드럼 lot (틀단)** 단위로 돌아간다. 수주 환산수량이 lot size에 미달하면 반드시 **잉여 (SM 재공)** 가 발생한다.

예: 150SQ 수주 700m → `DrumLotMaster.lot_stranding = 1000m` → 1000m 드럼 1개 생산, 300m 잉여.

**현 시스템의 구멍**: 이 300m 잉여는 **DB에 기록되지 않는다**. 다음 run에서 동일 SQ 신규 수주(예: 280m)가 들어오면 스케줄러는 300m 잉여의 존재를 모르고 **또 1000m 드럼을 잡는다**. 설비 점유 중복, 원가 낭비, 재고 미관리.

## 2. Evidence (코드 기반, file:line)

1. `backend/app/services/wip_parser.py:185` — `WipInventory` 행은 **Excel 업로드 경로에서만** INSERT. 배치 잉여로부터 생성되는 경로 없음.
2. `backend/app/infrastructure/models/production_batch.py:69` — `wip_output_expected_m` 컬럼 정의되어 있으나 **쓰기 코드 0개** (backend 전체 grep 확인).
3. `backend/app/infrastructure/models/wip_inventory.py:35` — `source_batch_id` FK 정의되어 있으나 값 넣는 코드 없음.
4. `backend/app/services/sm_inventory.py:99-116` — `create_shortage_batches()` 는 (a) `total_length_m=shortage` 로 **틀단 무시**, (b) internal caller 없음 (API endpoint `POST /wip/shortage-batches` 만 존재). 사실상 dead code.
5. `ProductionBatch(...)` 생성 지점 **7곳** 분산:
   - `batch_grouping.py:413-446` (그룹 헤더, `batch_seq=-1`)
   - `batch_grouping.py:474-507` (Phase 1 연선 수주, `batch_seq=1`)
   - `batch_grouping.py:524-561` (61-strand core CU, `batch_seq=0`)
   - `batch_grouping.py:580-616` (AL 61-strand core, `batch_seq=0`)
   - `batch_grouping.py:774-806` (Phase 2 일반 공정, `batch_seq>=1`)
   - `batch_grouping.py:1344-1382` (파이프라인 split 헤더, `batch_seq=-1`)
   - `sm_inventory.py:99-116` (shortage 보정)

## 3. 운영 시나리오 (합의)

- **실사 주기**: 주 3-4회
- **Run 주기**: 주 3-4회 (긴급수주 추가 또는 ERP 전체 갱신)
- **실사 데이터 업데이트**: 주 3-4회
- T1(계획) ↔ T3(실사) gap 최대 2-3일
- Run 사이에 긴급수주가 끼는 경우 상정 → 실사 이전 재공도 매칭 대상에 포함 필요 (= 매칭 기본 레벨2)

## 4. Premises (사용자 합의 완료)

1. 재공 lifecycle 은 4단계: `예상 → 실적_추정 → 실사_확정 → 사용완료`
2. 매칭 기본은 **레벨2**: `실사_확정 + 실적_추정 + 사용가능(orphan)`. `예상` 은 opt-in (emergency_mode 플래그).
3. T2 승격(예상→실적\_추정)은 기존 status-write endpoint 3곳 **모두** 재활용. 자동 전환 없음 (phantom inventory 방지).
4. WIP auto-create 는 **SQLAlchemy `after_insert` event listener** 로 중앙화. **단, 헤더 배치에만 fire** (`batch_seq == -1 AND process_name == "연선"` 게이트 필수. Eng review 블로커 #1).
5. 보정배치(shortage) 는 `DrumLotMaster.lot_stranding` 기준으로 lot 확장 후 생성. listener 경유로 잉여 재귀 처리.
6. 중복방지 5대 Guard 모두 적용. Listener INSERT 는 `ON CONFLICT DO NOTHING` 으로 race-safe.
7. 부분소진 추적(단일 `matched_order_id` → 리스트화) 는 Phase 2 로 유보.

## 5. 회계 프레임 — 상태 → 인식 시점 매핑

| 단계        | WIP status  | 회계 대응            | 생성/전환 트리거                                         | 매칭 허용           |
| ----------- | ----------- | -------------------- | -------------------------------------------------------- | ------------------- |
| T1 계획     | `예상`      | 계약자산/미청구수익  | ProductionBatch INSERT → listener auto-create (헤더만)   | Level 3 opt-in only |
| T2 생산완료 | `실적_추정` | 매출 실현 (발생주의) | status-write endpoint 3곳의 공통 hook → `completed` 승격 | **Level 2 기본**    |
| T3 실사확정 | `실사_확정` | 재고자산 감사확정    | Excel 업로드 + reconciliation 성공                       | **Level 1 기본**    |
| T4 수주소비 | `사용완료`  | 매출원가             | `match_wip()` 성공                                       | 풀에서 제외         |

Orphan (배치 미연결, Excel 에만 존재 — 레거시/외부 조달 재고):

- status = `사용가능` (현재 기본 유지), `source_batch_id = NULL`, UI 태그 "출처미상" 표시

## 6. 중복방지 5대 Guard

### G1 — `source_batch_id` UNIQUE partial index (DB 최후방어)

```sql
CREATE UNIQUE INDEX idx_wip_source_batch_unique
  ON wip_inventory (source_batch_id)
  WHERE source_batch_id IS NOT NULL;
```

배치 1개당 WIP row 최대 1개를 DB가 강제한다. Listener `ON CONFLICT DO NOTHING` 과 짝.

### G2 — Excel Reconciliation (INSERT 전 UPDATE 시도)

`wip_parser.py` 가 Excel row 를 받으면:

1. 후보 검색: `status IN ('예상', '실적_추정')` 중 `(process_stage, cross_section, voltage_class, core_colors, length±tolerance)` 매칭
2. **자동 매칭 tiebreaker** (결정적, Eng review 반영):
   1. Exact SQ / voltage 일치
   2. Narrowest length delta
   3. Earliest `created_at`
   4. 위에서도 1건 이하로 좁혀지지 않으면 UI dialog
3. 매칭 1건 확정 → auto-UPDATE → `status='실사_확정', actual_length_m=Excel값, variance_m=actual-expected`
4. 매칭 0건: orphan INSERT (`status='사용가능', source_batch_id=NULL`)

**색상 매칭 의미론**: set-overlap with null-as-wildcard. `colors(wip) ∩ colors(excel) ≠ ∅ OR 한쪽이 NULL`. 기존 `wip_matching.py:123-129` 의 substring 룰과 일관.

**Length tolerance 기본**: ±10% (운영 중 `DecisionCriteria` 로 조정).

### G3 — 매칭 기본 status 필터

```python
# wip_matching.py:41 수정
DEFAULT_STATUSES = ["사용가능", "실사_확정", "실적_추정"]  # Level 2
if emergency_mode:
    DEFAULT_STATUSES.append("예상")  # Level 3, G5 temporal guard 필수
wip_query = db.query(WipInventory).filter(WipInventory.status.in_(DEFAULT_STATUSES))
```

**Cross-run policy**: WIP 풀은 run_label 로 필터링하지 **않는다**. 이전 run 의 재공이 다음 run 의 수주에 매칭 가능. 이는 by design (설계의도 명시).

### G4 — Excel 멱등성 (`wip_upload_log` 테이블, **canonical rows hash**)

```python
class WipUploadLog(Base):
    __tablename__ = "wip_upload_log"
    upload_id = Column(Integer, primary_key=True)
    canonical_hash = Column(String(64), unique=True, nullable=False)  # SHA-256 of parsed rows
    raw_file_hash = Column(String(64))  # SHA-256 of raw bytes (debugging)
    run_label = Column(String(50))
    uploaded_at = Column(DateTime, default=datetime.utcnow)
    rows_inserted = Column(Integer, default=0)
    rows_updated = Column(Integer, default=0)
```

**canonical_hash** 는 Excel 을 파싱한 뒤 key 필드 정렬된 튜플 리스트 기준으로 계산 (공백/저장시간/sheet 순서 변경 무관). raw_file_hash 는 디버깅용. 동일 canonical_hash 재업로드 시 → "이미 처리된 파일입니다. 다시 반영?" 확인.

### G5 — Temporal Guard (Level 3 opt-in 시에만)

```python
if wip.status == "예상":
    source_batch = db.query(ProductionBatch).get(wip.source_batch_id)
    if source_batch.scheduled_end_datetime >= target_order.earliest_start:
        continue  # 순서역전 방지 (phantom inventory)
```

## 7. 설계 핵심 — `after_insert` Listener (**게이트 + ON CONFLICT**)

```python
# backend/app/services/wip_lifecycle_listener.py (신규)
from sqlalchemy import event
from sqlalchemy.dialects.postgresql import insert as pg_insert
from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.wip_inventory import WipInventory

# Eng review 블로커 #1: 헤더 배치에만 fire. 7개 생성지점 중 2개 (정규 헤더 + split 헤더) 만 조건 충족.
_WIP_GATE_PROCESSES = {"연선"}

@event.listens_for(ProductionBatch, 'after_insert')
def auto_create_expected_wip(mapper, connection, batch):
    """ProductionBatch INSERT 시 예상재공 WIP 자동 생성.

    게이트 (Eng review 블로커 #1):
      - batch_seq == -1 (그룹 헤더만. per-order / 61-strand / Phase 2 공정 제외)
      - process_name == "연선" (Stage 1 드럼 잉여만)
      - wip_output_expected_m > 0

    INSERT 정책:
      - connection.execute + PostgreSQL ON CONFLICT (source_batch_id) DO NOTHING
      - IntegrityError 전파로 outer flush abort 되는 것 방지 (Eng review Medium #4)
      - ORM session 우회 → WipInventory 쪽 after_insert listener 는 발화 안 함 (설계 의도)
    """
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
        core_colors=batch.core_colors,
        length_m=expected,
        count=1,
        total_length_m=expected,
        expected_length_m=expected,
        status="예상",
        source_batch_id=batch.batch_id,
        run_label=batch.run_label,
    ).on_conflict_do_nothing(index_elements=["source_batch_id"])
    connection.execute(stmt)
```

**Listener 등록 지점 (Eng review Low #8)**: `backend/app/infrastructure/database.py` 의 `Base` 선언 직후. 앱 시작 시 import 한 번만 발생하면 SQLAlchemy 가 전역 이벤트로 등록.

**Bulk API 금지 (Eng review Low #8)**: `Session.bulk_save_objects()` / `Session.bulk_insert_mappings()` 사용 시 after_insert listener **skip됨**. Stage 1 코드는 현재 `db.add()` 기반이라 안전하나, 향후 성능 최적화 명목으로 bulk API 전환 시 listener bypass 발생. 방지책:

```python
# backend/app/infrastructure/database.py
@event.listens_for(Session, 'do_orm_execute')
def _block_bulk_batch_insert(state):
    """ProductionBatch 에 대한 bulk insert 경로 차단."""
    # bulk_save_objects / bulk_insert_mappings 감지 로직
    ...
```

## 8. 보정배치(`create_shortage_batches`) 재작성 (Approach A)

```python
# backend/app/services/sm_inventory.py 수정
def create_shortage_batches(run_label: str, db: Session) -> dict:
    shortages = db.query(WipInventory).filter(
        WipInventory.run_label == run_label,
        WipInventory.variance_m < 0,
        WipInventory.status == "실사_확정",  # '실적' → '실사_확정' migration 필수
    ).all()

    # Eng review Medium #7: DrumLotMaster uniqueness 확인 후 tuple key 사용
    drum_lots = {}
    for lot in db.query(DrumLotMaster).all():
        key = (float(lot.cross_section), lot.voltage_class or "")  # 실제 uniqueness 검증 후 조정
        drum_lots[key] = float(lot.lot_stranding)

    created = 0
    for wip in shortages:
        shortage = abs(float(wip.variance_m))
        if shortage < 1:
            continue

        sq = float(wip.cross_section) if wip.cross_section else None
        volt = wip.voltage_class or ""
        lot_size = drum_lots.get((sq, volt)) if sq else None
        if lot_size and lot_size > 0:
            lot_count = math.ceil(shortage / lot_size)
            work_qty = lot_count * lot_size
            wip_output = work_qty - shortage   # 잉여분, listener 가 예상재공 생성
        else:
            work_qty = shortage
            wip_output = 0

        batch = ProductionBatch(
            run_label=run_label,
            sales_order_id=wip.matched_order_id,
            process_name=wip.process_stage or "연선",
            batch_seq=-1,  # listener 게이트 통과 필요
            total_length_m=work_qty,
            wip_output_expected_m=wip_output,  # listener 트리거
            sq_mm2=sq,
            voltage=volt,
            conductor_material=wip.material,
            core_colors=wip.core_colors,
            status="planned",
        )
        db.add(batch)
        created += 1
    db.flush()
    return {"shortage_batches_created": created}
```

## 9. `wip_output_expected_m` 계산식 — **헤더 배치만**

Eng review 블로커 #1 의 결과: 공식은 **그룹 헤더 (batch_seq=-1) 에만 적용**. 나머지 6개 생성지점에선 **설정하지 않음** (기본값 0 유지).

### 9-1. 정규 그룹 헤더 (`batch_grouping.py:413-446`)

```python
# 그룹 단위로 한 번 계산된 값을 헤더에 기록
wip_output_expected_m = max(0, work_qty_g - net_qty_g)
# work_qty_g: batch_grouping.py:364 lot 확장 생산량 (lot_count * lot_size)
# net_qty_g:  batch_grouping.py:350 수주 환산량 (defect_buffer, wip_strand_qty 제외)
```

### 9-2. Split 헤더 (`batch_grouping.py:1344-1382`) — **재계산 필수**

Eng review 블로커 #1: 원 헤더의 surplus 를 proportional 분할하면 틀림. Split 후 실제 lot count 와 수주량으로 재계산:

```python
# split 후 헤더 surplus 재계산
split_orders_conv_qty = sum(
    float(o.ordered_qty_m or 0) * max(int(o.core_count or 1), 1) * (1 + defect_buffer)
    for o in split_orders
)
split_work_qty = split_lot_count * lot_size_g  # split 후 lot 개수
wip_output_expected_m = max(0, split_work_qty - split_orders_conv_qty)
```

### 9-3. 기타 5개 생성지점

per-order(474), 61-strand core CU(524), AL core(580), Phase 2 공정(774), shortage 보정(sm_inventory.py)의 생성자:

- Phase 2, per-order, core 배치: `wip_output_expected_m` 설정하지 않음 (기본 0)
- shortage 보정: 위 §8 참조. `batch_seq=-1` 설정 + wip_output 계산 명시

**Invariant 문서화**: "wip_output_expected_m > 0 인 ProductionBatch 는 반드시 batch_seq=-1 이며 process_name='연선'". 단위 테스트로 보장.

## 10. T2 승격 Hook — **3개 endpoint 공통 헬퍼**

Eng review 블로커 #2: status-write endpoint 가 3곳이므로 모두 hook 필요.

```python
# backend/app/presentation/routes/plan_pipeline.py 공통 헬퍼 추가
def _promote_expected_to_estimated(batch_id: int, new_status: str, db: Session):
    """배치가 `completed` 로 전환될 때 연결된 WIP 를 `예상 → 실적_추정` 승격.

    - completed 로의 전환에만 반응 (wip_complete, in_progress 는 무시)
    - 멱등: 이미 실적_추정 이상이면 no-op
    - Cascade: 만약 이 배치가 non-header 인데 header 가 같이 completed 로 전환되면,
      호출자는 header.batch_id 도 함께 이 함수에 넘겨야 함.
    """
    if new_status != "completed":
        return
    wip = db.query(WipInventory).filter(
        WipInventory.source_batch_id == batch_id,
        WipInventory.status == "예상",
    ).first()
    if not wip:
        return
    wip.status = "실적_추정"
    wip.actual_length_m = wip.expected_length_m  # MES 없음 → 예상=실적 가정
```

이 헬퍼를 **3곳** 에서 호출:

1. **Single endpoint** `PATCH /batch/{batch_id}/status` (plan_pipeline.py:1555)

   ```python
   _promote_expected_to_estimated(batch.batch_id, new_status, db)
   if batch.batch_seq != -1:
       # non-header 가 completed 되면 header 도 cascade → header 의 WIP 도 승격
       header = db.query(ProductionBatch).filter(
           ProductionBatch.batch_group == batch.batch_group,
           ProductionBatch.batch_seq == -1,
       ).first()
       if header and header.status == "completed":
           _promote_expected_to_estimated(header.batch_id, "completed", db)
   ```

2. **Freeform endpoint** `PATCH /batch/{batch_id}` (plan_pipeline.py:1598+)
   body 의 status 필드가 `completed` 로 설정되면 동일 호출.

3. **Bulk endpoint** `PATCH /batch-group/{batch_group}/status` (plan_pipeline.py:1640-1679)
   그룹 내 각 배치에 대해 loop 돌면서 호출. 단일 `db.commit()`.

**주의**: `wip_complete` 상태는 **WIP 매칭으로 공정 skip** 을 의미 (CP-SAT 스케줄러가 설정). `completed` 와 구분되며 승격 hook 에 반응하지 **않는다**. 이미 skip된 배치는 새 잉여를 만들지 않으므로 정합.

## 11. 마이그레이션 Preflight + 데이터 Backfill

Eng review 블로커 #3: alembic 재사용만으론 부족.

### 11-1. Preflight check (마이그레이션 시작 전)

```python
# alembic migration 상단
def upgrade():
    conn = op.get_bind()
    # G1 UNIQUE index 생성 전 중복 확인
    dup = conn.execute(text("""
        SELECT source_batch_id, COUNT(*) as c
        FROM wip_inventory
        WHERE source_batch_id IS NOT NULL
        GROUP BY source_batch_id
        HAVING COUNT(*) > 1
    """)).fetchone()
    if dup:
        raise RuntimeError(
            f"UNIQUE 제약 위반 가능 데이터 발견: source_batch_id={dup.source_batch_id} "
            f"count={dup.c}. 중복 제거 후 재시도."
        )
    # status 라벨 rename
    conn.execute(text(
        "UPDATE wip_inventory SET status = '실사_확정' WHERE status = '실적'"
    ))
    # 이제 UNIQUE index 생성
    op.create_index(
        "idx_wip_source_batch_unique",
        "wip_inventory",
        ["source_batch_id"],
        unique=True,
        postgresql_where=text("source_batch_id IS NOT NULL"),
    )
```

### 11-2. Legacy 데이터 Backfill

PoC 는 legacy 데이터 없음 (dev_jaewoo 브랜치에서 일관 리셋). 배포 환경에 기존 ProductionBatch 가 있다면 별도 backfill 스크립트로 wip_output_expected_m > 0 배치에 대응 WIP row INSERT. 본 설계 scope 밖, 필요 시 Phase 2.

## 12. 구현 범위 (Phase 1 — Eng Review 반영)

| #   | 작업                                                                                                                            | 파일                             | LOC          | 의존   |
| --- | ------------------------------------------------------------------------------------------------------------------------------- | -------------------------------- | ------------ | ------ |
| 1   | `wip_output_expected_m` 계산 로직을 batch_grouping.py 정규 헤더 2곳 (§9-1, §9-2) 에 추가. 나머지 5곳은 **명시적 0 유지 + 주석** | batch_grouping.py                | ~20          | —      |
| 2   | `wip_lifecycle_listener.py` 신규 — `after_insert` listener + 게이트 + `ON CONFLICT DO NOTHING`                                  | wip_lifecycle_listener.py (신규) | ~60          | #1     |
| 3   | Listener 등록 + bulk API 차단 가드                                                                                              | infrastructure/database.py       | ~15          | #2     |
| 4   | Alembic 마이그레이션: G1 UNIQUE index + preflight check + `'실적' → '실사_확정'` rename                                         | alembic 신규 1 파일              | 1 파일       | #2     |
| 5   | `wip_upload_log` 테이블 + **canonical rows hash** 멱등성 체크                                                                   | alembic + wip_parser.py          | ~40 + 테이블 | —      |
| 6   | `wip_parser.py` reconciliation 로직 (후보검색 + tiebreaker + UPDATE/INSERT 분기)                                                | wip_parser.py                    | ~90          | #2, #5 |
| 7   | `wip_matching.py` status 필터 확장 + cross-run 주석                                                                             | wip_matching.py                  | ~25          | —      |
| 8   | `create_shortage_batches()` lot 확장 + `batch_seq=-1` + DrumLotMaster tuple key                                                 | sm_inventory.py                  | ~30          | #1, #2 |
| 9   | T2 승격 헬퍼 + **3 endpoint hook** (single, freeform, bulk) + cascade                                                           | plan_pipeline.py                 | ~45          | #2     |
| 10  | Reconciliation 다이얼로그 UI (애매한 매칭 사용자 선택)                                                                          | frontend                         | ~60          | #6     |

**합계 약 280 LOC + 마이그레이션 2 + 테이블 1 + UI 다이얼로그 1**.

## 13. 테스트 계획 (Eng Review 반영)

### 단위 테스트

- `test_wip_listener.py`:
  - 게이트 ON: header 배치 + `wip_output_expected_m > 0` → WIP 생성
  - 게이트 OFF: non-header, 다른 process, expected=0 → WIP 생성 없음 (Eng review Issue #1)
  - UNIQUE 충돌 → `ON CONFLICT DO NOTHING` 동작 확인
  - **Rollback propagation**: outer transaction rollback → WIP row 도 rollback (Eng review HIGH #3)
- `test_wip_reconciliation.py`:
  - Excel 매칭 0/1/N 후보 시나리오
  - **Tiebreaker**: exact match → narrowest delta → earliest created_at 순서 확정성
  - **Canonical hash 멱등성**: 공백/메타데이터 변경 시에도 동일 hash
  - Length tolerance 경계 (±10%)
  - 색상 set-overlap + null-as-wildcard
- `test_wip_matching_levels.py`: Level 1/2/3 status 필터 동작, cross-run 매칭 검증
- `test_shortage_batch_lot_expansion.py`:
  - `DrumLotMaster` tuple key 정확도 (multi-row per SQ)
  - 보정배치가 `batch_seq=-1` + `wip_output_expected_m` 설정 → listener 트리거 → recursion
- `test_batch_status_hook.py`:
  - 3개 endpoint (single, freeform, bulk) 모두 hook 발화 (Eng review HIGH #2)
  - Cascade: non-header completed → header 도 승격 시 WIP 승격
  - 멱등성: 이미 실적\_추정 이면 no-op
  - `wip_complete` → hook 무반응
- `test_migration_preflight.py`:
  - 중복 `source_batch_id` 있으면 upgrade abort
  - `'실적'` → `'실사_확정'` rename 정확도
- `test_unassign_cascade.py`: unassigned 배치의 WIP 처리 (filter out during reconciliation, Eng review LOW #8)

### E2E 테스트 (Playwright)

- 시연 시나리오 4단계 전체 flow (§14)
- Reconciliation 다이얼로그 UI (매칭 후보 여러 건 선택)

## 14. 검증 시나리오 (시연 스토리)

1. **월 오전** — 수주 A(150SQ 700m) 업로드 → Stage 1 → 1000m 드럼 배치 (`batch_seq=-1` 헤더)
   - Listener 게이트 통과 → `예상 WIP 300m` auto-create (`source_batch_id=A, status='예상'`)
2. **월 오후** — 운영자가 UI 에서 배치 A `completed` 클릭
   - `/batch/{id}/status` endpoint → 공통 헬퍼 → WIP `예상 → 실적_추정`
3. **화** — 긴급수주 B(150SQ 280m) 들어옴 → 새 run
   - `match_wip()` Level 2 기본값 → 실적\_추정 WIP 280m 소비
   - 수주 B `use_wip=True` → 신규 배치 생성 안 됨
4. **수** — 재공실사 Excel(실측 275m) 업로드
   - Reconciliation tiebreaker → source*batch=A 매칭 → `실사*확정`, `variance_m=-5`
   - `create_shortage_batches()` → lot_stranding 1000m 기준 신규 1 드럼 배치 (batch_seq=-1, wip_output_expected_m=995)
   - Listener → 새 배치에서 `예상 WIP 995m` auto-create (recursion ✓)

**데모 핵심 메시지**: "배치 단위 생산으로 생긴 잉여가 DB에 기록되고, 다음 수주에 자동 반영되며, 실사로 차이가 확인되면 보정배치도 틀단 원칙을 지킨다."

> CEO review 는 이 스토리의 **프레이밍을 "돈/가치"로 재작성** 권고 (Before/After + 금액 중심). Phase 2 demo polish 에서 반영 검토.

## 15. Distribution Plan

기존 웹앱 내 기능 확장. Docker compose 기존 pipeline 재사용.
DB 마이그레이션: `cd backend && alembic upgrade head`. Preflight check 실패 시 자동 abort.

## 16. Open Questions

- **OQ1**: Reconciliation 매칭 허용 오차 초기값 ±10% (DecisionCriteria 에서 조정 가능)
- **OQ2**: Orphan WIP UI 표시 ("출처미상" 태그) — pwc-design 적용 시 시각 규격 확정
- **OQ3 해결**: `DrumLotMaster` uniqueness key = **`cross_section` 단일 키** 확정.
  - 근거: (1) 모델에 `voltage_class`/`material` 컬럼 없음 — `cross_section`, `wire_diameter`, `wire_count`, 생산파라미터만 존재. (2) seed_db.py 14행 모두 `cross_section` 중복 없음 (16~633 SQ, 각 1행). (3) `batch_grouping.py:195, 988` 2곳에서 `dict[float, DrumLotMaster]` 패턴 (`float(cross_section)` 단일 키). 다른 서비스(`cp_sat_optimizer.py:498`, `schedule_optimizer.py:522`)는 `dict[int, float]` — `{int(cross_section): wire_diameter}` 구조로 DrumLotMaster 를 int 키로 참조하며, 이것도 cross_section 단일 키가 고유하다는 근거에 보탬. (4) Docker 미기동으로 DB 직접 SELECT 불가 → 모델+시드 실측으로 결정.
  - Task 13 구현 시 사용할 tuple key 공식: `drum_lots: dict[float, DrumLotMaster] = {float(d.cross_section): d for d in ...}`

## 17. 유보 (Phase 2+)

- **부분소진 추적**: 단일 `matched_order_id` 의 한계. `wip_consumption_log` 테이블
- **Level 3 (예상재공 매칭)**: G5 temporal guard 포함 full 활성화
- **Approach B**: batch_grouping / sm_inventory 공통 배치 생성 헬퍼 추출
- **MES 연동**: T2 승격 자동화
- **CEO 제안 5개**: 유휴 자본 대시보드, Dead Stock 영업 추천, 실사 AI trigger, 시연 스토리 금액 프레임, premise 검증. PoC 완주 후 scope 증분 검토.

## 18. Review Verdict & Revisions

### Eng Review 반영 (FAIL → PASS_WITH_CHANGES)

본 설계 v2 에서 다음 blockers 및 medium 이슈 반영:

| Eng 이슈                             | 반영 위치                                                                                             |
| ------------------------------------ | ----------------------------------------------------------------------------------------------------- |
| HIGH #1 Listener surplus 4x 다중계상 | §7 게이트 (`batch_seq==-1 AND process_name=="연선"`), §9 공식 적용범위 header-only, §9-2 split 재계산 |
| HIGH #2 Hook 2/3 endpoint bypass     | §10 공통 헬퍼 + 3개 endpoint hook + cascade                                                           |
| HIGH #3 Migration preflight 누락     | §11 preflight SELECT + status rename                                                                  |
| MEDIUM #4 IntegrityError DoS         | §7 `ON CONFLICT DO NOTHING`                                                                           |
| MEDIUM #5 file_hash 취약             | §G4 canonical rows hash                                                                               |
| MEDIUM #6 Tiebreaker 미정의          | §G2 결정적 tiebreaker 4-step                                                                          |
| MEDIUM #7 DrumLotMaster key          | §8 tuple key `(cross_section, voltage_class)`                                                         |
| LOW #8 Listener 등록 + bulk 금지     | §7 database.py 등록 + `Session do_orm_execute` 가드                                                   |
| LOW #9 추가 테스트                   | §13 7개 테스트 파일 목록 확장                                                                         |

### CEO Review 의도적 유보 (SELECTIVE EXPANSION → HOLD)

- Alt-1 유휴 자본 프레임, Alt-2 영업 추천, 부분소진 간소버전, 시연 스토리 재작성, premise 검증 **5개 제안 모두 Phase 2 유보**
- 유보 사유: "구현 역량 대비 복잡한 아키텍처 지양" (사용자 선호). 280 LOC + 기존 convention 준수만으로 핵심 가치 (재공 기록 → 다음 수주 반영 → recursion) 는 이미 시연 가능.
- Phase 2 진입 시 우선순위: Alt-1 (회계 프레임) > 시연 스토리 재작성 > 부분소진 > Alt-2 > Alt-3

## 19. What I noticed (office-hours 관찰)

사용자가 세션 내내 보여준 특성:

- **틀단 원칙을 즉시 짚음** — 제가 `create_shortage_batches()` 를 5m 보정배치라고 잘못 설명했을 때, 제조 도메인 비경험자라고 했지만 회계사의 "단위 일관성" 감각이 스케줄링 룰의 구조적 함의까지 추론
- **Recursion 한 줄로 포착** — "거기서 나온 건 그러면 재공 반영이 되는거고" 로 cycle 을 짚어냄. 문제 표면이 아니라 구조를 봄
- **Research-first 판단** — 추상 설계 전에 코드 사실 확인 요구. `create_shortage_batches()` dead code + 틀단 위반 발견은 이 결정의 성과
- **회계 프레임 즉각 동화** — 매출채권/재고자산/매출원가를 WIP lifecycle 에 매핑했을 때 즉시 반응. 도메인은 낯설어도 상태전이/인식시점 공통 패턴 포착
- **Scope 절제 본능** — 리뷰에서 CEO 제안 5개 나왔을 때 "너무 과해" 로 단칼에 컷. "구현 역량 대비 복잡한 아키텍처 지양" 원칙 일관 적용

## 20. The Assignment (다음 액션)

이 설계서 v2 (Eng review 반영) 를 **`superpowers:writing-plans`** 로 전달해서 Phase 1 실행 계획 작성.

10개 작업을 wave-based 병렬화 가능한 단위로 분해. 의존 그래프:

- Wave 0 (병렬): #1 (계산식), #5 (upload_log), #7 (matching filter), #10 UI
- Wave 1 (Wave 0 후): #2 (listener), #4 (UNIQUE index + preflight)
- Wave 2 (Wave 1 후): #3 (등록 + bulk 가드), #6 (reconciliation), #8 (shortage), #9 (hook)
- 테스트는 각 작업과 병행.
