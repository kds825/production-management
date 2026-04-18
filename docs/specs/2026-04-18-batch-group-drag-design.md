# Batch Group 드래그 재배치 설계서 (v2a)

**작성일:** 2026-04-18
**브랜치:** dev_jaewoo
**상태:** v2a — 2a(cascade API 위 layer) 채택, 단일 PR 범위
**선행 작업:** `docs/specs/2026-04-17-batch-group-unassign-design.md` (v1 closed-loop 완료: 29 commits)

---

## 1. 배경

### 1.1 v1(우클릭)까지의 상태

생산계획자는 우클릭 컨텍스트 메뉴로 batch_group을 미배정 풀로 이동하고, `BatchGroupCard`의 "계획으로 복원" 버튼으로 원래 자리로 되돌릴 수 있다. 드래그 경로는 스펙상 v2로 연기되어 있었다.

### 1.2 v2a 범위 — 본 문서

**드래그 경로 + cascade-v2 API 위에 얹힌 재배치**를 단일 PR로 구현한다. Push/pull/cascade 로직 자체는 브랜치에 이미 landed된 `cascade-preview v2` + `bulk-update-v2` + `schedule_change_sets(Undo)`를 **재사용**한다. Stage 2 scheduler 전체 재실행 / LLM 재계획 큐는 본 범위에서 제외.

### 1.3 요구사항

- **Flow X (unassign 드래그)**: 간트 블록을 좌측 "미배정 작업" 패널로 드래그하면 해당 batch_group 전체(연선·절연·시스)가 미배정으로 이동. 사유 선택 Modal은 v1 `UnassignConfirmModal` 재사용.
- **Flow Y (restore/reassign 드래그)**: `BatchGroupCard`를 간트의 설비 레인에 드롭하면, 드롭 지점이 앵커(첫 공정 = 연선) 위치가 되고, 후속 공정(절연·시스)은 선행 제약 + 납기 + 설비 캘린더 + 빈 슬롯을 고려해 자동 배치. 다른 planned task와 충돌 시 cascade-preview로 밀기/거부.
- **Undo**: 드래그로 적용된 모든 변경은 90s Undo 토스트로 되돌릴 수 있다(기존 `useScheduleChangeWithCascade` 재사용).
- **무결성**: batch_group 단위 불변. 단일 task 드래그는 비활성화.

### 1.4 "closed-loop + 재배치" 를 가능하게 하는 이유

v1의 pure soft-delete 모델(`status='unassigned'` 만)이 원본 `equipment_code`/`start_datetime`을 유지하므로, 앵커만 새 위치로 지시받으면 downstream은 기존 predecessor 체인으로 계산 가능. cascade-preview v2가 bolt-on 된 시점에 우리는 "새 위치 + 기존 스냅샷"을 조합해 충돌을 미리 계산할 수 있다.

---

## 2. 핵심 설계 결정

| #          | 결정                 | 선택                                                       | 근거                                                                                                              |
| ---------- | -------------------- | ---------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| Q1         | 드래그 단위          | **batch_group 전체**                                       | v1 스펙 §2 Q1 Cascade=batch_group. 단일 task 드래그하면 공정 chain 깨짐 (연선→절연→시스 선행 제약 위반 위험)      |
| Q2         | 드롭 타겟 (unassign) | **OrderInbox 패널(고정 드롭존)**                           | stick 배너, overlay 금지. 시각적 혼란 최소                                                                        |
| Q3         | 드롭 타겟 (restore)  | **간트 설비 레인(기존 `equipment-row`)**                   | 기존 drag-to-move 인프라 그대로 재사용                                                                            |
| Q4         | 앵커 task 선정       | **batch_group의 첫 공정 task (process_step min)**          | 연선이 시작이므로 사용자가 드롭한 지점 = 연선의 새 시작. 후속 공정은 predecessor 기반 auto-place                  |
| Q5         | 자동 배치 알고리즘   | **cascade-preview v2 validator 체인 재사용**               | `due_date` / `horizon` / `cycle` / `invalid_equipment` 검증 이미 존재. BFS orchestrator도 존재                    |
| Q6         | 충돌 해소 정책       | **pushes/pulls auto-apply + unresolved 시 드롭 거부**      | 스펙 §9 "밀기/취소" 모달은 v2a에서 단일 확인 Modal로 압축. unresolved는 warning toast + 롤백                      |
| Q7         | Undo                 | **기존 90s 토스트 재사용**                                 | `useScheduleChangeWithCascade`의 `UNDO_TOAST_DURATION_MS = 90_000` 상수 활용. 별도 토스트 X                       |
| Q8         | Stage 2 전체 재실행  | **제외(v2b 이후)**                                         | 사용자가 대략적 위치를 지시하는 UX 유지. 완전 자동 최적화는 별도 milestone                                        |
| Q9         | WIP 매칭             | **드래그 시작 자체 차단(useDraggable disabled)**           | v1 ContextMenu disabled와 동일 정책. 드래그 가능해 보이면 UX 혼란                                                 |
| Q10        | `isEditMode` gate    | **기존 gate 재사용 (edit-warning modal)**                  | 드래그의 모든 경로가 수정 모드 필수                                                                               |
| Q11 (신규) | restore-at API 구조  | **plan 단계에서 결정** (backend helper vs frontend 2-step) | cascade 모듈 코드 판독 후 단순한 쪽 선택. 디자인상 동등, 구현 복잡도에서 차이                                     |
| Q12        | 스킬/문서 사용       | **Task마다 `pwc-design`/`verify-pwc-design` + context7**   | 메모리 `feedback_ui_quality_no_ai_slop.md` + AUTO-9 규칙. `@dnd-kit`/라이브러리 API는 context7 MCP 최신 문서 조회 |

---

## 3. 아키텍처

### 3.1 재사용 vs 신규

**재사용(변경 없이 활용):**

- Backend
  - `POST /api/schedules/cascade-preview` (header `X-Cascade-API-Version: 2`)
  - `POST /api/schedules/bulk-update` v2 (Undo 지원 `schedule_change_sets` 테이블)
  - `POST /api/schedules/revert` (Undo 엔드포인트)
  - `POST /api/pipeline/batch-group/{bg}/unassign` (v1)
  - `POST /api/pipeline/batch-group/{bg}/restore` (v1 — 원위치 복원)
  - 서비스: `app/services/cascade/` (snap/bfs/validators/pull/service/reasons)
- Frontend
  - `@dnd-kit/core` v6.3.1, 기존 `DndContext` (scheduler/page.tsx)
  - `useScheduleChangeWithCascade` (90s Undo orchestrator)
  - `UnassignConfirmModal` (사유 4종 radio)
  - `BatchGroupCard`, `OrderInbox`, `GanttTaskBlock`
  - `useToastStore`, `useScheduleStore` (특히 `inFlightBatchGroups` race guard)

**신규:**

- Backend
  - (Q11 결정 필요) `POST /api/pipeline/batch-group/{bg}/restore-at` — body `{anchor_equipment_code, anchor_start}` → 내부에서 status flip + 앵커 위치 지정 + cascade validator 체인 호출 → 응답 = cascade-preview 호환 구조. **또는** 프론트가 2-step으로 현재 API 조합.
- Frontend
  - `useBatchGroupDrag` hook — drag orchestration 로직(cascade-preview 호출 → Modal → bulk-update-v2 → Undo)
  - `CascadePreviewModal` 컴포넌트 — pushes/pulls/unresolved 시각화 (기존 유사 컴포넌트 있으면 재사용; plan 단계에서 확인)
  - OrderInbox `useDroppable({id:"inbox-dropzone"})` 추가 + stick 배너
  - BatchGroupCard `useDraggable({id:bg-{batch_group}, data:{type:"batch_group", group}})` 추가
  - GanttTaskBlock — 같은 batch_group의 task들을 single draggable unit으로 그룹핑(선택적, UX 결정 사항)

### 3.2 드래그-드롭 타겟 매트릭스

| `active.data.type` | `over.data.type` / `over.id` | 처리                                                                                                  |
| ------------------ | ---------------------------- | ----------------------------------------------------------------------------------------------------- |
| `task`             | `equipment-row`              | (기존) 단일 task 이동. **단 batch_group에 속한 task면 경고 → 취소**                                   |
| `task`             | `inbox-dropzone`             | **(신규)** → `UnassignConfirmModal` 열기 → 사유 선택 후 `unassignBatchGroup` 호출                     |
| `order`            | `equipment-row`              | (기존) 주문 배정                                                                                      |
| `batch_group`      | `equipment-row`              | **(신규)** 드롭 지점에 앵커 task 배치 + cascade 계산 → Preview Modal → 확정 시 적용 + Undo            |
| `batch_group`      | `inbox-dropzone`             | **(신규, safe no-op)** drag cancel로 처리. 이미 미배정 상태인 카드를 다시 미배정으로 드롭 = 의미 없음 |

### 3.3 프론트 컴포넌트 트리 (Δ 만 표기)

```
scheduler/page.tsx (DndContext)
├── SchedulerView
│   └── GanttTaskBlock [batch_group drag handle 그룹핑, WIP 매칭 시 disabled]
├── OrderInbox
│   ├── CollapsiblePanel
│   │   ├── [NEW] useDroppable id="inbox-dropzone" + drag-active 시 stick 배너
│   │   ├── OrderCard (기존)
│   │   └── BatchGroupCard [useDraggable 추가, WIP 매칭 시 disabled]
│   └── ...
├── UnassignConfirmModal (기존 재사용)
└── [NEW or reuse] CascadePreviewModal
```

### 3.4 Data Flow: Flow X — 간트 → inbox (unassign)

```
간트 블록 드래그 시작 (GanttTaskBlock)
  ├─ batch_group에 속한 task인지 확인 — 속하지 않으면 기존 task drag 경로
  ├─ WIP 매칭 검증 → disabled면 드래그 불가
  └─ inFlightBatchGroups 검증 → 이미 in-flight면 드래그 불가
  ↓
OrderInbox 패널 상단에 stick 배너 출현
  "여기에 놓으면 미배정 작업으로 이동합니다"
  border-dashed + var(--color-brand-primary) + aria-live="polite"
  ↓
드롭 (over.id === "inbox-dropzone")
  ↓
handleDragEnd 분기: "task→inbox"
  ↓
UnassignConfirmModal 열기 (기존)
  ├─ 배치 묶음 정보 + 사유 4종 radio + "다시 보지 않기"
  └─ 확정 → useScheduleStore.getState().unassignBatchGroup(bg, reason)
  ↓
기존 v1 store action 흐름: 낙관적 업데이트 + 롤백 + Toast + race guard
```

### 3.5 Data Flow: Flow Y — BatchGroupCard → 설비 레인 (restore/reassign)

```
BatchGroupCard 드래그 시작
  ├─ WIP 매칭 검증 (v1과 동일)
  └─ inFlightBatchGroups 검증
  ↓
드롭 (over.type === "equipment-row")
  ↓
handleDragEnd 분기: "bg→equipment-row"
  ├─ target_equipment_code, target_anchor_start 계산 (기존 xToTime 로직 재사용)
  └─ 원위치 판정
      ├─ 원위치 → useScheduleStore.getState().restoreBatchGroup(bg) (v1 경로)
      └─ 다른 위치 → useBatchGroupDrag.requestRestoreAt(bg, eq, start)
  ↓
(다른 위치 경로)
useBatchGroupDrag
  ├─ Q11 경로 A: POST /api/pipeline/batch-group/{bg}/restore-at
  │     → 백엔드 atomic: status flip + anchor 위치 + downstream auto-place
  │     → 응답 = {request_id, pushes, pulls, unresolved, can_auto_resolve, preview_task_updates}
  └─ Q11 경로 B: 프론트 2-step orchestration
        1. POST /api/schedules/cascade-preview { task_id=anchor, new_start, new_equipment_code }
            ← 하지만 절연·시스는 unassigned이므로 snapshot에 없음 → 재작업 필요
        2. 보완 단계로 batch_group의 각 task를 순차 preview 호출
  ↓
응답 처리
  ├─ can_auto_resolve=true & unresolved=[] → CascadePreviewModal 열기
  │     "3개 작업 +2.5h 밀림" 등 요약 + 확정 버튼
  ├─ unresolved 있음 → warning toast(사유 표시) + 드롭 롤백 (카드 유지)
  └─ 확정 → bulk-update-v2 호출 + 90s Undo 토스트 (useScheduleChangeWithCascade 경유)
  ↓
성공 시
  ├─ unscheduledItems에서 해당 batch_group 제거
  ├─ tasks 재fetch (refreshTasks) 또는 낙관적 적용
  └─ success toast
```

### 3.6 Q11 — `restore-at` 구현 선택지 (plan 단계 결정)

**옵션 A: Backend helper 신설**

- 엔드포인트: `POST /api/pipeline/batch-group/{bg}/restore-at`
- Body: `{anchor_equipment_code, anchor_start}`
- 내부:
  1. `restore_batch_group` service 확장 — anchor override param 받기
  2. Anchor task: new_equipment_code, new_start 으로 세팅, new_end = new_start + duration
  3. Downstream tasks: predecessor end 이후로 working-time advance (기존 `reverse_advance`/calendar helper 재사용) + 빈 슬롯 탐색
  4. 배치 결과를 cascade validators(`due_date`/`horizon`/`cycle`/`invalid_equipment`)에 통과시키고 실패 시 unresolved 수집
  5. 성공 시 cascade-preview-on-snap 호출해 다른 planned task와의 충돌 preview 생성
  6. 최종 응답은 cascade-preview-v2와 호환(pushes/pulls/unresolved)
- 장점: atomic, 프론트 단순, Undo는 bulk-update-v2 재사용
- 단점: 백엔드 모듈 추가, 테스트 양 증가

**옵션 B: Frontend 2-step**

- 1. `restore_batch_group` 호출 (원위치 복원, status flip) — 이 시점엔 원래 자리에 들어갔다가
- 2. cascade-preview 호출하여 anchor task를 새 위치로 이동 — pushes/pulls 응답
- 3. bulk-update-v2 로 적용
- 장점: 백엔드 신설 없음
- 단점: 중간 상태(원위치 복원됨)가 DB에 노출됨 → 다른 사용자/조회에 잠깐 보임. Undo 흐름 복잡

**plan 단계에서 cascade/`bfs`/`validators` 코드 읽고 Option A가 단순하면 채택, 복잡하면 B. 디자인상 사용자 경험은 동일.**

---

## 4. UI/UX 상세

### 4.1 Stick 배너 (OrderInbox 드롭존)

드래그 active 시에만 표시. 패널 상단에 고정. 디자인 게이트 준수:

- 배경: `bg-[color:var(--color-bg-muted)]` 반투명
- border: `border-2 border-dashed border-[color:var(--color-brand-primary)]`
- 아이콘: heroicons `ArrowDownTrayIcon`
- 텍스트: "여기에 놓으면 미배정 작업으로 이동합니다"
- `aria-live="polite"` + role="status"
- 드롭존 hover 시 border 가 solid로 전환 (시각 피드백)

### 4.2 BatchGroupCard — drag affordance

- `cursor: grab` 기본, `cursor: grabbing` 드래그 중
- WIP 매칭 시 `cursor: not-allowed` + `title` 툴팁 ("WIP 매칭된 묶음은 이동 불가")
- 드래그 중 카드 본체는 `opacity-50` + 이동 방향 ghost
- 기존 "계획으로 복원" 버튼은 그대로 유지 (원위치 복원 빠른 경로)

### 4.3 CascadePreviewModal

응답의 요약 정보를 1-scroll 안에 표현:

- 상단: "재배치 미리보기"
- 중단:
  - 적용될 batch_group의 새 위치 (연선 @ EQ-X T, 절연 @ EQ-Y T', 시스 @ EQ-Z T'')
  - pushes 수 + 총 지연 시간 ("3 작업 +2.5h 뒤로 밀림")
  - pulls (만약 있으면) 간단 표기
- 하단: 취소 / 확정 버튼 (btnGhost, btnPrimary)
- aria-modal, role=dialog
- 확정 시 bulk-update-v2 호출 (useScheduleChangeWithCascade 경유)

### 4.4 Unresolved 처리

`unresolved[]` 중 첫 entry의 `reason` + `detail` 을 warning toast 로 노출:

- `invalid_equipment` → "선택한 설비가 공정 경로와 맞지 않습니다"
- `due_date` → "납기 초과로 자동 배치 불가"
- `horizon` → "스케줄 지평선 초과"
- `cycle` → "순환 종속성 감지"

토스트 duration 6000ms, `type="warning"`. 카드/블록은 드래그 시작 위치로 롤백.

### 4.5 접근성

- 드래그 시작 시 screen reader 안내: `aria-live="assertive"` region에 "드래그 중 — 이동 가능"
- 드롭존 focus-visible 시 동일 시각 피드백
- ESC 키 드래그 취소 (`@dnd-kit`의 KeyboardSensor 활성화)

---

## 5. 엣지 케이스

| #   | 상황                                                   | 처리                                                                             |
| --- | ------------------------------------------------------ | -------------------------------------------------------------------------------- |
| E1  | 드래그 중 다른 사용자가 동일 batch_group 변경          | cascade-preview 응답 후 확정 시점에 FOR UPDATE 재검증 (기존 service 내장)        |
| E2  | Anchor 드롭 위치가 working-time 밖 (주말/휴일)         | cascade validator `horizon`/`invalid_equipment` 중 하나 반환 → warning toast     |
| E3  | 공정 경로 불일치 (연선 batch_group을 절연 설비에 드롭) | cascade validator `invalid_equipment` → warning toast + 드롭 거부                |
| E4  | 납기 초과 불가피                                       | cascade validator `due_date` → warning toast                                     |
| E5  | 드롭 시점에 unscheduledItems에서 이미 제거된 카드      | inFlightBatchGroups 로 race 차단 (기존)                                          |
| E6  | 드래그 중 `isEditMode` OFF                             | 기존 edit-warning modal → 드롭 무시                                              |
| E7  | Unresolved + pushes 동시 존재                          | unresolved 우선 → warning + 드롭 거부 (pushes는 적용하지 않음)                   |
| E8  | bulk-update-v2 실패 (409 race)                         | 토스트 에러 + 원 상태 유지 (서버 상태가 진실)                                    |
| E9  | Undo 시점에 다른 사용자가 이미 후속 조치               | revert 엔드포인트가 실패 → error toast "되돌리기 실패"                           |
| E10 | 단일 task 드래그 (batch_group에 속한 경우)             | 드래그 시작 직후 감지 → hint toast "묶음 단위로만 이동 가능합니다" + 드래그 취소 |

---

## 6. 테스트 전략

### 6.1 Frontend 단위 (Vitest)

- `useBatchGroupDrag.test.ts`
  - 원위치 드롭 → `restoreBatchGroup` 호출, cascade-preview 미호출
  - 다른 위치 드롭 → cascade-preview 호출 + modal 열림 → 확정 시 bulk-update 호출
  - unresolved 응답 → warning toast + modal 미오픈
  - cascade-preview 400/500 → error toast + 상태 롤백
- `scheduleStore.unassignBatchGroup` — dropzone 경로용 회귀 테스트 추가 (기존 로직 재사용이지만 호출 경로 추가)

### 6.2 Backend 단위 (pytest)

- Option A 채택 시: `test_restore_at_service.py`
  - anchor 위치만 주면 predecessor 체인 자동 배치
  - 납기 초과 시 unresolved `due_date` 반환, DB 무변경
  - 공정 경로 불일치 설비 시 `invalid_equipment`
  - 성공 경로 → pushes/pulls 포함 응답
  - 라우트 테스트 (200/400/404/409)

### 6.3 E2E (Playwright) — 기존 spec 확장

- `batch-group-unassign.spec.ts`에 4 시나리오 추가:
  - **S5**: 간트 블록 드래그 → inbox 드롭 → Modal 사유 선택 → 미배정 + Toast
  - **S6**: BatchGroupCard 드래그 → 원위치 드롭 → 즉시 복원(cascade preview 생략)
  - **S7**: BatchGroupCard 드래그 → 다른 설비/시각 → CascadePreviewModal → 확정 → Gantt 반영 + Undo 토스트 90s
  - **S8**: BatchGroupCard 드래그 → 공정 불일치 설비 → warning toast + 카드 유지

### 6.4 디자인 게이트 (Phase 진입 전)

`verify-pwc-design` 스킬로:

- CascadePreviewModal 신규 파일 스캔
- stick 배너 구현 파일 스캔
- 0 samildevkit / 0 raw hex / 0 italic / 0 gradient / 0 emoji

### 6.5 수동 QA

- [ ] 간트 블록 드래그 → inbox 패널 호버 시 stick 배너 출현
- [ ] 드롭 시 Modal 열림, 사유 선택 후 batch_group 전체 미배정
- [ ] BatchGroupCard 드래그 → 원위치 드롭 시 즉시 복원 (preview 없음)
- [ ] 다른 설비 드롭 시 cascade preview modal 표시
- [ ] unresolved (공정 불일치) 시 warning toast + 롤백
- [ ] Undo 90s 내 클릭 시 원상 복귀
- [ ] WIP 매칭 batch_group → 드래그 시작 차단
- [ ] ESC 드래그 취소
- [ ] isEditMode OFF 시 edit-warning

---

## 7. 구현 범위 요약

| 카테고리                           | 건수 (예상)                                                                                                                       |
| ---------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| Backend 신규 엔드포인트 (Option A) | 1 (`/restore-at`) — 또는 Option B 채택 시 0                                                                                       |
| Backend 서비스 변경                | `batch_group_lifecycle.restore_batch_group`에 `anchor_override` 옵션 파라미터 (Option A)                                          |
| Backend 단위 테스트 신규           | 4~6                                                                                                                               |
| Frontend 컴포넌트 신규             | `useBatchGroupDrag` hook, `CascadePreviewModal` (또는 재사용)                                                                     |
| Frontend 컴포넌트 수정             | `OrderInbox`(droppable+배너), `BatchGroupCard`(draggable), `GanttTaskBlock`(group drag), `scheduler/page.tsx`(handleDragEnd 분기) |
| Frontend 단위 테스트 신규          | 4~5                                                                                                                               |
| E2E 신규 시나리오                  | 4 (S5~S8)                                                                                                                         |

**예상 공수:** 2~3일 (구현 + 테스트 + QA)

---

## 8. v2b 이후 (본 스펙 외)

- 완전 자동 최적 배치 (Stage 2 partial-rerun) — 사용자가 "아무 설비든"만 지시
- 재계획 큐 + LLM 제안 (CEO 전략 기회) — 미배정 풀에 쌓인 batch_group들을 AI가 재배치 제안
- Multi-select 드래그 (여러 batch_group 동시)
- Drag preview에 실시간 cascade 계산 표시 (드래그 중 시각화)

---

## 9. 메모리/스킬 지침

구현 Phase 각 Task에 명시적으로 포함:

- **UI Task**: Spec 내용을 코드로 옮기기 전 `Skill(skill="pwc-design")` 호출로 토큰/패턴 참조, 구현 후 `Skill(skill="verify-pwc-design")` 호출로 위반 검증.
- **라이브러리 API 사용**: `@dnd-kit/core`, `zustand`, `@tanstack/react-query`(있다면), `@heroicons/react`, `lucide-react` 등 호출 전 `ToolSearch`로 context7 스키마 로드, `mcp__claude_ai_Context7__resolve-library-id` → `query-docs`로 최신 문서 조회.
- **Backend API**: FastAPI/SQLAlchemy Alembic API 사용 시 동일 절차로 context7 문서 조회.
- **금지 사항 (v1과 동일)**:
  - `samildevkit` import 절대 금지
  - Raw hex 하드코딩 금지 (`var(--color-*)` 단독)
  - 이모지 금지 (heroicons/lucide만)
  - `--no-verify` 사용 금지 (pre-commit hook 준수)
  - `git add -A` / `git add .` 금지 (concurrent work 보존)

---

## 10. 참고 파일

### 재사용 대상 (읽기만)

- `backend/app/services/cascade/{snap,bfs,validators,pull,service,reasons}.py`
- `backend/app/presentation/routes/schedules.py` (cascade-preview v2, bulk-update v2, revert)
- `backend/app/presentation/schemas/cascade.py`
- `backend/app/services/batch_group_lifecycle.py` (v1)
- `backend/app/presentation/routes/plan_pipeline.py` (v1 unassign/restore/snapshots)
- `frontend/src/features/scheduler/hooks/useScheduleChangeWithCascade.ts`
- `frontend/src/features/scheduler/store/scheduleStore.ts`
- `frontend/src/features/scheduler/components/{OrderInbox,BatchGroupCard,GanttTaskBlock,UnassignConfirmModal,ContextMenu}.tsx`
- `frontend/src/app/(main)/scheduler/page.tsx`

### 신규 또는 수정

- `backend/app/services/batch_group_lifecycle.py` (Option A 채택 시 `restore_batch_group` 확장 또는 `restore_batch_group_at` 신설)
- `backend/app/presentation/routes/plan_pipeline.py` (Option A 채택 시 `restore-at` 라우트)
- `backend/tests/test_batch_group_lifecycle.py` (신규 테스트 추가)
- `backend/tests/test_routes.py` (신규 테스트 클래스 추가)
- `frontend/src/features/scheduler/hooks/useBatchGroupDrag.ts` (신규)
- `frontend/src/features/scheduler/components/CascadePreviewModal.tsx` (신규 또는 재사용)
- `frontend/src/features/scheduler/components/OrderInbox.tsx` (useDroppable + stick 배너)
- `frontend/src/features/scheduler/components/BatchGroupCard.tsx` (useDraggable)
- `frontend/src/features/scheduler/components/GanttTaskBlock.tsx` (batch_group 단위 drag 그룹핑)
- `frontend/src/app/(main)/scheduler/page.tsx` (handleDragEnd 분기 추가)
- `frontend/e2e/batch-group-unassign.spec.ts` (S5~S8 추가)
- `frontend/src/features/scheduler/store/scheduleStore.ts` (필요 시 소규모 어댑터만)

### 메모리 (읽기 전용, 구현 중 참조)

- `~/.claude/projects/-Users-jaewookim-Desktop-Project-KBI-PoC/memory/feedback_ui_quality_no_ai_slop.md`
- `~/.claude/projects/-Users-jaewookim-Desktop-Project-KBI-PoC/memory/feedback_architecture_simplicity.md`
- `~/.claude/projects/-Users-jaewookim-Desktop-Project-KBI-PoC/memory/feedback_commit_verification.md`
- `~/.claude/projects/-Users-jaewookim-Desktop-Project-KBI-PoC/memory/feedback_tfrg_color.md`
