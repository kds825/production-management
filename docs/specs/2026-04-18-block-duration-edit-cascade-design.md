# 블록 소요시간 편집 · 연쇄 재배치 · 납기초과 필터 연동 설계

- **작성일**: 2026-04-18
- **브랜치**: `dev_jaewoo`
- **상태**: Draft (사용자 리뷰 대기)
- **선행 커밋**: `978648e feat(ui): shared Toast component`, `c70bdc7 feat(scheduler): JIT auto_schedule 통합 + 파이프라인 체인 fixed-point`

## 1. 목표와 요구사항

### 1.1 사용자 요구
1. 작업 수정 모달(우클릭 → 수정하기)에서 **소요시간(h) 필드를 편집 가능**하게 한다.
   - 소요시간 ↑ → 시작일시 고정, 종료일시 연장
   - 소요시간 ↓ → 시작일시 고정, 종료일시 단축
2. 변경 결과로 다음 블록과 **겹침(충돌)** 발생 시, 확인 모달을 띄워 **재배치 동의**를 받는다.
3. 재배치는 **동일 설비의 충돌 블록부터 push**되며, 그 영향으로 **후속 공정(연선 → 절연/시스)** 시간이 **연쇄 업데이트**된다.
4. 드래그 이동 시에도 동일한 연쇄/충돌 확인/재배치 로직이 적용되어야 한다.
5. 헤더의 **납기 초과 카운터**는 현재 적용된 필터(전체/저압만/고압만/공정별)에 반응하여 업데이트된다.

### 1.2 비목표 (YAGNI)
- 다중 사용자 동시 편집 잠금(optimistic concurrency). PoC 범위 밖, 향후 훅 포인트만 설계에 명시.
- 블록 부분 수락 UI (pushes만 받고 pulls만 거절 등 행 단위 선택). 전체 수락/거절만.
- 자동 일정 최적화(백그라운드 JIT 재실행) — 사용자 명시 액션에만 반응.

## 2. 현재 구현 상태 (스냅샷)

| 항목 | 상태 | 위치 |
|---|---|---|
| 작업 수정 모달 | ✅ | `frontend/src/features/scheduler/components/TaskFormModal.tsx` |
| 소요시간 필드 읽기전용 | ⚠️ 수정 대상 | `TaskFormModal.tsx:456-462` |
| 블록 드래그 이동 | ✅ `dnd-kit` | `frontend/src/app/(main)/scheduler/page.tsx:700-906` |
| 같은 설비 push (`cascadePush`) | ✅ 프론트 구현 | `scheduleStore.ts:79-122` |
| 동일 수주 후속공정 시간 연동 | ✅ 부분 | `scheduleStore.ts:375-394` |
| Cross-equipment cascade | ❌ 미구현 | — |
| 충돌 미리보기 API | ✅ | `POST /api/schedules/cascade-preview` (`backend/app/presentation/routes/schedules.py:749-876`) |
| 일괄 적용 API | ✅ | `POST /api/schedules/tasks/bulk-update` |
| 충돌 해소 모달 | ✅ | `ConflictResolutionModal` (드래그 흐름에만 연결됨) |
| Toast | ✅ | `frontend/src/shared/ui/Toast.tsx` (`useToastStore`) |
| 납기초과 카운터 | ⚠️ 필터 미반영 가능성 | 헤더 컴포넌트 (위치 구현 단계에서 특정) |

## 3. 핵심 설계 원칙

1. **스케줄링 진실 소스(Single Source of Truth) = 백엔드 `cascade-preview`**
   - 프론트엔드 `scheduleStore`의 별도 `cascadePush` 선계산은 제거하거나 시각 미리보기(pre-snap)용으로만 축소한다. 알고리즘 중복을 만들지 않는다.
2. **2-Phase 상호작용** (모달 편집 / 드래그 / 종료일 수정 모두 동일 경로)
   - Phase A: `cascade-preview` 로 영향 범위 계산 → `ConflictResolutionModal` 에 제안 표시
   - Phase B: 사용자 승인 → `tasks/bulk-update` 트랜잭션으로 일괄 반영
3. **Fail-Fast**: 입력 검증은 프론트·서버 양쪽에서 각각 방어.
4. **Push는 필수 해소, Pull은 선택 제안**: 둘 다 같은 모달에서 표시하되 시맨틱 구분.
5. **해소 불가 상태는 숨기지 않는다**: `unresolved[]` 로 노출하고, 존재하면 "전체 적용" 버튼을 비활성화한다.
6. **디자인 시스템**: `pwc-design` 스킬 규칙에 따라 samildevkit 패키지는 **직접 import 하지 않고** 프로젝트 내부에서 토큰/컴포넌트를 구현한다. 구현 후 `verify-pwc-design` 으로 검증.

## 4. 아키텍처와 데이터 흐름

### 4.1 변경 파이프라인

```
(1) 사용자 액션 (모달 duration/start/end 편집 · 드래그 이동 · 설비 변경)
      │
      ▼
(2) POST /api/schedules/cascade-preview
        { task_id, new_start, new_end, new_equipment_code? }
      │
      ▼
(3) 응답: { pushes[], pulls[], unresolved[], can_auto_resolve, iter_count }
      │
      ├── pushes = pulls = unresolved = ∅ → 즉시 bulk-update
      │
      └── 제안/경고 있음 → ConflictResolutionModal 열기
             │ 🔴 충돌 해소 (pushes)
             │ 🟡 앞당김 제안 (pulls)
             │ ⛔ 해소 불가 (unresolved) — 있으면 "전체 적용" disabled
             ▼
(4) 사용자 "전체 적용" → POST /api/schedules/tasks/bulk-update (트랜잭션)
      │
      ▼
(5) 프론트 schedule state 교체 → visibleTasks 재계산 → 납기초과 카운터 자동 반영
```

### 4.2 시맨틱 용어

- **Push**: 변경의 필연적 결과로 다른 블록이 뒤로 밀리는 제안. 수락하지 않으면 충돌 상태가 남으므로 "전체 적용"으로만 해소.
- **Pull**: 변경으로 생긴 슬랙을 활용하여 후속 공정을 당길 수 있다는 **선택적** 제안. 수락 안 하면 기존 스케줄 유지.
- **Unresolved**: 알고리즘이 해소할 수 없는 잔여 충돌 (due date 위반, 공간 없음, 반복 상한 초과).
- **Successor chain**: 같은 `(sales_order_id, sales_order_line)` 의 다음 공정 (연선 → 절연 → 시스).

## 5. 백엔드 변경

### 5.1 엔드포인트 계약 확장 — `POST /api/schedules/cascade-preview`

**요청**
```json
{
  "task_id": "string",
  "new_start": "ISO 8601",
  "new_end": "ISO 8601",
  "new_equipment_code": "string|null"
}
```

**응답**
```json
{
  "pushes": [
    {
      "task_id": "...",
      "old_start": "...", "old_end": "...",
      "new_start": "...", "new_end": "...",
      "reason": "same_equipment_conflict|successor_chain|cross_equipment_conflict"
    }
  ],
  "pulls": [
    {
      "task_id": "...",
      "old_start": "...", "old_end": "...",
      "new_start": "...", "new_end": "...",
      "reason": "successor_slack_available"
    }
  ],
  "unresolved": [
    {
      "task_id": "...",
      "reason": "due_date_violation|no_space_forward|cycle_detected|invalid_equipment",
      "detail": "..."
    }
  ],
  "can_auto_resolve": true,
  "iter_count": 3
}
```

### 5.2 알고리즘 — `plan_cascade_preview`

**위치**: `backend/app/services/cascade_planning.py` (신규). `jit_scheduling.py` 의 유틸(`OperatingCalendar.advance`, pipeline-chain lookup)을 재사용.

**의사코드**

```
상수:
  MAX_WAVES = 5       # cascade 전파 깊이(세대). 5 단계를 넘으면 cycle로 간주
  HARD_TASK_LIMIT = 500  # 안전장치 (무한루프 방지)

1. snap = deepcopy(current_tasks)
2. apply(snap, task_id, new_start, new_end, new_equipment_code)
3. frontier = [task_id]           # 이번 wave 에서 처리할 대상
4. pushes = []; pulls = []; unresolved = []
5. push_count_by_task = defaultdict(int)
6. for wave in 0 .. MAX_WAVES:
     if frontier empty: break
     next_frontier = []
     for T in frontier:
         # (a) 같은 설비 내 충돌 이웃
         for N in same_equipment_neighbors_after(T, snap):
             if N.start < T.end:
                 new_N_start = calendar.advance(T.end)
                 new_N_end   = new_N_start + N.duration
                 pushes.append({N, reason=same_equipment_conflict, old, new})
                 apply(snap, N, new_N_start, new_N_end)
                 push_count_by_task[N.id] += 1
                 next_frontier.append(N)
         # (b) successor chain (같은 sales_order_line)
         for S in successor_tasks(T):
             if S.start < T.end:
                 new_S_start = calendar.advance(T.end)
                 new_S_end   = new_S_start + S.duration
                 pushes.append({S, reason=successor_chain, old, new})
                 apply(snap, S, new_S_start, new_S_end)
                 push_count_by_task[S.id] += 1
                 next_frontier.append(S)
         if len(pushes) > HARD_TASK_LIMIT:
             unresolved.append({reason=cycle_detected, detail="hard limit reached"})
             goto step 7
     frontier = dedup(next_frontier)
   # wave loop 종료 후 frontier 가 여전히 비어있지 않으면:
   if frontier not empty:
       for t in frontier:
           unresolved.append({t, reason=cycle_detected, detail="cascade depth > MAX_WAVES"})

7. # due date / 공간 검증
   for each changed task in snap:
      if new_end > batch.due_date: unresolved.append(due_date_violation)
      if new_end > horizon_end:    unresolved.append(no_space_forward)

8. # 동일 task 반복 push 감지 (보조)
   for t, count in push_count_by_task.items():
       if count > MAX_WAVES:
           unresolved.append({t, reason=cycle_detected, detail=f"pushed {count} times"})

9. # Pull 제안 (backward pass, snap에 반영하지 않음)
   if new_end(변경 task) < old_end(변경 task):  # 블록이 짧아졌거나 앞당겨짐
       # 수주 체인을 따라 순차 전파. prev_end 는 직전 공정의 (제안 적용시) 끝.
       prev_end = 변경 task.new_end
       for S in successor_chain_of(변경 task):
           slack = S.old_start - prev_end   # 선행제약 반영: S 는 prev_end 이후만 시작 가능
           if slack > 0:
               proposed_S_start = calendar.reverse_advance(S.old_start, slack_hours)
               proposed_S_end   = proposed_S_start + S.duration
               pulls.append({ S, new_start=proposed_S_start, new_end=proposed_S_end,
                              reason=successor_slack_available })
               prev_end = proposed_S_end
           else:
               prev_end = S.old_end  # 슬랙 없음 → 다음 successor 도 기존 일정 기준
       # Pull 은 해당 설비 내 이웃 pull 은 수행하지 않음 (YAGNI). 수주 체인만.

10. can_auto_resolve = (len(unresolved) == 0)
11. return { pushes, pulls, unresolved, can_auto_resolve, iter_count: wave_used }
```

**wave 의미**: cascade 전파의 "세대 수". wave 0 = 변경 task 자체, wave 1 = 그 영향을 받은 직접 이웃/후속, wave 2 = 그 이웃들의 이웃… MAX_WAVES=5 는 "5단계 이상 깊이의 연쇄는 실질적으로 수동 재배치가 필요한 상황" 이라는 운영 가정.

응답의 `iter_count` 는 **실제 사용된 wave 수**(1 ≤ n ≤ MAX_WAVES+1) 를 담는다.

### 5.3 `POST /api/schedules/tasks/bulk-update` 보강
- 기존 존재. 서버측에서 `changes[]` 를 **한 트랜잭션**으로 적용(부분 실패 방지).
- 응답으로 갱신된 task 목록을 반환 → 프론트는 state 일괄 교체.
- **재검증 가드**: 트랜잭션 내에서 각 변경에 대해
  - 같은 설비 내 겹침 0 검증
  - 수주 체인의 선행/후행 제약 검증 (`predecessor.end ≤ S.start`)
  - 위반 시 422 + 위반 상세 반환 → 프론트는 Toast 에 사유 표시 + 사용자에게 cascade-preview 재실행 유도. 이로써 Pull 제안이 다른 제약(같은 설비 선행 task 등)과 모순되는 엣지케이스가 있어도 커밋이 차단된다.

### 5.4 JIT 영향
- `jit_scheduling.py` 는 수정하지 않고 유틸만 호출. 단, `_forward_pass` 안에 "사전 잠금된 구간" 회피 로직이 있다면 그 유틸을 분리해 `cascade_planning.py` 가 재사용.

## 6. 프론트엔드 변경

### 6.1 `TaskFormModal.tsx` — 3필드 편집 규칙

- 소요시간 필드: `<div>` → `<input type="number" step="0.1" min="0.1">`. (`pwc-design` 토큰으로 NumberInput 구현. 프로젝트 내부 컴포넌트, samildevkit 직접 import 금지.)
- **편집 규칙** (마지막 편집 소스를 추적하지 않음, 각 핸들러가 독립 규칙):
  - `onDurationChange(h)`: `start` 고정 → `end = start + h*3600s`
  - `onStartChange(dt)`: `end` 고정 → `duration = (end - dt)/3600000`
  - `onEndChange(dt)`: `start` 고정 → `duration = (dt - start)/3600000`
- **검증**: `duration <= 0 || end <= start || isNaN` → 저장 버튼 disabled + 입력 하단 경고(`--color-negative-600`).
- **확인 클릭**: `useScheduleChangeWithCascade(changes)` 공통 훅 호출 (§6.3).

### 6.2 `ConflictResolutionModal` — 섹션화

| 섹션 | 조건 | 컬러 토큰 | 열 |
|---|---|---|---|
| 🔴 충돌 해소 | `pushes[] ≠ ∅` | `--color-negative-500` | task, 설비, `기존 → 신규`, Δ(+Xh) |
| 🟡 앞당김 제안 | `pulls[] ≠ ∅` | `--color-warning-500` | 동일 포맷, Δ(−Xh) |
| ⛔ 해소 불가 | `unresolved[] ≠ ∅` | `--color-negative-700` | task, 사유 |

**버튼 상태** (samildevkit 스펙 기반 `Button primary`):
- `unresolved[] ≠ ∅` → "전체 적용" disabled, "닫기"만.
- `pulls` 만 있고 `pushes` 없음 → 라벨 `"앞당김 적용"`.
- `pushes + pulls` → 라벨 `"전체 적용"`.
- `pushes` 만 있음 → 라벨 `"재배치 적용"`.

### 6.3 공통 훅 `useScheduleChangeWithCascade`

`TaskFormModal` 과 `scheduler/page.tsx:handleDragEnd` 가 공유.

```
input: { task_id, new_start, new_end, new_equipment_code? }

flow:
  1. POST cascade-preview
  2. if (pushes + pulls + unresolved == ∅)
       → POST bulk-update([변경본만])
       → Toast success → 닫기
  3. else
       → setConflictModal({ pushes, pulls, unresolved, can_auto_resolve })
       → modal "적용" 클릭 시 POST bulk-update(pushes ∪ pulls ∪ [변경본])
       → Toast success → 닫기
  4. error 처리 (§7.2)
```

### 6.4 `scheduler/page.tsx` 드래그 리팩터링
- 기존 `handleDragEnd` 의 프론트측 `cascadePush` 호출 제거.
- `moveTask` → `useScheduleChangeWithCascade` 로 대체.
- 드래그 중 미리보기(`handleDragMove`)는 시각 효과만 유지, 커밋은 하지 않는다.

### 6.5 납기 초과 카운터 (헤더)

- 구현 단계에서 컴포넌트 위치 특정 후:
```ts
const overdueCount = useMemo(
  () => visibleTasks.filter(isOverdue).length,
  [visibleTasks]  // 이미 필터 selector로 만들어진 결과
);
```
- `visibleTasks` 는 `useFilterStore` + `useScheduleStore` 조합 셀렉터. 스케줄 변경·필터 변경 둘 다에 반응.
- 카운터가 0이면 칩 색상 중립 토큰(`--color-neutral-300`)으로 전환, 1 이상이면 기존 빨강.

## 7. 엣지케이스 · 에러 처리

### 7.1 엣지케이스 카탈로그

| # | 시나리오 | 처리 |
|---|---|---|
| E1 | 소요시간 0/음수 입력 | 프론트 Fail-Fast + 서버 400 가드 |
| E2 | `end ≤ start` | 동일 |
| E3 | 다른 process 설비로 드래그 (연선 블록 → 절연 설비) | 프론트 드래그 가드 유지. cascade-preview는 `unresolved[invalid_equipment]` 반환 |
| E4 | iter_count ≥ MAX_ITER | `unresolved[cycle_detected]`. 전체 적용 disabled |
| E5 | push 대상이 주말/비가동 gap에 걸림 | `OperatingCalendar.advance` 로 skip |
| E6 | Pull 이 이전 공정 선행 제약 위반 | 제안 단계에서 drop |
| E7 | 동일 task 두 탭 동시 편집 | PoC 범위 밖. last-writer-wins. `task.updated_at` 기반 optimistic lock 훅 포인트만 설계에 명시 |
| E8 | bulk-update 트랜잭션 실패 | 롤백. Toast error + 모달 유지 |
| E9 | cascade-preview 빈 응답 | 모달 skip, 바로 bulk-update, Toast success |
| E10 | 납기 초과 카운터 0 | 중립 컬러 전환 |
| E11 | 드래그 후 후속 공정 설비에서 cross-equipment 충돌 | §5.2 의 단계 (b) 가 연쇄 처리 |

### 7.2 에러 처리 원칙
- **5xx / network**: Toast(`error`) "재배치 계산에 실패했습니다. 잠시 후 다시 시도해주세요." 모달은 닫지 않음.
- **4xx (검증 실패)**: 모달 내 인라인 에러.
- **타임아웃**: cascade-preview 서버 목표 p95 < 1초. 초과 시 INFO 로그.

### 7.3 로깅
- cascade-preview 호출마다 `{task_id, iter_count, pushes_n, pulls_n, unresolved_n}` INFO 로그.
- bulk-update 트랜잭션 실패는 ERROR 로그.

## 8. 테스트 전략

### 8.1 백엔드 unit (`backend/tests/services/test_cascade_planning.py`)
- T1 단일 블록 duration 증가 → 같은 설비 다음 블록 push
- T2 successor chain push (연선 → 절연 → 시스)
- T3 successor 자기 설비에서 충돌 → cross-equipment push
- T4 due_date 위반 → unresolved
- T5 MAX_ITER 초과 → unresolved[cycle_detected]
- T6 Pull 제안 — slack 있을 때만 포함
- T7 Pull 이 predecessor 선행 제약 위반 시 drop (E6)
- T8 `new_equipment_code` 변경 + cascade
- T9 주말 gap skip (E5)

### 8.2 백엔드 integration (`backend/tests/api/test_cascade_preview.py`)
- 응답 스키마 계약
- bulk-update 트랜잭션 원자성 (중간 실패 시 전체 롤백)

### 8.3 프론트 unit
- `TaskFormModal`: 3필드 상호작용 (duration 편집, start 편집, end 편집)
- `useScheduleChangeWithCascade`: 응답 분기 (빈 응답 → skip, 내용 있음 → modal open, unresolved → 버튼 disable)
- `ConflictResolutionModal`: 섹션 조건부 렌더링
- 납기초과 카운터: 필터 변경 시 재계산

### 8.4 E2E (Playwright)
CLAUDE.md 규칙: "UI 기능은 E2E 검증 필수".

- `frontend/e2e/block-duration-edit.spec.ts`
  1. 블록 우클릭 → 수정 → 소요시간 81 → 90 → 확인 → 충돌 모달 → 전체 적용 → 스케줄 반영
  2. 소요시간 축소 → Pull 제안 모달 → 거부(닫기) → 원본만 축소, 후속 그대로
  3. 소요시간 → due_date 위반 유도 → unresolved 섹션 + 적용 disabled
- `frontend/e2e/block-drag-cascade.spec.ts`
  1. 블록 드래그 → cross-equipment 충돌 → 모달 → 적용 → 후속 공정 설비 블록 이동 확인
- `frontend/e2e/overdue-counter-filter.spec.ts`
  1. 필터 변경(전체/고압만/저압만)에 따라 카운터 값 변화 확인, 합이 전체 기준 N 이 되는지

### 8.5 PR 머지 gate
- 백엔드 pytest 100% 통과
- 프론트 vitest 100% 통과
- Playwright 3개 스펙 통과
- `verify-pwc-design` 경고 0
- graphify 재빌드 후 god-node 증가 없음

## 9. 롤아웃 순서 (writing-plans 시드)

1. 백엔드 `plan_cascade_preview` + unit tests (T1–T9)
2. API 계약 변경 + integration tests
3. 프론트 공통 훅 `useScheduleChangeWithCascade` + unit
4. `ConflictResolutionModal` 섹션화 + unit
5. `TaskFormModal` 3필드 편집 + unit
6. `scheduler/page.tsx` 드래그 리팩터링 (프론트 `cascadePush` 제거/축소)
7. 납기 초과 카운터 필터 반응화
8. E2E 3개 스펙
9. graphify rebuild + `verify-pwc-design`
10. atomic commit 단위로 각 단계 커밋

## 10. 결정 이력

- **Q1 확인 팝업**: `ConflictResolutionModal` 재사용 (A 채택)
- **Q2 3필드 규칙**: B — 각 핸들러 독립 규칙 (duration 편집만 신규, start/end 현재 동작 유지)
- **Q3 연쇄 깊이 / 실패**: A — Full cascade + 실패시 차단
- **Q4 방향성**: A(혼합) — Push 자동 제안, Pull도 자동 제안하되 **둘 다 사용자 컨펌 필수** (자동 적용 없음)
- **Q5 구현 위치**: 접근 1 — 백엔드 `cascade-preview` 확장, 프론트는 얇은 orchestrator
- **추가 스코프**: 납기 초과 카운터 필터 반응화

## 11. 미해결/추후 과제
- 다중 사용자 optimistic concurrency (E7)
- 자동 일정 최적화 버튼 (pull 후보 기반 "공간 압축" 별도 기능)
- cascade-preview 응답 캐싱 (동일 입력 → 결과 재사용)
