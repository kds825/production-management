# 1000+ 줄 파일 리팩토링 — 설계 문서

**작성일:** 2026-04-28
**브랜치:** `refactoring` (이어서 작업, atomic commit 누적)
**대상 파일 수:** 12 (backend 7 + frontend 5)
**전제:** 동작 100% 동일 — parity 11 + main_parity 27 + 474 backend tests + frontend
typecheck/lint + e2e smoke 회귀 0 으로 강제 검증.

---

## 1. 배경

`docs/superpowers-final-report.md` §3 에서 "1000+ 줄 파일 분해" 항목이 부분 성공
(3개 중 1개 클린, 1개 부분, 1개 의도적 미분리) 으로 종료됐다. 그 후 Phase 5
리팩토링과 신규 기능 추가로 1000+ 줄 파일이 9개로 증가했고, 700~900 줄 구간에도
분해 후보가 있다. 본 문서는 backend 스크립트 (`seed_db.py`, `seed_data.py`)를
제외한 12 개 파일 (1000+ 7 개 + 700~900 5 개) 을 동작 동일성을 보장하면서
분해하는 설계를 정리한다.

이전 보고서가 `plan_pipeline.py` 를 "single concern" 이라 의도적으로 미분리한
판단은 본 작업에서 재평가된다 — 5 개 URL namespace 가 명확히 분리되고
`split_batch_group` 단독 410 줄이 추출 가능하다.

---

## 2. 목표 / 비목표

### 목표

1. 12 개 파일의 LOC 를 1000 이하로 낮추기 (1000+ 7 개) + 700~900 5 개 파일도
   책임 분해 (책임이 한 파일에 모이지 않게).
2. 분해 후 동작 100% 동일 — parity hash, 모든 테스트, e2e smoke 회귀 0.
3. atomic commit 단위로 진행 — 각 commit 직후 검증 게이트 통과.
4. 문서화된 known-debt (post-pilot-backlog `cp_sat_schedule()` 1317 줄,
   `plan_pipeline.py` ≤500 목표, frontend 3 파일) 해결.

### 비목표 (out of scope)

- 새 기능 추가, dead code 정리 (별도 PR), 의존성 업그레이드.
- `seed_db.py` (1596 줄), `seed_data.py` (894 줄) — backend 시드 스크립트, 사용자 명시 제외.
- LLM 호출 경로 통합 (`llm_explainer.py` legacy — 별도 known-debt).
- Decision Card per-constraint trace 미연결 (별도 known-debt).
- 새 파일에 추상화 도입 (Protocol / Registry / ABC / Service base class) —
  CLAUDE.md 명시 금지.

---

## 3. 분해 전략 — 파일별

### 3.1 Phase 1 (Green — mechanical 분할, parity 위험 낮음)

#### B-1. `backend/app/presentation/routes/plan_pipeline.py` (2,537 → ~50)

29 endpoints 가 5 URL namespace 로 분할된다. 외부 import path
(`app.presentation.routes.plan_pipeline.<name>`) 는 보존 — sub-module 의 router
와 helper 를 main 모듈에서 re-include / re-export.

| 새 파일                               | 책임                                                                                                                                               | 추정 LOC |
| ------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- | -------- |
| `routes/plan_pipeline.py`             | `APIRouter` + sub-router include + 공유 helpers (`_run_ai_background`, `_ai_cache`)                                                                | ~100     |
| `routes/plan_pipeline_stage1.py`      | `/stage1` POST + `/stage1/update` POST + `/stage1/{run_label}/*` GET (batches, wip-inventory, outsourced, ai-summary, export) + apply_urgent_order | ~900     |
| `routes/plan_pipeline_stage2.py`      | `/stage2` (sync/async/status) + `/stage2/{run_label}/ai-status` + trigger-reanalysis                                                               | ~180     |
| `routes/plan_pipeline_batch_group.py` | `/batch-group/{name}/orders` + `/process-flow` + `/split` + `/batch-group-snapshots` + unassign / restore / restore-at                             | ~700     |
| `routes/_batch_group_split.py`        | `split_batch_group` 본체 (410 줄) — endpoint 는 batch_group 모듈 에 남고 비즈니스 로직만 추출                                                      | ~410     |
| `routes/plan_pipeline_batch.py`       | `/batch/{id}` PATCH + `/batch/{id}/status` PATCH + `/batch-group/{name}/status` PATCH + `/batch-status-summary`                                    | ~250     |
| `routes/plan_pipeline_runs.py`        | `/runs` GET + `/runs/compare` GET + `/runs/{run_label}` DELETE                                                                                     | ~410     |
| `routes/_pipeline_shared.py` (선택)   | `_run_ai_background`, `_start_ai_background`, `_execute_stage2_core`, `_parse_stage2_body` 등                                                      | ~150     |

`_ai_cache` (in-memory dict + threading.Lock) 는 `_pipeline_shared.py` 의 모듈
레벨 변수로 이동. 모든 sub-router 가 이 모듈을 import — singleton 의미 유지.

monkeypatch 표적 (`auto_schedule`) 은 sub-router 모듈 안에 import + alias 로
유지. 기존 테스트 (`test_*.py` 의 `mocker.patch("...plan_pipeline.auto_schedule")`)
호환 위해 `routes/plan_pipeline.py` 가 `auto_schedule = ...` 를 re-export.

**Atomic commit 단위 (4 commits 권장):**

1. `_pipeline_shared.py` 추출 (helper 6 개) — 의존성 적음
2. `plan_pipeline_runs.py` + `plan_pipeline_batch.py` 추출 — 독립 namespace
3. `plan_pipeline_stage1.py` + `plan_pipeline_stage2.py` 추출 — Stage 도메인
4. `plan_pipeline_batch_group.py` + `_batch_group_split.py` 추출 — 가장 큰 변경

각 commit 후: `pytest backend/tests/ -q` + parity 11.

#### F-1. `frontend/src/app/(main)/plan-register/page.tsx` (1,910 → ~400)

이미 5 개 sub-component 가 같은 파일에 정의돼 있다 — 별도 파일로 분리하면
mechanical. `ErpUploadSection` 단독 877 줄은 추가 sub-component 로 분할.

| 새 파일                                                                                           | 추출 대상                                                                                     | LOC  |
| ------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------- | ---- |
| `features/plan-register/components/DiffSummaryPanel.tsx`                                          | `DiffSummaryPanel` (line 184~436)                                                             | ~250 |
| `features/plan-register/components/OrderDiffSummaryPanel.tsx`                                     | `OrderDiffSummaryPanel` (line 436~518)                                                        | ~80  |
| `features/plan-register/components/BatchGridWithFrozen.tsx`                                       | `BatchGridWithFrozen` (line 518~696)                                                          | ~180 |
| `features/plan-register/components/WipUploadSection.tsx`                                          | `WipUploadSection` (line 696~917)                                                             | ~220 |
| `features/plan-register/components/ErpUploadSection.tsx`                                          | `ErpUploadSection` (line 917~1794) — 추가 분할 후보 (split-review, batch-grid, file-input 등) | ~600 |
| `features/plan-register/components/ErpUploadSection.{SplitReview,BatchGridDisplay,FileInput}.tsx` | ErpUploadSection 내부 sub-component (선택, 위험 평가 후) — depth ≤ 3 위해 평탄 prefix 패턴    | ~300 |
| `features/plan-register/types.ts`                                                                 | `WipFile`, `ParsedOrder`, `BatchSummary`, `Stage1Result` 등 interface 12 개                   | ~150 |
| `app/(main)/plan-register/page.tsx`                                                               | `PlanRegisterPage` 본체 + getKstToday + import                                                | ~400 |

**주의:** Next 16 (frontend/AGENTS.md). `next/navigation` `useSearchParams` 가 build
실패 — 현재는 `useEffect + window.location.search` 직접 읽기 패턴. 분할 시
이 패턴 그대로 유지. SSR boundary 변경 금지.

**Atomic commit 단위 (3 commits 권장):**

1. `types.ts` + 작은 컴포넌트 4 개 (DiffSummaryPanel, OrderDiffSummaryPanel,
   BatchGridWithFrozen, WipUploadSection) 추출
2. `ErpUploadSection.tsx` 추출 (단일 큰 컴포넌트)
3. ErpUploadSection 내부 sub-component 추가 분할 (선택 — 위험 평가 후)

각 commit 후: typecheck + lint.

#### F-2. `frontend/src/app/(main)/scheduling-review/page.tsx` (1,060 → ~400)

단일 컴포넌트. fetch hook 책임 추출.

| 새 파일                                                | 추출 대상                                              |
| ------------------------------------------------------ | ------------------------------------------------------ |
| `features/scheduling-review/hooks/useRunsList.ts`      | runs 로드 + selectedRun resolution (URL > prev > 최신) |
| `features/scheduling-review/hooks/useRunCompare.ts`    | compare 모달 + diff fetch                              |
| `features/scheduling-review/hooks/useExcelDownload.ts` | Excel 다운로드 + loading                               |
| `features/scheduling-review/hooks/useUrlRunLabel.ts`   | URL 쿼리스트링 → run_label 추출 (Next 16 안전 패턴)    |
| `features/scheduling-review/components/RunsHeader.tsx` | run 목록 + 비교 버튼 + Excel 버튼 (가능 시)            |
| `app/(main)/scheduling-review/page.tsx`                | SchedulingReviewPage 본체                              |

**Atomic commit 단위 (2 commits):**

1. hooks 4 개 추출 (page.tsx 로직만 이동)
2. RunsHeader 컴포넌트 추출 (선택)

#### F-3. `frontend/src/features/scheduling-review/components/ProductionBatchTable.tsx` (1,079 → ~500)

단일 컴포넌트. column 정의 + 셀 렌더 + 필터 책임 분리.

| 새 파일                                                                          | 추출 대상                                   |
| -------------------------------------------------------------------------------- | ------------------------------------------- |
| `features/scheduling-review/components/production-batch-table/columnDefs.ts`     | `COL_DEFS`, `ColKey`, getter 함수           |
| `features/scheduling-review/components/production-batch-table/cellRenderers.tsx` | 각 컬럼 셀 렌더 함수 (read + edit mode)     |
| `features/scheduling-review/components/production-batch-table/filters.ts`        | `ColFilters`, 필터 로직, getCellValueStatic |
| `features/scheduling-review/components/ProductionBatchTable.tsx`                 | 메인 컴포넌트 (table 본체 + state)          |

depth ≤ 3 제약 — `production-batch-table/` 디렉토리는 components/ 하위라 4 단계.
대안: 평탄 명명 `productionBatchTable_columnDefs.ts` 등. **이 spec 에서는 평탄
패턴으로 수정**:

```
features/scheduling-review/components/ProductionBatchTable.tsx                  ← main
features/scheduling-review/components/ProductionBatchTable.columnDefs.ts        ← 추출
features/scheduling-review/components/ProductionBatchTable.cellRenderers.tsx    ← 추출
features/scheduling-review/components/ProductionBatchTable.filters.ts           ← 추출
```

**Atomic commit 단위 (1 commit):**

1. 3 개 helper 추출 + ProductionBatchTable 본체 import 갱신

#### F-4. `frontend/src/features/scheduler/components/GanttTaskBlock.tsx` (1,060 → ~700)

메인 컴포넌트 + 4 개 pure helper. helper 만 분리하면 ~360 줄 감소.

| 새 파일                                             | 추출 대상                                                                                |
| --------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| `features/scheduler/utils/ganttTaskBlockHelpers.ts` | `splitByWeekends`, `isSheathEquipment`, `getSheathColor`, `snapToHour`, `isFrozenStatus` |
| `features/scheduler/components/GanttTaskBlock.tsx`  | 메인 컴포넌트                                                                            |

**Atomic commit 단위 (1 commit):**

1. helpers 추출 + import 갱신

#### B-4. `backend/app/application/validation/constraint_checker.py` (705 → ~80)

22 개 `_check_*` 함수 (각 30~50 줄). 카테고리별 모듈 분리.

| 새 파일                            | 추출 함수                                                                                                                                                |
| ---------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `validation/constraint_checker.py` | `validate_all`, `validate_overlap_only`, `has_overlap` (orchestrator + 공개 API)                                                                         |
| `validation/_checks_hard.py`       | `_check_overlap`, `_check_precedence`, `_check_sq_range`                                                                                                 |
| `validation/_checks_due.py`        | `_check_delivery`, `_check_due_type`, `_check_priority_order`                                                                                            |
| `validation/_checks_setup.py`      | `_check_color_group`, `_check_setup_time`                                                                                                                |
| `validation/_checks_calendar.py`   | `_check_friday_hours`, `_check_holiday`, `_check_absence_hours`                                                                                          |
| `validation/_checks_material.py`   | `_check_material_separation`, `_check_defect_buffer`, `_check_material_availability`, `_check_raw_material_availability`, `_check_procurement_lead_time` |
| `validation/_checks_misc.py`       | `_check_safety_education`, `_check_equipment_utilization`, `_check_gc_routing`                                                                           |

depth ≤ 3 OK (`application/validation/_checks_X.py`).

**Atomic commit 단위 (3 commits):**

1. `_checks_hard.py` + `_checks_due.py` + `_checks_setup.py` 추출
2. `_checks_calendar.py` + `_checks_material.py` + `_checks_misc.py` 추출
3. `constraint_checker.py` 재구성 — orchestrator만 남김

#### B-5. `backend/app/application/decisions/build_card.py` (761 → ~200)

22 개 함수. 책임별 그룹핑.

| 새 파일                      | 추출 함수                                                                                                                                      |
| ---------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| `decisions/build_card.py`    | `build_decision_card` (메인 orchestrator) + small util                                                                                         |
| `decisions/_card_why.py`     | `_build_why_lines`, `_scenario_key_hint`, `_safe_verdict_summary`, `_collect_audit_metrics`, `_extract_metric`                                 |
| `decisions/_card_impact.py`  | `_build_impact_block`, `_build_equipment_day`, `_post_hoc_bundle_metrics`, `_build_alternatives`, `_build_placement_calc`, `__dict_for_bundle` |
| `decisions/_card_helpers.py` | `_load_context`, `_empty_card`, `_process_label`, `_sub_chip`, `_format_placement_text`, `_task_view`, `_build_debug_block`                    |

**Atomic commit 단위 (3 commits):**

1. `_card_helpers.py` 추출 (가장 단순, 의존성 적음)
2. `_card_why.py` 추출
3. `_card_impact.py` 추출 + build_card.py 재구성

#### B-6. `backend/app/infrastructure/exporters/excel_exporter.py` (741 → ~200)

`_write_sheet` 230 줄 단독 + 8 개 helper.

| 새 파일                               | 추출 함수                                                                                                                                    |
| ------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| `exporters/excel_exporter.py`         | `export_plan` (orchestrator)                                                                                                                 |
| `exporters/excel_exporter_sheet.py`   | `_write_sheet` 단일 함수 (230줄) — 추가 분해 후보                                                                                            |
| `exporters/excel_exporter_helpers.py` | `_merge_lot_splits`, `_resolve_sheet_name`, `_build_remarks`, `_write_data_row`, `_write_subtotal`, `_write_annotation`, `_apply_col_widths` |

depth ≤ 3 OK (`infrastructure/exporters/excel_exporter_*.py`).

**Atomic commit 단위 (2 commits):**

1. `excel_exporter_helpers.py` 추출 (작은 helper 7 개)
2. `excel_exporter_sheet.py` 추출 (`_write_sheet` 단독)

`_write_sheet` 내부 추가 분해는 위험 평가 후 선택 (Stage B-6b).

#### F-5. `frontend/src/features/scheduling-review/components/SchedulingResultTable.tsx` (902 → ~500)

ProductionBatchTable과 같은 평탄 prefix 패턴.

| 새 파일                                                                         | 추출 대상                               |
| ------------------------------------------------------------------------------- | --------------------------------------- |
| `features/scheduling-review/components/SchedulingResultTable.crud.tsx`          | `CrudMode`, `CrudButton`, edit handlers |
| `features/scheduling-review/components/SchedulingResultTable.cellRenderers.tsx` | 셀 렌더링 함수                          |
| `features/scheduling-review/components/SchedulingResultTable.tsx`               | 메인 컴포넌트 (table 본체 + state)      |

**Atomic commit 단위 (1 commit):**

1. CrudButton + cellRenderers 추출 + import 갱신

### 3.2 Phase 2 (Yellow — 함수 분해, parity 검증 강제)

함수 분해의 위험은 hash 변경으로 즉시 탐지되지만, hash 가 변하면 어디서 변했는지
bisect 해야 한다. 따라서 commit 을 매우 작게 (≤ 200 줄 변경) 유지하고 직후
parity 게이트 강제.

#### B-2. `backend/app/application/ingest/batch_grouper.py` (1,185 → ~150)

`create_batches()` 858 줄 (line 146~1006) 분해.

**Stage 2a (β level — helper 함수만 추출, state-bag 없음):**

```
application/ingest/batch_grouper.py        ← orchestrator (~150줄)
application/ingest/_batch_grouper_loader.py    ← 마스터 + 파라미터 로드 (~100줄)
                                              ├ ConstraintParams.load
                                              ├ orders/wip_by_order_line/drum_lots/
                                              │ routings/items/speeds/customers
                                              ├ extra/defect/remnant 파라미터
                                              └ speed_lookup 빌드
application/ingest/_batch_grouper_strand.py    ← Phase 1 연선 그룹 (~400줄)
                                              └ create_strand_batches(orders, ...) → list[ProductionBatch]
application/ingest/_batch_grouper_per_order.py ← Phase 2 수주별 (~200줄)
                                              └ create_per_order_batches(orders, ...) → list[ProductionBatch]
application/ingest/_batch_grouper_finalize.py  ← 정렬 + 그룹 부여 + DB + dedupe (~200줄)
                                              ├ apply_remnant_blackout
                                              ├ sort_batches
                                              ├ apply_sheath_secondary_sort
                                              ├ assign_batch_groups
                                              ├ persist_batches
                                              └ deduplicate_group_headers (이미 분리됨)
```

`create_batches()` 본체는 5 개 helper 호출 + result 집계만 남김.

**Atomic commit 단위 (4 commits):**

1. `_batch_grouper_loader.py` 추출 — 가장 단순 (DB 로드)
2. `_batch_grouper_finalize.py` 추출 — 출력 단계 (DB 기록 + dedupe)
3. `_batch_grouper_per_order.py` 추출 — Phase 2 (작은 블록 먼저)
4. `_batch_grouper_strand.py` 추출 — Phase 1 (가장 큼, 마지막)

각 commit 후: parity 11 + main_parity 27.

**Stage 2b (α level — dataclass state-bag, 위험 평가 후 진행):**

```python
@dataclass
class _BatchGrouperContext:
    db: Session
    run_label: str
    orders: list[SalesOrder]
    wip_by_order_line: dict[str, WipInventory]
    drum_lots: dict[float, DrumLotMaster]
    routings: dict[str, ProcessRouting]
    items: dict[str, ItemMaster]
    customers: dict[str, CustomerMaster]
    speed_lookup: dict[tuple, SpeedMaster]
    constraint_params: ConstraintParams
    base_extra: float
    sample_extra: float
    defect_buffer_pct: float
    remnant_threshold_m: float
    warnings: list[str]
```

helper 시그니처가 `def create_strand_batches(orders, wip_by_order_line, drum_lots,
routings, items, ...)` 에서 `def create_strand_batches(ctx)` 로 단순화.

**Stage 2a 검증 통과 후 별도 commit. 위험 발견 시 skip 가능.**

#### B-3. `backend/app/application/scheduling/cp_sat/orchestrator.py` (1,468 → ~300)

`cp_sat_schedule()` 1270 줄 분해. 가장 위험.

**Stage 3a (β level — helper 함수만 추출):**

```
application/scheduling/cp_sat/orchestrator.py        ← 본체 (~300줄)
application/scheduling/cp_sat/_load_inputs.py        ← §1-3 DB 로드 + override rebind (~150줄)
application/scheduling/cp_sat/_solver_runner.py      ← §6 모델 구성 + §7-pre/a/b solve (~200줄)
                                                    ├ build_cp_sat_model → BuiltModel
                                                    ├ run_lex_solver
                                                    └ run_weighted_sum_solver
application/scheduling/cp_sat/_calendar_apply.py     ← §8 캘린더 그리디 (~550줄, 추가 분해 후보)
                                                    ├ resolve_first_due_by_strand_cluster
                                                    ├ apply_sheath_color_sort
                                                    ├ preload_existing_timeline
                                                    └ apply_calendar_greedy
application/scheduling/cp_sat/_preemption_runner.py  ← §9 선점 후속 (~50줄)
application/scheduling/cp_sat/_trace_writer.py       ← Task 2A.3 trace write (~130줄)
```

`cp_sat_schedule()` 본체는 phase별 helper 호출 + result 집계.

**Atomic commit 단위 (5 commits):**

1. `_load_inputs.py` 추출 — §1-3 DB 로드 (가장 단순)
2. `_trace_writer.py` 추출 — write 단계 (output)
3. `_preemption_runner.py` 추출 — §9 (작은 블록)
4. `_solver_runner.py` 추출 — §6/7 (model_builder 가 이미 분리됨, runner 만 추출)
5. `_calendar_apply.py` 추출 — §8 (가장 큼, 가장 위험)

각 commit 후: parity 11 + main_parity 27 + decision-card-rationale 검증
(`pytest backend/tests/decisions/`).

**Stage 3b (α level — dataclass state-bag, 위험 평가 후):**

```python
@dataclass
class _CpSatContext:
    # 입력
    run_label: str
    db: Session
    base_date: datetime
    batches: list[ProductionBatch]
    equipment_by_process: dict[str, list[EquipmentMaster]]
    speed_map: dict[tuple, SpeedMaster]
    color_setup_map: dict[str, float | None]
    constraint_params: ConstraintParams
    welding_min: float
    sq_to_wire_d: dict[int, float]
    # 모델
    batch_groups: list
    group_meta: dict
    built_model: BuiltModel | None
    # 솔버 출력
    solver_status: str
    objective_value: int
    # §8 누적 state
    timeline: dict
    predecessor_map: dict
    sq_to_equip: dict
    process_first_output_by_sq: dict
    # 결과
    result: dict[str, Any]
    warnings: list[str]
```

`_calendar_apply` 의 시그니처가 30+ 인자에서 `(ctx, ...)` 로 단순화.

**Stage 3a 검증 통과 후 별도 commit. 위험 발견 시 skip 가능.**

#### B-7. `backend/app/application/scheduling/greedy/optimization_loop.py` (1,029 → ~250)

`_assign_group()` 569 줄 (line 311~879) + `_run_optimization_once` 104 줄 + 보조
helpers. greedy 본체로 orchestrator 동급 위험. parity 11/11 + main_parity 27/27
강제.

**Stage 7a (β level — helper 함수 추출):**

```
application/scheduling/greedy/optimization_loop.py     ← orchestrator + 작은 helpers (~250줄)
                                                      ├ _run_optimization_once
                                                      ├ _group_earliest_due
                                                      ├ _seed_state_from_existing
                                                      ├ class GroupingContext
                                                      ├ _get_tp_line_speed
                                                      └ _get_sheath_type
application/scheduling/greedy/_group_and_sort.py       ← `_group_and_sort` 120줄 (~150줄)
application/scheduling/greedy/_assign_group.py         ← `_assign_group` 569줄 — 그리디 본체 (~600줄, 추가 분해 후보)
```

`_assign_group` 569 줄은 자체로 1000+ 가 아니지만 응집도가 낮은 상태. 파일 분리만
으로 optimization_loop 본체는 ≤ 300 줄로 떨어진다.

**Atomic commit 단위 (2 commits):**

1. `_group_and_sort.py` 추출 — 작은 함수 먼저
2. `_assign_group.py` 추출 — 큰 본체

각 commit 후: parity 11 + main_parity 27.

**Stage 7b (α level — `_assign_group` 내부 분해, 위험 평가 후):**

`_assign_group` 569 줄 내부의 timeline / equipment_state / 색상 클러스터 등
누적 state 를 dataclass `_GreedyAssignContext` 로 묶고, slot 찾기 / 배치 적용 /
색상 처리 등 phase 별 helper 로 분해. orchestrator 의 Stage 3b 와 같은 전략.

**Stage 7a 검증 통과 후 별도 commit. 위험 발견 시 skip 가능.**

---

## 4. 검증 게이트

### 4.1 Per-commit 게이트 (모든 commit 직후 실행)

| 게이트                     | 명령                                                          | 기대 시간 |
| -------------------------- | ------------------------------------------------------------- | --------- |
| Backend unit + integration | `cd backend && pytest tests/ -q`                              | ~30초     |
| Parity (11 fixtures)       | `cd backend && pytest tests/test_parity_harness.py -m parity` | ~3~5분    |
| Main parity (27)           | `cd backend && pytest tests/main_parity/ -m parity`           | ~5분      |
| Frontend type              | `cd frontend && npm run typecheck`                            | ~10초     |
| Frontend lint              | `cd frontend && npm run lint`                                 | ~5초      |

Backend commit 은 backend 게이트 4 개. Frontend commit 은 frontend 게이트 2 개.

### 4.2 PR 종료 게이트

| 게이트       | 명령                                                                                     |
| ------------ | ---------------------------------------------------------------------------------------- |
| E2E smoke    | `cd frontend && npx playwright test sprint-feedback.spec.ts verification-stage1.spec.ts` |
| Manual smoke | `docs/qa-manual-smoke-2-2-5-5-2-4.md` 따라 1 회 (Phase 2 종료 시)                        |

### 4.3 회귀 발생 시 절차

1. parity hash 가 변경되면 → `git bisect` 로 commit 식별 → 그 commit 만 revert
2. 의도된 hash 변경 (절대 발생하지 않아야 함) 인 경우 → `parity-update:` prefix
   commit 으로 fixture 재freeze (본 spec 범위 내에서는 발생 금지)

---

## 5. 작업 순서

```
Phase 1 (Green) — refactoring 브랜치
├ Commit 1.1: B-1.1 _pipeline_shared.py 추출
├ Commit 1.2: B-1.2 plan_pipeline_runs/batch.py 추출
├ Commit 1.3: B-1.3 plan_pipeline_stage1/stage2.py 추출
├ Commit 1.4: B-1.4 plan_pipeline_batch_group.py + _batch_group_split.py 추출
├ Commit 1.5: B-4.1 _checks_hard/due/setup.py 추출
├ Commit 1.6: B-4.2 _checks_calendar/material/misc.py 추출
├ Commit 1.7: B-4.3 constraint_checker.py 재구성
├ Commit 1.8: B-5.1 _card_helpers.py 추출
├ Commit 1.9: B-5.2 _card_why.py 추출
├ Commit 1.10: B-5.3 _card_impact.py 추출 + build_card.py 재구성
├ Commit 1.11: B-6.1 excel_exporter_helpers.py 추출
├ Commit 1.12: B-6.2 excel_exporter_sheet.py 추출
├ Commit 1.13: F-1.1 plan-register types + 4 sub-component
├ Commit 1.14: F-1.2 ErpUploadSection 추출
├ Commit 1.15: F-1.3 ErpUploadSection 내부 분할 (선택)
├ Commit 1.16: F-2 scheduling-review hooks
├ Commit 1.17: F-3 ProductionBatchTable helpers
├ Commit 1.18: F-4 GanttTaskBlock helpers
├ Commit 1.19: F-5 SchedulingResultTable helpers
└ ✅ Phase 1 종료 — e2e smoke 1 회

Phase 2 (Yellow) — 같은 refactoring 브랜치 위에 누적
├ Commit 2.1: B-2 Stage 2a — _batch_grouper_loader 추출
├ Commit 2.2: B-2 Stage 2a — _batch_grouper_finalize 추출
├ Commit 2.3: B-2 Stage 2a — _batch_grouper_per_order 추출
├ Commit 2.4: B-2 Stage 2a — _batch_grouper_strand 추출
├ Commit 2.5: B-2 Stage 2b — dataclass state-bag (선택)
├ Commit 2.6: B-3 Stage 3a — _load_inputs 추출
├ Commit 2.7: B-3 Stage 3a — _trace_writer 추출
├ Commit 2.8: B-3 Stage 3a — _preemption_runner 추출
├ Commit 2.9: B-3 Stage 3a — _solver_runner 추출
├ Commit 2.10: B-3 Stage 3a — _calendar_apply 추출
├ Commit 2.11: B-3 Stage 3b — dataclass state-bag (선택)
├ Commit 2.12: B-7 Stage 7a — _group_and_sort 추출
├ Commit 2.13: B-7 Stage 7a — _assign_group 추출
├ Commit 2.14: B-7 Stage 7b — dataclass state-bag (선택)
└ ✅ Phase 2 종료 — e2e smoke + manual smoke
```

대략 30~33 commits. 사용자가 PR 시점 결정 (Phase 1 후 / Phase 2 후 / 둘 다 합쳐).

---

## 6. 위험 평가

| 위험                                                                     | 확률 | 영향 | 완화                                                                                                                                         |
| ------------------------------------------------------------------------ | ---- | ---- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| `cp_sat_schedule()` §8 분해 시 parity hash 변경                          | 중   | 높음 | commit ≤ 200 줄, 직후 parity 게이트, 변경 시 즉시 revert                                                                                     |
| `split_batch_group` 410 줄 추출 시 monkeypatch path 변경                 | 중   | 중   | `_batch_group_split.py` 의 `split_batch_group` 을 `plan_pipeline_batch_group.py` 에서 import + endpoint 본체에서 호출. 외부 import path 보존 |
| `_ai_cache` (in-memory dict) 분리 시 race condition                      | 낮   | 중   | `_pipeline_shared.py` 의 모듈-레벨 singleton + Lock 유지. 다중 import 가 동일 dict 참조                                                      |
| Frontend Next 16 SSR boundary 위반                                       | 낮   | 높음 | `useEffect + window.location` 패턴 보존. `useSearchParams` 도입 금지                                                                         |
| `auto_schedule` monkeypatch 깨짐 (test\_\*.py)                           | 중   | 낮   | `routes/plan_pipeline.py` 가 `auto_schedule` re-export. 기존 patch path 보존                                                                 |
| ErpUploadSection (877 줄) 추가 분할 시 prop drilling                     | 낮   | 낮   | 내부 분할은 commit 1.15 로 선택사항. 위험 평가 후 진행 또는 skip                                                                             |
| Phase 2 Stage 2b/3b/7b dataclass 도입 시 hash 변경                       | 중   | 중   | Stage 2a/3a/7a 검증 통과 후에만 진행. parity 회귀 발견 시 skip 가능                                                                          |
| `_assign_group` 569 줄 추출 시 그리디 본체 hash 변경                     | 중   | 높음 | orchestrator 동급 위험. commit 단독, 직후 parity + main_parity 강제                                                                          |
| `constraint_checker.py` 카테고리 분리 시 `validate_all` import path 변경 | 낮   | 중   | `validate_all` 은 `constraint_checker.py` 에 남고 helper 만 sub-module 로. 외부 import path 보존                                             |
| `excel_exporter._write_sheet` 230 줄 단독 추출 시 export_plan 호출 깨짐  | 낮   | 낮   | 모듈 레벨 함수 추출, 시그니처 그대로. unit test 로 검증                                                                                      |

### 6.1 두 review 반영 추가 위험 (2026-04-28)

| 위험                                                                                                                                                   | 확률     | 영향 | 완화                                                                                                                                                                               |
| ------------------------------------------------------------------------------------------------------------------------------------------------------ | -------- | ---- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `_execute_stage2_core` / `_parse_stage2_body` sub-module 이동 시 `monkeypatch.setattr(plan_pipeline, ...)` 깨짐                                        | **확정** | 높   | **두 함수는 `plan_pipeline.py` 에 그대로 둠** — `_pipeline_shared.py` 추출 대상에서 제외. `_ai_cache` / `_run_ai_background` / `_start_ai_background` 만 이동. (plan Appendix A.1) |
| `compare_runs` / `list_runs` / `delete_run` sub-router 이동 시 `from ...plan_pipeline import compare_runs` 깨짐 (test_stage1_update_versioning.py:177) | **확정** | 높   | `plan_pipeline.py` 에서 `from .plan_pipeline_runs import compare_runs, list_runs, delete_run` 명시적 re-export. (plan Appendix A.2)                                                |
| sub-router 가 `from plan_pipeline import _execute_stage2_core` 패턴 사용 시 monkeypatch 무효                                                           | **확정** | 높   | sub-router 는 `import plan_pipeline; plan_pipeline._execute_stage2_core(...)` 패턴 강제 — attribute lookup 으로 patch 적용. (plan Appendix A.3)                                    |
| parity 게이트가 endpoint 측 DB 부작용 (db.commit, audit_log delete, wip_matched_id 변경) 못 감지                                                       | 높       | 높   | `split_batch_group` 회귀 테스트 신규 작성 + `decisions` / `excel_exporter` byte-diff 게이트 추가. (plan Appendix A.4, A.5)                                                         |
| Stage 2a helper 12+ 인자 positional 전달 시 dict/float/list 타입 혼선                                                                                  | 중       | 중   | `_GrouperInputs` dataclass 를 Stage 2a 부터 **필수** 도입 (선택 → 강제 승격). frozen=True 로 mutate 차단. (plan Appendix A.6)                                                      |
| Task 2.10 `_calendar_apply` 550줄 단일 commit 추출 시 회귀 bisect 범위 과대                                                                            | 중       | 높   | Task 2.10 → 5 sub-step (a~e) 으로 분할. 각 sub-step 직후 parity 강제. bisect 범위 550 → ~190줄. (plan Appendix A.7)                                                                |
| Stage X-b "선택" sunk-cost fallacy 로 무리 진행                                                                                                        | 중       | 중   | 정량 skip 기준 3 조건 (Stage Xa 무회귀 + 12+ args + ctx 요구 후속 작업 명시) 모두 만족 시에만 진행. (plan Appendix A.9)                                                            |
| `_ai_cache` singleton 보장 — multiple worker 환경 (uvicorn `--workers >1`) 미지원                                                                      | 낮       | 중   | **본 PoC 는 단일 worker 가정.** future production 전환 시 Redis / shared memory 로 cache 외부화 필요 — known-debt 등록.                                                            |

---

## 7. Rollback 전략

- 각 commit 이 atomic — `git revert <commit>` 로 즉시 되돌림.
- Phase 1 / Phase 2 사이 자연 boundary — Phase 2 commit 만 revert 가능.
- main 으로 merge 전이라면 `git reset --hard <known-good>` 도 가능.
- parity fixture 재freeze 가 필요한 경우는 spec 범위 외 (의도되지 않은 변경).

---

## 8. 작업 시간 추정

| Phase                                                     | 추정        |
| --------------------------------------------------------- | ----------- |
| Phase 1 (Green) — backend 12 commits + frontend 7 commits | 2~3 일      |
| Phase 2 Stage 2a/3a/7a (Yellow β) — 11 commits            | 3~4 일      |
| Phase 2 Stage 2b/3b/7b (Yellow α, 선택) — 3 commits       | 1~2 일      |
| 검증 + 문서 갱신                                          | 0.5 일      |
| **총**                                                    | **6~10 일** |

각 commit 의 parity 게이트 시간 (~10분/commit × 18 backend commits ≈ 3 시간)
이 누적 — 실제 코드 작성 시간 외 검증 시간을 별도 계산.

---

## 9. 종료 조건

- 1000+ 7 개 파일 모두 ≤ 999 줄 (단, Phase 2 Stage 2b/3b/7b skip 시 일부 helper
  모듈이 500~600 줄로 남을 수 있음 — 1000+ 가 아니면 OK).
- 700~900 5 개 파일도 책임 분해 — 한 파일에 한 책임 원칙 충족 (LOC 자체는
  500~700 으로 줄어듦).
- 모든 검증 게이트 통과 (per-commit + PR 종료).
- post-pilot-backlog 의 해당 항목 (`cp_sat_schedule()` 1317, `plan_pipeline.py`
  ≤500, frontend 3 개) "완료" 로 마킹.
- 본 spec 의 Stage 2b/3b/7b 가 skip 됐으면 그 사유 (parity 위험 등) 를
  post-pilot-backlog 로 라우팅.

---

## 10. 후속 작업 (별도 PR)

- `services/llm_explainer.py` legacy 통합 (post-pilot-backlog 등록).
- Decision Card per-constraint trace wiring (post-pilot-backlog 등록).
- `seed_db.py` (1596 줄), `seed_data.py` (894 줄) — backend 시드 스크립트, 본 spec 범위 외.
