# Batch Group 미배정 복원 기능 설계서 (v2 — 3 리뷰 반영)

**작성일:** 2026-04-17 (v3: 2026-04-18)
**브랜치:** dev_jaewoo
**상태:** v3 — v2 2차 리뷰(CEO 8.0 / Eng 8.0 / Design 7.8) 블로커 4건 + CEO 조건 A/B 반영
**변경 이력:**

- v1 (초안): 11 Phase, 드래그+우클릭 이중 경로, 스냅샷 컬럼 + reassign API 포함
- v2: CEO HOLD SCOPE + Eng Critical 5개 + Design MUST FIX 4개 반영. 드래그/reassign/push 전량 v2-deferred
- **v3 (본 문서)**: v2 2차 리뷰의 SHIP 블로커 4건 반영
  - B1: Toast 공용 컴포넌트 — 409 silent failure 해결
  - B2: `scheduler/page.tsx`를 unscheduledItems 마이그레이션 Files에 추가
  - B3: `ScheduleTask.wip_matched_id` 필드 실제 연결 (UI disabled 판정 사전 차단)
  - B4: restore 성공 후 `refreshTasks()` 호출 → 간트 즉시 반영
  - CEO 조건 B: 미배정 사유 tagging (자재지연/설비고장/납기재협상) — audit_log + UI
  - CEO 조건 A: Phase 5~7 → 단일 "Phase 5: UI 통합"

---

## 1. 배경 및 목적

### 1.1 기존 기능 오해 해소

사용자가 `ContextMenu`의 "계획으로 되돌리기" 항목을 "미배정 작업으로 이동"으로 오해. 실제 동작은 `batch.status`를 `in_progress`/`completed` → `planned`로 되돌리는 **상태 롤백**(자리 유지). 미배정 이동 기능은 미구현.

### 1.2 요구사항 (v1 축소 범위)

생산계획자가 이미 스케줄된 `batch_group`을 미배정 풀로 **옮겼다가 원래 자리로 복원**할 수 있어야 함.

- **v1 Primary 트리거:** 우클릭 컨텍스트 메뉴 "미배정으로 이동"
- **v1 미배정 시 사유 기록 (신규 v3):** 자재지연 / 설비고장 / 납기재협상 / 기타 중 택1
- **v1 복원 방식:** BatchGroupCard의 "계획으로 복원" 버튼 클릭 → 원래 equipment/시각으로 status만 flip
- **Cascade:** 연선 이동 시 후속 공정(절연/시스)도 함께 (batch_group 단위)
- **v1 Feedback UI (신규 v3):** 공용 Toast 컴포넌트 — 409 충돌/성공/실패 안내
- **v2 Deferred:** 드래그 경로, Undo 토스트, 다른 위치로의 재배치(reassign), ConflictResolutionModal push 전략

### 1.3 v1이 "closed-loop"인 이유

하이브리드 soft-delete 모델 덕분에 미배정된 batch/task의 `equipment_id` / `start_datetime` 원본이 **그대로 DB에 남음**. "계획으로 복원" = status를 `unassigned` → `planned`로 flip. 재배치 로직 없이도 완전한 왕복이 성립.

---

## 2. 핵심 설계 결정 (Q&A 이력 + 리뷰 반영)

| #          | 결정 사항               | v1 선택                                                               | 비고                                                                                                                                           |
| ---------- | ----------------------- | --------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| Q1         | Cascade 범위            | **batch_group 전체 단위**                                             | 연선 색상/틀 체인 보존                                                                                                                         |
| Q2         | 데이터 모델             | **Pure soft-delete** (`status='unassigned'` 만)                       | Eng #1, CEO Red#3 반영 — 스냅샷 컬럼 제거. equipment_id/start_datetime은 행에 그대로 존재하므로 별도 스냅샷 불필요                             |
| Q3         | 상태 제약               | **Strict — `planned` 상태만 허용**                                    | 진행중/완료 포함 시 메뉴 disabled                                                                                                              |
| Q4         | 트리거 경로             | **v1: 우클릭만 / v2: + 드래그**                                       | CEO Red#2 반영 — PoC 오조작 리스크 감소                                                                                                        |
| Q5         | 미배정 카드 표시        | **단일 BatchGroupCard** (기존 `OrderCard` 병존)                       | 단 v1에서는 드래그 불가, "계획으로 복원" 버튼 사용                                                                                             |
| Q6         | 복원 방식               | **Simple status flip (원위치)** / v2에서 다른 위치로 이동             | Eng #3/#4 반영 — push 전략 미구현이므로 v1은 원위치 복원만                                                                                     |
| Q7 (신규)  | WIP 매칭 batch          | **Disabled 처리**                                                     | CEO Red#5 반영 — WIP 매칭 batch는 메뉴 항목 비활성화                                                                                           |
| Q8 (신규)  | 아이콘 정책             | **heroicons/lucide 선형 아이콘만**                                    | Design MUST#1 — 이모지 전량 금지                                                                                                               |
| Q9 (신규)  | 색상 시스템             | **CSS 변수 단독 (fallback hex 금지)**                                 | Design MUST#2 — verify-pwc-design 위반 사전 차단                                                                                               |
| Q10 (신규) | 디자인 시스템 적용 방식 | **samildevkit 내용을 프로젝트 코드에 직접 구현** (패키지 import 금지) | 2026-04-18 사용자 명시: "samildevkit은 우리 코드에 명시하면 안돼". `<Button>`/`<Chip>`은 `<button>`/`<span>` + Tailwind + CSS 변수로 직접 작성 |

### 2.1 리뷰 반영 요약표

| 항목                                       | 리뷰어                             | v1 반영 방식                                                                                    |
| ------------------------------------------ | ---------------------------------- | ----------------------------------------------------------------------------------------------- |
| 스코프 축소                                | CEO                                | 드래그/reassign/push/ConflictResolutionModal 통합 → v2 deferred                                 |
| 스냅샷 컬럼 제거                           | CEO+Eng                            | Alembic 마이그레이션 축소 (enum 확장만, 컬럼 추가 없음)                                         |
| Service layer commit 경계 수정             | Eng Critical #1                    | 서비스는 `db.flush()`만, 라우트가 단일 commit (audit 포함)                                      |
| duration 단순화                            | Eng Critical #2                    | v1 복원은 status flip만이라 duration 재계산 불필요                                              |
| cascade_strategy push 제거                 | Eng Critical #3                    | v1은 원위치 복원만 지원                                                                         |
| N+1 쿼리 + equipmentMatchesGroup validator | Eng Critical #4                    | v1에는 reassign 자체가 없어 불필요. v2 스코프로 이동                                            |
| TaskFormModal.tsx 포함                     | Eng Critical #5                    | 마이그레이션 영향 파일 목록에 추가                                                              |
| 이모지 전량 heroicons                      | Design MUST #1                     | Spec UI 섹션 + Plan 전체 이모지 제거                                                            |
| raw hex fallback 제거                      | Design MUST #2                     | `var(--color-*)` 단독 사용. fallback은 `tailwind.config` default로 위임                         |
| samildevkit 패키지 import **금지**         | Design MUST #3 + 사용자 2026-04-18 | 패키지 import 제거. 디자인 패턴을 `<button>`/`<span>`/`<div>` + Tailwind + CSS 변수로 직접 구현 |
| DropZone overlay                           | Design MUST #4                     | v2 스코프 — v1 UI에 DropZone 자체가 없음                                                        |
| race condition guard                       | Eng Design#3                       | `inFlightBatchGroups: Set<string>` 스토어 필드                                                  |
| useScheduleData snapshots fetch            | Eng Missing#2                      | v1에 포함 (페이지 새로고침 시 복원)                                                             |
| partial index                              | Eng Missing#1                      | Alembic에 `CREATE INDEX ... WHERE status='unassigned'` 포함                                     |
| Microcopy 일관성                           | Design #10                         | "되돌리기" → "이동" 동사 통일, "묶음"은 dev-only                                                |

---

## 3. 아키텍처

### 3.1 데이터 모델 (단순화됨)

```
┌─────────────────┐   ┌──────────────────────┐   ┌────────────────┐
│   sales_order   │   │   production_batch   │   │  schedule_task │
├─────────────────┤   ├──────────────────────┤   ├────────────────┤
│ id (PK)         │◄──│ batch_id (PK)        │◄──│ task_id (PK)   │
└─────────────────┘   │ batch_group          │   │ batch_id (FK)  │
                      │ status*              │   │ equipment_id   │
                      │ wip_matched_id       │   │ start_datetime │
                      └──────────────────────┘   │ end_datetime   │
                                                  │ status*        │
                                                  └────────────────┘
   * status 애플리케이션 레벨 literal:
     'planned' | 'in_progress' | 'completed' | 'unassigned'
```

**변경 사항:**

- VARCHAR `status`에 `'unassigned'` 값 추가 (애플리케이션 레벨 validation)
- Python `Literal["planned","in_progress","completed","unassigned"]` 타입 정의 (`batch_group_lifecycle.py`)
- **신규 컬럼 없음** (previous_equipment_id, previous_start 제거)
- **partial index** 추가: `CREATE INDEX ix_batch_status_unassigned ON production_batch(batch_group) WHERE status='unassigned'`
- Alembic 마이그레이션 1개 (index만)

### 3.2 백엔드 API (v1: 2개)

```
POST /api/pipeline/batch-group/{batch_group}/unassign
  Request: (body 없음)
  Response 200: { "batch_group", "affected_batches": [], "affected_tasks": [], "idempotent": bool }
  Response 400: planned 외 상태 포함
  Response 404: batch_group 없음
  Response 409: 동시성 충돌

POST /api/pipeline/batch-group/{batch_group}/restore
  Request: (body 없음)
  Response 200: { "batch_group", "restored_tasks": [task_id, ...], "conflicts": [] }
  Response 409: 원래 자리가 이미 다른 planned task로 점유됨 (conflicts 배열 반환)

GET /api/pipeline/batch-group-snapshots
  Response 200: { "groups": [BatchGroupSnapshot] }
```

**v2 Deferred APIs:**

- `POST /batch-group/{bg}/reassign` (다른 위치로 이동 + push 전략)

### 3.3 프론트엔드 컴포넌트 트리 (v1)

```
scheduler/page.tsx (DndContext 변경 없음)
├── SchedulerView               (기존 그대로)
├── ContextMenu                 (수정: "미배정으로 이동" 항목 + WIP/상태 disabled)
├── UnassignConfirmModal        (신규: heroicons + shared/ui/styles 프리셋, 패키지 import 없음)
└── CollapsiblePanel            (기존 그대로 — v1에서 DropZone 없음)
    └── OrderInbox              (수정: InboxItem union)
        ├── OrderCard           (기존)
        └── BatchGroupCard      (신규: "계획으로 복원" 버튼, 드래그 없음)
```

### 3.4 프론트 타입 확장

```typescript
// types/index.ts
export type TaskStatus = "planned" | "in_progress" | "completed" | "unassigned";

export interface ProcessChainItem {
  process: string;
  equipment_group: string;
}

export interface BatchGroupSnapshot {
  batch_group: string;
  customer: string;
  spec: string;
  color: string;
  total_length_m: number;
  delivery_date: string;
  processes: ProcessChainItem[];
  order_count: number;
}

export type InboxItem =
  | { kind: "order"; order: Order }
  | { kind: "batch_group"; group: BatchGroupSnapshot };
```

### 3.5 스토어 액션 (v1)

```typescript
interface ScheduleState {
  unscheduledItems: InboxItem[]; // unscheduledOrders → 변경
  inFlightBatchGroups: Set<string>; // race condition guard (Eng Design#3)
}

interface ScheduleActions {
  unassignBatchGroup: (batchGroup: string) => Promise<void>;
  restoreBatchGroup: (batchGroup: string) => Promise<void>;
  loadBatchGroupSnapshots: () => Promise<void>; // 페이지 로드 시 호출
}
```

**v2 Deferred Actions:**

- `reassignBatchGroup(batchGroup, equipmentId, start, strategy)`

---

## 4. 데이터 플로우

### 4.1 Flow A — 미배정 이동 (우클릭 경로)

```
사용자 액션: 간트 블록 우클릭 → "미배정으로 이동"
  ↓
프론트 사전검증
  ├─ selectedTask.batch_group의 모든 task.status === 'planned'
  ├─ selectedTask.batch.wip_matched_id == null (WIP 매칭 batch 제외)
  └─ 위반 시 항목 disabled + tooltip
  ↓
UnassignConfirmModal 표시 — 사용자 확인
  ↓
스토어 inFlightBatchGroups에 batch_group 추가 (race guard)
  ↓
낙관적 업데이트:
  ├─ tasks에서 해당 batch_group 제거
  └─ unscheduledItems에 BatchGroupSnapshot 추가
  ↓
POST /api/pipeline/batch-group/{bg}/unassign
  ├─ 서비스: FOR UPDATE + 상태 검증 + status='unassigned' (flush)
  ├─ 라우트: audit_log INSERT + commit (단일 트랜잭션)
  └─ 응답
  ↓
응답 처리
  ├─ 성공: inFlight 제거 + fireReanalysis
  └─ 실패: 낙관적 업데이트 복원 + inFlight 제거 + 에러 토스트
```

### 4.2 Flow B — 원위치 복원 (BatchGroupCard "계획으로 복원" 버튼)

```
사용자 액션: BatchGroupCard의 "계획으로 복원" 버튼 클릭
  ↓
RestoreConfirmModal 표시 — 원위치/납기/공정체인 확인
  ↓
POST /api/pipeline/batch-group/{bg}/restore
  ├─ 서비스: FOR UPDATE + status unassigned 검증
  ├─ 원래 equipment_id/start_datetime이 이미 다른 planned task로 점유됐는지 확인
  ├─ 점유됨 → 409 + conflicts 배열 반환
  ├─ 점유 없음 → status='planned' flip (flush)
  └─ 라우트: audit_log INSERT + commit
  ↓
응답
  ├─ 200: 프론트가 복원된 task들을 tasks 배열에 재삽입 + unscheduledItems에서 제거
  └─ 409: 경고 토스트 "원래 자리에 다른 작업이 있습니다. (v2에서 재배치 지원 예정)"
```

**v2 Deferred:** 점유 충돌 시 ConflictResolutionModal로 사용자가 "밀기/취소" 선택, 또는 다른 설비/시각으로 reassign.

---

## 5. UI/UX 상세 (AI slop 제거 완료)

### 5.1 우클릭 컨텍스트 메뉴 (아이콘 = heroicons)

```
┌───────────────────────────────┐
│  [PencilIcon]  수정             │
│  [DocumentDuplicateIcon] 복사   │
│  [ScissorsIcon] 배치 분할       │
│─────────────────────────────── │
│  상태 변경                       │  (자리 유지)
│  [PlayIcon] 진행중으로 변경      │
│  [CheckCircleIcon] 완료로 변경  │
│  [ArrowUturnLeftIcon] 계획으로  │
│                  되돌리기        │
│─────────────────────────────── │
│  배치 관리                       │  (신규 섹션, "스케줄 해제"→명확화)
│  [ArrowDownTrayIcon]            │
│    미배정으로 이동               │  ← disabled 가능
│─────────────────────────────── │
│  [TrashIcon] 삭제               │
└───────────────────────────────┘
```

**아이콘 import 예:**

```typescript
import { ArrowDownTrayIcon } from "@heroicons/react/24/outline";
// 또는 lucide-react: import { ArrowDownToLine } from "lucide-react";
```

**"미배정으로 이동" 항목 상태 규칙:**

| 조건                                            | 상태     | 툴팁                                            |
| ----------------------------------------------- | -------- | ----------------------------------------------- |
| batch_group 모든 task `planned` + WIP 매칭 없음 | **활성** | "이 묶음을 미배정 작업으로 이동"                |
| 하나라도 `in_progress`/`completed`              | disabled | "진행중인 공정 포함 — 먼저 계획으로 되돌리세요" |
| WIP 매칭된 batch_group                          | disabled | "WIP 매칭된 묶음은 이동할 수 없습니다"          |

### 5.2 UnassignConfirmModal — 직접 구현 (samildevkit 패키지 import 없음)

**원칙:** samildevkit 내용을 프로젝트 코드에 직접 구현. `<Button>`은 일반 `<button>`에 CSS 변수 + Tailwind로 작성. 패키지 import 금지.

로컬 재사용을 위해 `frontend/src/shared/ui/buttons.tsx` 같은 얇은 어댑터에 Button 스타일 프리셋만 모아두고, 각 모달/카드에서 일반 `<button>` + `className`으로 호출한다. 어댑터 파일 이름/주석 어디에도 "samildevkit" 문자열 금지.

```tsx
// UnassignConfirmModal.tsx — 예시 (패키지 import 없음)
import { XMarkIcon } from "@heroicons/react/24/outline";

const btnPrimary =
  "px-3 py-1.5 text-xs font-medium rounded text-[color:var(--color-text-inverse)] " +
  "bg-[color:var(--color-brand-primary)] hover:opacity-90 transition-opacity";
const btnGhost =
  "px-3 py-1.5 text-xs font-medium rounded " +
  "text-[color:var(--color-text-primary)] hover:bg-[color:var(--color-bg-muted)]";

<div role="dialog" aria-modal="true" /* ... */>
  <header className="flex items-start justify-between mb-3">
    <h3 className="text-sm font-semibold text-[color:var(--color-text-primary)]">
      이 묶음을 미배정 작업으로 이동합니다
    </h3>
    <button onClick={onCancel} aria-label="닫기">
      <XMarkIcon width={16} height={16} />
    </button>
  </header>
  <dl className="text-xs space-y-1 mb-4">
    <div className="flex gap-2">
      <dt className="text-[color:var(--color-text-secondary)]">포함 공정:</dt>
      <dd>연선 → 절연 → 시스</dd>
    </div>
    <div className="flex gap-2">
      <dt className="text-[color:var(--color-text-secondary)]">포함 수주:</dt>
      <dd>3건</dd>
    </div>
  </dl>
  <p className="text-xs text-[color:var(--color-text-secondary)] mb-5">
    미배정으로 이동 후 "계획으로 복원" 버튼으로 언제든 원래 자리로 돌릴 수
    있습니다.
  </p>
  <div className="flex justify-end gap-2">
    <button className={btnGhost} onClick={onCancel}>
      취소
    </button>
    <button className={btnPrimary} onClick={onConfirm}>
      미배정으로 이동
    </button>
  </div>
</div>;
```

**규칙:**

- `import ... from "samildevkit"` **절대 금지**
- 모든 색상은 `var(--color-*)` 경유. raw hex 금지 (fallback 포함)
- Button variant는 **로컬 상수(`btnPrimary` / `btnGhost` / `btnSecondary`)** 또는 공용 className 유틸로 재사용
- 이탤릭, 그라데이션, 이모지 금지 — heroicons만

### 5.3 BatchGroupCard — 직접 구현 (Chip/Button 패턴 inline)

```
┌──────────────────────────────────────┐ 200px × auto
│  [Link2Icon] 3수주       [연선 chip] │   수주수 + 설비 그룹 chip
│  TFR-CV 2C·25SQ·흑                   │   규격·색상 (1줄 통합)
│  고객: 수요자 A 외 2                  │   고객사 요약
├──────────────────────────────────────┤
│  [chip]연선 › [chip]절연 › [chip]시스 │   공정체인
├──────────────────────────────────────┤
│                   납기 4/22          │   납기만 (총 길이 → hover tooltip)
├──────────────────────────────────────┤
│    [계획으로 복원]                    │   버튼, width 100%, sm 크기
└──────────────────────────────────────┘
```

**구현 규칙:**

- Chip = `<span className={chipMuted}>연선</span>` 형태로 inline. 별도 컴포넌트 파일 없어도 OK — `btnPrimary` / `chipMuted` 같은 className 상수를 `frontend/src/shared/ui/styles.ts` 또는 각 컴포넌트 상단에 정의
- 예: `const chipMuted = "text-[10px] px-1.5 py-0.5 rounded bg-[color:var(--color-bg-muted)] text-[color:var(--color-text-secondary)]"`
- border 색상: equipment_group → CSS 변수 매핑 (예: `var(--color-process-연선)`)
- 아이콘: `Link2` (lucide) 또는 `LinkIcon` (heroicons outline) — **import from "samildevkit" 금지**
- "계획으로 복원" 버튼: `<button className={btnSecondary}>` 패턴
- **Cognitive load 개선 (Design #3):** 총 길이는 카드에서 제거, `title` 속성으로 hover 시 표시

### 5.4 CollapsiblePanel (v1: 변경 없음)

v2에서 DropZone 추가 예정. v1은 기존 기능 그대로.

### 5.5 접근성

- 컨텍스트 메뉴 각 항목: Tab 네비게이션 + `aria-disabled` 속성
- BatchGroupCard: `role="group"` + `aria-label="배치 묶음 {batch_group}, {수주수}건, 복원 가능"`
- "계획으로 복원" 버튼: `aria-label="묶음을 원래 자리로 복원"`
- BatchGroupCard 전체는 `tabIndex={0}` — 키보드 포커스 가능

### 5.6 Microcopy 일관성

| 용어              | 사용 맥락             | 금지                        |
| ----------------- | --------------------- | --------------------------- |
| "미배정으로 이동" | 동작 버튼/메뉴 항목   | "되돌리기"는 상태 롤백 전용 |
| "계획으로 복원"   | BatchGroupCard 버튼   | "재배치"는 v2 용어          |
| "묶음"            | UI 전면 (사용자 용어) | "batch_group" (dev-only)    |
| "공정체인"        | 공정 흐름 설명        | —                           |

---

## 6. 엣지 케이스

| #   | 상황                                          | v1 처리                                            |
| --- | --------------------------------------------- | -------------------------------------------------- |
| E1  | 동시성 — 다른 사용자가 먼저 in_progress 변경  | FOR UPDATE + 재검증 → 409                          |
| E2  | 중복 클릭                                     | `inFlightBatchGroups` guard + 멱등 backend         |
| E3  | WIP 매칭 batch_group                          | 메뉴 disabled (Q7)                                 |
| E4  | batch_seq=-1 헤더 우클릭                      | batch_group 기준 동작 (전체 포함)                  |
| E5  | 복원 시 원래 자리 점유됨                      | 409 + 경고 토스트 "v2에서 재배치 지원"             |
| E6  | WIP 매칭된 batch unassigned 후 Stage 1 재실행 | 기존 정책 유지 — DELETE됨                          |
| E7  | split된 batch_group                           | 각 독립 group으로 처리                             |
| E8  | 간트 task 없는 batch_group                    | 우클릭 대상 없음                                   |
| E9  | audit_log 삽입 실패                           | 본 트랜잭션은 성공 처리 (try/except 내부)          |
| E10 | 페이지 새로고침 시 복원                       | `loadBatchGroupSnapshots` 호출로 OrderInbox 재구성 |

**v2로 이관된 케이스:** 공정 경로 불일치 재배치, 공휴일 보정, 납기 초과 경고, ConflictResolutionModal push 전략.

---

## 7. 테스트 전략

### 7.1 백엔드 단위 (`backend/tests/test_batch_group_lifecycle.py`)

- `test_unassign_planned_group_success`
- `test_unassign_blocked_if_in_progress`
- `test_unassign_blocked_if_completed`
- `test_unassign_blocked_if_wip_matched` (신규 — Q7)
- `test_unassign_preserves_audit_log` (soft-delete 핵심 검증)
- `test_unassign_idempotent`
- `test_unassign_not_found`
- `test_restore_success`
- `test_restore_blocked_if_original_slot_occupied` (409)
- `test_restore_idempotent` (이미 planned면 무변경)

### 7.2 백엔드 통합 (`test_routes.py`)

- `TestBatchGroupUnassignRoutes` × 3 (200, 400, 404)
- `TestBatchGroupRestoreRoutes` × 3 (200, 409, 404)
- `TestBatchGroupSnapshotsRoute` × 1

### 7.3 프론트 단위 (Vitest)

- `unassignBatchGroup` 낙관적 + 롤백 + race guard
- `restoreBatchGroup` 성공 + 409 경고
- `loadBatchGroupSnapshots` fetch → unscheduledItems 반영

### 7.4 E2E (Playwright) — 4 시나리오 (축소, CEO Red#1)

- **S1:** 우클릭 경로 unassign → 간트 3블록 사라짐 + BatchGroupCard 추가
- **S2:** in_progress batch → 메뉴 disabled + tooltip 표시
- **S3:** BatchGroupCard "계획으로 복원" 클릭 → 간트 복원 성공
- **S4:** WIP 매칭 batch → 메뉴 disabled

### 7.5 Phase 직전 디자인 게이트 (Design MUST#1~4 사전)

**Phase 6 진입 전에 수행** (사후가 아님):

- `verify-pwc-design` 스킬 호출하여 Spec 5.1~5.3 mock + 실제 구현 파일을 스캔
- 이모지, raw hex, grad, italic 위반 0건 확인
- 위반 있으면 Phase 6 진입 차단

### 7.6 수동 QA

- [ ] 빈 batch_group 우클릭 → 메뉴 안 보임
- [ ] 미배정 후 새로고침 → BatchGroupCard 복원 (snapshots GET)
- [ ] 3개 batch_group 연속 미배정 → UI/DB 정합
- [ ] Stage 1 재실행 → unassigned 데이터 소실 확인
- [ ] audit_log에 UNASSIGNED/RESTORED 이벤트 표시
- [ ] 복원 후 원래 자리에 정확히 같은 equipment/시각에 도착

---

## 8. 구현 범위 요약 (v1 축소)

| 카테고리             | 건수                                                        | 비고                        |
| -------------------- | ----------------------------------------------------------- | --------------------------- |
| Alembic 마이그레이션 | 1 (partial index만)                                         | 스냅샷 컬럼 제거            |
| 백엔드 API 신규      | 3 (unassign, restore, snapshots)                            | reassign은 v2               |
| 백엔드 서비스 함수   | 2 (unassign/restore)                                        | reassign은 v2               |
| 프론트 컴포넌트 신규 | 2 (BatchGroupCard, UnassignConfirmModal)                    | drag 없음                   |
| 프론트 컴포넌트 수정 | 4 (ContextMenu, OrderInbox, useScheduleData, TaskFormModal) | TaskFormModal 추가 (Eng C5) |
| 스토어 액션 신규     | 3 (unassign, restore, loadSnapshots)                        | reassign은 v2               |
| 백엔드 단위 테스트   | 10                                                          | reassign 테스트 제외        |
| 프론트 단위 테스트   | 3                                                           | —                           |
| E2E 테스트           | 4 (S1~S4)                                                   | 기존 7개 → 4개로 축소       |

**v1 예상 공수:** 2-3일 (CEO 권고 대비 부합)

---

## 9. v2 Deferred (후속 스코프)

v1 시연 피드백 수집 후 재평가:

- **드래그 경로**: 간트 블록 → CollapsiblePanel 드롭 (DropZone + stick 배너 / overlay 아님)
- **Undo 토스트** (10초, heroicons): 드래그 직후 6→10초 되돌리기
- **Reassign API**: 원래 자리 외 다른 설비/시각 + `cascade_strategy='push'`
- **ConflictResolutionModal 확장**: 밀기/취소 + 납기 초과 경고
- **공정 경로 validator**: `equipmentMatchesGroup` 백엔드 재구현
- **N+1 최적화**: `selectinload` + 단일 overlap 쿼리
- **"재계획 큐 + LLM 제안"** (CEO 전략 기회): 미배정 풀을 AI 제안 큐로 격상
- **미배정 사유 tagging** (CEO 전략 기회): "자재지연/설비고장/납기재협상"

---

## 10. 참고 파일

### 현재 코드 기준 (수정 대상)

- `frontend/src/features/scheduler/components/ContextMenu.tsx`
- `frontend/src/features/scheduler/store/scheduleStore.ts`
- `frontend/src/features/scheduler/components/OrderInbox.tsx`
- `frontend/src/features/scheduler/components/TaskFormModal.tsx` (Eng C5)
- `frontend/src/features/scheduler/hooks/useScheduleData.ts`
- `frontend/src/features/scheduler/types/index.ts`
- `backend/app/presentation/routes/plan_pipeline.py`
- `backend/app/infrastructure/models/production_batch.py` (Literal type만)
- `backend/app/infrastructure/models/schedule_task.py` (Literal type만)

### 신규 파일

- `backend/alembic/versions/c3d4e5f6a7b8_add_unassigned_status_partial_index.py`
- `backend/app/services/batch_group_lifecycle.py`
- `backend/tests/test_batch_group_lifecycle.py`
- `frontend/src/features/scheduler/components/BatchGroupCard.tsx`
- `frontend/src/features/scheduler/components/UnassignConfirmModal.tsx`
- `frontend/src/features/scheduler/store/__tests__/unassignBatchGroup.test.ts`
- `frontend/src/features/scheduler/store/__tests__/restoreBatchGroup.test.ts`
- `frontend/e2e/batch-group-unassign.spec.ts`

### 메모리 원칙 (읽기 전용)

- `~/.claude/projects/-Users-jaewookim-Desktop-Project-KBI-PoC/memory/feedback_tfrg_color.md`
- `~/.claude/projects/-Users-jaewookim-Desktop-Project-KBI-PoC/memory/feedback_architecture_simplicity.md`
- `~/.claude/projects/-Users-jaewookim-Desktop-Project-KBI-PoC/memory/feedback_ui_quality_no_ai_slop.md`
- `~/.claude/projects/-Users-jaewookim-Desktop-Project-KBI-PoC/memory/feedback_commit_verification.md`
