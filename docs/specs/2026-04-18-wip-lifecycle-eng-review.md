# Eng Review — WIP Lifecycle Design

**Reviewer**: Engineering architect (cold review, subagent, read-only Plan mode)
**Date**: 2026-04-18
**Target**: `docs/specs/2026-04-18-wip-lifecycle-design.md`
**Verdict**: FAIL (blocker-level correctness bugs) → ships as PASS_WITH_CHANGES after fixes below
**Overall score**: 5/10

## Dimensions

| Dimension                 | Score | Status                  |
| ------------------------- | ----- | ----------------------- |
| Data flow correctness     | 4/10  | FAIL                    |
| Transaction boundaries    | 5/10  | FAIL                    |
| Concurrency / race safety | 6/10  | PASS (PoC load profile) |
| Test coverage adequacy    | 5/10  | FAIL                    |
| Migration safety          | 5/10  | FAIL                    |
| Scope discipline          | 8/10  | PASS                    |

## Issues Found

### [HIGH] #1 — `wip_output_expected_m` 공식이 5개 중 4개 생성지점에서 틀림

- **Where**: design §9, `batch_grouping.py:413-446` (header), `:474-507` (order), `:524-561` (61-strand CU), `:580-616` (AL core), `:774-806` (Phase 2), `:1344-1382` (split header)
- **Problem**: `work_qty_g`, `net_qty_g` 는 `batch_grouping.py:350` / `362-364` 에서 **그룹 단위로 한 번** 계산. 개별 order 배치(line 474), 61-strand core, Phase 2 공정은 완전히 다른 수량 의미. Listener 가 전체 생성지점에 fire 하면 **한 그룹당 4개 WIP row 생성 = 4x 다중 계상**. UNIQUE index 는 batch_id 중복만 막지, 그룹 전체 다중은 못 막음.
- **Fix**: Listener gate 를 **`batch_seq == -1 AND process_name == "연선" AND wip_output_expected_m > 0`** 으로 제한. Split header(1344) 는 별도 재계산: `split_lots * lot_size - Σ(order × core × (1+defect_buffer))`.
- **Missing test**: "연선 그룹 1개 + 수주 3건 → 정확히 WIP row 1개 생성, source_batch_id=header"

### [HIGH] #2 — T2 승격 hook 이 3개 endpoint 중 1개만 커버

- **Where**: design §10, `plan_pipeline.py:1554-1595` (single `/batch/{id}/status`), `:1598+` (freeform `/batch/{id}`), `:1640-1679` (bulk `/batch-group/{g}/status`)
- **Problem**: 설계서 §10 의 hook 은 single endpoint 만. 나머지 2개는 silent bypass.
  - Bulk endpoint 는 시연 flow 의 주 경로("운영자가 completed 클릭") 에 해당 — **시연에서 T2 승격이 안 일어남**
  - Single endpoint cascade 로 header 가 `completed` 전환 시 hook 이 non-header batch_id 를 조회 → WIP 못 찾음 (Issue #1 수정 후엔 WIP 가 header 에만 연결되므로)
- **Fix**:
  - 3개 endpoint 에 공통 `_promote_expected_to_estimated(batch_id, db)` 헬퍼 적용
  - Cascade 케이스에선 header.batch_id 로 조회
  - 멱등성: `wip.status != '실적_추정'` 체크

### [HIGH] #3 — Migration precheck / 데이터 backfill 누락

- **Where**: design §11 rows 3, 4, §14
- **Problem**:
  - UNIQUE partial index 가 기존 중복 `source_batch_id` 에 실패 가능. Precheck 없음.
  - `sm_inventory.py:79` 는 `WipInventory.status == "실적"` 필터. 새 status `"실사_확정"` 로 mass rename 누락 시 **`create_shortage_batches` 가 silent 0 반환**.
  - Listener 는 신규 INSERT 에만 fire. 기존 ProductionBatch 중 `wip_output_expected_m > 0` 인 것 backfill 안 됨.
- **Fix**:
  - Alembic preflight: `SELECT source_batch_id, COUNT(*) ... HAVING COUNT(*) > 1` — 중복 있으면 abort
  - `UPDATE wip_inventory SET status = '실사_확정' WHERE status = '실적'`
  - PoC 는 legacy 데이터 없음 명시적 문서화 or backfill 쿼리 추가

### [MEDIUM] #4 — Listener `IntegrityError` 미처리 → flush abort → DoS

- **Where**: design §7
- **Problem**: G1 UNIQUE 가 fire 하면 `connection.execute(...)` 가 IntegrityError → 전체 flush abort → ProductionBatch INSERT 까지 rollback. Guard 가 역으로 DoS.
- **Fix**: `INSERT ... ON CONFLICT (source_batch_id) DO NOTHING` (PostgreSQL). Race-safe + bug-safe + silent.

### [MEDIUM] #5 — Excel `file_hash` 가 raw bytes → 공백/저장시간/메타데이터 변경에 취약

- **Where**: design §6-G4
- **Problem**: 동일 물리 실사를 다시 저장하면 hash 달라짐 → 재처리 다이얼로그 빈발 → 사용자가 "Yes" 학습 → guard 무력화
- **Fix**: 파싱 후 canonical rows 튜플 리스트를 hash (정렬 + 합산). "row 내용 같고 sheet 순서만 다름" 도 동일 판정.

### [MEDIUM] #6 — Reconciliation tiebreaker 미정의

- **Where**: design §6-G2, §15 OQ1
- **Problem**: ±10% tolerance 창 겹침 시 자동 매칭 규칙 불명. 비결정적 → 재실행 시 결과 다름.
- **Fix**: 우선순위 확정: (1) exact SQ/voltage 일치 (2) narrowest length delta (3) earliest created_at (4) UI dialog.
  - `core_colors` 매칭 의미론 확정: **set-overlap with null-as-wildcard**. `wip_matching.py:123-129` 의 기존 substring 과 일관.

### [MEDIUM] #7 — `DrumLotMaster` dict key 가 cross_section 단일

- **Where**: design §8, `sm_inventory.py`
- **Problem**: `{float(lot.cross_section): ...}` 는 multi-row per SQ (전압/재료별 다른 lot) 일 경우 마지막 것만 살아남음.
- **Fix**: 실제 uniqueness key 검증 후 tuple key 또는 명시적 assert.

### [LOW] #8 — 미커버 케이스

- Unassigned 배치의 WIP reconciliation (cascade unassign or filter)
- Bulk `db.bulk_save_objects()` 전환 금지 명시 (listener bypass)
- Listener 등록 위치 (`app/main.py` 또는 `database.py`) 명시 누락
- Cross-run WIP 매칭 "by design" 명시

### [LOW] #9 — Missing tests

- Rollback propagation test
- Listener NOT fire for non-header test
- Bulk PATCH hook coverage test
- Header-cascade WIP lookup test
- IntegrityError path test
- Unassigned batch reconciliation test
- Canonical-rows hash idempotency test

## Premise Challenges

- **§4.4 "Listener 중앙화 — 7 지점 개별수정 불필요"**: 부분 참. Listener 에도 gate (`batch_seq == -1`) 필요하므로 per-site 지식 재배치. 중앙화는 사실이나 주장만큼 극적이지 않음.
- **§4.3 "기존 status endpoint 재활용"**: 부분 참. 3개 endpoint 중 1개만 hook — 나머지 bypass.
- **§14 "기존 alembic 경로 재사용, 특별 조치 불필요"**: 거짓. Precheck + data rename migration 필수.

## Recommended Design Changes

1. Listener gate: `batch_seq == -1 AND process_name == "연선" AND wip_output_expected_m > 0`
2. Listener INSERT: `ON CONFLICT (source_batch_id) DO NOTHING`
3. T2 hook: 3개 endpoint 공통 헬퍼
4. Canonical rows hash (파싱 후)
5. Alembic preflight + `'실적' → '실사_확정'` rename
6. Reconciliation tiebreaker 확정 (exact → narrowest delta → earliest → UI)
7. Split header(1344) surplus 재계산 명시
8. Unassign → WIP orphan cascade
9. Listener 등록 위치 문서화 (`app/infrastructure/database.py`)
10. `Session.bulk_save_objects` 금지 runtime guard

## Verdict

설계 모양(listener + UNIQUE + hook + reconcile)은 올바르고 minimal. 3개 blocker 만 고치면 ship 가능. 총 **+1 day** 견적, 240 → 280 LOC.

**Blocker 요약**: (1) Listener gate (2) Hook 3 endpoint (3) Migration precheck + rename. 이거 안 고치고 시연 가면 surplus 4x 계상 + T2 승격 silent 실패로 데모 중 재공 매칭이 안 되는 시나리오 발생.

## Critical Files

- `backend/app/services/batch_grouping.py` (gate 조건 + surplus 계산)
- `backend/app/presentation/routes/plan_pipeline.py` (3 endpoint hook)
- `backend/app/services/sm_inventory.py` (lot 확장 + '실적'→'실사\_확정')
- `backend/app/services/wip_parser.py` (canonical hash + reconciliation)
- `backend/app/services/wip_matching.py` (status 필터 + cross-run 명시)
- `backend/app/infrastructure/database.py` (listener 등록 지점)
