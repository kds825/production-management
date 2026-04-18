# 블록 소요시간 편집 · 연쇄 재배치 설계 (v2, 비판 리뷰 반영)

- **작성일**: 2026-04-18
- **리비전**: v2 (CEO / Senior Eng / UX / DevEx 병렬 리뷰 반영)
- **브랜치**: `dev_jaewoo`
- **상태**: Draft (사용자 승인 후 writing-plans 진입)

## 1. 목표와 요구사항

### 1.1 사용자 요구

1. 작업 수정 모달에서 **소요시간(h) 필드를 편집 가능**하게 한다 (시작일시 고정, 종료일시 재계산).
2. 변경 결과로 다음 블록과 **겹침(충돌)** 발생 시, 확인 모달을 띄워 **재배치 동의**를 받는다.
3. 재배치는 **동일 설비의 충돌 블록부터 push**되며, 그 영향으로 **후속 공정(연선 → 절연/시스)** 시간이 **연쇄 업데이트**된다.
4. 드래그 이동 시에도 동일한 연쇄/충돌 확인/재배치 로직이 적용된다.

### 1.2 비목표 (YAGNI)

- 다중 사용자 동시 편집 잠금. PoC 범위 밖, 훅 포인트만 설계.
- 자동 일정 최적화(백그라운드 JIT 재실행). 명시 액션에만 반응.
- Pull 제안의 row 단위 개별 체크박스 (대신 master toggle 로 간소화).

### 1.3 분리된 스코프 (별도 PR로 진행)

- **납기 초과 카운터 필터 반응화**: 본 기능과 코드 결합도가 낮고 PR 리스크를 줄이기 위해 분리. 별도 spec(`2026-04-18-overdue-counter-filter-aware.md`) 로 후속 처리.

## 2. 현재 구현 상태 스냅샷

| 항목                                | 상태                         | 위치                                                                 |
| ----------------------------------- | ---------------------------- | -------------------------------------------------------------------- |
| 작업 수정 모달                      | ✅                           | `frontend/src/features/scheduler/components/TaskFormModal.tsx`       |
| 소요시간 필드 읽기전용              | ⚠️ 수정 대상                 | `TaskFormModal.tsx:456-462`                                          |
| 기존 모달 하드코딩 hex              | ⚠️ 수정 대상 (§9 토큰 치환)  | `TaskFormModal.tsx` 인라인 style, `ConflictResolutionModal.tsx` 다수 |
| 블록 드래그 이동                    | ✅ `dnd-kit`                 | `frontend/src/app/(main)/scheduler/page.tsx:700-906`                 |
| 같은 설비 push (`cascadePush`)      | ✅ 프론트 — 본 설계에서 제거 | `scheduleStore.ts:79-122`                                            |
| 동일 수주 후속공정 시간 연동        | ✅ 부분                      | `scheduleStore.ts:375-394`                                           |
| Cross-equipment cascade             | ❌ 미구현                    | —                                                                    |
| 충돌 미리보기 API                   | ✅ 부분                      | `POST /api/schedules/cascade-preview`                                |
| 일괄 적용 API                       | ✅                           | `POST /api/schedules/tasks/bulk-update`                              |
| 충돌 해소 모달                      | ✅ 드래그 흐름 한정          | `ConflictResolutionModal`                                            |
| Toast                               | ✅                           | `frontend/src/shared/ui/Toast.tsx`                                   |
| `OperatingCalendar.reverse_advance` | ❌ 신규 구현 필요            | `backend/app/services/operating_calendar.py` (예정)                  |

## 3. 핵심 설계 원칙

1. **스케줄링 진실 소스(SSOT) = 백엔드 `cascade-preview`**. 프론트 `cascadePush` 는 제거.
2. **2-Phase 상호작용** (모달 · 드래그 공통): Preview → 사용자 승인 → bulk-update.
3. **Fail-Fast**: 프론트·서버 양쪽 방어.
4. **설명가능성(Explainability)** (신규): 모든 push/pull/unresolved 는 `reason` 코드를 가지며 UI에 **사용자 언어** 로 번역되어 노출된다. 추가로 백엔드가 **자연어 요약 한 문장** 을 동시에 반환한다.
5. **되돌리기 경로 보장** (신규): 모든 bulk-update 는 `change_set_id` 를 발급하며, 마지막 1회에 한해 **Undo** 가능 (Toast 액션).
6. **Kill switch 우선** (신규): `FEATURE_FLAG_CASCADE_V2` off 시 기존 단순 업데이트 경로로 즉시 회귀. 배포 후 문제 발생 시 재배포 없이 토글.
7. **디자인 시스템**: `pwc-design` 토큰·컴포넌트를 **프로젝트 내 구현** (samildevkit 직접 import 금지). 기존 하드코딩 hex 는 본 PR 스코프에서 토큰으로 치환. `verify-pwc-design` 경고 0.
8. **모듈 SRP**: cascade 백엔드는 5개 파일로 분할 (§5.2 참조). graphify god-node in-degree ≤ 15.

## 4. 아키텍처와 데이터 흐름

### 4.1 파이프라인

```
(1) 사용자 액션 (모달 duration/start/end 편집 · 드래그 이동 · 설비 변경)
      │  + 프론트에서 FEATURE_FLAG_CASCADE_V2 확인
      ▼
(2) POST /api/schedules/cascade-preview  [X-Cascade-API-Version: 2]
        { task_id, new_start, new_end, new_equipment_code? }
      │
      ▼
(3) 응답: {
      request_id, summary,
      pushes[], pulls[], unresolved[],
      can_auto_resolve, iter_count, truncated
    }
      │
      ├── pushes = pulls = unresolved = ∅ → 즉시 bulk-update
      │
      └── 제안/경고 있음 → ConflictResolutionModal 열기
             · 상단: summary (한 문장 요약)
             · 간트 고스트 오버레이 (변경 전/후 미리보기)
             · 충돌 해소 (pushes)  — reason 한국어 매핑
             · 앞당김 제안 (pulls) — [포함] 마스터 토글 (기본 on)
             · 해소 불가 (unresolved) — "수동 조정 진입" CTA
             ▼
(4) 사용자 "적용" → POST /api/schedules/tasks/bulk-update  (트랜잭션)
      │  서버: change_set_id 발급 + 재검증 + 422 on violation
      ▼
(5) 프론트 state 교체 → Toast "적용 완료 [되돌리기]" (90초 유지)
      │
      └── [되돌리기] 클릭 → POST /api/schedules/revert/{change_set_id}
```

### 4.2 시맨틱

- **Push**: 필연적 연쇄. "Pull 토글" 과 무관하게 항상 적용.
- **Pull**: 선택적 제안. **마스터 토글** 이 꺼지면 bulk-update 페이로드에서 제외.
- **Unresolved**: 알고리즘 해소 불가. "적용" 비활성화 + 사유별 가이드 + 수동 조정 CTA.
- **Reason 코드 → UI 매핑**:
  | code | UI 문구 |
  |---|---|
  | `same_equipment_conflict` | "같은 설비의 다음 블록과 충돌로 밀림" |
  | `cross_equipment_conflict` | "후속 공정 설비의 다른 블록과 충돌로 밀림" |
  | `successor_chain` | "선행 공정 지연으로 시작 시간 밀림" |
  | `successor_slack_available` | "선행 공정 단축으로 앞당김 가능" |
  | `due_date_violation` | "납기 초과 — 재배치 불가" |
  | `no_space_forward` | "해당 설비에 공간 없음" |
  | `cycle_detected` | "연쇄가 너무 복잡 — 수동 조정 필요" |
  | `invalid_equipment` | "해당 설비는 이 공정을 수행할 수 없음" |

## 5. 백엔드

### 5.1 API 계약 — `POST /api/schedules/cascade-preview`

**요청 헤더**: `X-Cascade-API-Version: 2` (프론트 고정; 없거나 불일치 시 400).

**요청 바디**

```json
{
  "task_id": "string",
  "new_start": "ISO 8601 (naive KST)",
  "new_end": "ISO 8601 (naive KST)",
  "new_equipment_code": "string|null"
}
```

TZ 규약: **naive KST 통일**. ISO `Z` 접미는 400 리턴. 서버/DB 모두 naive `datetime`.

**응답**

```json
{
  "request_id": "uuid",
  "summary": "PO-123 절연공정이 3h 늘어, 후속 시스 2건과 동일설비 블록 1건을 밀면 납기(4/28) 안에 들어옵니다.",
  "pushes": [{ "task_id","equipment_code","batch_label","old_start","old_end","new_start","new_end","reason":"same_equipment_conflict|successor_chain|cross_equipment_conflict" }],
  "pulls":  [{ "task_id","equipment_code","batch_label","old_start","old_end","new_start","new_end","reason":"successor_slack_available" }],
  "unresolved": [{ "task_id","equipment_code","batch_label","reason":"due_date_violation|no_space_forward|cycle_detected|invalid_equipment","detail":"..." }],
  "can_auto_resolve": true,
  "iter_count": 2,
  "truncated": false
}
```

`truncated` 는 `HARD_TASK_LIMIT (500)` 초과 시 `true` + 계산 조기 종료 + `unresolved` 전체화.

### 5.2 알고리즘 — `plan_cascade_preview`

**모듈 분할** (graphify god-node 방지, SRP):

```
backend/app/services/cascade/
├── snap.py        # deepcopy + in-memory apply 유틸 (순수 함수)
├── bfs.py         # wave 기반 전파 (same-eq 양방향 + successor chain)
├── validators.py  # due-date / horizon / cycle 검증
├── pull.py        # 선택적 Pull 제안 생성
└── service.py     # 오케스트레이터, 라우터가 호출
```

**상수**: `MAX_WAVES = 4` (파이프라인 깊이 3 + 안전마진 1), `HARD_TASK_LIMIT = 500`.

**의사코드 (service.py orchestrator)**

```
1. snap = snap.deepcopy(tasks_in_horizon)
2. snap.apply(task_id, new_start, new_end, new_equipment_code)
3. frontier = [changed_task]
4. pushes, pulls, unresolved = [], [], []
5. push_count = defaultdict(int)

6. for wave in 0 .. MAX_WAVES:
     if frontier empty: break
     if len(pushes) > HARD_TASK_LIMIT:
         unresolved.append({reason="cycle_detected", detail="hard limit"})
         truncated = True; break
     next_frontier = []
     processed_in_wave = set()
     for T in frontier:
         # (a) same-equipment 양방향 겹침 (prev·next 모두, v1 B2 수정)
         for N in bfs.same_equipment_overlapping(T, snap):
             if N.id in processed_in_wave: continue
             same_eq_prev = same_eq_prev_end(N, snap)
             new_N_start = calendar.advance(max(T.new_end, same_eq_prev))
             new_N_end   = new_N_start + N.duration
             reason = same_equipment_conflict
             pushes.append({N, reason, old, new})
             snap.apply(N, new_N_start, new_N_end)
             push_count[N.id] += 1
             processed_in_wave.add(N.id)
             next_frontier.append(N)
         # (b) successor chain (같은 sales_order_line)
         for S in bfs.successor_tasks(T):
             if S.id in processed_in_wave: continue
             earliest = max(T.new_end, same_eq_prev_end(S, snap))  # v1 B4 수정
             if S.start < earliest:
                 new_S_start = calendar.advance(earliest)
                 new_S_end   = new_S_start + S.duration
                 reason = successor_chain if earliest == T.new_end else cross_equipment_conflict
                 pushes.append({S, reason, old, new})
                 snap.apply(S, new_S_start, new_S_end)
                 push_count[S.id] += 1
                 processed_in_wave.add(S.id)
                 next_frontier.append(S)
     frontier = dedup(next_frontier)

   # wave 수렴 실패
   if frontier not empty:
       for t in frontier: unresolved.append({t, reason=cycle_detected})

7. validators.validate_due_date(snap)  -> unresolved
8. validators.validate_horizon(snap)   -> unresolved
9. validators.validate_cycles(push_count, MAX_WAVES) -> unresolved

10. if snap.new_end(changed_task) < snap.old_end(changed_task):
        pulls = pull.propose_for_successors(changed_task, snap)   # §5.2.1

11. can_auto_resolve = (len(unresolved) == 0)
12. summary = build_summary(changed_task, pushes, pulls, unresolved)   # §5.2.3
13. return { request_id, summary, pushes, pulls, unresolved, can_auto_resolve, iter_count: wave_used, truncated }
```

**핵심 수정 사항 (v1 대비)**:

- `same_equipment_overlapping` 은 prev·next **양방향** — 설비 변경 시 새 설비 앞 task 와의 겹침을 놓치던 v1 B2 버그 수정.
- successor advance 기준에 `max(T.new_end, same_eq_prev_end(S))` — successor S 가 자기 설비 앞 task 와 겹치던 v1 B4 버그 수정.
- `processed_in_wave` set 으로 wave 내 중복 처리 차단 (v1 B3 수정).

### 5.2.1 Pull 제안 (pull.py)

```
def propose_for_successors(changed_task, snap) -> list[pull]:
    pulls = []
    prev_end = snap.new_end(changed_task)
    for S in successor_chain_of(changed_task):
        same_eq_prev = same_eq_prev_end(S, snap)
        earliest = max(prev_end, same_eq_prev)   # B3-B4 복합 가드 (v1 수정)
        if earliest < S.old_start:
            slack = S.old_start - earliest
            proposed_start = calendar.reverse_advance(S.old_start, slack)
            proposed_end   = proposed_start + S.duration
            pulls.append({S, new_start=proposed_start, new_end=proposed_end,
                          reason=successor_slack_available})
            prev_end = proposed_end
        else:
            prev_end = S.old_end
    return pulls
```

### 5.2.2 신규 유틸 — `OperatingCalendar.reverse_advance`

```
reverse_advance(dt, duration: timedelta) -> datetime
# dt 에서 duration 만큼의 작업시간을 '역방향' 으로 뺀 시점 반환.
# 주말·비가동 gap 을 skip (forward advance 의 mirror).
```

`backend/app/services/operating_calendar.py` 에 추가. unit test 포함 (주말/연휴 skip, 하루 끝 넘어가기).

### 5.2.3 Summary 빌더 (service.py)

백엔드가 자연어 한 문장을 **규칙기반 템플릿** 으로 생성. LLM 호출 없음.

```
"{batch_label} {process} {duration_delta} {direction}, 후속 {n_successors}건 및 설비충돌 {n_same_eq}건 {movement}{due_constraint}"
예: "PO-123 절연 3h 늘어, 후속 시스 2건 및 설비충돌 1건 밀림, 납기 4/28 내."
```

### 5.3 `POST /api/schedules/tasks/bulk-update` 보강

**요청**

```json
{ "changes": [{"task_id","new_start","new_end","new_equipment_code?"}], "expected_cascade_request_id": "uuid" }
```

`expected_cascade_request_id` 는 preview↔commit 로그 상관관계용.

**응답 (성공)**

```json
{ "change_set_id": "uuid", "updated_tasks": [...] }
```

**응답 (422 — 재검증 실패)**

```json
{
  "error_code": "VALIDATION_OVERLAP_SAME_EQUIPMENT|VALIDATION_PREDECESSOR_VIOLATION|VALIDATION_DUE_DATE_VIOLATION|FEATURE_DISABLED|CONCURRENT_UPDATE",
  "offending_task_id": "string",
  "detail": "string",
  "can_retry": true|false
}
```

**트랜잭션 내 검증 순서**:

1. 피처 플래그 체크 → off 시 `FEATURE_DISABLED`
2. 각 change
   - 같은 설비 내 overlap
   - 수주 체인 선행·후행 제약 (`predecessor.end ≤ S.start`)
3. 동시 업데이트 감지 (`task.updated_at > preview_request_time`) → `CONCURRENT_UPDATE`, `can_retry=true`
4. `change_set_id` 발급 + INSERT into `schedule_change_sets` (revert 원본 스냅샷 저장)

### 5.4 신규 엔드포인트 — `POST /api/schedules/revert/{change_set_id}`

- 최근 change_set 1개만 활성 (older 는 auto-expire).
- Freshness 검증: change_set 이후 다른 change_set 생성되었으면 `409 Conflict`.
- 성공 시 Toast "되돌렸습니다."

### 5.5 피처 플래그 — `FEATURE_FLAG_CASCADE_V2`

- 환경변수 `FEATURE_FLAG_CASCADE_V2=on|off` (dev=on, prod 배포 당시 on, 사고 시 off).
- **off 시 동작**:
  - cascade-preview: `FEATURE_DISABLED` error_code 반환
  - bulk-update: multiple changes 요청 400, 단일 task 만 허용
  - 프론트: 기존 `scheduleStore.moveTask` legacy 경로 사용
- PoC demo 는 on 전제. 문제 발생 시 재배포 없이 off.

## 6. 프론트엔드

### 6.1 `TaskFormModal.tsx` — 3필드 편집

- 소요시간 필드: `<div>` → `<input type="number" step="0.1" min="0.1">` (프로젝트 토큰 `NumberInput` 구현, samildevkit 직접 import 금지).
- **편집 규칙** (Q2-B 채택):
  - `onDurationChange(h)`: start 고정, `end = start + h*3600s`
  - `onStartChange(dt)`: end 고정, duration 파생
  - `onEndChange(dt)`: start 고정, duration 파생
- **검증**: `duration <= 0 || end <= start || isNaN` → 저장 disabled + 경고 텍스트 (`--color-negative-600`).
- **UX known limitation**: Start 편집 시 "블록 통째 이동 (duration 유지)" 옵션은 본 PR 범위 밖. §11 미해결.
- **확인 클릭** → `useScheduleChangeWithCascade` (§6.3).

### 6.2 `ConflictResolutionModal` — 섹션 + 고스트 오버레이 + 자연어 요약

**헤더**: 상단에 백엔드 summary 문장 렌더링 (§5.2.3).

**간트 고스트 오버레이 (Tier 2)**

- 모달 open 상태에서 간트 본체에 **변경 전 = 실선**, **변경 후 제안 = 반투명 고스트 (50% opacity, dashed border)**.
- 모달 table row hover → 해당 블록을 간트에서 focus ring 토큰으로 하이라이트.
- 구현: 간트 컴포넌트에 `previewOverlay?: CascadePreviewResponse` prop 추가.

**섹션 테이블**
| 섹션 | 조건 | 아이콘 (heroicons) | 컬러 토큰 | 열 |
|---|---|---|---|---|
| 충돌 해소 | `pushes[] ≠ ∅` | `XCircleIcon` | `--color-negative-500` | task · 설비 · 기존 → 신규 · Δ · 사유 |
| 앞당김 제안 | `pulls[] ≠ ∅` | `ExclamationTriangleIcon` | `--color-warning-500` | 동일 + **[Pull 포함] 마스터 토글 (기본 on)** |
| 해소 불가 | `unresolved[] ≠ ∅` | `NoSymbolIcon` | `--color-negative-700` | task · 사유 · **[수동 조정 진입] CTA** |

사유 열은 §4.2 매핑 테이블로 한국어화.

**버튼** (단일 primary)

- 라벨 고정 `"적용"`
- 서브텍스트 (동적): `"+2건 재배치 · -3건 앞당김"`
- `unresolved[] ≠ ∅` → disabled + 하단 가이드 "먼저 수동 조정이 필요합니다."

**Undo Toast** (적용 완료 후)

- `Toast.show("적용 완료", { action: { label: "되돌리기", onClick: () => revert(change_set_id) }, durationMs: 90_000 })`

**"수동 조정 진입" 딥링크**

- `onClick` → 해당 task 의 수주 상세 뷰로 라우팅. 경로는 구현 단계에서 기존 수주 뷰 URL 패턴 따라 결정.

**AI slop 회피**: 이모지 아이콘 대신 heroicons + 컬러 토큰 조합.

### 6.3 공통 훅 `useScheduleChangeWithCascade`

```
input: { task_id, new_start, new_end, new_equipment_code? }

flow:
  1. FEATURE_FLAG_CASCADE_V2 off → legacy 경로 (기존 moveTask)
  2. POST cascade-preview
  3. 응답 분기:
     - pushes+pulls+unresolved 비어있음 → bulk-update 바로, Toast 'success'
     - 아니면 → ConflictResolutionModal state open
  4. 모달 "적용" → bulk-update({
         changes: pushes + (pullToggleOn ? pulls : []) + changed_task,
         expected_cascade_request_id
     })
  5. 422 처리:
     - error_code 별 분기 + retry counter (max 3)
     - 3회 초과 → Toast "자동 해소 불가. 수동 조정으로 이동해주세요." + unresolved 전환
  6. 성공 → react-query invalidate('tasks') + Undo Toast
```

### 6.4 `scheduler/page.tsx` 드래그 리팩터링

- 기존 프론트 `cascadePush` 호출 제거 (flag off 경로만 남김).
- `moveTask` → `useScheduleChangeWithCascade` 로 통일.
- 드래그 중 preview round-trip 동안 블록에 **반투명 + 스피너 오버레이** 표시.

## 7. 엣지케이스 · 에러 처리

### 7.1 엣지케이스 카탈로그

| #   | 시나리오                             | 처리                                                       |
| --- | ------------------------------------ | ---------------------------------------------------------- |
| E1  | 소요시간 0/음수                      | 프론트 Fail-Fast + 서버 400                                |
| E2  | `end ≤ start`                        | 동일                                                       |
| E3  | 다른 process 설비로 드래그           | 드래그 가드 유지. preview 는 `invalid_equipment`           |
| E4  | iter_count ≥ MAX_WAVES               | `cycle_detected` + 적용 disabled                           |
| E5  | push 이 주말/gap                     | `OperatingCalendar.advance` 로 skip                        |
| E6  | Pull 이 선행 제약 위반               | §5.2.1 `max(prev_end, same_eq_prev)` 가드로 drop           |
| E7  | 동시 두 탭 편집                      | 422 `CONCURRENT_UPDATE` + 재preview 유도                   |
| E8  | bulk-update 트랜잭션 실패            | 롤백 + Toast + 모달 유지 + `tasks` 쿼리 재조회 강제        |
| E9  | cascade-preview 빈 응답              | 모달 skip, 즉시 bulk-update                                |
| E10 | 설비 변경 시 새 설비 앞 task 와 겹침 | §5.2 양방향 탐색으로 push (B2 수정)                        |
| E11 | same-eq & successor 중첩             | wave 내 `processed_in_wave` dedup (B3 수정)                |
| E12 | 422 재시도 3회 초과                  | "수동 조정 안내" Toast + unresolved 전환                   |
| E13 | 피처 플래그 off                      | legacy 단일 업데이트 경로                                  |
| E14 | `truncated=true`                     | 적용 disabled + "변경 범위가 너무 큽니다. 수동 조정 권장"  |
| E15 | Undo 중 다른 change_set 생성됨       | 409 Conflict + Toast "되돌릴 수 없습니다 (이후 변경 존재)" |

### 7.2 에러 처리 원칙

- 5xx / network: Toast(`error`) + 모달 유지.
- 4xx 검증: 모달 내 인라인 에러 + error_code 별 분기.
- bulk-update 성공 후: `tasks` react-query invalidate → stale state 방지.
- 타임아웃: cascade-preview 서버 p95 < 1s 목표.

### 7.3 로깅 · 메트릭

**구조화 로그 (JSON)**

```json
{
  "request_id", "route": "/cascade-preview",
  "task_id", "new_start", "new_end", "new_equipment_code",
  "wave_used", "pushes_n", "pulls_n", "unresolved_n",
  "reason_histogram": {"same_equipment_conflict": 3, "successor_chain": 2},
  "duration_ms", "truncated", "status": "ok|error"
}
```

bulk-update / revert 도 동일 `request_id` correlation.

**Prometheus 메트릭**

- `cascade_preview_duration_seconds` (histogram)
- `cascade_unresolved_total{reason}` (counter)
- `cascade_revert_total` (counter)
- `cascade_feature_flag_state{state=on|off}` (gauge)

## 8. 테스트 전략

### 8.1 백엔드 unit (`backend/tests/services/test_cascade_planning.py`)

- T1 duration 증가 → 같은 설비 다음 블록 push
- T2 successor chain push (연선 → 절연 → 시스)
- T3 successor 자기 설비 뒤쪽 충돌 → cross-equipment push
- T4 due_date 위반 → unresolved
- T5 MAX_WAVES 초과 → `cycle_detected`
- T6 Pull — slack 있을 때 포함 + reverse_advance 동작
- T7 Pull 이 predecessor 선행 제약 위반 시 drop
- T8 `new_equipment_code` 변경 + cascade
- T9 주말 gap skip
- **T10 설비 변경 시 새 설비 앞 task 와 겹침 → push (B2 수정)**
- **T11 same-eq & successor 중첩 dedup (B3 수정)**
- **T12 Pull 이 같은 설비 앞 task 와 충돌 시 drop / slack 재계산 (B4 수정)**

### 8.2 백엔드 integration (`backend/tests/api/test_cascade_preview.py`)

- 응답 스키마 계약 (`truncated, request_id, summary`)
- **T13 bulk-update 422 재시도 3회 후 수동 조정 전환**
- **T14 p95 성능** (500 task fixture, `pytest-benchmark`, p95 < 1s assertion)
- revert 엔드포인트 성공 / 409
- 피처 플래그 off → cascade-preview FEATURE_DISABLED, bulk-update 단일 업데이트만

### 8.3 프론트 unit

- `TaskFormModal` 3필드 상호작용 규칙
- `useScheduleChangeWithCascade`: 빈 응답 skip / 응답 있음 open / 422 재시도 상한 / Undo
- `ConflictResolutionModal`: reason 한국어 매핑 / Pull 토글 off → pulls 제외 / unresolved disable
- 간트 고스트 오버레이: preview prop → 변경 전/후 동시 렌더

### 8.4 E2E (Playwright)

- `freezeTime()` + seeded schedule fixture 로 deterministic.
- `frontend/e2e/block-duration-edit.spec.ts`
  1. 소요시간 81 → 90 → 충돌 모달 → 적용 → 스케줄 반영 + Undo Toast
  2. Undo Toast 클릭 → 원상복구
  3. 소요시간 축소 → Pull 제안 → 마스터 토글 off → 적용 → 원본만 축소
  4. due_date 위반 유도 → unresolved + 적용 disabled + 수동조정 CTA
- `frontend/e2e/block-drag-cascade.spec.ts`
  1. 드래그 → cross-equipment 충돌 → 모달 → 적용
- `frontend/e2e/feature-flag-off.spec.ts`
  1. 플래그 off → 기존 경로, cascade-preview 호출 없음

### 8.5 PR 머지 gate

- 백엔드 pytest 100% (T1-T14)
- 프론트 vitest 100%
- Playwright 3개 스펙
- `verify-pwc-design` 경고 0 (기존 하드코딩 hex 치환 포함)
- **graphify god-node in-degree ≤ 15** (cascade/ 모듈 분할 확인)
- **benchmark baseline 기록 + regression 없음**

## 9. 롤아웃 순서 (writing-plans 시드)

1. **피처 플래그 도입** — env 변수 + middleware + 프론트 config (즉시 kill switch 확보)
2. **`OperatingCalendar.reverse_advance`** + unit test
3. **cascade/ 모듈 스캐폴드** — snap.py, bfs.py, validators.py, pull.py, service.py
4. **service.py orchestrator + unit T1-T5**
5. **bfs 양방향 + dedup (T10-T11)**
6. **pull 생성 + T6-T7, T12**
7. **validators + T4, T5**
8. **cascade-preview 라우터 v2 + integration + summary 빌더**
9. **bulk-update 재검증 + error_code + change_set 저장**
10. **revert 엔드포인트**
11. **프론트 `useScheduleChangeWithCascade` 훅**
12. **`ConflictResolutionModal` 섹션화 + reason 한국어 매핑**
13. **간트 고스트 오버레이 (Tier 2)**
14. **`TaskFormModal` 3필드 편집 + 하드코딩 hex 토큰 치환**
15. **`scheduler/page.tsx` 드래그 리팩터링 + 프론트 `cascadePush` 제거**
16. **E2E 3개 스펙 + freezeTime fixture**
17. **로깅·메트릭 + benchmark baseline 기록**
18. **`verify-pwc-design` + graphify rebuild + god-node 검증**
19. **`document-release` — README / ARCHITECTURE.md / CLAUDE.md 갱신**
20. **atomic commit 단위로 각 단계 커밋**

**분리 PR**: 납기 초과 카운터 필터 반응화 (별도 spec 후속).

## 10. 결정 이력

- **Q1 확인 팝업**: `ConflictResolutionModal` 재사용 (A)
- **Q2 3필드 규칙**: B — 각 핸들러 독립 규칙
- **Q3 연쇄 깊이 / 실패**: A — Full cascade + 실패시 차단
- **Q4 방향성**: A 혼합 — Push 자동 제안, Pull 제안 + **마스터 토글 승인**
- **Q5 구현 위치**: 접근 1 — 백엔드 cascade-preview 확장
- **v2 리비전 반영**: CEO/Eng/UX/DevEx 병렬 리뷰 Tier 1 + Tier 2 전체 반영
  - B1 reason UI 매핑, B2 양방향 BFS, B3 wave dedup, B4 Pull prev 가드, B5 Pull 마스터 토글, B6 Undo, B7 수동조정 CTA, B8 feature flag, B9 error_code enum
  - Tier 2: 간트 고스트 오버레이, 자연어 summary
- **MAX_WAVES=4**: 파이프라인 실제 깊이 3 + 안전마진 1 (v1의 5 는 과도)
- **납기초과 필터**: 분리 PR
- **UX Blocker 1 (블록 통째 이동 토글)**: Q2-B 원안 유지, §11 미해결

## 11. 미해결 / 추후 과제

- 블록 통째 이동 토글 — Start 편집 시 duration 유지 옵션.
- 납기 초과 카운터 필터 반응화 (별도 PR).
- 다중 사용자 optimistic concurrency.
- Pull per-row opt-in (현재 마스터 토글만).
- LLM 기반 summary 품질 향상.
- cascade-preview 응답 캐시.
