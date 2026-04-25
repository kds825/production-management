# Architecture Target — backend/app Clean Architecture (v2, REVIEWED)

**작성**: 2026-04-26 (Phase 0 ultrathink 종합)
**REVIEWED**: 2026-04-26 — gstack eng-manager + codex 2nd opinion 양쪽 병렬 리뷰 후 8 patch 반영 (§1.4 참조).
**입력**:

- `docs/architecture-audit-services.md` (85 files, 17,910 LOC)
- `docs/architecture-audit-boundaries.md` (79 violations)
- `docs/architecture-audit-cleanup.md` (cleanup 후보)
- `docs/architecture-audit-monkeypatch.md` (31 patch sites, 21 shell-anchored)
- 직접 검증한 audit false positives (preemption, decision_aggregator, greedy/loaders, `_template_explanation`, `_detect_rule_based_risks` — 모두 LIVE, audit 권고와 반대)
- `docs/architecture-target-review-eng.md` (gstack eng-manager review, APPROVE WITH CHANGES, 5 must-fix)
- codex consult 결과 (인라인 §1.4)
- 사용자 메모리 (`feedback_architecture_simplicity`, `feedback_constraint_arch_simplicity`)
- 직전 세션 §11 deferred 5 항목

**용도**: Phase 1+ 실행의 단일 청사진. 본 문서 = Phase 1 진입 게이트 (사용자 승인 완료, 2026-04-26).

---

## 1. 사고 원칙 (ultrathink + reviewer 통합)

### 1.1 Audit 결과 신뢰성 등급

| Agent                  | 결과 신뢰                                                                   | 활용                                                             |
| ---------------------- | --------------------------------------------------------------------------- | ---------------------------------------------------------------- |
| A (services inventory) | 인벤토리 ✓ / "dead suspects" 다수 false positive                            | 인벤토리만 신뢰, dead 판정은 직접 검증 후 채택                   |
| B (boundaries)         | ✓ 정확 (lazy import 추적 정밀)                                              | Top 10 ROI 그대로 활용                                           |
| C (cleanup)            | dead 판정 다수 false (`_template_explanation` / `_detect_rule_based_risks`) | async `explain_decision` 1건만 신뢰. shell collapse roadmap 은 ✓ |
| D (monkeypatch)        | ✓ 정확 (21 shell-anchored 카운트 일치)                                      | 마이그레이션 안전 패턴 그대로 활용                               |

**교훈**: agent 결과는 단서이지 결정이 아니다. 의심 항목은 직접 grep 검증.

### 1.2 사용자 simplicity 원칙

- **추상화 도입 금지**: Protocol / registry / ABC / Service 베이스 클래스 신설 ❌
- **디렉토리 깊이 ≤ 3 단계**: `domain/`, `application/{X}/`, `infrastructure/{X}/`. value_objects/, entities/ 디렉토리화 ❌
- **shared/ 신설 ❌**: cross-cutting 은 `application/_shared/` 로 흡수 (parent layer 안에 격리)
- **dataclass + 함수만**: 도메인 로직은 frozen dataclass + pure function
- **이미 잘 작동하는 패턴 유지**: `solver/constraints/` 6 카테고리 (Approach C), `_shared` underscore-prefix

### 1.3 가치/비용 분석

| 작업                                                    | 가치           | 비용         | 본 라운드 채택                             |
| ------------------------------------------------------- | -------------- | ------------ | ------------------------------------------ |
| services/ 루트 30 파일 → sub-package 분류               | High           | Low (git mv) | ✓ Phase 1                                  |
| domain/ leaf 추가 (sheath_cluster, tardiness 이동)      | High           | Low          | ✓ Phase 1                                  |
| domain/constraint_rules.py 신설 (resolve_spec/color)    | High           | Low          | ✓ Phase 1 (REVIEWER M5)                    |
| infra/ leaf (calendar_engine, parsers, exporters, llm/) | High           | Low          | ✓ Phase 1                                  |
| application/\_shared/ 신설                              | High           | Medium       | ✓ Phase 1                                  |
| application/scheduling/{cp_sat, greedy}/ sub-package    | High           | Medium       | ✓ Phase 1                                  |
| llm_explainer.py 분해                                   | High           | Medium       | ✓ Phase 2                                  |
| cp_sat_schedule 1657→≤200 LOC + lex wiring              | High           | High         | ✓ Phase 3                                  |
| `_run_optimization_once` SchedulerState SRP             | High           | High         | ✓ Phase 4                                  |
| **characterization smoke test (Phase 1 진입 게이트)**   | High           | Low          | ✓ **Phase 0.5 신설 (REVIEWER 공통)**       |
| schedule_optimizer.py D7-C shell 삭제                   | Medium         | Medium       | △ **Phase 5 로 deferral (codex 5번)**      |
| routes/ ORM 직접 import 정리 (19+)                      | Medium         | High         | △ Phase 1 step 5 일부만, 전체는 post-pilot |
| domain entities/ value_objects/ 디렉토리 분리           | Low            | Medium       | ❌ 미실시                                  |
| shared/ 신설 layer                                      | Low            | Low          | ❌ 미실시                                  |
| Protocol / Registry / ABC 신설                          | Low (negative) | Medium       | ❌ 미실시                                  |

### 1.4 Reviewer feedback 통합 — 8 patch

| #   | 출처                  | Patch                                                                                                               |
| --- | --------------------- | ------------------------------------------------------------------------------------------------------------------- |
| P1  | codex 5번             | **Shell 삭제를 Phase 5 로 이동**. Phase 1~4 동안 90 LOC 무료 보험 유지                                              |
| P2  | EM M1                 | **Phase 1 step 4 → 4a / 4b 분리**. solver/\* 이동 (cp_sat_optimizer 위치 유지) → 별도 commit 에 rename              |
| P3  | EM M2                 | **Phase 1 step 6 단순화**. route flip 만; test patch retarget 은 shell 유지 상태에서, shell delete 는 Phase 5       |
| P4  | EM M5 + codex 2번     | **`domain/constraint_rules.py` 신설**. `resolve_spec_setup_min`, `resolve_color_change_min` 분리                    |
| P5  | EM M3                 | **Phase 4 SchedulerState 격리 unit test** (`tests/test_scheduler_state_isolation.py`)                               |
| P6  | EM M4                 | **Phase 3 lex_min_time adapter + INFEASIBLE fallback + §9.2 dual run_label** 명시                                   |
| P7  | codex 5번 ("missing") | **Phase 0.5 신설 — characterization smoke test**. old `app.services.*` ↔ new path 가 같은 객체 resolve. shell 보호. |
| P8  | EM NTH#1              | **§6 검증 gate 강화**: 매 step `python -c "import app.main; from app import services"` + cycle detector             |

### 1.5 효율 재추정 (REVIEWER 후)

| Phase    | 직전 추정  | 수정 추정   | 비고                                                     |
| -------- | ---------- | ----------- | -------------------------------------------------------- |
| 0        | 1.5h       | 1.5h ✓      | 완료                                                     |
| 0.5      | (없음)     | 0.5h        | smoke test + 즉시 삭제                                   |
| 1        | 3-4h       | 5-6h        | step 4a/4b 분리 + step 6 단순화로 commit 수 증가         |
| 2        | 1.5h       | 2h          | hallucination filter 신규 unit test 명시                 |
| 3        | 3h         | 4-5h        | adapter + INFEASIBLE fallback + dual run_label fixture   |
| 4        | 3-4h       | 4h          | SchedulerState 격리 test 추가                            |
| 5        | 1.5h       | 2-3h        | shell delete + 21 patch site flip + ERP e2e + gstack QA  |
| **총합** | **~13.5h** | **~18-22h** | **multi-session 가능성**, 매 phase 후 atomic commit 보존 |

---

## 2. Target Layer Structure

```
backend/app/
├── domain/                              # 순수 도메인 — DB/I/O/외부 SDK 의존 0
│   ├── __init__.py
│   ├── constants.py                    # 그대로 (PROCESS_ORDER, weights — single source)
│   ├── entities.py                     # 그대로 (Equipment, Order, ScheduleTask 등 dataclass)
│   ├── constraints.py                  # 그대로 (validators)
│   ├── constraint_rules.py             # NEW (P4) — resolve_spec_setup_min / resolve_color_change_min (pure dispatch)
│   ├── sheath_cluster.py               ← services/sheath_cluster.py (310 LOC, dataclass + pure)
│   ├── batch_sheath_keys.py            ← services/batch_grouping/{constants,sheath}.py 합본
│   └── tardiness.py                    ← services/tardiness_metrics.py (no DB, pure 계산)
│
├── application/                         # use-case orchestration — DB OK, I/O OK
│   ├── __init__.py
│   ├── _shared/                        # cross-cutting
│   │   ├── __init__.py
│   │   ├── audit_logger.py             ← services/audit_logger.py
│   │   ├── constraint_params.py        ← services/constraint_params.py (load + dataclass ONLY; resolve_* → domain/constraint_rules.py)
│   │   ├── calendar_ops.py             ← services/scheduling_shared/calendar_ops.py
│   │   ├── group_ops.py                ← services/scheduling_shared/group_ops.py
│   │   ├── slot_filters.py             ← services/scheduling_shared/slot_filters.py
│   │   └── db_ops.py                   ← services/scheduling_shared/db_ops.py
│   ├── scheduling/
│   │   ├── __init__.py
│   │   ├── cp_sat/                     # CP-SAT 스택
│   │   │   ├── __init__.py
│   │   │   ├── orchestrator.py         ← cp_sat_optimizer.py 의 cp_sat_schedule() (Phase 3 분해, ≤200 LOC)
│   │   │   ├── helpers.py              ← cp_sat_optimizer.py 의 _resolve_num_workers / _build_snapshot_weights / _priority_label / _compute_group_duration
│   │   │   ├── input_builder.py        ← services/solver/input_builder.py
│   │   │   ├── model_builder.py        ← services/solver/model_builder.py
│   │   │   ├── objective.py            ← services/solver/objective.py
│   │   │   ├── snapshot.py             ← services/solver/snapshot.py
│   │   │   ├── trace_writer.py         ← services/solver/trace_writer.py
│   │   │   ├── preemption.py           ← services/solver/preemption.py
│   │   │   ├── decision_aggregator.py  ← services/solver/decision_aggregator.py
│   │   │   ├── lex_min_time.py         ← services/solver/lex_min_time.py (Phase 3 wiring + adapter)
│   │   │   ├── constraint_loader.py    ← services/solver/constraint_loader.py
│   │   │   └── constraints/            ← services/solver/constraints/ 그대로
│   │   ├── greedy/
│   │   │   ├── __init__.py
│   │   │   ├── auto_schedule.py        ← services/greedy/auto_schedule.py (retry harness)
│   │   │   ├── optimization_loop.py    ← services/greedy/optimization_loop.py (Phase 4 SRP)
│   │   │   ├── scheduler_state.py      # NEW Phase 4 — ~17 cache 변수 dataclass + isolation test
│   │   │   ├── reschedule_affected.py  ← services/greedy/reschedule_affected.py
│   │   │   ├── slot_finder.py          ← services/greedy/slot_finder.py
│   │   │   ├── jit_scheduling.py       ← services/jit_scheduling.py
│   │   │   └── loaders/                ← services/greedy/loaders/ 그대로
│   ├── decisions/
│   │   ├── __init__.py
│   │   ├── narrator.py                 ← services/decision_narrator.py (hallucination filter)
│   │   ├── explain_batch.py            # NEW Phase 2 — llm_explainer.explain_decision_sync 흡수
│   │   ├── summarize_run.py            # NEW Phase 2 — llm_explainer.generate_batch_summary_sync 흡수
│   │   └── risk_detector.py            # NEW Phase 2 — _detect_rule_based_risks 분리
│   ├── validation/
│   │   ├── __init__.py
│   │   ├── constraint_checker.py       ← services/constraint_checker.py
│   │   ├── schedule_validators.py      ← services/schedule_validators.py
│   │   └── batch_group_lifecycle.py    ← services/batch_group_lifecycle.py
│   ├── ingest/                         # ERP/WIP 입수 + Stage1/2 entry + batch grouping
│   │   ├── __init__.py
│   │   ├── pipeline_orchestrator.py    ← services/pipeline/orchestrator.py
│   │   ├── stage1.py                   ← services/pipeline/stage1.py
│   │   ├── stage2.py                   ← services/pipeline/stage2.py
│   │   ├── run_labeler.py              ← services/pipeline/run_labeler.py
│   │   ├── batch_grouper.py            ← services/batch_grouping/grouper.py (1091 LOC)
│   │   ├── batch_splitter.py           ← services/batch_grouping/splitting.py
│   │   ├── batch_helpers.py            ← services/batch_grouping/helpers.py
│   │   ├── wip_matching.py             ← services/wip_matching.py
│   │   └── wip_promotion.py            ← services/wip_promotion.py
│   ├── cascade/                        ← services/cascade/ 그대로
│   ├── stage2_job_queue.py             ← services/stage2_job_queue.py
│   └── sm_inventory.py                 ← services/sm_inventory.py
│
├── infrastructure/
│   ├── __init__.py
│   ├── database.py                     # 그대로
│   ├── memory_store.py                 # 그대로
│   ├── models/                         # 그대로 (SQLAlchemy ORM models)
│   ├── logging/                        # 그대로
│   ├── calendar_engine.py              ← services/calendar_engine.py
│   ├── llm/
│   │   ├── __init__.py                 ← services/llm_providers/__init__.py
│   │   ├── anthropic_provider.py       ← services/llm_providers/anthropic.py
│   │   └── template_provider.py        ← services/llm_providers/template.py
│   ├── parsers/
│   │   ├── __init__.py
│   │   ├── erp_parser.py               ← services/erp_parser.py
│   │   └── wip_parser.py               ← services/wip_parser.py
│   ├── exporters/
│   │   ├── __init__.py
│   │   ├── excel_exporter.py           ← services/excel_exporter.py
│   │   └── wip_template.py             ← services/wip_template.py
│   └── wip_lifecycle_listener.py       ← services/wip_lifecycle_listener.py
│
├── presentation/                       # 그대로 (routes, schemas)
│   ├── routes/
│   └── schemas/
│
└── services/                           # **Phase 5 끝까지 schedule_optimizer.py D7-C shell 만 보존**
    └── schedule_optimizer.py           # 90 LOC, Phase 5 §9.5 에서 21 patch site flip 후 삭제
```

---

## 3. Deferred 5 항목 신 위치 매핑

| #   | 직전 deferred                               | 본 라운드 신 위치                                                                                                                                                                       | 처리 phase                                      |
| --- | ------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------- |
| 1   | `llm_explainer.py` (620 LOC)                | `application/decisions/{explain_batch, summarize_run, risk_detector}.py` + `infrastructure/llm/`                                                                                        | **Phase 2**                                     |
| 2   | `schedule_optimizer.py` D7-C shell (90 LOC) | **Phase 5 에서 삭제** (P1: codex 권고). Phase 1~4 동안 무료 보험 유지. Phase 5 §9.5 에서 21 patch site flip + route flip 후 단일 atomic commit 으로 shell + `services/__init__.py` 삭제 | **Phase 5 §9.5**                                |
| 3   | `cp_sat_schedule()` 1657 LOC                | `application/scheduling/cp_sat/orchestrator.py` ≤200 LOC + helpers.py + body 의 §1~§9 분산                                                                                              | **Phase 3** (P6: adapter + INFEASIBLE fallback) |
| 4   | `_run_optimization_once` 964 LOC            | `application/scheduling/greedy/optimization_loop.py` (≤100 LOC orchestrator) + `scheduler_state.py` (NEW) + 헬퍼 3개                                                                    | **Phase 4** (P5: isolation unit test)           |
| 5   | `lex_min_time` × `cp_sat_schedule` wiring   | `min_time_mode: bool` flag + adapter (`LexResult` → `BuiltModel.solve_result` shape) + INFEASIBLE → weighted-sum fallback + scenarios 12, 13                                            | **Phase 3** (P6)                                |

---

## 4. Migration 순서 — leaf-first

각 step **atomic commit + parity-quick + main-parity gate + smoke import + cycle detector** (P8). EXPECTED_DRIFT 외 회귀 0 확인 후 다음.

### Phase 0.5 — Characterization & 즉시 삭제 (NEW, P7)

**소요**: ~30분. Phase 1 진입 게이트.

#### Step 0.5a — Characterization smoke test

새 파일 `backend/tests/test_namespace_smoke.py`:

```python
"""Phase 1+ 마이그레이션의 namespace 무결성 보증.

old `app.services.*` 와 new `app.{domain,application,infrastructure}.*`
양쪽이 같은 함수 객체로 resolve 되는지 검증한다. shell 이 보호되는 한
이 테스트는 phase 1~4 동안 모두 그린이어야 한다. Phase 5 에서 shell
삭제 시 본 테스트도 함께 정리한다.
"""

# 본 round 종료까지 shell 유지가 보장되는 21 monkeypatch 표적 +
# 그 외 안전한 캐릭터라이징 대상.
import importlib

PAIRS = [
    # (legacy module path, attribute, new module path)
    # Phase 1 step 1 후:
    ("app.services.sheath_cluster", "build_sheath_clusters", "app.domain.sheath_cluster"),
    ("app.services.tardiness_metrics", "count_tardiness", "app.domain.tardiness"),
    # Phase 1 step 2 후:
    ("app.services.calendar_engine", "calculate_end_datetime", "app.infrastructure.calendar_engine"),
    # Phase 1 step 3 후:
    ("app.services.audit_logger", "log_decision", "app.application._shared.audit_logger"),
    ("app.services.constraint_params", "ConstraintParams", "app.application._shared.constraint_params"),
    # ... (각 phase step 별로 추가)
]


def test_legacy_paths_resolve_to_new_objects(subtests=None):
    """모든 PAIRS 의 legacy attr 와 new attr 가 동일 객체 (is) 인지 확인."""
    failures = []
    for legacy_mod, attr, new_mod in PAIRS:
        try:
            lm = importlib.import_module(legacy_mod)
            nm = importlib.import_module(new_mod)
        except ImportError as e:
            # phase 진행 중에는 일부 new_mod 가 아직 없을 수 있음 — skip 정책
            failures.append(f"{new_mod} not importable yet: {e}")
            continue
        if not hasattr(lm, attr) or not hasattr(nm, attr):
            failures.append(f"{legacy_mod}.{attr} or {new_mod}.{attr} missing")
            continue
        if getattr(lm, attr) is not getattr(nm, attr):
            failures.append(f"{legacy_mod}.{attr} is NOT {new_mod}.{attr}")
    assert not failures, "\n".join(failures)
```

**용도**: 매 step 의 import-경로 update 후 본 테스트가 그린이어야 다음 step 진입. monkeypatch contract 의 무성한 보험.

#### Step 0.5b — 안전한 즉시 삭제 (검증 완료)

| 삭제 대상                                                                                                       | LOC  | 검증           |
| --------------------------------------------------------------------------------------------------------------- | ---- | -------------- |
| `llm_explainer.py::explain_decision` (async) + 의존 `_call_llm`, `_call_openai`, `_call_anthropic` (모두 async) | ~150 | grep 0 callers |
| `services/__init__.py` (empty file)                                                                             | 0    | 검증됨         |

**보존 (audit C 잘못 판정)**: `_template_explanation`, `_detect_rule_based_risks`, `_call_llm_sync`, `_build_context` ✓ 모두 LIVE.

원자 commit: `chore(cleanup): drop dead async explain_decision + helpers + empty services/__init__.py`

### Phase 1 step 1 — `domain/` leaf 추가 (의존성 0)

- `git mv backend/app/services/sheath_cluster.py backend/app/domain/sheath_cluster.py`
- `git mv backend/app/services/tardiness_metrics.py backend/app/domain/tardiness.py`
- 신규 합본 `backend/app/domain/batch_sheath_keys.py` ← `batch_grouping/{constants,sheath}.py` 의 union (실제 두 파일 read → 한 파일 write → 두 원본 git rm)
- 동일 commit 에 importer (`solver/constraints/process/sheath_color_hard.py`, `batch_grouping/grouper.py`, `cp_sat_optimizer.py`, `greedy/optimization_loop.py`) import 경로 update

### Phase 1 step 2 — `infrastructure/` leaf 추가

- `git mv calendar_engine.py → infrastructure/calendar_engine.py`
- `git mv erp_parser.py → infrastructure/parsers/erp_parser.py` (디렉토리 신설)
- `git mv wip_parser.py → infrastructure/parsers/wip_parser.py`
- `git mv excel_exporter.py → infrastructure/exporters/excel_exporter.py`
- `git mv wip_template.py → infrastructure/exporters/wip_template.py`
- `git mv llm_providers/__init__.py → infrastructure/llm/__init__.py`
- `git mv llm_providers/anthropic.py → infrastructure/llm/anthropic_provider.py`
- `git mv llm_providers/template.py → infrastructure/llm/template_provider.py`
- `git mv wip_lifecycle_listener.py → infrastructure/wip_lifecycle_listener.py`
- 각 commit 에 importer 경로 update + smoke test 갱신

### Phase 1 step 3 — `domain/constraint_rules.py` + `application/_shared/` 신설 (P4)

- 신규 `domain/constraint_rules.py` 작성 — `resolve_spec_setup_min` + `resolve_color_change_min` 추출 (현재 `services/constraint_params.py:78~106`). pure dispatch over frozen dataclass, DB 의존 0.
- `git mv services/audit_logger.py → application/_shared/audit_logger.py`
- `git mv services/constraint_params.py → application/_shared/constraint_params.py` (load + dataclass 만 남김; resolve\_\* 는 위에서 분리됨)
- `git mv services/scheduling_shared/{calendar_ops,group_ops,slot_filters,db_ops}.py → application/_shared/`
- 8 importer (cp*sat_optimizer, greedy/auto_schedule, greedy/loaders/master_data, greedy/optimization_loop, solver/constraints/process/{sheath_color_hard, sheath_color_sequence}, solver/input_builder, batch_grouping/grouper) 의 `from app.services.constraint_params import resolve*_`→`from app.domain.constraint*rules import resolve*_` 로 변경
- ConstraintParams 자체 import 는 `from app.application._shared.constraint_params import ConstraintParams`

### Phase 1 step 4a — solver/\* 이동 (cp_sat_optimizer.py 위치 유지) (P2)

- `git mv services/solver/* → application/scheduling/cp_sat/`
- `git mv services/jit_scheduling.py → application/scheduling/greedy/jit_scheduling.py`
- `git mv services/greedy/{auto_schedule, optimization_loop, reschedule_affected, slot_finder}.py + loaders/ → application/scheduling/greedy/`
- `cp_sat_optimizer.py` 자체는 **services/ 위치 유지**. 단, 본 파일 안의 모든 `from app.services.solver.*` import 를 `from app.application.scheduling.cp_sat.*` 로 update
- 동일 commit 에 `services/schedule_optimizer.py` shell 의 `from app.services.greedy.*` import 도 새 경로로 update (shell 자체는 살아있음, P1)
- 18 importer (대부분 cp_sat_optimizer, schedule_optimizer shell) 의 import path update

### Phase 1 step 4b — cp_sat_optimizer.py → orchestrator.py rename (P2)

- 단일 atomic commit: `git mv services/cp_sat_optimizer.py → application/scheduling/cp_sat/orchestrator.py`
- 본 파일의 의미적 분해는 Phase 3. 본 step 은 위치 변경만.
- importers (`pipeline/stage1.py`, `services/schedule_optimizer.py`, tests) 경로 update
- helpers 추출은 Phase 3 의 일부로 deferral (본 step 은 1657 LOC 통째 이동)

### Phase 1 step 5 — 잔여 `services/` 모듈 application 으로 + routes 호출처 update

- `git mv services/decision_narrator.py → application/decisions/narrator.py`
- `git mv services/{constraint_checker, schedule_validators, batch_group_lifecycle}.py → application/validation/`
- `git mv services/pipeline/* → application/ingest/` (단, 파일 이름 유지 또는 plan §2 의 명료화 적용)
- `git mv services/batch_grouping/{grouper, splitting, helpers}.py → application/ingest/`
- `git mv services/{wip_matching, wip_promotion}.py → application/ingest/`
- `git mv services/cascade → application/cascade` (sub-package 통째 이동)
- `git mv services/{stage2_job_queue, sm_inventory}.py → application/`
- 모든 importer (특히 `routes/*`) 경로 update
- `services/llm_explainer.py` 는 Phase 2 까지 위치 유지
- `services/schedule_optimizer.py` 는 Phase 5 까지 위치 유지 (본 step 의 routes 변경에서 일부 importer 가 shell 을 우회 — shell 은 21 monkeypatch site 만 의존)

### Phase 1 step 6 — routes import flip (P3, 단순화)

- `routes/schedules/__init__.py:81`, `routes/schedules/cascade.py:47`, `routes/plan_pipeline.py:41` 의 `from app.services.schedule_optimizer import ...` 를 새 path 로 flip
- `routes/audit.py:8`, `routes/plan_pipeline.py:66,909` 의 `from app.services.llm_explainer import ...` 는 Phase 2 까지 유지 (llm_explainer 자체가 Phase 2 분해 대상)
- **shell 은 본 step 에서 삭제하지 않음** (Phase 5)
- 21 monkeypatch site 의 retarget 도 본 step 에서 하지 않음 (Phase 5)
- characterization smoke test 갱신 — routes/\* 는 새 path 사용 확인

### Phase 2 — llm_explainer 분해

- Step 1: `application/decisions/risk_detector.py` 추출 — `_detect_rule_based_risks` (pure 룰)
- Step 2: `application/decisions/summarize_run.py` 작성 + `narrator.explain_with_filter` 적용 + ai-summary route 전환 + parity (LLM_NONDETERMINISTIC shape-only)
- Step 3: `application/decisions/explain_batch.py` 작성 + audit/explain route 전환 + parity
- Step 4: 신규 unit tests — hallucination filter 가 두 use-case 에서 작동, **N≥3 specific hallucinated noun classes** 거부 (EM NTH#4)
- Step 5: `services/llm_explainer.py` 삭제 + main-parity gate

### Phase 3 — cp_sat_schedule 분해 + lex wiring (P6)

- Step 1 — ultrathink 로 `cp_sat_schedule` body 의 §1~§9 식별 + `CpSatRunState` dataclass 설계
- Step 2 — §1~§5 (입력 검증 / 그루핑 / 그룹 메타) 추출
- Step 3 — §6~§7 (모델 / 솔버) 분기점에 lex adapter 도입:

  ```python
  # application/scheduling/cp_sat/orchestrator.py
  def cp_sat_schedule(..., min_time_mode: bool = False) -> dict:
      _built = build_model(...)
      if min_time_mode:
          lex_result = solve_lex_min_time(_built, ...)  # LexResult 반환
          if lex_result.status == "INFEASIBLE":
              # FALLBACK: weighted-sum 으로 재시도
              compose_objective(_built, ...)
              result = solve_weighted_sum(_built, ...)
              audit_log("lex_min_time INFEASIBLE — fell back to weighted-sum")
          else:
              result = _adapt_lex_to_solve_result(lex_result, _built)  # 신규 adapter
      else:
          compose_objective(_built, ...)
          result = solve_weighted_sum(_built, ...)
      ...
  ```

- Step 4 — `_adapt_lex_to_solve_result` 함수 신설 — `LexResult` (dataclass) → 기존 `BuiltModel` 기반 결과 shape 으로 변환. 같은 ScheduleTask insert 경로 사용
- Step 5 — §9.2 dual-mode 비교 시 **별도 run_label** 사용 (같은 run_label 에 두 번 POST 는 stage2 purge 가 stage1 데이터를 절대 건드리지 않으므로 동치 비교 가능 단 `_purge_run_data` 가 schedule_task 를 비우는 행동에 주의 — fixture 12, 13 는 별도 run_label 또는 stage1 재실행 후 비교)
- Step 6 — 시나리오 12 (모두 due 충족), 13 (past-due 강제) fixture 추가 + 결정론적 통과
- **Early warning signal** (codex 4번): 추출 helper 가 거대 인자 list 요구 / mutable state 누수 / weighted-sum parity drift 가 lex_min_time=True 시도 전에 발생. 이 신호 발견 시 **즉시 정지** 하고 SchedulerState dataclass 부터 도입할지 (Phase 4 와 swap) 검토.

### Phase 4 — SchedulerState SRP (P5)

- Step 1 — `application/scheduling/greedy/scheduler_state.py` 신설 — 직전 세션 §6.2 의 ~17 cache 변수 dataclass:

  ```python
  @dataclass
  class SchedulerState:
      # mutable state
      timeline: dict[str, list[tuple[datetime, datetime]]] = field(default_factory=dict)
      predecessor_map: dict[tuple[str, int], int] = field(default_factory=dict)
      process_end_by_sq: dict[tuple[str, int], datetime] = field(default_factory=dict)
      # ... (총 ~10 mutable)
      # frozen-style master data (read-only after construction)
      equipment_by_process: dict = field(default_factory=dict)
      speed_map: dict = field(default_factory=dict)
      constraint_params: ConstraintParams = field(default_factory=ConstraintParams)
      welding_min: int = 30
  ```

  EM NTH#2: master data 부분 frozen 화 검토 — 단, `@dataclass(frozen=True)` 는 nested field 를 막지 않으므로 **convention 으로만** ("read-only after construction" docstring + `__post_init__` 이 검증 없는 simple init)

- Step 2 — 추출 함수: `_seed_state_from_existing(state, run_label, db)`, `_group_and_sort(schedulable, state)`, `_assign_group(state, group_key, group_batches, db, run_label, base_date)`
- Step 3 — `auto_schedule.py` 의 retry block 이 `SchedulerState()` 를 **루프 내부에서** 생성 (cross-contamination 방지)
- Step 4 — 신규 `tests/test_scheduler_state_isolation.py` (P5):

  ```python
  def test_retry_isolation(monkeypatch):
      """retry 1 의 mutation 이 retry 2 시작 시 보이면 안 됨."""
      seen_states = []

      def fake_run(run_label, db, *, base_date):
          # SchedulerState 가 retry 마다 새로 생성되는지 확인
          state = SchedulerState()
          state.timeline.setdefault("EQ-1", []).append((some_dt, other_dt))
          seen_states.append(state)
          return {"total_tasks": 0, "violations": [], "warnings": []}

      monkeypatch.setattr(
          "app.application.scheduling.greedy.optimization_loop._run_optimization_once",
          fake_run,
      )
      # retry harness 2회 호출 강제
      auto_schedule(run_label, db, base_date=base_dt)

      # 두 retry 의 state.timeline 이 서로 다른 dict 객체인지
      assert seen_states[0].timeline is not seen_states[1].timeline
      assert seen_states[1].timeline == {}  # 두 번째는 깨끗한 상태
  ```

- Step 5 — `_run_optimization_once` body ≤ 100 LOC orchestrator 화 + parity 검증

### Phase 5 — 실 ERP e2e + gstack QA + **D7-C shell 정리** + 최종 main-parity (P1, P3)

- §9.1 실 ERP 파일 양 서버 동치 확인 (`Documents/ERP생산계획_v1.xls`, `Documents/SM재고리스트.xls`)
- §9.2 lex vs weighted-sum 비교 — **별도 run_label** 두 개 생성 후 비교 → `docs/lex-mode-comparison.md`
- §9.3 gstack/qa skill walkthrough — Stage1 업로드 → Stage2 실행 → Gantt → Decision Card → 콘솔 에러 0 → screenshots
- §9.4 **shell delete + 21 patch site flip** (P3 deferred from Phase 1):
  1. `tests/test_overlap_retry.py`, `tests/test_pipeline_alignment.py`, `tests/test_jit_integration.py` 등 21 patch site 의 monkeypatch target 을 `app.services.schedule_optimizer.X` → 새 namespace (예: `app.application.scheduling.greedy.auto_schedule._run_optimization_once`) 로 retarget
  2. `services/schedule_optimizer.py` shell + 빈 `services/__init__.py` 삭제
  3. 21 retarget 의 atomic commit + 1 shell delete commit (총 2 commit) — 빠른 rollback 가능
- §9.5 최종 main-parity 27/27 + smoke test 갱신 + worktree 제거

---

## 5. 안전한 즉시 삭제 (Phase 0.5b 에서 처리)

§4 Phase 0.5b 참조. 반복 회피.

---

## 6. 검증 게이트 — 매 step (P8)

```bash
# 1. smoke import (cycle detector 겸용)
cd /Users/jaewookim/Desktop/Project/KBI_PoC/backend && \
  venv/bin/python -c "import app.main; from app import services; from app import domain; from app import application; from app import infrastructure" || exit 1

# 2. characterization smoke test
cd /Users/jaewookim/Desktop/Project/KBI_PoC/backend && \
  venv/bin/python -m pytest tests/test_namespace_smoke.py -v

# 3. 백엔드 pytest (428+ green)
cd /Users/jaewookim/Desktop/Project/KBI_PoC/backend && \
  venv/bin/python -m pytest tests/ -q --ignore=tests/test_parity_harness.py

# 4. parity-quick (3/3)
cd /Users/jaewookim/Desktop/Project/KBI_PoC/backend && \
  venv/bin/python -m pytest tests/test_parity_harness.py -m parity --parity-quick -v

# 5. refac backend 재시작 (코드 변경 반영) — 매 step 의 마지막
pkill -f "uvicorn.*--port 8000"
cd /Users/jaewookim/Desktop/Project/KBI_PoC/backend && \
  nohup venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 > /tmp/backend_refac.log 2>&1 &
sleep 3

# 6. main-parity gate (27/27)
/Users/jaewookim/Desktop/Project/KBI_PoC/backend/venv/bin/python \
  tests/main_parity/parity_endpoint_diff.py --report /tmp/parity_phase<N>.md
```

EXPECTED_DRIFT 외 0 회귀 확인 후 다음 step.

---

## 7. 위험 / 대비책

| 위험                                                           | 대비책                                                                                                                                  |
| -------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| `git mv` 후 import 경로 누락 → import 깨짐                     | 매 commit 에 §6 step 1 (smoke import) 통과 필수                                                                                         |
| Auto-formatter 가 deferred import 절단                         | 모든 신규 import 에 `# noqa: F401  # used at <위치>` (직전 세션 trap)                                                                   |
| backend/ vs root cwd 혼동                                      | 모든 pytest 명령 절대경로 (`cd /Users/jaewookim/Desktop/Project/KBI_PoC/backend && venv/bin/python -m pytest …`)                        |
| protobuf negative-indexing trap                                | `proto.domain[-1]` 금지, `list(proto.domain)[-1]` (직전 세션 trap)                                                                      |
| schedule_optimizer.py shell 삭제 시 21 patch site 깨짐         | **shell 을 Phase 5 까지 유지** (P1). Phase 5 §9.4 에서 21 retarget commit + shell delete commit 분리 — 빠른 rollback                    |
| LLM 응답 / DB-stateful drift 회귀 오인                         | `hide_list_len=True` shape-only 비교 (직전 세션 정착)                                                                                   |
| **Phase 3 derail risk (codex 4번)**                            | 추출 helper 거대 인자 list / mutable state 누수 / weighted-sum parity drift 발견 시 **즉시 정지** + Phase 4 SchedulerState 와 swap 검토 |
| Phase 4 SchedulerState retry contamination                     | (P5) `tests/test_scheduler_state_isolation.py` — 매 retry 의 `state.timeline` 이 서로 다른 dict 객체임을 assert                         |
| llm_explainer 마이그레이션 중 ai-summary text 변동             | `LLM_NONDETERMINISTIC` shape-only 정책 — text drift 무시                                                                                |
| 디렉토리 신설 후 routes ORM 직접 import (19+) 미해결           | 본 라운드는 routes 의 import path flip (Phase 1 step 6) 만, ORM-via-service refactor 는 post-pilot backlog 에 격상 등록                 |
| Phase 1 의 다단 commit (≥10) 컨텍스트 한계                     | 매 step atomic commit + 본 문서 §11 markers 갱신 → 다음 세션 재개 가능                                                                  |
| Phase 1 step 4a/4b 분리 부족 시 cp_sat solver+rename collision | (P2) step 4a 끝까지 cp_sat_optimizer.py 위치 유지; step 4b 는 단순 rename 만                                                            |
| §9.2 dual-mode 비교 시 같은 run_label 사용                     | (P6) 별도 run_label 두 개 사용. stage1 재실행 또는 stage1 결과 복제 (현재 plan 의 run_label 재사용은 stage2_purge 동작과 호환)          |
| gstack QA 가 frontend 버그 수정 시 backend parity 깨짐         | 가능성 낮음. 그래도 fix 후 main-parity 재실행                                                                                           |

---

## 8. 종료 조건 (Phase 1 단독)

- [ ] `services/` 디렉토리에 `schedule_optimizer.py` 와 `llm_explainer.py` 만 남음 (둘 다 Phase 2 / 5 에서 제거)
- [ ] `domain/` 에 `constraint_rules.py` (NEW), `sheath_cluster.py`, `tardiness.py`, `batch_sheath_keys.py` 추가
- [ ] `application/_shared/`, `application/scheduling/{cp_sat,greedy}/`, `application/{decisions, validation, ingest, cascade}/` 모두 존재
- [ ] `infrastructure/{calendar_engine, llm/, parsers/, exporters/}` 신설
- [ ] routes/audit.py + routes/plan_pipeline.py 가 `services.llm_explainer` 임시 path 사용 (Phase 2 종료 시점에 정리)
- [ ] routes/schedules/\* 가 새 import path 사용
- [ ] async `explain_decision` + 그 helper 4개 삭제 (~150 LOC) — Phase 0.5b
- [ ] `services/__init__.py` 삭제 — Phase 0.5b
- [ ] `tests/test_namespace_smoke.py` 그린
- [ ] **27/27 main-parity gate identical**
- [ ] **428+ backend pytest green** + 신규 smoke + lex/scheduler-state isolation (Phase 3/4 도달 시) green
- [ ] **11/11 parity-quick green**
- [ ] frontend build 그대로 (frontend 무변경)
- [ ] `services/schedule_optimizer.py` shell **유지** (Phase 5 까지 보호)

---

## 9. ROI 우선순위

1. **High value, low cost — 즉시**:
   - Phase 0.5 (smoke test + 즉시 삭제)
   - Phase 1 step 1, 2 (leaf 이동)
2. **High value, medium cost**:
   - Phase 1 step 3, 4a, 4b, 5
   - cp_sat / greedy sub-package 재배치
3. **High value, high cost**:
   - Phase 2 llm_explainer 분해
   - Phase 3 cp_sat_schedule 분해 + lex wiring
   - Phase 4 SchedulerState
4. **Medium value, high cost — Phase 1 부분만**:
   - routes import path flip (Top 5)
5. **Low value — 미실시**:
   - domain/ 디렉토리화
   - shared/ layer 신설
   - Protocol/registry/ABC

---

## 10. 사용자 승인 — 완료 (2026-04-26)

**상태**: ✅ APPROVED (proceed). Phase 0.5 진입.

승인 항목:

1. Layer 구조 (§2) — domain/\_shared 미신설, application/\_shared/ 채택, sub-package 분류
2. Migration 순서 (§4) — leaf-first, Phase 0.5 + 1.{1,2,3,4a,4b,5,6} + 2~5
3. Deferred 5 매핑 (§3) — 모두 본 7-phase 안에 흡수
4. 즉시 삭제 항목 (§4 Phase 0.5b) — async `explain_decision` + helpers
5. D7-C shell 처리 — Phase 5 까지 유지, Phase 5 §9.4 에서 retarget + delete (P1)
6. routes ORM 정리 — Top 5 만 본 라운드, 나머지 post-pilot
7. 효율 재추정 — ~18-22h, multi-session 가능

---

## 11. End-state markers (다음 세션 재개용)

다음 세션 시작 시 본 §11 을 먼저 읽고 어디서 재개할지 결정:

- [✓] Phase 0 (audit) 완료 — 4 audit + target 작성 + 사용자 승인
- [ ] Phase 0.5a — characterization smoke test 작성
- [ ] Phase 0.5b — 즉시 삭제 commit
- [ ] Phase 1 step 1 — domain/ leaf
- [ ] Phase 1 step 2 — infrastructure/ leaf
- [ ] Phase 1 step 3 — domain/constraint_rules.py + application/\_shared/
- [ ] Phase 1 step 4a — solver/\* 이동
- [ ] Phase 1 step 4b — cp_sat_optimizer.py → orchestrator.py rename
- [ ] Phase 1 step 5 — 잔여 services/ 이동
- [ ] Phase 1 step 6 — routes import flip
- [ ] Phase 2 — llm_explainer 분해
- [ ] Phase 3 step 1~6 — cp_sat_schedule 분해 + lex wiring
- [ ] Phase 4 — SchedulerState SRP
- [ ] Phase 5 §9.1~§9.5 — e2e + QA + shell delete + 최종 parity

**문서 작성**: 2026-04-26 Phase 0 종료 시점. 사용자 승인 후 즉시 갱신.
**다음 업데이트**: 매 phase step 완료 후 §11 marker 갱신.
