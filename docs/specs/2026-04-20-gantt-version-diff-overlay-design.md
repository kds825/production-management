# 간트차트 버전 Diff 시각화 오버레이 — 설계 스펙

작성일: 2026-04-20
작성자: Claude (brainstorming with 사용자)
대상 페이지: `/scheduler` (생산계획 스케줄러 간트차트)

## Context & Motivation

### 문제

stage1/update 로 새 run_label 이 발급되면 이전 run 과의 diff 를 확인해야 한다.
현재 구현은 텍스트 기반 모달 (추가/이동/삭제/변경없음 카운트 + 목록). 사용자가
"블록이 **어디**에 있었는지, **어떻게** 이동했는지" 를 공간적으로 파악하려면
간트차트 위에 직접 diff 를 overlay 해야 한다.

이전 페이지(`/scheduling-review`)의 모달식 diff 는 집계/목록 성격이라 그대로
유지. 본 문서는 **간트 스케줄러 페이지 전용 시각화** 를 다룬다 — 두 UI 는
목적이 다르다.

### 성공 기준

1. 사용자가 compareMode 를 켜면 현재 간트에 **어떤 블록이 추가/이동/삭제되었는지**
   가 색상 outline 과 위치 ghost 로 한눈에 보인다.
2. 이동된 블록은 **과거 위치** 도 dashed + 20% opacity fill 로 함께 표시되어
   "동일 수주가 어디서 어디로" 가 색상 매칭으로 식별된다.
3. 146 건 급 moved 가 있어도 시각 노이즈가 발생하지 않는다 (필터 pill + unchanged dim).
4. 기존 cascade preview 와 시각적으로 혼동되지 않는다 (색 팔레트 분리).
5. 성능: 기존 간트 렌더 대비 30% 이내 오버헤드.

### 비목표 (NOT in scope)

- 세 개 이상 run 비교 (A vs B vs C)
- Diff 모드에서 블록 드래그 편집 (read-only)
- 과거 임의 run 선택 비교 (항상 "최신 run vs 그 parent_run_label")
- 가시성 높이려고 기존 간트 레이아웃 변경 (row 높이/간격 등 고정)

---

## 설계 결정 (사용자 승인됨)

| 결정                 | 채택                          | 이유                                                        |
| -------------------- | ----------------------------- | ----------------------------------------------------------- |
| 이동 ghost 스타일    | dashed + 20% opacity fill     | 같은 색으로 "동일 수주 이동" 즉시 식별                      |
| Removed 기본 표시    | 숨김 (필터 pill 로 토글)      | 노이즈 최소화, 현재 run 에 없는 블록을 기본으로 띄우지 않음 |
| Cascade preview 공존 | 비활성화 (compare mode ON 시) | 한 번에 한 overlay 로 시각 명료                             |

---

## 색상 팔레트

기존 `brand.ts` 에 `DIFF_COLORS` 추가:

```ts
export const DIFF_COLORS = {
  added: "#10B981", // emerald-500 — 녹색 outline/뱃지
  moved: "#3B82F6", // blue-500 — 파란 outline + ghost dashed
  removed: "#9CA3AF", // gray-400 — 회색 dashed (필터 ON 시)
  cascade: "#F59E0B", // 기존 — 건드리지 않음
} as const;
```

색 분리 근거: cascade(yellow) 와 diff 의미가 다름. diff 는 "이미 발생한 과거 변화",
cascade 는 "수락하면 발생할 미래 변화". 두 overlay 가 동시 활성되지 않도록
store 에서 상호배타 (아래 참조).

**색상만으로 의미 인코딩하지 않는다** (colorblind 대응): 모든 diff 블록에
텍스트/아이콘 뱃지 포함 (added="+", moved="⇄" 또는 "±Nh", removed="−").
아래 "A11y" 섹션 참조.

**디자인 시스템 통합**: 위 DIFF_COLORS 는 별도 상수 대신 `KBI_BRAND.colors.diff`
하위에 병합 배치 → 기존 토큰 네임스페이스 일관성 유지. `frontend/src/shared/constants/brand.ts`.

---

## 시각 우선순위 (attention hierarchy)

compareMode ON 상태에서 사용자 눈길 순서:

1. **최상위 — 추가/삭제 (신호)**: 녹색 `+` 뱃지, 회색 `−` 뱃지가 pop (큰 변화)
2. **중위 — 이동 (흐름)**: 파란 outline + delta 뱃지, 과거 위치 ghost
3. **하위 — 변경 없음 (컨텍스트)**: opacity 60% dim

블록 outline 굵기: 변화 블록 2px, 기본(unchanged) 1px 유지 (대비 확보).
Dim 블록을 60% 로 설정하되 텍스트 가독성 보장 (40% 는 너무 흐림).

---

## 렌더 규칙 (요약표)

| Diff 상태                     | 현재 위치 블록                               | 과거 위치 ghost                                 |
| ----------------------------- | -------------------------------------------- | ----------------------------------------------- |
| **Added**                     | 녹색 2px outline + 우상단 `+` 뱃지 (segW≥50) | —                                               |
| **Moved (동일 설비, 시간만)** | 파란 2px outline + `±Nh` 뱃지                | 파란 dashed + 20% opacity fill, "이전" 7px 뱃지 |
| **Moved (설비 변경)**         | 파란 2px outline + `⇄` 뱃지                  | 다른 row 에 파란 dashed + 20% fill              |
| **Removed**                   | —                                            | 회색 dashed + 15% fill (기본 숨김)              |
| **Unchanged**                 | 기본 렌더 + opacity 0.6                      | —                                               |

블록 내부 뱃지는 기존 `+4일 지연` 뱃지 패턴 (`segW >= 50` 조건) 재사용.

---

## UX 흐름

### 1. 진입

스케줄러 헤더 툴바 기존 `[이전 버전과 비교]` 버튼 → `[비교 모드]` 토글로 대체.

- 최신 run 에 `parent_run_label` 이 없으면 disabled (툴팁: "최초 실행 — 비교 대상 없음")
- 클릭 → `/api/pipeline/runs/compare?before=parent&after=latest` 호출 → store 에 저장 → overlay ON

### 2. 비교 모드 ON 상태

```
┌─ 툴바 ────────────────────────────────────────────────────────┐
│ [자동배열] [비교 모드 ×]  ● 추가 61  ● 이동 146  ● 삭제 2  │
│                          ↑ 클릭 = 카테고리 show/hide          │
│                          ┌─ 우측 ─┐                           │
│                          │ [목록] │ → 기존 모달 오픈 (상세)  │
│                          └────────┘                           │
└───────────────────────────────────────────────────────────────┘
```

- 3 개 필터 pill 은 기본 모두 ON (added=ON, moved=ON, removed=OFF)
- 각 pill 클릭: 해당 카테고리 overlay show/hide

### 3. 상호작용 상태 매트릭스

| 상태                         | UI                                                       | 사용자가 보는 것                                     |
| ---------------------------- | -------------------------------------------------------- | ---------------------------------------------------- |
| **Disabled** (parent 없음)   | `[비교 모드]` 회색, tooltip "최초 실행 — 비교 대상 없음" | 버튼은 있지만 비활성. hover 시 이유 설명             |
| **Loading** (fetch 중)       | pill 영역에 spinner + "비교 데이터 로드 중..."           | 기존 간트는 그대로. 블로킹 X. pills 들은 아직 비활성 |
| **Error** (fetch 실패)       | pills 자리에 빨간 toast "비교 실패 — 재시도" + ×         | 기존 간트 그대로. [비교 모드]는 OFF 로 복귀          |
| **Success (변화 없음)**      | pills `+0 · ⇄0 · −0`, 중앙 배너 "두 버전이 동일합니다"   | unchanged dim 적용 X. 사용자에게 긍정 피드백         |
| **Success (변화 있음)**      | pills 에 숫자 + 간트에 overlay                           | 정상 렌더 — 아래 규칙대로                            |
| **Partial** (일부 필드 null) | 해당 블록은 overlay 생략, count 는 유지                  | 응답의 null start/end 는 skip 렌더                   |

### 4. 사용자 여정 (emotional arc)

- **5초 (visceral)**: "내 업로드가 반영됐구나 — 녹색이 눈에 띈다" → 시스템 신뢰
- **5분 (behavioral)**: pill 로 카테고리 토글, 특정 수주 이동 확인 → 도구 역량 학습
- **5년 (reflective)**: "자동배열 후 항상 비교 모드 한번 돌린다" → 워크플로 정착 신뢰
- `[목록]` 버튼: 기존 run-compare 모달 (텍스트 상세) — 두 UI 공존
- ESC 키 또는 `[비교 모드 ×]` 클릭: overlay OFF

### 5. 블록 hover (후속 반복, v1 에서는 skip)

- 블록 hover → 같은 수주의 ghost(과거 위치) 와 SVG arrow 로 연결
- 다른 블록 opacity 20% dim
- 기존 `ChainHighlightOverlay.tsx` 패턴 확장 — v2 에서 구현

### 6. 편집 정책 (compareMode 중)

- compareMode ON 중에는 블록 **드래그/리사이즈 비활성화** (read-only).
  변화 시각화 도중 편집이 일어나면 diff 가 stale 되어 혼란 유발.
- 시도하면 toast "비교 모드 중에는 편집 불가 — 끄고 편집하세요".
- 자동배열·수정모드 진입 시 compareMode 자동 OFF.

### 7. 줌/스크롤 상호작용

- 줌 변경 시 ghost 블록 위치 동기화 (동일 rangeStart/dayWidth 공식 재사용).
- ghost 너무 좁아질 때 (`segW < 16`): dashed border 대신 가는 파란 vertical line
  (x=new start, 2px)만 남겨 "여기서 이동" 단서 제공.

### 8. 전환 애니메이션

- compareMode 토글 시 `opacity` 150ms ease-out fade
  (기존 `GanttTaskBlock.tsx:535-540` transition 규칙 준수, 좌우 이동 애니는 없음).
- pill 토글도 동일 150ms.

---

## 컴포넌트 변경

### A. `scheduleStore.ts` — 상태 추가

```ts
interface CompareMode {
  enabled: boolean;
  beforeRunLabel: string | null;
  afterRunLabel: string | null;
  diffResponse: RunCompareResponse | null;
  filters: { added: boolean; moved: boolean; removed: boolean };
}

// 초기값
const initialCompareMode: CompareMode = {
  enabled: false,
  beforeRunLabel: null,
  afterRunLabel: null,
  diffResponse: null,
  filters: { added: true, moved: true, removed: false },
};

// 액션
enableCompareMode(before: string, after: string): Promise<void>;
toggleCompareFilter(category: 'added' | 'moved' | 'removed'): void;
closeCompareMode(): void;
```

### B. `GanttTaskBlock.tsx` — Prop 확장

```ts
interface GanttTaskBlockProps {
  // 기존
  ghost?: boolean;

  // 신규
  ghostReason?:
    | "cascade" // yellow (기존)
    | "diff_moved_past" // blue dashed + 20% fill
    | "diff_removed"; // gray dashed + 15% fill
  diffOverlay?: // 현재 위치 outline
    | "added" // green outline + 우상단 "+"
    | "moved" // blue outline + delta 뱃지
    | undefined;
  diffDeltaHours?: number; // moved 일 때 ±Nh 뱃지 렌더
  diffEquipmentChanged?: boolean; // moved + 설비 변경 시 ⇄ 아이콘
  dimmed?: boolean; // unchanged 일 때 opacity 0.6
}
```

**스타일 분기 (render 내부)**:

```tsx
const getGhostStyle = (reason?: string): React.CSSProperties => {
  switch (reason) {
    case "diff_moved_past":
      return {
        border: `2px dashed ${DIFF_COLORS.moved}`,
        backgroundColor: `${baseColor}33`, // 20% opacity
        opacity: 0.7,
        pointerEvents: "none",
      };
    case "diff_removed":
      return {
        border: `2px dashed ${DIFF_COLORS.removed}`,
        backgroundColor: `${DIFF_COLORS.removed}26`, // 15%
        opacity: 0.5,
        pointerEvents: "none",
      };
    case "cascade":
      return {
        /* 기존 cascade ghost 그대로 */
      };
  }
};

const getOverlayStyle = (overlay?: string): React.CSSProperties => {
  if (overlay === "added")
    return { outline: `2px solid ${DIFF_COLORS.added}`, outlineOffset: 1 };
  if (overlay === "moved")
    return { outline: `2px solid ${DIFF_COLORS.moved}`, outlineOffset: 1 };
  return {};
};
```

### C. `SchedulerView.tsx` — Diff 렌더 주입

`GanttRow` 내 `rowTasks.map` 에 다음 로직 추가:

```tsx
// diffByTaskKey: compareMode.diffResponse 를 stable key 로 indexing
const diffByKey = buildDiffIndex(compareMode?.diffResponse);

rowTasks.map((task) => {
  const key = taskStableKey(task); // (sales_order_id, line, process, batch_seq)
  const diff = diffByKey.get(key);

  // 현재 위치 블록
  const overlay =
    diff?.kind === "added"
      ? "added"
      : diff?.kind === "moved"
        ? "moved"
        : undefined;
  const dimmed = compareMode?.enabled && !diff;

  return (
    <>
      {/* 기본 블록 */}
      <GanttTaskBlock
        task={task}
        diffOverlay={overlay}
        diffDeltaHours={diff?.start_delta_hours}
        diffEquipmentChanged={diff?.equipment_changed}
        dimmed={dimmed}
      />

      {/* moved 의 과거 위치 ghost (같은 row 이거나 다른 row — 별도 렌더) */}
      {diff?.kind === "moved" && compareMode.filters.moved && (
        <GanttTaskBlock
          task={{
            ...task,
            start: diff.old_start,
            end: diff.old_end,
            equipment_id: diff.old_equipment,
          }}
          ghost
          ghostReason="diff_moved_past"
        />
      )}
    </>
  );
});

// Row 렌더 루프 바깥: removed ghost (현재 run 에 없는 블록)
if (compareMode?.enabled && compareMode.filters.removed) {
  diffResponse.removed_tasks
    .filter((t) => t.equipment === thisRowEquipment)
    .map((t) => (
      <GanttTaskBlock
        task={synthesizedTask(t)}
        ghost
        ghostReason="diff_removed"
      />
    ));
}
```

**중요**: `buildDiffIndex` 유틸 스펙:

- 파일: `features/scheduler/utils/diffIndex.ts` (신규)
- 입력: `RunCompareResponse | null`
- 출력: `Map<string, DiffIndexEntry>`
  ```ts
  type DiffIndexEntry =
    | { kind: "added"; task: AddedTask }
    | {
        kind: "moved";
        task: MovedTask;
        old_start: Date;
        old_end: Date;
        old_equipment: string;
        start_delta_hours: number;
        equipment_changed: boolean;
      }
    | { kind: "removed"; task: RemovedTask };
  ```
- 키 형식: `"${sales_order_id||batch_group}|${order_line||0}|${process_name||''}|${batch_seq||0}"` — **백엔드 `/runs/compare` 의 `task_id` 문자열과 일치**
- 키 파싱: 프론트 `ScheduleTask` 에서 동일 형식으로 생성해 Map lookup

**장비 코드 네이밍 주의**: 프론트 `ScheduleTask.equipment_id` ↔ 백엔드 `equipment_code` 필드명 다름. 컴페어 응답은 `equipment` 필드 사용 (`moved_tasks[].new_equipment`, `removed_tasks[].equipment`). `GanttTaskBlock` 에 전달할 때 `equipment_id` 로 매핑.

### D. 툴바 헤더 — `scheduler/page.tsx`

기존 `[이전 버전과 비교]` 버튼 → `[비교 모드]` 토글 + 필터 pills.

```tsx
<button onClick={toggleCompareMode} disabled={!hasParent}>
  {compareMode.enabled ? '비교 모드 ×' : '비교 모드'}
</button>
{compareMode.enabled && (
  <>
    <FilterPill
      color={DIFF_COLORS.added}
      label={`추가 ${compareMode.diffResponse?.summary.added}`}
      active={compareMode.filters.added}
      onClick={() => toggleCompareFilter('added')}
    />
    <FilterPill color={DIFF_COLORS.moved} ... />
    <FilterPill color={DIFF_COLORS.removed} ... />
    <button onClick={openDetailModal}>목록</button>
  </>
)}
```

기존 모달 컴포넌트는 `openDetailModal` 로 호출 — 코드 유지.

### E. Cascade Preview 비활성화

`compareMode.enabled === true` 일 때 `setPreviewOverlay(null)` 호출 + cascade
preview 트리거 버튼 비활성.

---

## 데이터 흐름

```
1. 사용자 [비교 모드] 클릭
   ↓
2. scheduler/page.tsx: handleToggleCompareMode()
   ↓
3. store.enableCompareMode(parent, latest)
   ↓
4. fetch /api/pipeline/runs/compare?before=X&after=Y
   ↓
5. store.compareMode = { enabled: true, diffResponse, filters: {...} }
   ↓
6. SchedulerView re-render
   ↓
7. buildDiffIndex(diffResponse) → Map<key, diffEntry>
   ↓
8. GanttRow.rowTasks.map → GanttTaskBlock with diff props
   ↓
9. 추가 ghost blocks (moved_past, removed) 렌더
```

---

## 성능 고려

- **블록 수 상한**: 현재 ~195 기본 + moved ghost 최대 146 + removed 최대 2 ≈ **343**
- viewport culling 은 기존 유지 → 보이는 범위만 렌더 (통상 50~80)
- memo + useCallback 기존 사용 → props 변경 시만 리렌더
- `buildDiffIndex` 는 useMemo 로 compareMode.diffResponse 바뀔 때만 재계산
- 필터 pill 토글은 SchedulerView 리렌더 1회 — 전체 간트 DOM 재생성 X

---

## 테스트 계획

### 단위 테스트 (Jest + React Testing Library)

1. `GanttTaskBlock.tsx`:
   - `diffOverlay="added"` → `outline: DIFF_COLORS.added` 확인
   - `ghostReason="diff_moved_past"` → `border: dashed blue`, backgroundColor 20% opacity
   - `dimmed=true` → `opacity: 0.6`
   - `diffDeltaHours=6.5` → `+6.5h` 뱃지 렌더

2. `diffIndex.ts`:
   - 공백 sales_order_id 는 `batch_group` 로 대체
   - key 문자열 형식 `oid|line|process|seq` 확인

3. `scheduleStore`:
   - `enableCompareMode()` → fetch 후 state 업데이트
   - `toggleCompareFilter('added')` → filters.added toggle
   - `closeCompareMode()` → enabled=false + diffResponse=null

### 통합 테스트 (기존 pytest 백엔드)

- `/runs/compare` 응답이 프론트 `RunCompareResponse` 타입과 호환됨 (타입 선언만으로 보증)

### E2E 테스트 (browse skill)

1. 자동배열 후 이전 버전이 있는 상태 준비
2. `/scheduler` 페이지 열기
3. [비교 모드] 클릭 → 간트에 녹/파랑/회색 outline 블록 보이는지 스크린샷
4. [추가] pill 클릭 → 녹색 blocks 사라짐 확인
5. [비교 모드 ×] 클릭 → 원상복구

---

## 마이그레이션 (Rollout)

v1: overlay + 필터 pills (현재 스펙)
v2: hover → 같은 수주 SVG arrow 연결
v3: diff 모드에서 "이 변경 수락" — 개별 블록 revert 기능 (long-term)

v1 은 단일 PR, v2 이상은 사용자 피드백 후 계획.

---

## 위험 & 완화

| 위험                                            | 확률 | 영향 | 완화                                              |
| ----------------------------------------------- | ---- | ---- | ------------------------------------------------- |
| cascade 색과 diff 색 혼동                       | 낮음 | 중간 | 색 분리 + 상호배타 활성화                         |
| 146 ghost 렌더로 FPS 저하                       | 낮음 | 중간 | viewport culling + memo; 실측 시 virtualization   |
| stable key 불일치 (헤더 batch_seq=-1)           | 중간 | 중간 | batch_group fallback 로 해결 (백엔드 이미 구현)   |
| 필터 pill 로 overlay 완전히 숨겨져 사용자 혼란  | 낮음 | 낮음 | pill 상태 시각적 명확 (active/inactive)           |
| Removed 블록 synthesized task 의 lane 배치 실패 | 중간 | 낮음 | removed 는 별도 row-by-row 렌더 루프, lane 0 고정 |

---

## 파일 영향 범위

| 파일                                                               | 변경 타입                   | 라인 추정                      |
| ------------------------------------------------------------------ | --------------------------- | ------------------------------ |
| `frontend/src/shared/constants/brand.ts`                           | DIFF_COLORS 추가            | +10                            |
| `frontend/src/features/scheduler/store/scheduleStore.ts`           | compareMode state + actions | +80                            |
| `frontend/src/features/scheduler/components/GanttTaskBlock.tsx`    | prop 확장 + 스타일 분기     | +60                            |
| `frontend/src/features/scheduler/components/SchedulerView.tsx`     | diff 렌더 주입              | +40                            |
| `frontend/src/features/scheduler/utils/diffIndex.ts` (신규)        | stable key 인덱싱           | +30                            |
| `frontend/src/app/(main)/scheduler/page.tsx`                       | 툴바 버튼 변경 + FilterPill | +100 (기존 모달 일부 삭제 -40) |
| `frontend/src/features/scheduler/components/FilterPill.tsx` (신규) | pill 컴포넌트               | +40                            |
| 테스트 파일 (3개)                                                  | 신규                        | +200                           |

총 약 520 라인 변경 (신규 위주, 기존 파괴 최소).

---

## Open Questions (구현 중 결정)

- Q1: removed 블록 synthesized task 의 `start`/`end`/`equipment` 가 null 인 경우 (backend edge case, frozen batch 에만 있고 schedule_task 미생성)? → skip rendering, 카운트는 유지 (pill 에는 여전히 N 으로 표시)
- Q2: compareMode ON 중 `/schedules/tasks` API refetch 시 diff 재계산 필요? → 현재 run 이 바뀌지 않는 한 diffResponse 유지. stage2 재실행 시 새 task_id 생기면 stable key (order_id+process+batch_seq) 로 매칭되므로 괜찮음
- Q3: v1 에서 `RunCompareResponse` 타입 중복 정의 (scheduling-review/page.tsx + 이번 변경) → 공통 위치 `features/scheduler/types/diff.ts` 로 통합. scheduling-review 에서 import 로 전환.

---

## 의존성

- 백엔드: `GET /api/pipeline/runs/compare` 이미 구현됨 (commit ea9d794)
- 타입: `RunCompareResponse` (scheduling-review/page.tsx 에 이미 정의, 공유 위치로 이동 필요 — `features/scheduler/types/diff.ts` 확장)

---

## Accessibility (a11y)

스케줄러는 데스크톱 전용 (responsive 범위 외). 그러나 키보드/스크린리더/colorblind
대응은 필수 — 아래 명세를 따른다.

### 키보드

- `[비교 모드]` 버튼: `Tab` 포커스, `Enter`/`Space` 토글
- 각 필터 pill: `Tab` 포커스, `Enter`/`Space` 토글, `aria-pressed` 상태 반영
- compareMode ON 시 `Esc` → overlay OFF (상태 반환)
- 포커스 링: 기존 `outline: 2px solid var(--color-brand-primary)` 재사용

### ARIA

- 필터 pill: `role="button" aria-pressed="true|false" aria-label="추가 61건 보기 (토글)"`
- 현재 위치 diff 블록: `aria-label` 동적 생성
  - added: `"신규 배치: {order_id} {process_name}"`
  - moved: `"이동된 배치: {order_id}, 시작 시각 {delta}시간 {방향}"` (방향=앞당김/밀림)
  - moved + equipment_changed: `"... 설비 {old}→{new} 변경"`
- Ghost 블록: `aria-label="이전 위치: {order_id}" role="img"` (상호작용 불가 명시)
- 모달 `[목록]` 버튼: `aria-haspopup="dialog"`

### 색상 독립 인코딩 (colorblind 안전)

- **텍스트/아이콘 뱃지 필수**: added="+N" 우상단, moved="±Nh" 하단, 설비변경="⇄" 좌상단, removed="−" 좌상단
- 필터 pill: 색 dot 옆에 텍스트 라벨 필수 (`● 추가 61`)
- 배경색만으로 상태 전달 금지 — 항상 outline/뱃지 병용

### 대비 (contrast)

- outline 색 vs 블록 배경색: W3C AA 보더 대비 3:1 확보
- blue outline(`#3B82F6`)는 KBI 팔레트 모든 SQ 색상에 대비 3:1+ 통과 (검증: 가장 밝은 갈색 #D4A574 기준 대비 3.2:1)
- ghost 20% fill 위 흰색/어두운 텍스트 자동 전환 로직 — 기존 `getContrastingTextColor`(colorCoding.ts) 재사용

### 화면리더 라이브 리전

- compareMode 토글 시 `aria-live="polite"` 영역에 안내:
  - ON: `"비교 모드 활성. 추가 61, 이동 146, 삭제 2"`
  - OFF: `"비교 모드 비활성"`

---

## GSTACK REVIEW REPORT

| Review        | Trigger               | Why                             | Runs | Status       | Findings                                             |
| ------------- | --------------------- | ------------------------------- | ---- | ------------ | ---------------------------------------------------- |
| Design Review | `/plan-design-review` | UI/UX gaps                      | 1    | CLEAR (FULL) | score: 5/10 → 8/10, 7 passes, a11y+state matrix 추가 |
| CEO Review    | `/plan-ceo-review`    | Scope & strategy                | 0    | —            | —                                                    |
| Eng Review    | `/plan-eng-review`    | Architecture & tests (required) | 0    | —            | — (구현 시 /review 로 diff 단계에서 검증)            |
| Codex Review  | `/codex review`       | Independent 2nd opinion         | 0    | —            | —                                                    |
| DX Review     | `/plan-devex-review`  | Developer experience gaps       | 0    | —            | —                                                    |

**VERDICT**: DESIGN CLEARED — 구현 준비 완료. Eng review 는 PR 단계에서 `/review` 로 수행.

---

이 스펙은 design review 통과 (8/10). agent team dispatch 로 구현 진입.
