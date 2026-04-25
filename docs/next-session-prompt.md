# Next Session — 상세 작업 지침 프롬프트

> **사용법:** 다음 Claude 세션 시작 시 이 파일 경로를 알려주고
> "이 문서대로 진행해" 라고 지시하면 됩니다.
> 본 문서는 **harness 원칙** + **superpowers** + **agent team**
>
> - **ultrathink** 기반의 4-phase 작업 계획서입니다.

---

## 0. 사명 (Mission)

**작업 맥락**:

- 프로젝트: KBI 케이블 제조 생산계획 스케줄러 (FastAPI + CP-SAT + Next.js 16)
- 브랜치: `refactoring`, origin/main 대비 +125 commits
- 직전 라운드 결과: `docs/superpowers-final-report.md` 참조 (Weeks 1-9 + harness 일부 완료)
- 핵심 미해결: 함수-내 SRP 위반 2개, 제약조건 코드 분산, 일부 dead code, lex-min 최적화 미도입

**이번 라운드 목표** (사용자 요구사항 직접 인용):

> 1. 단일 책임 함수로 불러오기, 확장 가능한 형태로 db 등에서 가져오는 방식
> 2. 그리디 등 안 쓰는 코드 모두 정리, 워크플로우에서 실제 사용되는 코드 기준
> 3. 제약조건 용도별 카테고리화 (전체 / 공정별 / 제품별 / 기타) → 카테고리 디렉토리 + import
> 4. 납기를 반드시 준수하면서 최소시간(makespan) 해를 구하는 lexicographic 최적화

**위 4개를 4-phase 로 분해해 순차 실행한다.**

---

## 1. 시작 전 필독 문서 (Required Reading)

**반드시 이 순서로 읽고, 핵심 사실을 메모리에 적재한 뒤 Phase 0 진입.**

| #   | 경로                                                                       | 읽는 이유                                                                                           |
| --- | -------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| 1   | `docs/superpowers-final-report.md`                                         | 직전 라운드 진실/거짓 구분, known-debt §8                                                           |
| 2   | `docs/post-pilot-backlog.md`                                               | 정식 등록된 미해결 항목                                                                             |
| 3   | `docs/constraint-catalog.md`                                               | 38개 ConstraintConfig 행 + W-\* 가중치, category/applicable_processes/implementation_type 필드 의미 |
| 4   | `docs/private-symbol-inventory.md`                                         | D7-C re-export shells 의 권위 있는 명단                                                             |
| 5   | `backend/app/services/cp_sat_optimizer.py:1-200`                           | scaling 상수 + cp_sat_schedule signature                                                            |
| 6   | `backend/app/services/solver/model_builder.py` 전체                        | BuiltModel 구조 + IntVar 컬렉션                                                                     |
| 7   | `backend/app/services/greedy/optimization_loop.py` 전체                    | `_run_optimization_once` 907 LOC 의 state 캐시 목록                                                 |
| 8   | `backend/app/infrastructure/models/constraint_config.py`                   | DB 스키마: applicable_processes JSONB, implementation_type enum                                     |
| 9   | `backend/app/services/decision_narrator.py` + `llm_providers/anthropic.py` | 신규 LLM stack (legacy llm_explainer 와 비교용)                                                     |

**Reading 후 자기점검 질문** (문서/코드 기반으로 답변 가능해야 진입):

- BuiltModel 의 `tardiness_vars`, `transition_terms`, `edd_pair_terms`, `slack_terms_meta`, `idle_terms`, `sheath_end_terms` 의 정확한 타입과 길이는?
- ConstraintConfig.applicable_processes 가 비어있을 때 vs. ['연선'] 일 때 동작 차이는 무엇인가?
- 현재 objective 가 가지는 항이 몇 개이며, 각각의 weight 출처(ConstraintConfig W-\* 행 vs. 코드 상수)는?
- `_run_optimization_once` 가 변경하는 모듈-외부 상태(DB 행 INSERT/UPDATE) 는 무엇인가?

답을 못하면 다시 읽기. **추측 금지.**

---

## 2. Harness 원칙 (Non-negotiable)

본 라운드 모든 작업에서 다음을 강제한다 (`superpowers:verification-before-completion` 의 Iron Law):

**NO COMPLETION CLAIMS WITHOUT FRESH VERIFICATION EVIDENCE**

| 주장                 | 필요 증거                                                                     |
| -------------------- | ----------------------------------------------------------------------------- |
| "테스트 통과"        | `pytest tests/ -q --ignore=tests/test_parity_harness.py` 출력 (이 메시지에서) |
| "parity 통과"        | `pytest tests/test_parity_harness.py -m parity --parity-quick -v` 출력        |
| "빌드 OK"            | `cd frontend && npm run build` 출력                                           |
| "함수 동등 동작"     | parity 11/11 그린 + objective_value diff ≤ 0.1%                               |
| "dead code 정리"     | grep 으로 모든 importer 0건 확인 + 테스트 통과                                |
| "Decision Card 작동" | `curl /api/decisions/<batch_id>/latest` HTTP 200 + contributions 배열         |

**금지 사항**:

- "should work" / "probably" / "I believe" 표현
- 검증 없이 다음 phase 로 진입
- atomic commit 누락 (각 phase 의 sub-step 마다 commit, 사용자 확인 필요 시 정지)
- formatter 가 import 를 잘라먹는 회귀 (직전 라운드에서 3번 발생) — 항상 `# noqa: F401` + 명시 코멘트

---

## 3. Main-Branch Parity Gate (필수, 모든 phase 종료 시 강제 실행)

**사용자 명시 요구**:

> "반드시 main 의 코드랑 비교해서 모든 기능이 유지되는지를 검토하는 내용도 들어가야 해. 실제 서버 실행시켜서 비교해야 하는 거고."

리팩터 라운드는 _내부_ 구조만 바꾸지 _기능 외형_ 은 동일해야 한다. pytest +
parity 만으로는 endpoint contract drift / response shape 변경 / status code 회귀
를 잡을 수 없다. **실제로 main 서버를 띄워 같은 입력에 대한 응답을 비교한다.**

### 3.1 환경 셋업 — git worktree 로 main 동시 실행

```bash
# 새 터미널에서:
cd /Users/jaewookim/Desktop/Project/KBI_PoC
git worktree add ../KBI_PoC_main_baseline main
cd ../KBI_PoC_main_baseline

# main 의 backend 띄우기 (포트 8001 — refactoring 의 8000 와 충돌 회피)
cd backend
python3.11 -m venv venv && source venv/bin/activate
pip install -q -r requirements.txt
PORT=8001 nohup python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8001 \
  > /tmp/backend_main.log 2>&1 &

# 동일 .env (Supabase) 사용해야 데이터 동등 비교 가능 — 단,
# Stage1 은 데이터를 destructive 하게 갈아엎으므로 read-only 비교만 가능.
# Stage1 같은 destructive 호출은 _별도 ephemeral DB_ 또는 트랜잭션 SAVEPOINT
# 안에서만 비교한다 (3.4 참조).
```

`refactoring` 브랜치 backend 는 8000 포트로 그대로 두고, main 은 8001 로 띄운다.

### 3.2 Endpoint 인벤토리 — 비교 대상 28개

다음 endpoint 들에 대해 **응답 status / response shape / 핵심 필드 값** 을 비교:

| 카테고리            | Endpoint                                           | 비교 차원                                         |
| ------------------- | -------------------------------------------------- | ------------------------------------------------- |
| **Pipeline GET**    | GET /api/pipeline/runs                             | length, run_label, batch_count                    |
|                     | GET /api/pipeline/runs/{run_label}                 | full payload diff                                 |
|                     | GET /api/pipeline/stage1/{run_label}/batches       | length, [batch_id, equipment_code]                |
|                     | GET /api/pipeline/stage1/{run_label}/wip-inventory | length, status                                    |
|                     | GET /api/pipeline/stage1/{run_label}/outsourced    | length, status                                    |
|                     | GET /api/pipeline/stage1/{run_label}/ai-summary    | shape (PoC: 캐시-driven, fuzzy match OK)          |
|                     | GET /api/pipeline/batch/{batch_id}                 | full payload                                      |
|                     | GET /api/pipeline/batch-status-summary             | 카운트 dict                                       |
|                     | GET /api/pipeline/wip-template                     | content-type, byte length 근사                    |
| **Schedule GET**    | GET /api/schedules/tasks?run_label=…               | length, equipment_code 분포                       |
|                     | GET /api/schedules/versions                        | length                                            |
|                     | GET /api/schedules/versions/{id}                   | full                                              |
| **Constraint**      | GET /api/constraints                               | 38 rows, params_json 동일                         |
|                     | GET /api/constraints/{id}/history                  | length                                            |
| **Master**          | GET /api/master/equipment                          | length                                            |
|                     | GET /api/master/items 등                           | length                                            |
| **Decision**        | GET /api/decisions/{batch_id}/latest               | contributions 배열 길이/keys                      |
| **Audit**           | GET /api/audit/{run_label}                         | total                                             |
|                     | GET /api/audit/explain/{batch_id}                  | shape                                             |
| **Pipeline POST**   | POST /api/pipeline/stage1 (실 ERP)                 | run_label 형식, batches by_process                |
|                     | POST /api/pipeline/stage1/update incremental       | 동일                                              |
|                     | POST /api/pipeline/stage2 cpsat                    | engine, total_tasks, solver_status, overlap_alert |
| **Pipeline DELETE** | DELETE /api/pipeline/runs/{run_label}              | status code, side effect (batches=0)              |
| **Deprecated**      | POST /api/pipeline/stage1/urgent                   | **refactoring=410, main=500** (diff 의도적)       |
| **Schedules POST**  | POST /api/schedules/cascade-preview                | preview shape                                     |
|                     | POST /api/schedules/revert/{change_set_id}         | status                                            |

### 3.3 비교 스크립트 — `tests/main_parity/parity_endpoint_diff.py`

신규 작성. 자동 실행 가능 + 사람이 읽기 좋은 diff 리포트.

```python
# tests/main_parity/parity_endpoint_diff.py
"""실 서버 두 인스턴스 (refactoring:8000 vs main:8001) 의 응답을 비교.

비교 정책:
  1. status code 는 정확히 일치.
  2. 응답 body 는 normalize_for_compare() 후 deep diff.
     - 시간 필드 (created_at, started_at, finished_at) 마스킹.
     - run_id (uuid4) 마스킹.
     - run_label (run-time generated) 은 매핑 dict 로 양측 동치 처리.
  3. 의도적 drift (예: /stage1/urgent 410 vs 500) 는 EXPECTED_DRIFT 로 화이트리스트.

사용:
  python tests/main_parity/parity_endpoint_diff.py \
      --refactoring http://127.0.0.1:8000 \
      --main http://127.0.0.1:8001 \
      --run-label 20260425_225331 \
      --report /tmp/parity_diff_report.md
"""
EXPECTED_DRIFT = {
    "POST /api/pipeline/stage1/urgent": "refactoring=410(retired), main=500(FK bug)",
    # 추가 발견 시 PR 에서 합의 후 등록
}

def normalize(payload):
    """시간/uuid 마스킹 + 정렬."""
    ...

def diff_endpoint(method, path, payload=None, files=None):
    r1 = http(REFAC, method, path, payload, files)
    r2 = http(MAIN, method, path, payload, files)
    if r1.status_code != r2.status_code:
        return Drift(reason="status_code", refac=r1.status_code, main=r2.status_code)
    return deep_diff(normalize(r1.json()), normalize(r2.json()))
```

### 3.4 Destructive endpoint 비교 (Stage1, Stage2)

데이터를 갈아엎는 호출은 ephemeral DB 두 개 또는 트랜잭션 격리가 필요.

**전략 A — ephemeral DB**:

```bash
# docker-compose 의 throwaway db 사용 (Makefile db-fresh 와 동일 패턴)
docker compose up -d db
DATABASE_URL=postgresql://kbi:kbi_poc_2026@localhost:5432/kbi_scheduler \
  alembic upgrade head && python seed_db.py

# 동일 DATABASE_URL 로 양 서버 띄우고 연속 호출이 아니라 _순차_ 실행 +
# 사이에 fresh seed:
#   ① main 으로 stage1 → 응답 저장 → DB 리셋
#   ② refactoring 으로 stage1 → 응답 저장
#   ③ 두 응답 diff
```

**전략 B — read-only 우회**:
실 Supabase 에서 이미 만들어진 run_label 에 대해 GET 호출만 비교. POST 비교는
ephemeral DB 로만.

전략 A 를 기본, B 는 시간 부족 시 fallback.

### 3.5 실 ERP 파일 e2e 비교

본 라운드 종료 시 다음 시나리오 모두 양쪽 서버에서 실행 + 응답 동치 확인:

```bash
ERP=Documents/ERP생산계획_v1.xls
WIP=Documents/SM재고리스트.xls
URG=Documents/긴급수주.xlsx

for SERVER in http://127.0.0.1:8001 http://127.0.0.1:8000; do
  echo "=== $SERVER ==="
  curl -s -X POST "$SERVER/api/pipeline/stage1" \
    -F "erp_file=@$ERP" -F "wip_file=@$WIP" -F "split_gap_days=3" \
    | jq '.run_label, .batches.total_batches, .batches.by_process'
done
# 두 응답이 동일한 batches.by_process 분포 + 동일한 split_candidates 길이를
# 가져야 한다 (run_label 만 다름 — 시각 prefix 라 정상).
```

### 3.6 검증 결과 보고

각 phase 종료 시 다음 표가 비어 있지 않아야 commit 허용:

| Endpoint                             | refactoring 응답  | main 응답         | diff?                                     |
| ------------------------------------ | ----------------- | ----------------- | ----------------------------------------- |
| GET /api/pipeline/runs               | 200, 1 run        | 200, 1 run        | identical                                 |
| GET /api/pipeline/stage1/.../batches | 200, 1030         | 200, 1030         | identical                                 |
| POST /api/pipeline/stage2            | 200, FEASIBLE 30s | 200, FEASIBLE 28s | objective ±0.1%                           |
| GET /api/decisions/.../latest        | 200, 38 contribs  | **404**           | **EXPECTED** (Decision Card harness 신규) |
| POST /api/pipeline/stage1/urgent     | 410               | 500               | EXPECTED (deprecated)                     |
| …                                    | …                 | …                 | …                                         |

EXPECTED 가 아닌 모든 diff 는 회귀로 간주 → 직전 commit revert + 재설계.

### 3.7 자동화 (CI 게이트로 진입)

`.github/workflows/main-parity.yml` (또는 로컬 pre-merge hook):

```yaml
- name: Spin up main + refactoring
  run: |
    git worktree add main_baseline main
    cd main_baseline/backend && uvicorn app.main:app --port 8001 &
    cd ../../backend && uvicorn app.main:app --port 8000 &
    sleep 5
- name: Endpoint diff
  run: python tests/main_parity/parity_endpoint_diff.py --report report.md
- uses: actions/upload-artifact@v4
  with: { path: report.md }
```

CI 통합은 nice-to-have — **로컬 pre-PR 실행** 이 본 라운드의 강제 게이트.

### 3.8 worktree 정리

본 라운드 종료 후:

```bash
git worktree remove ../KBI_PoC_main_baseline
```

---

## 4. Phase 0 — Dead/Duplicate Code 정리 (소요: ~30 min)

### 4.1 직전 라운드 audit 결과 (Explore agent)

- **DEAD-CONFIRMED**: `backend/app/services/urgent_scheduler.py` (410 LOC)
  - production importer 0건 (route 410'd, test 만 import)
  - 삭제 + `tests/test_urgent_reoptimize.py` 의 의존 끊기
- **CAN-DELETE-AFTER-MIGRATION**: D7-C re-export shells
  - `schedule_optimizer.py`: 40개 re-export
  - `cp_sat_optimizer.py`: 14개 re-export
  - 작업: tests grep → 직접 import 로 변경 → shell 삭제
- **NEEDS-MANUAL-CHECK**: `llm_explainer.py` (620 LOC)
  - audit route + plan_pipeline 이 여전히 의존
  - decision_narrator 의 hallucination filter 가 누락된 buggy 경로
  - **이번 라운드 결정**: audit 도 `decision_narrator` 로 통합. `llm_explainer` 삭제.

### 4.2 작업 절차

```
[ ] git checkout -b cleanup/dead-code  # phase별 PR 분리
[ ] urgent_scheduler.py + 의존 테스트 제거 → pytest 그린 → commit
[ ] schedule_optimizer.py 의 40개 re-export 별 test grep → 직접 import 변경 → shell 삭제 → pytest 그린 → commit
[ ] cp_sat_optimizer.py 의 14개 re-export 동일 절차 → commit
[ ] llm_explainer.py: audit route 를 decision_narrator 로 마이그레이션 → llm_explainer.py 삭제 → pytest 그린 → commit
```

### 4.3 검증 게이트

```bash
cd backend && source venv/bin/activate && pytest tests/ -q --ignore=tests/test_parity_harness.py
pytest tests/test_parity_harness.py -m parity --parity-quick -v   # 3/3
cd ../frontend && npm run build                                   # OK
```

**+ §3 main-parity gate 실행**: refactoring (8000) vs main (8001) 두 서버 띄우고
`tests/main_parity/parity_endpoint_diff.py` 실행 — GET 28개 endpoint 모두 응답 동치
(EXPECTED_DRIFT 외). 회귀 발견 시 직전 commit revert.

네 게이트 모두 그린 → Phase 1.

---

## 5. Phase 1 — Constraint Plug-in Architecture (소요: ~3-4h, 핵심 작업)

### 5.1 사용자 요구 카테고리

| 카테고리                      | 적용 범위                        | 해당 constraint_id                                    |
| ----------------------------- | -------------------------------- | ----------------------------------------------------- |
| **GLOBAL** (전체 적용)        | 모든 batch / run-level           | 1-1, 1-2, 1-3, 6-1, 6-2, 6-3, 6-4 + 모든 W-\*         |
| **PER-PROCESS** (공정별)      | applicable_processes ⊃ [proc]    | 4-1, 4-2, 4-3, 4-4, 4-5, 9-1, 9-2, 9-3                |
| **PER-PRODUCT** (품목/규격별) | spec / product_group / sq_mm2 별 | 5-1, 5-2, 5-3, 5-4, 5-5, 10-1, 10-2, 10-3, 10-4, 10-5 |
| **INVENTORY** (재고/자재)     | WIP/SM 재고 + 자재 수급          | 2-1, 2-2, 2-3, 2-4, 8-1, 8-2, 8-3, 3-1                |
| **COLOR** (색상)              | sheath_color                     | 3-2, 3-3, 3-4                                         |
| **FAULT** (불량/설비)         | 설비 가용성                      | 7-1, 7-2                                              |

### 5.2 모듈 구조 (목표)

```
backend/app/services/solver/constraints/
  __init__.py              # registry, scope enum
  base.py                  # Constraint Protocol + ConstraintScope enum + BuildContext
  registry.py              # discover() + load_active() — DB → 인스턴스
  global_/
    __init__.py
    tardiness.py           # 1-1
    arrival_shipping.py    # 1-2
    urgent_change.py       # 1-3
    calendar_safety.py     # 6-1
    friday_short.py        # 6-2
    absentee.py            # 6-3
    holiday.py             # 6-4
    weights.py             # W-* 모음
  process/
    __init__.py
    spec_change_setup.py   # 4-1 (cv/sheath/stranding/insulation min)
    color_change.py        # 4-2
    drum_winding.py        # 4-3
    welding.py             # 4-4
    taping_speed.py        # 4-5
    multicore_trigger.py   # 9-1
    gc_routing.py          # 9-2
    tfr_gv_bypass.py       # 9-3
  product/
    __init__.py
    sq_equipment.py        # 5-1, 10-1
    stranding_type.py      # 5-2
    multicore_priority.py  # 5-3
    bare_wire.py           # 5-4
    tfr_gv_insulation.py   # 5-5
    conductor_material.py  # 10-2
    sheath_material.py     # 10-3
    voltage.py             # 10-4
    four_core_calc.py      # 10-5
  inventory/
    __init__.py
    wip_priority.py        # 2-1
    outsource_rule.py      # 2-2
    drum_unit.py           # 2-3
    stranding_61_split.py  # 2-4
    extra_length.py        # 3-1
    tape_compound.py       # 8-1
    lead_time.py           # 8-2
    raw_material.py        # 8-3
  color/
    __init__.py
    color_grouping.py      # 3-2
    equipment_color.py     # 3-3
    black_remnant.py       # 3-4
  fault/
    __init__.py
    defect_buffer.py       # 7-1
    equipment_fault.py     # 7-2
```

### 5.3 Constraint Protocol

```python
# constraints/base.py
from typing import Protocol, runtime_checkable
from enum import Enum
from dataclasses import dataclass

class ConstraintScope(str, Enum):
    GLOBAL = "global"
    PROCESS = "process"
    PRODUCT = "product"
    INVENTORY = "inventory"
    COLOR = "color"
    FAULT = "fault"

@dataclass
class BuildContext:
    """모델 빌드 시점에 constraint 가 접근 가능한 read-only 환경."""
    model: cp_model.CpModel
    group_meta: dict[str, GroupMeta]
    start_vars: dict[str, cp_model.IntVar]
    end_vars: dict[str, cp_model.IntVar]
    equip_vars: dict[str, dict[str, cp_model.IntVar]]
    # …all BuiltModel collections

@runtime_checkable
class Constraint(Protocol):
    constraint_id: str       # "4-1", "W-EDDP" 등
    scope: ConstraintScope
    is_enabled: bool

    @classmethod
    def from_config(cls, row: ConstraintConfig) -> "Constraint":
        """DB row → 구체 인스턴스. params_json 파싱 책임은 각 클래스."""
        ...

    def applies_to_batch(self, batch: ProductionBatch) -> bool:
        """이 batch 에 본 제약을 적용해야 하는가? (per-process / per-product 필터링)"""
        ...

    def add_to_model(self, ctx: BuildContext) -> ConstraintTerms:
        """CP-SAT 모델에 hard/soft 항 추가. 반환값은 trace_writer/decision_aggregator
        가 사용할 IntVar 컬렉션 (penalty_vars, hard_literals)."""
        ...

    def validate(self, schedule: ScheduleResult) -> list[Violation]:
        """post-solve 검증 — constraint_checker 의 자리."""
        ...

@dataclass
class ConstraintTerms:
    penalty_vars: list[cp_model.IntVar]
    hard_literals: list[cp_model.IntVar]
```

### 5.4 Registry (DB-driven loading)

```python
# constraints/registry.py
def load_active(db: Session) -> list[Constraint]:
    """ConstraintConfig 의 모든 enabled 행을 인스턴스로 변환.

    Why DB-driven: 사용자가 admin UI 에서 toggle/param 변경하면 코드 변경 없이
    다음 solve 부터 반영. 새 constraint 추가 시 (1) 클래스 작성, (2) 디렉토리에
    배치, (3) constraint_config 행 추가, 세 단계로 끝남.
    """
    rows = db.query(ConstraintConfig).filter(ConstraintConfig.is_enabled.is_(True)).all()
    out: list[Constraint] = []
    for row in rows:
        cls = _CONSTRAINT_REGISTRY.get(row.constraint_id)
        if cls is None:
            logger.warning("constraint %s 가 코드에 없음 — skip", row.constraint_id)
            continue
        out.append(cls.from_config(row))
    return out

# discovery: 디렉토리 스캔으로 _CONSTRAINT_REGISTRY 자동 채우기
def _discover_constraints() -> dict[str, type[Constraint]]:
    import importlib, pkgutil
    registry = {}
    for scope_pkg in (global_, process, product, inventory, color, fault):
        for _, mod_name, _ in pkgutil.iter_modules(scope_pkg.__path__):
            mod = importlib.import_module(f"{scope_pkg.__name__}.{mod_name}")
            for name in dir(mod):
                obj = getattr(mod, name)
                if isinstance(obj, type) and isinstance(obj(), Constraint):
                    registry[obj.constraint_id] = obj
    return registry

_CONSTRAINT_REGISTRY = _discover_constraints()
```

### 5.5 model_builder + cp_sat_optimizer 변경

```python
# model_builder.py — 마이그레이션 후
def build_model(...):
    model = cp_model.CpModel()
    # …초기 변수 생성 (start_vars, end_vars 등) 그대로 …

    ctx = BuildContext(model=model, group_meta=group_meta, ...)

    # DB-driven constraint 로드 + 적용
    all_terms: dict[str, ConstraintTerms] = {}
    for c in load_active(db):
        all_terms[c.constraint_id] = c.add_to_model(ctx)

    # objective composition (옛 compose_objective 의 자리)
    objective_terms = []
    for cid, terms in all_terms.items():
        weight = ...  # W-* 와 매핑
        for v in terms.penalty_vars:
            objective_terms.append(weight * v)
    model.Minimize(sum(objective_terms))

    return BuiltModel(model=model, ..., constraint_terms=all_terms)
```

### 5.6 마이그레이션 순서 (점진적, 매 step 마다 parity)

```
[ ] base.py + registry.py + 빈 디렉토리 + Protocol 작성
[ ] global_/tardiness.py — 1-1 만 먼저 클래스화. 기존 model_builder 의 1-1 로직과 등가성 검증 (parity 그린).
[ ] global_/weights.py — W-* 모음. 기존 ModelWeights 와 등가성 검증.
[ ] process/spec_change_setup.py — 4-1. parity.
[ ] process/color_change.py — 4-2. parity.
[ ] product/* 마이그레이션 — 한 번에 하나씩 parity.
[ ] inventory/, color/, fault/ — 동일 절차.
[ ] 모든 constraint 가 클래스화되면 model_builder 의 인라인 로직 제거 → "build_model 은 ctx + load_active 만 호출하는 ~50 LOC 짜리 thin orchestrator" 가 됨.
```

### 5.7 검증 게이트

- 각 step 마다 `pytest tests/test_parity_harness.py -m parity --parity-quick -v` (3/3)
- 모든 마이그레이션 후 `pytest tests/test_parity_harness.py -m parity -v` (11/11 full)
- objective_value 의 diff ≤ 0.1% (정확한 동치 보장 어렵지만 수치 안정성 확인)
- **§3 main-parity gate**: refactoring vs main 의 `POST /api/pipeline/stage2` 응답
  비교 — engine, total_tasks, solver_status, overlap_alert 가 동일해야 함.
  objective_value 의 절대값은 다를 수 있으나 (constraint 클래스화 과정에서 수치
  scaling 의 미세 변동) 동일 입력에 대해 **동일 schedule_task 배치** (equipment +
  start ± 1 working-min) 가 나와야 한다.

---

## 6. Phase 2 — 함수-내 SRP: state-bag 도입 (소요: ~2-3h)

### 6.1 대상

- `cp_sat_optimizer.cp_sat_schedule()` (~1300 LOC 함수 본체)
- `greedy/optimization_loop._run_optimization_once()` (907 LOC)

### 6.2 `_run_optimization_once` 의 state 식별

지금 함수 안에 흩어진 로컬 캐시 (직전 라운드 audit 에서 확인):

```
timeline                          # equipment_code → list[(start, end)]
predecessor_map                   # (order_id, line) → last_task_id
last_batch_on_equip               # equipment_code → ProductionBatch
sq_to_equip                       # (process, sq) → equipment_code
process_end_by_sq                 # (process, sq) → datetime
process_first_output_by_sq        # (process, sq) → datetime
first_insul_output                # datetime
core_first_drum_by_main_sq        # int → datetime
batch_groups                      # OrderedDict[batch_group, list[Batch]]
sq_to_wire_d                      # SQ → wire_diameter
wire_d_earliest                   # wire_d → date
welding_min                       # int (from constraint_params)
constraint_params                 # ConstraintParams
speed_map                         # (eq, sq) → SpeedMaster
equipment_by_process              # process → list[Equipment]
existing_tasks                    # list[ScheduleTask]
tasks_created                     # list (return)
```

### 6.3 SchedulerState dataclass

```python
# greedy/scheduler_state.py
@dataclass
class SchedulerState:
    # ── timeline / occupation ───────────────────────────────────────
    timeline: dict[str, list[tuple[datetime, datetime]]] = field(default_factory=dict)

    # ── precedence / pipeline state ─────────────────────────────────
    predecessor_map: dict[tuple[str, int], int] = field(default_factory=dict)
    process_end_by_sq: dict[tuple[str, int], datetime] = field(default_factory=dict)
    process_first_output_by_sq: dict[tuple[str, int], datetime] = field(default_factory=dict)
    first_insul_output: datetime | None = None
    core_first_drum_by_main_sq: dict[int, datetime] = field(default_factory=dict)

    # ── equipment routing ──────────────────────────────────────────
    sq_to_equip: dict[tuple[str, int], str] = field(default_factory=dict)
    last_batch_on_equip: dict[str, ProductionBatch] = field(default_factory=dict)

    # ── 마스터 데이터 (read-only, run 동안 고정) ───────────────────
    equipment_by_process: dict[str, list[EquipmentMaster]] = field(default_factory=dict)
    speed_map: dict[tuple[str, float], SpeedMaster] = field(default_factory=dict)
    constraint_params: ConstraintParams = field(default_factory=ConstraintParams)
    welding_min: int = 30

    # ── result accumulator ─────────────────────────────────────────
    tasks_created: list[ScheduleTask] = field(default_factory=list)
```

### 6.4 함수 분해 후 구조

```python
def _run_optimization_once(run_label, db, base_date=None) -> dict:
    state = SchedulerState()
    _seed_master_data(state, db)             # equipment_by_process, speed_map, constraint_params
    batches = _load_planned_batches(run_label, db)
    if not batches: return _empty_result()

    schedulable, wip_skipped = _filter_wip_skippable(batches, db)
    base_date = _resolve_base_date(run_label, base_date)
    _seed_state_from_existing(state, run_label, db)  # 기존 ScheduleTask 로 timeline/predecessor 채우기

    batch_groups = _group_and_sort(schedulable, state)

    for group_key, group_batches in batch_groups.items():
        _assign_group(state, group_key, group_batches, db, run_label, base_date)

    return _build_result(state, wip_skipped)
```

각 sub-helper 가 50-100 LOC. `_assign_group` 이 가장 무거울 텐데 (~150 LOC) 그것도 SRP 단일.

### 6.5 마이그레이션 절차 (parity 보호)

```
[ ] SchedulerState dataclass 만 추가 (사용 안 함). pytest + parity 그린 확인.
[ ] _seed_master_data 추출 — _run_optimization_once 안에서 인라인 호출. parity 그린.
[ ] _load_planned_batches 추출. parity.
[ ] _filter_wip_skippable. parity.
[ ] _seed_state_from_existing (가장 미묘 — _seed_pipeline_* 변수들). parity.
[ ] _group_and_sort. parity.
[ ] _assign_group 추출 — 이 단계에서 함수가 ~50 LOC 의 thin orchestrator 가 됨. parity.
```

### 6.6 cp_sat_schedule 동일 적용

같은 패턴: `CpSatRunState` dataclass + thin orchestrator. `model_builder`/`compose_objective` 가 이미 외부 함수이므로 절차는 더 짧다.

### 6.7 검증 게이트

- step 마다 `--parity-quick` 그린
- 마지막 step 후 full parity 11/11 + 474 backend 테스트 그린
- **§3 main-parity gate**: 두 서버에서 동일 ERP 파일로 stage2 호출 → schedule*task
  배치 (batch_id, equipment_code, start_datetime ± 1m, end_datetime ± 1m) 동치
  확인. 함수 분해는 *순수\_ 리팩터이므로 결과가 정확히 같아야 한다.

---

## 7. Phase 3 — Lexicographic Min-Time 최적화 (소요: ~2h)

### 7.1 사용자 요구

> "납기를 반드시 준수하는 방식으로 계획이 짜지도록 여러 해 중 최소시간 해를 구하는 방식"

### 7.2 설계

**Lexicographic optimization** = 우선순위 다단계 목적함수.

```
Phase A (납기 충족):
  minimize  max_tardiness = max(tardiness_vars[g] for g in groups)
  → 최적값 T*
  Case 1: T* = 0 → 모든 납기 충족 가능 ★
  Case 2: T* > 0 → 일부 납기 초과 불가피, T* 가 최소 가능 max-tardy

Phase B (최소 시간):
  add constraint  max_tardiness <= T*    # Phase A 의 결과를 hard 로 고정
  minimize  makespan = max(end_vars[g] for g in groups)
  → 같은 납기 만족도 안에서 가장 짧은 일정
```

### 7.3 구현

```python
# solver/lex_min_time.py (신규)
def solve_lex_min_time(
    built: BuiltModel,
    *,
    time_limit_phase_a_sec: int = 20,
    time_limit_phase_b_sec: int = 20,
) -> LexResult:
    """납기 충족 우선, 그 안에서 makespan 최소화.

    Why two phases not weighted sum: 가중합은 weight 비율에 따라 trade-off
    가 달라지고 운영자가 직관적으로 이해하기 어려움. Lexicographic 은
    "납기 먼저, 그 다음 시간" 이라는 도메인 의미와 1:1 대응.
    """
    model = built.model

    # Phase A: minimize max tardiness
    max_tard = model.NewIntVar(0, MAX_HORIZON_MIN, "max_tardiness")
    if built.tardiness_vars:
        model.AddMaxEquality(max_tard, list(built.tardiness_vars.values()))
    else:
        model.Add(max_tard == 0)

    model.Minimize(max_tard)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_phase_a_sec
    status_a = solver.Solve(model)
    if status_a not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return LexResult(status="INFEASIBLE_A", ...)
    t_star = solver.Value(max_tard)

    # Phase B: fix max_tard <= t_star, minimize makespan
    model.Add(max_tard <= t_star)
    makespan = model.NewIntVar(0, MAX_HORIZON_MIN, "makespan")
    model.AddMaxEquality(makespan, list(built.end_vars.values()))

    model.Minimize(makespan)
    solver.parameters.max_time_in_seconds = time_limit_phase_b_sec
    status_b = solver.Solve(model)

    return LexResult(
        status="OPTIMAL" if status_b == cp_model.OPTIMAL else "FEASIBLE",
        t_star=t_star,
        makespan_star=solver.Value(makespan),
        all_tardy=t_star > 0,
        # …
    )
```

### 7.4 통합 지점

`cp_sat_schedule` 안의 `solver.solve(model)` 호출을 `solve_lex_min_time(_built, ...)` 로 교체.

기존 `solver.solve` 단일-목적 경로는 **opt-in flag** 로 보존:

```python
def cp_sat_schedule(..., min_time_mode: bool = False):
    if min_time_mode:
        result = solve_lex_min_time(_built, ...)
    else:
        # 기존 weighted-sum 경로
        result = solve_weighted_sum(_built, ...)
```

이유: weighted-sum 경로는 EDD penalty / slack 등 미세한 우선순위 표현력이 있고
parity tests 가 그 결과를 동결해 둠. 새 경로는 순수 makespan 만이라 parity
대신 별도의 lex-specific 시나리오를 추가한다.

### 7.5 새 parity 시나리오

```
backend/tests/fixtures/parity_scenarios/
  12_lex_due_feasible.json    # 모든 납기 충족 가능 → T*=0, makespan 최소
  13_lex_due_infeasible.json  # 일부 납기 불가능 → T*>0, makespan 그 안에서 최소
```

### 7.6 검증 게이트

- 기존 11/11 parity 그린 (weighted-sum 경로 보존 확인)
- 신규 시나리오 12, 13 도 결정론적으로 통과
- 실 ERP 데이터 (run_label=20260425_225331) 에 대해 `min_time_mode=True` 로 수동 실행 → makespan 비교
- **§3 main-parity gate**: lex 모드는 main 에 없는 신규 기능이므로 `min_time_mode=False`
  (default) 호출에 대해서만 main 과 동치 확인. `min_time_mode=True` 결과는 별도
  artifact 로 저장 (sample run_label 단위 makespan 표).

---

## 8. Agent Team / Skill 사용 전략

| Phase                 | 권장 도구                                                                                                                                       |
| --------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| Phase 0 (cleanup)     | Explore agent (importer 스캔), Bash (grep + pytest)                                                                                             |
| Phase 1 (constraints) | **superpowers:subagent-driven-development** — 각 카테고리 디렉토리를 별도 subagent task 로. 매 step 마다 spec-reviewer + code-quality-reviewer. |
| Phase 2 (state-bag)   | general-purpose agent 1명 — 한 함수당 1명, 직렬 (병렬 시 conflict 위험).                                                                        |
| Phase 3 (lex)         | 단독 작업 + Plan 모드로 설계 검토 후 진입.                                                                                                      |
| 모든 phase            | **superpowers:verification-before-completion** + **superpowers:test-driven-development** (특히 새 constraint 클래스).                           |

**ultrathink 사용 지점**:

- Phase 1 의 Constraint Protocol 설계 (한 번 설계 잘못하면 38개 클래스 재작성)
- Phase 2 의 state-bag 분할 (어떤 state 가 어느 sub-helper 에 속하는가)
- Phase 3 의 max_tard 계산이 INFEASIBLE 인 경우 처리 (currently sheath_color_hard fallback 과 충돌하는가?)

세 지점에서 명시적으로 "**ultrathink**" 키워드를 써서 깊은 추론 모드로 들어가라.

---

## 9. Atomic Commit 정책

각 phase 의 sub-step 끝마다 commit. **모든 commit 은 다음 형식**:

```
<type>(<scope>): <one-line subject>

<body — why, not what>

<verification-evidence — paste actual test output or curl response>

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

push 는 **사용자 명시적 요청 시에만**. 본 문서가 push 권한을 부여하지 않는다.

---

## 10. 위험 / Risk Register

| 위험                                                                                    | 완화책                                                                                  |
| --------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| Phase 1 마이그레이션 중 parity 깨짐                                                     | step-by-step + 매 step parity-quick. 깨지면 즉시 revert + 재설계.                       |
| `_discover_constraints` 가 import-time 사이드이펙트                                     | 명시적 register-by-decorator 패턴 검토. ultrathink 단계에서 결정.                       |
| Phase 2 state-bag 도입 시 race condition (auto_schedule 의 retry loop 가 state 를 공유) | SchedulerState 는 매 retry 마다 새로 생성. 공유 X.                                      |
| Phase 3 의 lex Phase B 가 time-limit 안에 못 끝남                                       | 첫 FEASIBLE 도 채택 + warning. operational mode 는 weighted-sum 유지.                   |
| Auto-formatter 가 deferred import 를 제거                                               | 모든 deferred import 에 `# noqa: F401  + 사용 위치 코멘트`. CI lint 강화 검토.          |
| 사용자가 phase 중간에 중단/재개 요청                                                    | 매 phase 의 진입 조건 / 종료 조건이 본 문서에 명시 — 어디서 멈췄는지 task list 로 추적. |

---

## 11. 종료 조건 (Definition of Done)

본 라운드는 **다음 모두를 만족해야** "100% 완료" 라 부를 수 있다:

- [ ] urgent_scheduler.py 삭제 + 테스트 마이그레이션 완료
- [ ] llm_explainer.py 삭제 (decision_narrator 통합)
- [ ] D7-C re-export shells 모두 제거 (40 + 14 = 54개)
- [ ] `solver/constraints/` 6개 카테고리 디렉토리 + 38개 클래스 + registry
- [ ] `model_builder.build_model` 이 ≤200 LOC (현재 824)
- [ ] `cp_sat_optimizer.cp_sat_schedule` 이 ≤200 LOC (현재 ~1300)
- [ ] `optimization_loop._run_optimization_once` 가 ≤100 LOC (현재 907)
- [ ] `solver/lex_min_time.py` 신규 + 시나리오 12, 13 parity green
- [ ] 474+ backend pytest, 11/11 full parity, 107+ frontend vitest, production build — 모두 그린
- [ ] 실 ERP 파일 e2e (Documents/ERP생산계획\_v1.xls + 긴급수주.xlsx) 로 lex 모드 작동 확인
- [ ] **§3 main-parity gate** — refactoring(8000) vs main(8001) 두 서버 동시 실행, 28
      endpoint 비교 리포트 생성 (`/tmp/parity_diff_report.md`), EXPECTED_DRIFT 외
      차이 0건 확인. 실 ERP 파일 e2e 도 두 서버 모두에서 실행 → schedule_task
      배치 동치 확인 (objective_value 는 ±0.1% 허용)
- [ ] `docs/superpowers-final-report.md` 의 §8 known-debt 5개 중 (1)/(2) 가 닫힘
- [ ] 본 문서가 작업 후 `done` 으로 marker 업데이트되거나 archive

---

## 12. 시작 시 첫 메시지 템플릿

다음 세션 첫 발화에 이렇게 적어라 (자기 진입 지시):

```
사용자가 docs/next-session-prompt.md 따라 진행하라고 함.
지금부터:
1. §1 의 9개 문서 모두 읽고 자기점검 4문항 답변 가능 상태 확인.
2. §2 의 harness 원칙 + §3 의 main-parity gate 셋업 적재.
3. main 브랜치 worktree 생성 + 8001 포트 baseline 서버 띄우기 (§3.1).
4. tests/main_parity/parity_endpoint_diff.py 신규 작성 + GET 28개 endpoint
   현 시점 baseline diff (refactoring=main 인지) 확인 — 본 라운드 시작 시점에
   기능 동치를 먼저 증명한 뒤 진입.
5. Phase 0 진입 — TaskCreate 로 4개 sub-task 생성.

각 phase 종료 시 §3 main-parity gate 강제 실행. EXPECTED_DRIFT 화이트리스트
밖 회귀 0건 확인 후에만 다음 phase 로 진입.

ultrathink 모드 — 추측 없이 코드 + 문서 기반 추론.
```

---

**문서 작성**: 2026-04-25, 직전 라운드 최종 commit `2707c21` 기준.
**다음 업데이트**: Phase 0 완료 시 본 문서에 "Phase 0 ✓" 마커 추가.
