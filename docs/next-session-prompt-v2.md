# Next Session v2 — backend/app Clean Architecture + Deferred 흡수

> **사용법:** 다음 Claude 세션 시작 시 이 파일 경로를 알려주고 "이 문서대로 진행해" 라고 지시.
> 본 문서는 **harness 원칙** + **superpowers** + **agent team** + **ultrathink** + **main 과의 상호 검증** + **gstack QA** 의 7-phase 작업 계획서.

---

## 0. 사명 (Mission)

**작업 맥락**:

- 직전 세션 결과: `docs/next-session-prompt.md` §11 (commit `6b14773`) — Phase 0~3 partial 완료, ~1985 LOC 감축, 13 신규 모듈, 27/27 main-parity gate green.
- 브랜치: `refactoring`, origin/main 대비 +138 commits.
- 직전 세션이 명시적으로 **deferred** 한 5 항목 (모두 본 라운드에서 흡수):
  1. `llm_explainer.py` (620 LOC) → `decision_narrator` 마이그레이션 (hallucination filter 의 신규 도입 feature work)
  2. `schedule_optimizer.py` (90 LOC re-export 셸) 제거 + ~35 importer 의 직접 import 전환 (D7-C invariant)
  3. `cp_sat_schedule` (1657 LOC) 분해 → ≤200 LOC orchestrator
  4. `_run_optimization_once` assignment loop (~700 LOC) SRP — `SchedulerState` dataclass
  5. 실 ERP 파일 e2e + lex_min_time × cp_sat_schedule wiring (`min_time_mode` opt-in flag)

**이번 라운드 신규 사용자 요구** (직접 인용):

> backend/app 자체가 지금 clean architecture로 안 되어 있는 것 같다 (완전하게는 적어도). backend/app/services 특히 이 폴더 루트폴더에 너무 코드들이 난잡하게 들어가있다. 이것도 좀 정리되지 않을까. 단순 폴더정리를 하는걸 뛰어넘어서 각 코드를 모두 읽고 중복 내용이나 dead 내용 등이 있는지 파악해. 필요하면 clean archi 관점에서 코드 통합 / 분할 등을 통해 보기좋게 업데이트.

**위 6항목을 7-phase 로 분해해 순차 실행한다.** Phase 0 (audit) 의 결과가 Phase 1+ 의 작업 순서를 데이터-기반으로 결정한다.

---

## 1. 시작 전 필독 문서 (Required Reading)

| #   | 경로                                                                                            | 읽는 이유                                                             |
| --- | ----------------------------------------------------------------------------------------------- | --------------------------------------------------------------------- |
| 1   | `docs/next-session-prompt.md` §11                                                               | 직전 세션 end-state markers + 학습된 trap 패턴 + Approach C 결정 근거 |
| 2   | `docs/superpowers-final-report.md` §8                                                           | 그 이전 세션 known-debt 5 항목                                        |
| 3   | `docs/post-pilot-backlog.md`                                                                    | 정식 등록 backlog (architecture audit 발견 항목과 cross-reference)    |
| 4   | `ls backend/app/services/` 루트 + 하위                                                          | 현재 파일 인벤토리 — 서비스 루트 산만함의 실측                        |
| 5   | `backend/app/services/llm_explainer.py` + `decision_narrator.py` + `llm_providers/anthropic.py` | 마이그레이션 대상 + 타겟 인터페이스 비교                              |
| 6   | `backend/app/services/cp_sat_optimizer.py:1-200` + `solver/*` 인벤토리                          | cp_sat_schedule 분해 진입점                                           |
| 7   | `backend/app/services/greedy/optimization_loop.py:200~960` (assignment loop)                    | SchedulerState 분해 대상 — 직전 세션이 multi-hour 라 deferred         |
| 8   | `backend/app/services/schedule_optimizer.py` + 35 importer grep                                 | D7-C 셸 제거 + monkeypatch contract 전환 영향                         |
| 9   | `backend/app/{domain,infrastructure,presentation}/` 디렉토리 구조                               | clean arch boundary 의 현 상태                                        |

**Reading 후 자기점검 (답 못하면 재독)**:

- `backend/app/services/` 루트에 정확히 몇 개 파일이 있고 각각의 책임 (도메인 / 응용 / 인프라 / 미분류) 라벨링은?
- 어떤 파일이 SQLAlchemy ORM 직접 import (infrastructure 의존), 어떤 파일이 ConstraintParams + domain entity 만 다루는 (도메인 logic)?
- llm_explainer.py 의 4 public 함수 (`explain_decision_sync`, `generate_batch_summary_sync`, `_detect_rule_based_risks`, `explain_decision`) 각각의 호출처는?
- cp_sat_schedule 의 §1~§N 섹션 분리 가능한 단위는?
- `_run_optimization_once` assignment loop 가 사용하는 local cache 변수는 정확히 몇 개? (직전 세션 §6.2 인벤토리 참조)
- `feedback_constraint_arch_simplicity` 메모리 — Protocol/registry 미도입 결정이 본 라운드에도 적용되는가? (적용. 단 boundary 명확화는 별도 차원)

---

## 2. Harness 원칙 (직전 세션 검증된 — 변경 없음)

`superpowers:verification-before-completion` 의 Iron Law:

**NO COMPLETION CLAIMS WITHOUT FRESH VERIFICATION EVIDENCE**

| 주장            | 필요 증거                                                         |
| --------------- | ----------------------------------------------------------------- |
| 테스트 통과     | `pytest tests/ -q --ignore=tests/test_parity_harness.py` 출력     |
| parity-quick    | `pytest tests/test_parity_harness.py -m parity --parity-quick -v` |
| parity-full     | `pytest tests/test_parity_harness.py -m parity -v` (11/11)        |
| 빌드 OK         | `cd frontend && npm run build`                                    |
| dead code 정리  | grep importer 0건 + 테스트 통과                                   |
| 마이그레이션 OK | main-parity gate 27/27 identical (또는 EXPECTED_DRIFT 등록)       |
| QA OK           | gstack/qa skill 의 walkthrough 출력 + 콘솔 에러 0                 |

**금지 사항** (직전 세션에서 학습된 trap):

- "should work" / "probably" / "I believe"
- 검증 없는 phase 진입 / atomic commit 누락
- formatter 가 F401 import 절단 — 신규 import 는 항상 `# noqa: F401  # used at <위치>` 코멘트로 보호
- backend/ vs repo-root cwd 혼동 — 모든 pytest 명령 `cd /Users/jaewookim/Desktop/Project/KBI_PoC/backend && ...` 절대경로
- protobuf negative-indexing trap — `proto.domain[-1]` 금지, `list(proto.domain)[-1]` 사용
- 모듈 이동 시 동일 commit 에 import 경로 update 동봉 (분리 시 중간 상태가 깨짐)
- LLM 응답 / xlsx byte_len 차이를 회귀로 오인 — `LLM_NONDETERMINISTIC` / `STATEFUL_OR_BINARY` 화이트리스트 활용

---

## 3. Main-Branch Parity Gate (직전 세션 셋업 그대로)

### 3.1 셋업

```bash
cd /Users/jaewookim/Desktop/Project/KBI_PoC
git worktree add ../KBI_PoC_main_baseline main
cp backend/.env ../KBI_PoC_main_baseline/backend/.env

# 양 서버 (refactoring venv 공유 — main 도 호환 검증됨)
cd backend && nohup venv/bin/python -m uvicorn app.main:app --port 8000 > /tmp/backend_refac.log 2>&1 &
cd /Users/jaewookim/Desktop/Project/KBI_PoC_main_baseline/backend && \
  nohup /Users/jaewookim/Desktop/Project/KBI_PoC/backend/venv/bin/python -m uvicorn app.main:app --port 8001 > /tmp/backend_main.log 2>&1 &

# baseline 검증 (refactoring 시작 시점에 main 과 동치 — 27/27 expected)
cd /Users/jaewookim/Desktop/Project/KBI_PoC && \
  /Users/jaewookim/Desktop/Project/KBI_PoC/backend/venv/bin/python tests/main_parity/parity_endpoint_diff.py \
    --report /tmp/parity_baseline_v2.md
```

### 3.2 검증 정책 (직전 세션 정착)

- LLM endpoints (ai-summary, audit/explain) + DB-stateful (runs, export, wip-template) 은 `hide_list_len=True` shape-only 자동 비교
- EXPECTED_DRIFT: stage1/urgent (refac=410 vs main=500), refactoring-only routes (Decision Card, change-sets, constraint baselines)
- 신규 발견 drift 는 본 라운드 PR 에서 합의 후 `EXPECTED_DRIFT` 등록

### 3.3 매 phase 종료 시

```bash
# refactoring backend 재시작 (코드 변경 반영)
pkill -f "uvicorn.*--port 8000"
cd /Users/jaewookim/Desktop/Project/KBI_PoC/backend && \
  nohup venv/bin/python -m uvicorn app.main:app --port 8000 > /tmp/backend_refac.log 2>&1 &
sleep 3

# diff 실행
/Users/jaewookim/Desktop/Project/KBI_PoC/backend/venv/bin/python \
  tests/main_parity/parity_endpoint_diff.py --report /tmp/parity_phase<N>.md
# → 27/27 identical 또는 EXPECTED_DRIFT 외 0 regression 확인 후에만 다음 phase
```

### 3.4 종료 시 정리

```bash
git worktree remove /Users/jaewookim/Desktop/Project/KBI_PoC_main_baseline
pkill -f "uvicorn.*--port 8000" ; pkill -f "uvicorn.*--port 8001"
```

---

## 4. Phase 0 — Architecture Audit (소요: ~1.5h)

본 라운드의 **핵심** — 데이터-기반 audit 으로 Phase 1+ 의 작업 순서 확정. 추측 금지.

### 4.1 Audit 산출물 (모두 disk 에 저장 → orchestrator context 절약)

**A. `docs/architecture-audit-services.md`** — Service inventory:

`backend/app/services/` 루트 + 하위의 모든 .py 에 대해:

- LOC, public symbol 목록
- import 의존성 (어느 layer → 어느 layer)
- 호출처 grep (routes, 다른 services, tests)
- 분류: domain-service / application-service / infrastructure-adapter / orchestrator / 미분류
- 중복 의심 (다른 파일과 책임 겹침)
- dead 의심 (호출처 0~1)

**B. `docs/architecture-audit-boundaries.md`** — Layer boundary violations:

- domain/ 이 infrastructure 를 import 한 사례
- services/ 가 routes/ 를 역방향 import 한 사례
- presentation/routes/ 가 domain 을 직접 import 한 사례 (응용 service 우회)
- 순환 의존성 (importlib.cycles 시나리오)

**C. `docs/architecture-audit-cleanup.md`** — Duplicate/dead candidates:

- 직전 세션 deferred 5 항목 + 본 audit 신규 발견
- 각 항목: 위치 / 호출처 / 통합/삭제/유지 권장 / 위험도

**D. `docs/architecture-audit-monkeypatch.md`** — Test contract impact:

- D7-C 셸 제거 + Phase 1 모듈 이동 시 영향 받는 monkeypatch / patch.object 사용처
- 각 case 에 대한 안전한 전환 패턴 (모듈 namespace → 직접 target)

### 4.2 Audit 방법 — 병렬 Explore agent dispatch

`superpowers:dispatching-parallel-agents` 활용:

```
Agent 1 (Explore, very thorough): services/ 루트 + 하위 인벤토리
       → docs/architecture-audit-services.md 직접 작성

Agent 2 (Explore, very thorough): domain/ + infrastructure/ + presentation/ 의
       boundary 위반 추적
       → docs/architecture-audit-boundaries.md 직접 작성

Agent 3 (Explore, medium): routes/ 의 service 호출 패턴 + duplicate route logic
       → docs/architecture-audit-cleanup.md 의 cleanup 후보 일부

Agent 4 (Explore, medium): tests/ 의 monkeypatch 대상 매핑
       → docs/architecture-audit-monkeypatch.md 직접 작성
```

각 agent 는 self-contained prompt + 산출물 경로를 명시. orchestrator 는 4 agent 결과를 종합하여 cleanup 문서 + target 문서 작성.

### 4.3 ultrathink 결정 지점 — Target Architecture

Phase 0 종료 시점, **명시적 ultrathink 모드**:

audit 결과를 바탕으로 `docs/architecture-target.md` 작성. 다음을 포함:

1. **Target layer structure** (e.g.):

   ```
   backend/app/
   ├── domain/
   │   ├── constants.py
   │   ├── entities/        # ProductionBatch / ScheduleTask 의 pure 비즈니스 룰
   │   ├── services/        # ConstraintParams / sheath_cluster — entity 횡단
   │   └── value_objects/
   ├── application/
   │   ├── pipeline/
   │   ├── scheduling/
   │   │   ├── cp_sat/
   │   │   └── greedy/
   │   └── decisions/
   ├── infrastructure/
   │   ├── database/, models/, llm/, calendar/, logging/
   ├── presentation/routes/
   └── shared/              # cross-cutting (audit_logger 등)
   ```

   단, **사용자 메모리 simplicity 원칙**과 직전 세션의 Approach C 결정에 따라 **불필요한 추상화 (entities/value_objects 분리, 모든 use-case 별 디렉토리) 는 보류**. PoC 단계에 적합한 _최소 의미있는_ 분리만.

2. **각 deferred 항목의 새 위치**:
   - llm_explainer 4 함수 → `application/decisions/{explain_batch, summarize_run, risk_detector}.py` + `infrastructure/llm/` (LLM 호출)
   - schedule_optimizer.py 셸 → 삭제 (Phase 1 종료 시점에 자연스럽게)
   - cp_sat_schedule 분해 → `application/scheduling/cp_sat/` 하위로 분산
   - SchedulerState → `application/scheduling/greedy/scheduler_state.py`

3. **마이그레이션 순서** (의존성 역방향 — leaf 먼저 이동):
   - 가장 작은 의존성부터 (예: shared/audit_logger)
   - 점진적으로 application/ → infrastructure/
   - cp_sat / greedy 같은 큰 모듈 마지막

4. **ROI 평가** — 각 이동의 비용 vs 가치:
   - 단순 디렉토리 이동 (low cost, low value) — bulk 처리
   - 의미적 분리 (medium cost, high value) — 우선
   - 통합 (medium cost, varies) — audit-cleanup.md 권장 따라
   - 분할 (high cost, varies) — cp_sat_schedule, \_run_optimization_once 만

→ **사용자 승인 후 Phase 1 진입.** 승인 없이 진행 금지.

### 4.4 검증 게이트

- [ ] 4 audit 문서 + target 문서 모두 disk 존재
- [ ] 사용자 target 승인
- [ ] 본 phase 동안 코드 변경 0 → main-parity gate 27/27 (재실행 불필요, 직전 baseline 그대로 유효)

---

## 5. Phase 1 — services/ 루트 정리 + Clean Arch 재정렬 (소요: ~3-4h)

Phase 0 의 `architecture-target.md` 가 결정한 구조로 점진적 마이그레이션.

### 5.1 마이그레이션 절차

**각 step 마다 atomic commit + parity-quick + main-parity gate**:

- **Step 1**: 빈 target 디렉토리 + `__init__.py` (각 디렉토리의 책임 docstring)
- **Step 2**: `audit-cleanup.md` 의 dead 의심 항목 처리 — 호출처 0건 재확인 후 삭제
- **Step 3**: `audit-cleanup.md` 의 중복 의심 항목 처리 — 통합 (한 파일에 합치고 다른 파일 삭제) 또는 분할
- **Step 4**: 한 모듈씩 이동 (`git mv`) — 우선순위 leaf 먼저:
  - shared/ 후보 (audit_logger 등)
  - infrastructure/ 후보 (llm_providers, calendar_engine)
  - domain/ 후보 (constraint_params, sheath_cluster)
  - application/ 후보 (pipeline, scheduling/cp_sat, scheduling/greedy, decisions)
- **Step 5**: re-export 셸 일시 유지 → 모든 importer 전환 → 셸 삭제
  - **D7-C deferred #2 가 본 step 에서 자연스럽게 처리됨** (schedule_optimizer.py 셸은 새 위치로 모듈 이동되며 셸 자체 불필요)
- **Step 6**: layer boundary 위반 수정
  - infrastructure import 하는 domain 파일 → 의존성 caller 가 inject (DI)
  - routes 가 직접 ORM import → application service 경유

### 5.2 검증 게이트 (매 step)

- [ ] `pytest tests/ -q --ignore=tests/test_parity_harness.py` 그린 (LOC 변동 0 — 순수 이동)
- [ ] parity-quick 3/3
- [ ] main-parity gate 27/27 identical
- [ ] 새 import 경로 grep — 모든 importer 가 새 path 사용

### 5.3 ultrathink 사용

각 모듈 이동 시 boundary 모호한 케이스에서 ultrathink:

- audit_logger 가 cross-cutting 인가 infrastructure 인가? — 사용 패턴 (어느 layer 에서 호출) 으로 결정
- ConstraintParams 가 도메인-service 인가 application-service 인가? — DB 의존성 여부로 결정 (의존하면 application 의 loader)
- decision_narrator 가 application 인가 infrastructure 인가? — provider 인터페이스 의존성 + use-case orchestration 비율로 결정

---

## 6. Phase 2 — llm_explainer → decision_narrator 마이그레이션 (소요: ~1.5h)

직전 세션 deferred #1. Phase 1 의 새 구조 (`application/decisions/` + `infrastructure/llm/`) 위에서 자연스럽게.

### 6.1 분리 설계

`llm_explainer.py` (620 LOC) 의 4 함수 →

- `application/decisions/explain_batch.py` — `explain_decision_sync` 의 use-case (DB fetch + context 빌드 + Provider 호출 + filter 적용)
- `application/decisions/summarize_run.py` — `generate_batch_summary_sync` 의 use-case
- `domain/services/risk_detector.py` — `_detect_rule_based_risks` (pure rule logic, no LLM)
- LLM 호출 부분은 `infrastructure/llm/anthropic_provider.py` (이미 있음, decision_narrator 가 의존)
- decision_narrator 의 `explain_with_filter` (hallucination filter) 를 두 use-case 에 적용 — **신규 capability**

### 6.2 마이그레이션 단계

- Step 1: domain/services/risk_detector.py 추출 (pure logic, easiest win)
- Step 2: application/decisions/summarize_run.py 작성 + ai-summary route 전환 + parity (LLM_NONDETERMINISTIC shape-only)
- Step 3: application/decisions/explain_batch.py 작성 + audit/explain route 전환 + parity
- Step 4: 신규 unit tests — hallucination filter 가 두 use-case 에서 작동
- Step 5: llm_explainer.py 삭제 + main-parity gate

### 6.3 검증 게이트

- [ ] parity-quick 3/3
- [ ] main-parity gate 27/27 (LLM endpoint 는 hide_list_len shape 정책)
- [ ] 신규 unit tests: hallucination filter coverage
- [ ] llm_explainer.py importer 0건 grep

---

## 7. Phase 3 — cp_sat_schedule 분해 + lex wiring (소요: ~3h)

직전 세션 deferred #3 + #5. cp_sat_optimizer.py (1657 LOC) 의 `cp_sat_schedule()` 함수.

### 7.1 ultrathink — 함수 본체 섹션 식별

함수 본체의 §1~§N 식별 (예시):

- §1 입력 검증 + ConstraintParams 로드
- §2 batch 로드 + group_meta 생성 (`SolverInput.group_meta`)
- §3 frozen / warm_start snapshot 빌드 (DB 접근)
- §4 model_builder.build_model 호출 (이미 추출됨)
- §5 compose_objective 호출 (이미 추출됨)
- §6 solver.solve (weighted-sum default / **lex 신규 wiring 진입점**)
- §7 결과 파싱 + ScheduleTask 생성 + audit log
- §8 fallback 처리 (greedy_fallback)

각 섹션의 의존성 → `CpSatRunState` dataclass 설계 → 점진적 추출.

### 7.2 lex_min_time wiring (deferred #5)

```python
def cp_sat_schedule(..., min_time_mode: bool = False):
    if min_time_mode:
        result = solve_lex_min_time(_built, ...)
    else:
        compose_objective(_built, ...)
        result = solve_weighted_sum(_built, ...)  # 기존 경로 보존
```

- `min_time_mode=False` (default) 경로는 parity tests 가 결과를 동결 → 변경 금지
- `min_time_mode=True` 경로는 신규 fixture 시나리오 12 (모두 due 충족) + 13 (past-due 강제) 로만 검증

### 7.3 검증 게이트

- [ ] parity 11/11 full (weighted-sum 경로 동결 확인)
- [ ] 신규 시나리오 12, 13 fixture 추가 + 결정론적 통과
- [ ] main-parity gate stage2 endpoint engine="cpsat" 응답 동치
- [ ] cp_sat_schedule body ≤ 200 LOC orchestrator
- [ ] 실 ERP 파일로 lex_min_time 수동 실행 + makespan 기록

---

## 8. Phase 4 — \_run_optimization_once SchedulerState SRP (소요: ~3-4h)

직전 세션 deferred #4. greedy/optimization_loop.py 의 assignment loop (~700 LOC).

### 8.1 ultrathink — SchedulerState 설계

직전 세션 §6.2 인벤토리의 ~17 local cache 변수를 dataclass 로:

```python
# greedy/scheduler_state.py
@dataclass
class SchedulerState:
    # timeline / occupation
    timeline: dict[str, list[tuple[datetime, datetime]]] = field(default_factory=dict)

    # precedence / pipeline state
    predecessor_map: dict[tuple[str, int], int] = field(default_factory=dict)
    process_end_by_sq: dict[tuple[str, int], datetime] = field(default_factory=dict)
    process_first_output_by_sq: dict[tuple[str, int], datetime] = field(default_factory=dict)
    first_insul_output: datetime | None = None
    core_first_drum_by_main_sq: dict[int, datetime] = field(default_factory=dict)

    # equipment routing
    sq_to_equip: dict[tuple[str, int], str] = field(default_factory=dict)
    last_batch_on_equip: dict[str, ProductionBatch] = field(default_factory=dict)

    # 마스터 데이터 (read-only, run 동안 고정)
    equipment_by_process: dict[str, list[EquipmentMaster]] = field(default_factory=dict)
    speed_map: dict[tuple[str, float], SpeedMaster] = field(default_factory=dict)
    constraint_params: ConstraintParams = field(default_factory=ConstraintParams)
    welding_min: int = 30

    # result accumulator
    tasks_created: list[ScheduleTask] = field(default_factory=list)
```

### 8.2 분해 절차 (직전 세션 §6.4 그대로)

- `_seed_state_from_existing(state, run_label, db)` — 기존 ScheduleTask 로 timeline/predecessor 채우기 (가장 미묘 — _seed_pipeline_\* 변수들)
- `_group_and_sort(schedulable, state)` — batch_groups 빌드 + 색상/납기 정렬
- `_assign_group(state, group_key, group_batches, db, run_label, base_date)` — 핵심 assignment (~150 LOC, 가장 무거움)

각 추출마다 parity-quick + main-parity.

### 8.3 위험 — retry harness 와의 race

`auto_schedule.py` 의 retry loop 가 SchedulerState 를 공유하면 retry 간 cross-contamination 가능. **SchedulerState 는 매 retry 마다 새로 생성** (caller 책임). 본 phase 에서 retry harness 도 같이 점검.

### 8.4 검증 게이트

- [ ] parity 11/11 full (assignment 결과 결정론적)
- [ ] main-parity gate stage2 endpoint 동치 (schedule_task 배치: batch_id, equipment_code, start ± 1m, end ± 1m)
- [ ] `_run_optimization_once` body ≤ 100 LOC
- [ ] retry harness 의 SchedulerState reset 검증 unit test

---

## 9. Phase 5 — 실 ERP e2e + gstack QA + 종료 검증 (소요: ~1.5h)

### 9.1 실 ERP 파일 e2e (Phase 0 셋업 활용)

```bash
ERP=Documents/ERP생산계획_v1.xls
WIP=Documents/SM재고리스트.xls
URG=Documents/긴급수주.xlsx

# 양 서버 동일 입력
for SERVER in http://127.0.0.1:8000 http://127.0.0.1:8001; do
  echo "=== $SERVER ==="
  curl -s -X POST "$SERVER/api/pipeline/stage1" \
    -F "erp_file=@$ERP" -F "wip_file=@$WIP" -F "split_gap_days=3" \
    | jq '.run_label, .batches.total_batches, .batches.by_process'
done
# → batches.by_process 분포 + split_candidates 길이 동일 확인
```

### 9.2 lex 모드 vs weighted-sum 비교

같은 run_label 에 대해:

```bash
RL=$(curl ... | jq -r .run_label)
curl -X POST "http://127.0.0.1:8000/api/pipeline/stage2" \
  -H "Content-Type: application/json" \
  -d "{\"run_label\": \"$RL\", \"min_time_mode\": false}" | jq '.engine, .makespan_min, .total_tardiness'
curl -X POST "http://127.0.0.1:8000/api/pipeline/stage2" \
  -H "Content-Type: application/json" \
  -d "{\"run_label\": \"$RL\", \"min_time_mode\": true}" | jq '.engine, .makespan_min, .total_tardiness'
```

→ 두 모드의 makespan / tardiness / engine 차이를 `docs/lex-mode-comparison.md` 에 기록.

### 9.3 gstack QA — 실제 브라우저 walkthrough

`/qa` 또는 `/gstack` 스킬로 frontend 의 stage2 워크플로우:

- ERP 업로드
- Stage 1 batch 결과 확인
- Stage 2 실행 (engine=cpsat / greedy / lex 토글)
- Gantt 렌더링 정상
- Decision Card 펼치기 (audit/explain — hallucination filter 결과 확인)
- 콘솔 에러 0
- 핵심 인터랙션 스크린샷 (`docs/qa-screenshots/v2/`)

발견 버그는 atomic commit 으로 즉시 수정.

### 9.4 최종 main-parity

```bash
/Users/jaewookim/Desktop/Project/KBI_PoC/backend/venv/bin/python \
  tests/main_parity/parity_endpoint_diff.py --report /tmp/parity_final_v2.md
# → 27/27 identical, 0 regressions (외 EXPECTED_DRIFT)
```

### 9.5 정리

```bash
git worktree remove /Users/jaewookim/Desktop/Project/KBI_PoC_main_baseline
pkill -f "uvicorn.*--port 8000" ; pkill -f "uvicorn.*--port 8001"
```

---

## 10. Agent Team / Skill 사용 전략

| Phase            | 도구                                                                                                                                                                                  |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Phase 0 (audit)  | **`superpowers:dispatching-parallel-agents`** + 4 Explore agent (services/ inventory, boundary, cleanup, monkeypatch). 각 agent 가 산출물 직접 disk 작성 → orchestrator context 절약. |
| Phase 1 (재정렬) | **`superpowers:subagent-driven-development`** — leaf 모듈 이동을 subagent task 로 병렬화. boundary 위반 수정은 직렬.                                                                  |
| Phase 2 (llm)    | general-purpose agent 1명 — feature work (hallucination filter 신규 도입).                                                                                                            |
| Phase 3 (cp_sat) | Plan 모드로 분해 설계 → 직렬 subagent. lex wiring 은 신중하게 (parity 큰 영향).                                                                                                       |
| Phase 4 (greedy) | Phase 3 와 동일 패턴. retry harness 검증은 단독 직렬.                                                                                                                                 |
| Phase 5 (검증)   | **`/qa` 또는 `/gstack`** skill — 실제 브라우저 walkthrough.                                                                                                                           |

**ultrathink 명시 사용 지점**:

- Phase 0 종료 — `architecture-target.md` 결정 (한 번 잘못 설계하면 Phase 1+ 전체 재작업)
- Phase 1 step 마다 — boundary 모호 케이스 (audit_logger / ConstraintParams / decision_narrator 의 layer 결정)
- Phase 3 — `CpSatRunState` dataclass 설계 + lex wiring 의 INFEASIBLE fallback
- Phase 4 — `SchedulerState` 분해 (직전 세션이 multi-hour 라 deferred 한 핵심 작업)

---

## 11. 위험 / Risk Register

직전 세션 검증된 + 본 라운드 신규:

| 위험                                                          | 완화책                                                                                                  |
| ------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| Phase 1 모듈 이동 중 import 깨짐                              | 매 이동마다 `python -c "from app.X import Y"` smoke test + import 그래프 grep                           |
| Auto-formatter 가 deferred import 절단                        | `# noqa: F401  # used at <위치>` (직전 세션 확립 패턴)                                                  |
| backend/ vs root cwd 혼동                                     | 모든 pytest 명령 `cd /Users/jaewookim/Desktop/Project/KBI_PoC/backend && ...` 절대경로                  |
| protobuf negative-indexing trap                               | `proto.domain[-1]` 금지, `list(proto.domain)[-1]`                                                       |
| Supabase 공유 DB drift                                        | `hide_list_len` shape 정책 + `EXPECTED_DRIFT` 화이트리스트 (직전 세션 확립)                             |
| Phase 3/4 의 multi-hour 가 컨텍스트 한계 도달                 | 매 sub-step atomic commit + 본 문서 §12 markers 갱신 → 다음 세션 재개 가능                              |
| Clean arch 재정렬 중 routes/ ↔ services/ 양쪽 동시 수정       | 한 방향만 — re-export 셸 유지 → routes 점진 전환 → 셸 삭제                                              |
| llm_explainer 마이그레이션 중 ai-summary text 변동            | `LLM_NONDETERMINISTIC` 정책 — shape 만 검증, text drift 무시                                            |
| `_run_optimization_once` retry loop 가 SchedulerState 공유    | SchedulerState 는 매 retry 마다 새로 생성 (auto_schedule retry harness 도 같이 점검)                    |
| Phase 0 audit 결과 사용자 승인 지연                           | audit 자체는 코드 변경 0 — 사용자 검토 기간 동안 다른 phase 진입 금지 (구조 결정 전 작업은 재작업 위험) |
| gstack QA 가 발견한 frontend 버그 수정 시 backend parity 깨짐 | 가능성 낮음 (frontend 만 수정). 그래도 fix 후 main-parity 재실행                                        |
| Phase 1 디렉토리 이동 → IDE/사용자 mental model 혼란          | `architecture-target.md` 의 시각화 (mermaid diagram 등) + 매 step commit message 에 "moved X → Y" 명시  |

---

## 12. 종료 조건 (Definition of Done)

본 라운드 100% 완료:

- [ ] `docs/architecture-audit-{services,boundaries,cleanup,monkeypatch}.md` 4개 + `docs/architecture-target.md` 작성 + 사용자 승인
- [ ] backend/app/services/ 루트 산만 정리 — root .py 파일 ≤ 5 (또는 deprecation 셸만)
- [ ] domain/, application/, infrastructure/, presentation/, shared/ layer 명확히 분리, boundary violation 0 (Phase 0 audit 의 violations 모두 해소)
- [ ] llm_explainer.py 삭제 (decision_narrator + new use-cases 통합) + hallucination filter 가 ai-summary, audit/explain 양쪽에서 작동
- [ ] schedule_optimizer.py D7-C 셸 삭제 + 모든 importer 직접 import 전환
- [ ] cp_sat_schedule body ≤ 200 LOC orchestrator (현재 1657 → 분해)
- [ ] \_run_optimization_once body ≤ 100 LOC orchestrator + SchedulerState dataclass
- [ ] solver/lex_min_time.py wired into cp_sat_schedule (`min_time_mode` flag) + scenarios 12, 13 fixture parity-green
- [ ] 428+ backend pytest, 11/11 full parity, frontend build green, gstack QA walkthrough 0 console errors
- [ ] 실 ERP 파일 e2e — 양 서버 응답 동치 + lex vs weighted-sum 비교 docs 작성
- [ ] §3 main-parity gate 최종 27/27 identical
- [ ] 본 문서 §12 markers 모두 [x]
- [ ] `docs/superpowers-final-report.md` §8 known-debt 의 (1)/(2)/(3)/(4)/(5) 모두 닫힘
- [ ] CHANGELOG / ARCHITECTURE.md 업데이트 (직전 세션 + 본 세션 결과 종합)

---

## 13. Atomic Commit 정책 (직전 세션 그대로)

```
<type>(<scope>): <one-line subject>

<body — why, not what>

<verification-evidence — paste actual test output or curl response>

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

push 는 사용자 명시 요청 시에만. 본 문서가 push 권한을 부여하지 않는다.

---

## 14. 시작 시 첫 메시지 템플릿

다음 세션 첫 발화 (자기 진입 지시):

```
사용자가 docs/next-session-prompt-v2.md 따라 진행하라고 함.
지금부터:
1. §1 의 9개 reading 모두 읽고 자기점검 6문항 답변 가능 상태 확인.
2. §2 harness 원칙 + §3 main-parity gate 셋업 (worktree main + 양 서버 + baseline 27/27 검증).
3. Phase 0 architecture audit 진입 — superpowers:dispatching-parallel-agents 로 4 Explore agent 병렬 dispatch (services inventory, boundaries, cleanup, monkeypatch). 각 agent 가 자기 산출물 disk 직접 작성.
4. 4 audit 문서 종합 후 ultrathink 로 architecture-target.md 작성 → 사용자 승인 → Phase 1 진입.
5. Phase 1+ 매 step atomic commit + parity-quick + main-parity gate. EXPECTED_DRIFT 외 회귀 0건 확인 후에만 다음 step.

직전 세션 학습된 trap 회피:
- F401 noqa로 formatter import 절단 차단
- backend/ cwd 절대경로 (cd /Users/jaewookim/Desktop/Project/KBI_PoC/backend)
- protobuf list-cast (proto.domain[-1] 금지)

ultrathink 모드 — 추측 없이 코드 + 문서 기반 추론.
```

---

**문서 작성 시점**: 직전 세션 마지막 commit `6b14773` 기준.
**다음 업데이트 시점**: Phase 0 audit 완료 시 본 문서에 `architecture-target.md` link + audit 산출물 link 추가.
