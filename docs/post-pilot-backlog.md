# Post-pilot Backlog

> **목적.** Pilot 통과 이후로 미룬 P1/post-pilot 항목을 한 곳에 모은다. Week 6 dry-run 의 friction-log 에서 P1/post-pilot 으로 분류된 항목, 그리고 spec §3 "Explicitly deferred to post-pilot backlog (P2)" 항목이 여기로 라우팅된다.

## Routing rules

- **Week 6 friction-log** → `docs/dry-run-friction-log.md` 의 P1 / post-pilot row 를 이 파일로 옮긴다.
- **개발 중 발견된 큰 cleanup** → `docs/deletion-log.md` 에 기록된 Retained 항목 중 post-pilot 에서 손볼 것.
- **Spec §3 P2 항목** (이미 알려진 것):
  - `app/(main)/plan-register/page.tsx` (1,910 lines) split
  - `features/scheduling-review/components/ProductionBatchTable.tsx` (1,008 lines) split
  - `app/(main)/scheduling-review/page.tsx` (1,013 lines) split
  - LLM-proposes-constraint-changes (CEO 10-star feature)
  - `alternative_slots` async 엔드포인트
  - Backend+frontend 컨테이너화 (현재 native dev)
  - 백업/DR, on-call playbook, secrets rotation (Vault), PII anonymization, Grafana, RBAC

## 알려진 known-debt (Weeks 1-9 plan compromises)

### 코드

- **Decision Card per-constraint trace 미연결** — `solver_run` row 는 매 Stage 2 실행에서 정상 INSERT 되지만 `solver_decision` 은 항상 0건. 원인: `cp_sat_optimizer.py` 의 `write_trace` 호출이 `penalty_values={}, hard_literal_values={}` 빈 dict 를 넘긴다. `model_builder.py` 가 `BuiltModel.penalty_vars` / `hard_literals` 를 dataclass field 로 노출은 하지만 build 시점에 populate 하지 않는다 (Week 2 stub). 필요 작업: ① 모델 빌드 중 penalty/hard literal 을 만들 때마다 `constraint_id → IntVar` 매핑을 dict 에 채우고, ② cp_sat_optimizer.py:1262 `solver.solve()` 직후 `solver.Value(var)` 로 IntVar → 정수값을 추출, ③ write_trace 에 채운 dict 전달. 위험: solver 객체에 추가 책임을 지우면 trace 가 stale 한 BuiltModel 을 참조해 KeyError 가 날 수 있어 build 단계와 trace 호출 사이의 lifecycle 명확화 필요. UI 영향: `/api/decisions/{batch_id}/latest` 항상 404 → Decision Card 가 빈 상태로 폴백 (현재 graceful).
- **`backend/app/services/llm_explainer.py` (legacy 620 LOC)** — `routes/audit.py` + `routes/plan_pipeline.py` 가 여전히 사용 중. Week 4 의 `services/llm_providers/` + `decision_narrator.py` 와 코드 중복은 없으나 두 LLM 경로가 공존함. 단일 경로로 통합.
- **`cp_sat_optimizer.cp_sat_schedule()` 1,317-line 본체** — Week 2 에서 의도적으로 보존. `_try_preempt_for_urgent` (188 LOC), `_write_solver_snapshot` (151 LOC) 을 `solver/preemption.py` + `solver/snapshot.py` 로 추출 가능.
- **`routes/plan_pipeline.py`** — Week 4 4A.1 에서 `services/pipeline/` 4 모듈을 추출했으나 라우트 파일 자체는 여전히 2,567줄. Stage1 ingest / 동기화 / 상태 폴링 로직을 추가로 떼어내면 ≤500 줄 목표 달성 가능.

### 테스트

- **Scenario 10a/10b parity 중복** — `10a_all_constraints.json` 과 `10b_none_constraints.json` 이 byte-identical (`is_enabled` 가 `solver_input` 에 미반영). `ConstraintParams.load` 가 `is_enabled` 를 fixture payload 로 propagate 하도록 수정 후 재freeze (`parity-update:` prefix 필수) 또는 시나리오 10 의 차이를 다른 필드로 재정의.

### 운영

- **Pilot success criteria 재검증** — `docs/pilot-success-criteria.md` 가 `[BEST-GUESS]` 마커로 동결됨. dry-run 결과로 실측치 채우기.

## 라우팅 큐 (Week 6 dry-run 후 채워질 예정)

| 발견일            | 친찰                            | severity   | 출처 시나리오      | 해결 위치                                            |
| ----------------- | ------------------------------- | ---------- | ------------------ | ---------------------------------------------------- |
| (예시) 2026-04-30 | Decision Card LLM 응답 4초 지연 | post-pilot | dry-run 시나리오 4 | services/llm_providers/anthropic.py — streaming 도입 |

## 출처

- `docs/dry-run-friction-log.md` (P1 / post-pilot 항목 라우팅)
- `docs/specs/2026-04-23-production-handoff-refactor-design.md` §3
- `docs/deletion-log.md`
