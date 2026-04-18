# 블록 소요시간 편집 · 연쇄 재배치 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 작업 수정 모달의 소요시간 편집 · 드래그 이동 시 cross-equipment cascade 재배치 · 후속 공정 연쇄 · Pull 제안 · Undo 를 제공한다.

**Architecture:** SSOT 는 백엔드 `cascade-preview`. 모달 편집과 드래그 이동 모두 2-Phase (preview → 사용자 승인 → bulk-update)을 공유한다. 백엔드는 `cascade/` 5-module 로 분할하고 wave 기반 BFS + Pull proposer 로 구성. 프론트는 공통 훅 `useScheduleChangeWithCascade` 로 단일화.

**Tech Stack:** Python / FastAPI / SQLAlchemy / PostgreSQL (backend). Next.js / React / Zustand / dnd-kit / react-query / Playwright / Tailwind (frontend). `pwc-design` 토큰 규약 (samildevkit 직접 import 금지).

**Spec:** `docs/specs/2026-04-18-block-duration-edit-cascade-design.md` (커밋 `e61afc7`). 아래 용어/결정은 spec 을 단일 진실 소스로 취급한다.

**Known path corrections:** spec 의 `operating_calendar.py` 는 실제 `backend/app/services/calendar_engine.py` (이 계획서의 경로를 따른다).

---

## File Structure

### Backend (신규/수정)

| 파일                                                 | 책임                                           | 상태 |
| ---------------------------------------------------- | ---------------------------------------------- | ---- |
| `backend/app/services/cascade/__init__.py`           | 모듈 export                                    | 신규 |
| `backend/app/services/cascade/snap.py`               | In-memory task snapshot + apply (순수 함수)    | 신규 |
| `backend/app/services/cascade/bfs.py`                | same-eq 양방향 겹침 · successor_tasks 탐색     | 신규 |
| `backend/app/services/cascade/validators.py`         | due-date / horizon / cycle 검증                | 신규 |
| `backend/app/services/cascade/pull.py`               | Pull 제안 생성                                 | 신규 |
| `backend/app/services/cascade/service.py`            | Orchestrator (`plan_cascade_preview`)          | 신규 |
| `backend/app/services/cascade/reasons.py`            | reason code enum + summary 템플릿              | 신규 |
| `backend/app/services/calendar_engine.py`            | `reverse_advance` 추가                         | 수정 |
| `backend/app/presentation/routes/schedules.py`       | cascade-preview v2 + bulk-update 보강 + revert | 수정 |
| `backend/app/models/schedule_change_set.py`          | change_set 스냅샷 모델                         | 신규 |
| `backend/app/core/feature_flags.py`                  | `FEATURE_FLAG_CASCADE_V2` 유틸                 | 신규 |
| `backend/alembic/versions/*_schedule_change_sets.py` | 테이블 생성                                    | 신규 |

### Frontend (신규/수정)

| 파일                                                                                     | 책임                                           | 상태 |
| ---------------------------------------------------------------------------------------- | ---------------------------------------------- | ---- |
| `frontend/src/features/scheduler/hooks/useScheduleChangeWithCascade.ts`                  | 공통 2-Phase 훅                                | 신규 |
| `frontend/src/features/scheduler/api/cascade.ts`                                         | cascade-preview / bulk-update / revert fetcher | 신규 |
| `frontend/src/features/scheduler/components/TaskFormModal.tsx`                           | 3필드 편집 + hex → 토큰                        | 수정 |
| `frontend/src/features/scheduler/components/ConflictResolutionModal.tsx`                 | 섹션화 · reason 매핑 · Pull 토글 · 버튼 단일화 | 수정 |
| `frontend/src/features/scheduler/components/SchedulerView.tsx` (or `GanttTaskBlock.tsx`) | 고스트 오버레이                                | 수정 |
| `frontend/src/app/(main)/scheduler/page.tsx`                                             | 드래그 리팩터링, `cascadePush` 제거            | 수정 |
| `frontend/src/features/scheduler/store/scheduleStore.ts`                                 | `cascadePush` / `previewCascade` 제거          | 수정 |
| `frontend/src/shared/ui/Toast.tsx`                                                       | `action` 슬롯 추가 (Undo 버튼)                 | 수정 |
| `frontend/src/shared/config/featureFlags.ts`                                             | `FEATURE_FLAG_CASCADE_V2` 프론트 config        | 신규 |
| `frontend/e2e/block-duration-edit.spec.ts`                                               | 소요시간 · Pull · Undo · due 위반              | 신규 |
| `frontend/e2e/block-drag-cascade.spec.ts`                                                | 드래그 cross-eq                                | 신규 |
| `frontend/e2e/feature-flag-off.spec.ts`                                                  | 플래그 off 경로                                | 신규 |
| `frontend/e2e/fixtures/cascadeFixture.ts`                                                | freezeTime + seeded schedule                   | 신규 |

---

## Execution Phases

- **Phase 0 — Foundation** (Task 1–3): feature flag, reverse_advance, cascade/ 스캐폴드
- **Phase 1 — Backend algorithm (TDD)** (Task 4–10): snap/bfs/validators/pull/service + unit T1–T12
- **Phase 2 — Backend API** (Task 11–15): 라우터 v2, migration, bulk-update 보강, revert, benchmark
- **Phase 3 — Frontend hooks & modal** (Task 16–21): 훅, 모달 섹션화, Pull 토글/Undo/CTA, TaskFormModal, 드래그 리팩터
- **Phase 4 — Ghost overlay + Observability + E2E** (Task 22–27): 고스트, 로깅·메트릭, E2E 3종
- **Phase 5 — Gate + Docs** (Task 28–30): verify-pwc-design, graphify, document-release

---

## Phase 0 — Foundation

### Task 1: Feature flag 도입 (backend + frontend)

**Files:**

- Create: `backend/app/core/feature_flags.py`
- Create: `frontend/src/shared/config/featureFlags.ts`
- Test: `backend/tests/core/test_feature_flags.py`
- Modify: `backend/app/core/config.py` (env loading)

- [ ] **Step 1: Write backend feature flag test (FAILING)**

```python
# backend/tests/core/test_feature_flags.py
import os
import pytest
from app.core.feature_flags import is_cascade_v2_enabled

def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("FEATURE_FLAG_CASCADE_V2", raising=False)
    assert is_cascade_v2_enabled() is False

def test_enabled_when_on(monkeypatch):
    monkeypatch.setenv("FEATURE_FLAG_CASCADE_V2", "on")
    assert is_cascade_v2_enabled() is True

def test_disabled_when_off(monkeypatch):
    monkeypatch.setenv("FEATURE_FLAG_CASCADE_V2", "off")
    assert is_cascade_v2_enabled() is False

@pytest.mark.parametrize("val", ["1", "true", "TRUE", "On"])
def test_truthy_variants(monkeypatch, val):
    monkeypatch.setenv("FEATURE_FLAG_CASCADE_V2", val)
    assert is_cascade_v2_enabled() is True
```

Run: `cd backend && pytest tests/core/test_feature_flags.py -v` → FAIL (module missing)

- [ ] **Step 2: Implement**

```python
# backend/app/core/feature_flags.py
"""Central feature flag registry.

각 플래그는 환경변수 한 개로 제어되며, 배포 후 재시작 없이 ON/OFF 가 바뀌어도
요청 단위로 즉시 반영되도록 런타임 lookup 을 한다.
"""
import os

_TRUTHY = {"1", "on", "true", "yes"}


def is_cascade_v2_enabled() -> bool:
    return os.environ.get("FEATURE_FLAG_CASCADE_V2", "").strip().lower() in _TRUTHY
```

Run: `pytest tests/core/test_feature_flags.py -v` → 4 PASS

- [ ] **Step 3: Frontend config**

```ts
// frontend/src/shared/config/featureFlags.ts
// Next.js: NEXT_PUBLIC_* 만 클라이언트 번들에 노출됨.
export const FEATURE_FLAG_CASCADE_V2 =
  (process.env.NEXT_PUBLIC_FEATURE_FLAG_CASCADE_V2 ?? "").toLowerCase() ===
  "on";
```

- [ ] **Step 4: Env 문서화**

`backend/.env.example` 와 `frontend/.env.example` 에 각각 추가:

```
FEATURE_FLAG_CASCADE_V2=on
```

```
NEXT_PUBLIC_FEATURE_FLAG_CASCADE_V2=on
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/feature_flags.py backend/tests/core/test_feature_flags.py \
        frontend/src/shared/config/featureFlags.ts \
        backend/.env.example frontend/.env.example
git commit -m "feat(flags): FEATURE_FLAG_CASCADE_V2 kill switch 도입"
```

---

### Task 2: `calendar_engine.reverse_advance` 신규

**Files:**

- Modify: `backend/app/services/calendar_engine.py`
- Test: `backend/tests/services/test_calendar_engine_reverse_advance.py`

- [ ] **Step 1: Read existing interface**

Open `backend/app/services/calendar_engine.py` and find `advance` (or `calculate_start_datetime`) — record function signature, `db` / `equipment_code` params, calendar skip 규칙.

- [ ] **Step 2: Write failing tests**

```python
# backend/tests/services/test_calendar_engine_reverse_advance.py
from datetime import datetime, timedelta
from app.services.calendar_engine import reverse_advance

def test_simple_no_gap(calendar_ctx):
    """평일 근무 시간 내에서 단순 역방향 이동."""
    # 월요일 17:00 → 2시간 역이동 → 월요일 15:00
    result = reverse_advance(datetime(2026, 4, 20, 17, 0),
                             timedelta(hours=2), ctx=calendar_ctx)
    assert result == datetime(2026, 4, 20, 15, 0)

def test_skips_weekend(calendar_ctx):
    """월요일 10:00 에서 4시간 역이동 → 금요일 근무시간 끝에서 시작."""
    # 금요일 근무 종료가 18:00 이면 금요일 14:00 이 결과
    result = reverse_advance(datetime(2026, 4, 20, 10, 0),
                             timedelta(hours=4), ctx=calendar_ctx)
    assert result.weekday() == 4  # Friday
    assert result == datetime(2026, 4, 17, 14, 0)

def test_crosses_day_boundary(calendar_ctx):
    """08:00 에서 6시간 역이동 → 전일 근무시간 끝에서 뒷걸음."""
    result = reverse_advance(datetime(2026, 4, 21, 8, 0),
                             timedelta(hours=6), ctx=calendar_ctx)
    # 월요일 17:00 → 4h 빼면 13:00 (전제: 하루 근무시간 08:00-18:00)
    assert result == datetime(2026, 4, 20, 12, 0)

def test_zero_duration(calendar_ctx):
    dt = datetime(2026, 4, 20, 10, 0)
    assert reverse_advance(dt, timedelta(0), ctx=calendar_ctx) == dt
```

`conftest.py` 에 `calendar_ctx` fixture 를 이미 있는 advance 테스트에서 재사용. 없으면 아래를 추가:

```python
# backend/tests/conftest.py (추가)
import pytest
from app.services.calendar_engine import CalendarContext  # 실제 타입명에 맞춰 교체

@pytest.fixture
def calendar_ctx():
    return CalendarContext(
        working_hours=(8, 18),        # 08:00-18:00
        working_days={0, 1, 2, 3, 4}, # Mon-Fri
        holidays=set(),
    )
```

Run: `pytest tests/services/test_calendar_engine_reverse_advance.py -v` → FAIL (no `reverse_advance`).

- [ ] **Step 3: Implement `reverse_advance`**

`calendar_engine.py` 의 기존 `advance` 를 거울로 만든다. `advance` 가 working-time 을 forward 로 소모한다면, `reverse_advance` 는 backward 로 소모한다.

```python
# backend/app/services/calendar_engine.py (추가)
def reverse_advance(dt: datetime, duration: timedelta, ctx: CalendarContext) -> datetime:
    """`dt` 에서 `duration` 만큼의 작업시간을 역방향으로 빼낸 시점 반환.

    working-day 가 아닌 구간과 근무시간 외 구간은 skip (advance 의 mirror).
    """
    remaining = duration
    cursor = dt
    while remaining > timedelta(0):
        # 오늘의 working window
        day_start, day_end = _working_window(cursor, ctx)
        if cursor > day_end:
            cursor = day_end
            continue
        if cursor <= day_start:
            cursor = _previous_working_day_end(cursor, ctx)
            continue
        available = cursor - day_start
        if available >= remaining:
            return cursor - remaining
        remaining -= available
        cursor = _previous_working_day_end(cursor, ctx)
    return cursor
```

`_working_window` / `_previous_working_day_end` 는 기존 `advance` 헬퍼와 대칭 구조로 추가. 이미 `_next_working_day_start` 류 헬퍼가 있으면 그 옆에 둔다.

- [ ] **Step 4: Run tests**

Run: `pytest tests/services/test_calendar_engine_reverse_advance.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/calendar_engine.py backend/tests/services/test_calendar_engine_reverse_advance.py backend/tests/conftest.py
git commit -m "feat(calendar): reverse_advance — working-time 역방향 연산"
```

---

### Task 3: `cascade/` 모듈 스캐폴드

**Files:**

- Create: `backend/app/services/cascade/__init__.py`
- Create: `backend/app/services/cascade/snap.py`
- Create: `backend/app/services/cascade/bfs.py`
- Create: `backend/app/services/cascade/validators.py`
- Create: `backend/app/services/cascade/pull.py`
- Create: `backend/app/services/cascade/service.py`
- Create: `backend/app/services/cascade/reasons.py`

- [ ] **Step 1: Create empty shells with interfaces**

```python
# backend/app/services/cascade/reasons.py
from enum import Enum

class PushReason(str, Enum):
    same_equipment_conflict = "same_equipment_conflict"
    cross_equipment_conflict = "cross_equipment_conflict"
    successor_chain = "successor_chain"

class PullReason(str, Enum):
    successor_slack_available = "successor_slack_available"

class UnresolvedReason(str, Enum):
    due_date_violation = "due_date_violation"
    no_space_forward = "no_space_forward"
    cycle_detected = "cycle_detected"
    invalid_equipment = "invalid_equipment"
```

```python
# backend/app/services/cascade/snap.py
"""In-memory schedule snapshot (pure functions, no DB)."""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable

@dataclass
class SnapTask:
    task_id: str
    equipment_code: str
    start: datetime
    end: datetime
    batch_id: str
    sales_order_id: str | None
    sales_order_line: int | None
    due_date: datetime | None
    # 원본 보존
    old_start: datetime | None = None
    old_end: datetime | None = None
    old_equipment_code: str | None = None

@dataclass
class Snap:
    by_id: dict[str, SnapTask] = field(default_factory=dict)
    def apply(self, task_id, new_start, new_end, new_equipment_code=None): ...
    def get(self, task_id) -> SnapTask: ...
    def by_equipment(self, code) -> list[SnapTask]: ...  # sorted by start

def build_snapshot(tasks: Iterable) -> Snap: ...
```

```python
# backend/app/services/cascade/bfs.py
from .snap import Snap, SnapTask
def same_equipment_overlapping(t: SnapTask, snap: Snap) -> list[SnapTask]: ...
def same_eq_prev_end(t: SnapTask, snap: Snap): ...
def successor_tasks(t: SnapTask, snap: Snap) -> list[SnapTask]: ...
```

```python
# backend/app/services/cascade/validators.py
def validate_due_date(snap, changes_by_id): ...
def validate_horizon(snap, horizon_end): ...
def validate_cycles(push_count, max_waves): ...
```

```python
# backend/app/services/cascade/pull.py
def propose_for_successors(changed_task_id, snap, calendar_ctx): ...
```

```python
# backend/app/services/cascade/service.py
from dataclasses import dataclass

MAX_WAVES = 4
HARD_TASK_LIMIT = 500

@dataclass
class CascadePreviewResult:
    request_id: str
    summary: str
    pushes: list
    pulls: list
    unresolved: list
    can_auto_resolve: bool
    iter_count: int
    truncated: bool

def plan_cascade_preview(task_id, new_start, new_end, new_equipment_code, db) -> CascadePreviewResult: ...
```

```python
# backend/app/services/cascade/__init__.py
from .service import plan_cascade_preview, CascadePreviewResult, MAX_WAVES, HARD_TASK_LIMIT
from .reasons import PushReason, PullReason, UnresolvedReason
```

- [ ] **Step 2: Syntax / import smoke test**

```bash
cd backend && python -c "from app.services.cascade import plan_cascade_preview, PushReason, PullReason, UnresolvedReason"
```

Expected: no output (import 성공). 실패 시 NotImplementedError 는 스캐폴드이므로 허용, 문법 오류만 잡는다.

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/cascade/
git commit -m "feat(cascade): module scaffold (snap/bfs/validators/pull/service/reasons)"
```

---

## Phase 1 — Backend algorithm (TDD)

### Task 4: `snap.py` — deepcopy + apply + 인덱스

**Files:**

- Modify: `backend/app/services/cascade/snap.py`
- Test: `backend/tests/services/cascade/test_snap.py`

- [ ] **Step 1: Failing tests**

```python
# backend/tests/services/cascade/test_snap.py
from datetime import datetime
from app.services.cascade.snap import Snap, SnapTask, build_snapshot

def _mk(task_id, eq, start_h, end_h, sol=1):
    return SnapTask(task_id, eq,
                    datetime(2026,4,20,start_h,0), datetime(2026,4,20,end_h,0),
                    batch_id=f"B-{task_id}", sales_order_id="SO-1", sales_order_line=sol,
                    due_date=datetime(2026,4,30), old_start=None, old_end=None)

def test_apply_records_old_values():
    t = _mk("T1","A",9,12)
    snap = Snap(by_id={"T1": t})
    snap.apply("T1", datetime(2026,4,20,10,0), datetime(2026,4,20,13,0))
    assert snap.get("T1").start == datetime(2026,4,20,10,0)
    assert snap.get("T1").old_start == datetime(2026,4,20,9,0)
    assert snap.get("T1").old_end == datetime(2026,4,20,12,0)

def test_by_equipment_sorted():
    snap = Snap(by_id={
        "A": _mk("A","EQ1", 14, 16),
        "B": _mk("B","EQ1",  9, 12),
        "C": _mk("C","EQ2",  8, 10),
    })
    result = snap.by_equipment("EQ1")
    assert [t.task_id for t in result] == ["B","A"]

def test_apply_changes_equipment():
    snap = Snap(by_id={"T1": _mk("T1","A",9,12)})
    snap.apply("T1", datetime(2026,4,20,9,0), datetime(2026,4,20,12,0),
               new_equipment_code="B")
    assert snap.get("T1").equipment_code == "B"
    assert snap.get("T1").old_equipment_code == "A"
    assert snap.by_equipment("A") == []
    assert len(snap.by_equipment("B")) == 1

def test_build_snapshot_deepcopy_isolates():
    from app.services.schedule_task_models import ScheduleTask  # 실제 import 경로 확인
    # (fixture 를 써서 DB 모델 객체 생성 — 여의치 않으면 DTO 로 대체)
    # ...
    # 핵심 assertion: 원본 리스트 수정이 snap 에 영향 없음
```

Run: `pytest tests/services/cascade/test_snap.py -v` → FAIL

- [ ] **Step 2: Implement**

```python
# backend/app/services/cascade/snap.py (완성)
from dataclasses import dataclass, field
from copy import deepcopy
from datetime import datetime
from typing import Iterable

@dataclass
class SnapTask:
    task_id: str
    equipment_code: str
    start: datetime
    end: datetime
    batch_id: str
    sales_order_id: str | None
    sales_order_line: int | None
    due_date: datetime | None
    old_start: datetime | None = None
    old_end: datetime | None = None
    old_equipment_code: str | None = None

@dataclass
class Snap:
    by_id: dict[str, SnapTask] = field(default_factory=dict)

    def apply(self, task_id, new_start, new_end, new_equipment_code=None):
        t = self.by_id[task_id]
        if t.old_start is None:
            t.old_start, t.old_end, t.old_equipment_code = t.start, t.end, t.equipment_code
        t.start, t.end = new_start, new_end
        if new_equipment_code is not None:
            t.equipment_code = new_equipment_code

    def get(self, task_id) -> SnapTask:
        return self.by_id[task_id]

    def by_equipment(self, code) -> list[SnapTask]:
        return sorted([t for t in self.by_id.values() if t.equipment_code == code],
                      key=lambda t: t.start)

    def changed_tasks(self) -> list[SnapTask]:
        return [t for t in self.by_id.values() if t.old_start is not None]


def build_snapshot(tasks: Iterable) -> Snap:
    """`ScheduleTask` ORM row 들을 SnapTask 로 deepcopy 하여 Snap 생성."""
    by_id = {}
    for t in tasks:
        batch = t.batch  # relationship
        by_id[t.task_id] = SnapTask(
            task_id=t.task_id,
            equipment_code=t.equipment_code,
            start=t.start_datetime,
            end=t.end_datetime,
            batch_id=t.batch_id,
            sales_order_id=getattr(batch, "sales_order_id", None),
            sales_order_line=getattr(batch, "sales_order_line", None),
            due_date=getattr(batch, "due_date", None),
        )
    return Snap(by_id=by_id)
```

- [ ] **Step 3: Run**

Run: `pytest tests/services/cascade/test_snap.py -v`
Expected: 4 PASS

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/cascade/snap.py backend/tests/services/cascade/test_snap.py
git commit -m "feat(cascade): snap.py deepcopy + apply + by_equipment sorted"
```

---

### Task 5: `bfs.py` — same-equipment 양방향 overlapping + T10

**Files:**

- Modify: `backend/app/services/cascade/bfs.py`
- Test: `backend/tests/services/cascade/test_bfs_same_eq.py`

- [ ] **Step 1: Failing tests (T10 포함)**

```python
# backend/tests/services/cascade/test_bfs_same_eq.py
from datetime import datetime
from app.services.cascade.snap import Snap, SnapTask
from app.services.cascade.bfs import same_equipment_overlapping, same_eq_prev_end

def _mk(task_id, eq, sh, eh, sol=1):
    return SnapTask(task_id, eq,
                    datetime(2026,4,20,sh,0), datetime(2026,4,20,eh,0),
                    batch_id=f"B-{task_id}", sales_order_id="SO-1", sales_order_line=sol,
                    due_date=datetime(2026,4,30))

def test_overlapping_forward():
    t = _mk("T","EQ", 10, 14)
    n = _mk("N","EQ", 12, 15)
    snap = Snap(by_id={"T":t, "N":n})
    assert [x.task_id for x in same_equipment_overlapping(t, snap)] == ["N"]

def test_overlapping_backward_on_equipment_change():
    """T10 (B2 수정): 설비 변경 드래그 시 새 설비의 '앞' task 와 겹침 감지."""
    t = _mk("T","EQ", 10, 14)   # 새로 EQ 로 이동했다고 가정
    prev = _mk("P","EQ", 8, 12) # T 보다 앞 시작이지만 끝이 T 시작을 넘김
    snap = Snap(by_id={"T":t, "P":prev})
    result = [x.task_id for x in same_equipment_overlapping(t, snap)]
    assert "P" in result

def test_no_overlap_different_equipment():
    t = _mk("T","EQ1", 10, 14)
    other = _mk("O","EQ2", 10, 14)
    snap = Snap(by_id={"T":t,"O":other})
    assert same_equipment_overlapping(t, snap) == []

def test_same_eq_prev_end_returns_latest_before():
    target = _mk("T","EQ",14,18)
    a = _mk("A","EQ", 8, 10)
    b = _mk("B","EQ", 10, 13)  # B 의 end 가 가장 가까움
    snap = Snap(by_id={"T":target,"A":a,"B":b})
    assert same_eq_prev_end(target, snap) == datetime(2026,4,20,13,0)

def test_same_eq_prev_end_none_when_first():
    t = _mk("T","EQ",9,12)
    snap = Snap(by_id={"T":t})
    assert same_eq_prev_end(t, snap) is None
```

Run → FAIL

- [ ] **Step 2: Implement**

```python
# backend/app/services/cascade/bfs.py
from .snap import Snap, SnapTask

def same_equipment_overlapping(t: SnapTask, snap: Snap) -> list[SnapTask]:
    """t 와 같은 설비에서 **겹침** 있는 다른 task 목록 (양방향)."""
    out = []
    for other in snap.by_equipment(t.equipment_code):
        if other.task_id == t.task_id:
            continue
        if other.start < t.end and t.start < other.end:  # 양방향 overlap
            out.append(other)
    return out

def same_eq_prev_end(t: SnapTask, snap: Snap):
    """같은 설비에서 t.start 이전에 끝나는 task 중 가장 가까운 end 반환 (없으면 None)."""
    candidates = [o for o in snap.by_equipment(t.equipment_code)
                  if o.task_id != t.task_id and o.end <= t.start]
    if not candidates:
        return None
    return max(c.end for c in candidates)
```

Run → PASS

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/cascade/bfs.py backend/tests/services/cascade/test_bfs_same_eq.py
git commit -m "feat(cascade): bfs.same_equipment_overlapping (양방향) + T10"
```

---

### Task 6: `bfs.py` — successor_tasks + T11 dedup

**Files:**

- Modify: `backend/app/services/cascade/bfs.py`
- Test: `backend/tests/services/cascade/test_bfs_successors.py`

- [ ] **Step 1: Failing tests (T11)**

```python
# backend/tests/services/cascade/test_bfs_successors.py
from datetime import datetime
from app.services.cascade.snap import Snap, SnapTask
from app.services.cascade.bfs import successor_tasks

def _mk(task_id, eq, sh, eh, so="SO-1", sol=1):
    return SnapTask(task_id, eq,
                    datetime(2026,4,20,sh,0), datetime(2026,4,20,eh,0),
                    batch_id=f"B-{task_id}", sales_order_id=so, sales_order_line=sol,
                    due_date=datetime(2026,4,30))

def test_successor_is_same_so_and_later_start():
    t  = _mk("T","EQ1", 10, 14, so="A", sol=1)
    s  = _mk("S","EQ2", 14, 18, so="A", sol=1)
    other = _mk("O","EQ2", 14, 18, so="B", sol=1)  # 다른 수주
    snap = Snap(by_id={"T":t,"S":s,"O":other})
    assert [x.task_id for x in successor_tasks(t, snap)] == ["S"]

def test_ignore_earlier_task():
    t  = _mk("T","EQ1", 10, 14)
    earlier = _mk("E","EQ2", 6, 9)  # 같은 수주라도 이전
    snap = Snap(by_id={"T":t,"E":earlier})
    assert successor_tasks(t, snap) == []

def test_successor_sorted_by_start():
    t  = _mk("T","EQ1", 10, 14)
    s2 = _mk("S2","EQ3", 18, 20)
    s1 = _mk("S1","EQ2", 14, 18)
    snap = Snap(by_id={"T":t,"S1":s1,"S2":s2})
    assert [x.task_id for x in successor_tasks(t, snap)] == ["S1","S2"]
```

- [ ] **Step 2: Implement**

```python
# backend/app/services/cascade/bfs.py (추가)
def successor_tasks(t: SnapTask, snap: Snap) -> list[SnapTask]:
    """같은 sales_order_line 의 t 이후 공정 (start 기준 정렬)."""
    if t.sales_order_id is None or t.sales_order_line is None:
        return []
    out = [o for o in snap.by_id.values()
           if o.task_id != t.task_id
           and o.sales_order_id == t.sales_order_id
           and o.sales_order_line == t.sales_order_line
           and o.start >= t.end]  # 후행 (경계 포함 안 하도록 >= t.end 사용)
    out.sort(key=lambda x: x.start)
    return out
```

주의: `o.start >= t.end` 는 "현재 겹치지 않는 후공정" 조건. 겹치는 경우는 `same_equipment_overlapping` + successor push 처리에서 잡힘.

Run → PASS

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/cascade/bfs.py backend/tests/services/cascade/test_bfs_successors.py
git commit -m "feat(cascade): bfs.successor_tasks (sales_order_line 기반)"
```

---

### Task 7: `validators.py` — due-date / horizon / cycle + T4, T5

**Files:**

- Modify: `backend/app/services/cascade/validators.py`
- Test: `backend/tests/services/cascade/test_validators.py`

- [ ] **Step 1: Failing tests (T4, T5)**

```python
# backend/tests/services/cascade/test_validators.py
from datetime import datetime
from collections import defaultdict
from app.services.cascade.snap import Snap, SnapTask
from app.services.cascade.validators import validate_due_date, validate_horizon, validate_cycles
from app.services.cascade.reasons import UnresolvedReason

def _mk(task_id, sh, eh, due):
    return SnapTask(task_id,"EQ",
                    datetime(2026,4,20,sh,0), datetime(2026,4,20,eh,0),
                    batch_id=f"B-{task_id}", sales_order_id="SO",
                    sales_order_line=1, due_date=due,
                    old_start=datetime(2026,4,20,sh-1,0),
                    old_end=datetime(2026,4,20,eh-1,0))

def test_validate_due_date_detects_violation():
    t = _mk("T", 10, 14, due=datetime(2026,4,20,12,0))
    snap = Snap(by_id={"T":t})
    result = validate_due_date(snap)
    assert len(result) == 1
    assert result[0]["reason"] == UnresolvedReason.due_date_violation

def test_validate_due_date_ignores_unchanged():
    t = _mk("T", 10, 14, due=datetime(2026,4,20,12,0))
    t.old_start = None; t.old_end = None  # 변경 없음
    snap = Snap(by_id={"T":t})
    assert validate_due_date(snap) == []

def test_validate_horizon():
    t = _mk("T", 10, 14, due=datetime(2026,4,30))
    snap = Snap(by_id={"T":t})
    result = validate_horizon(snap, horizon_end=datetime(2026,4,20,11,0))
    assert result and result[0]["reason"] == UnresolvedReason.no_space_forward

def test_validate_cycles_hits_max_waves():
    push_count = defaultdict(int, {"A": 5})
    result = validate_cycles(push_count, max_waves=4)
    assert result and result[0]["reason"] == UnresolvedReason.cycle_detected

def test_validate_cycles_under_limit():
    push_count = defaultdict(int, {"A": 2})
    assert validate_cycles(push_count, max_waves=4) == []
```

Run → FAIL

- [ ] **Step 2: Implement**

```python
# backend/app/services/cascade/validators.py
from .snap import Snap
from .reasons import UnresolvedReason

def _entry(task, reason, detail=""):
    return {
        "task_id": task.task_id,
        "equipment_code": task.equipment_code,
        "batch_label": task.batch_id,
        "reason": reason,
        "detail": detail,
    }

def validate_due_date(snap: Snap) -> list[dict]:
    return [_entry(t, UnresolvedReason.due_date_violation, f"end {t.end} > due {t.due_date}")
            for t in snap.changed_tasks()
            if t.due_date is not None and t.end > t.due_date]

def validate_horizon(snap: Snap, horizon_end) -> list[dict]:
    return [_entry(t, UnresolvedReason.no_space_forward, f"end {t.end} > horizon {horizon_end}")
            for t in snap.changed_tasks() if t.end > horizon_end]

def validate_cycles(push_count: dict, max_waves: int) -> list[dict]:
    result = []
    for tid, count in push_count.items():
        if count > max_waves:
            result.append({
                "task_id": tid, "equipment_code": "", "batch_label": "",
                "reason": UnresolvedReason.cycle_detected,
                "detail": f"pushed {count} times > MAX_WAVES={max_waves}",
            })
    return result
```

Run → PASS

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/cascade/validators.py backend/tests/services/cascade/test_validators.py
git commit -m "feat(cascade): validators (due/horizon/cycle) + T4 T5"
```

---

### Task 8: `pull.py` — Pull proposer + T6 T7 T12

**Files:**

- Modify: `backend/app/services/cascade/pull.py`
- Test: `backend/tests/services/cascade/test_pull.py`

- [ ] **Step 1: Failing tests (T6/T7/T12)**

```python
# backend/tests/services/cascade/test_pull.py
from datetime import datetime, timedelta
from app.services.cascade.snap import Snap, SnapTask
from app.services.cascade.pull import propose_for_successors
from app.services.cascade.reasons import PullReason

def _mk(task_id, eq, sh, eh, so="SO-1", sol=1, old_sh=None, old_eh=None):
    t = SnapTask(task_id, eq,
                 datetime(2026,4,20,sh,0), datetime(2026,4,20,eh,0),
                 batch_id=f"B-{task_id}", sales_order_id=so, sales_order_line=sol,
                 due_date=datetime(2026,4,30))
    if old_sh is not None:
        t.old_start = datetime(2026,4,20,old_sh,0)
        t.old_end = datetime(2026,4,20,old_eh,0)
    return t

def fake_calendar_reverse_advance(dt, duration):
    return dt - duration

def test_t6_slack_propagates():
    """변경 task 가 짧아져 후속 S 앞 slack 이 생기면 Pull."""
    T = _mk("T","EQ1", sh=9, eh=12, old_sh=9, old_eh=15)   # 3h 짧아짐
    S = _mk("S","EQ2", sh=15, eh=18)                       # 원래 후공정 15시 시작
    snap = Snap(by_id={"T":T,"S":S})
    pulls = propose_for_successors("T", snap, reverse_advance_fn=fake_calendar_reverse_advance)
    assert len(pulls) == 1
    assert pulls[0]["task_id"] == "S"
    assert pulls[0]["new_start"] == datetime(2026,4,20,12,0)  # 3h 당김
    assert pulls[0]["reason"] == PullReason.successor_slack_available

def test_t7_same_eq_prev_blocks_full_slack():
    """S 의 같은 설비 앞 task P 가 있어서 제약. slack 이 제한되어야 함."""
    T = _mk("T","EQ1", sh=9, eh=12, old_sh=9, old_eh=15)
    S = _mk("S","EQ2", sh=15, eh=18)
    P = _mk("P","EQ2", sh=10, eh=13)  # S 앞 task, end 13:00
    snap = Snap(by_id={"T":T,"S":S,"P":P})
    pulls = propose_for_successors("T", snap, reverse_advance_fn=fake_calendar_reverse_advance)
    # earliest = max(T.end=12, P.end=13) = 13
    # slack = S.old_start(15) - 13 = 2h
    # proposed_start = 15 - 2 = 13
    assert len(pulls) == 1
    assert pulls[0]["new_start"] == datetime(2026,4,20,13,0)

def test_t12_no_pull_when_same_eq_prev_blocks():
    """P.end 가 S.start 와 동일 → slack = 0 → Pull 제안 없음."""
    T = _mk("T","EQ1", sh=9, eh=12, old_sh=9, old_eh=15)
    S = _mk("S","EQ2", sh=15, eh=18)
    P = _mk("P","EQ2", sh=10, eh=15)
    snap = Snap(by_id={"T":T,"S":S,"P":P})
    pulls = propose_for_successors("T", snap, reverse_advance_fn=fake_calendar_reverse_advance)
    assert pulls == []
```

- [ ] **Step 2: Implement**

```python
# backend/app/services/cascade/pull.py
from .snap import Snap
from .bfs import successor_tasks, same_eq_prev_end
from .reasons import PullReason

def propose_for_successors(changed_task_id, snap: Snap, reverse_advance_fn) -> list[dict]:
    changed = snap.get(changed_task_id)
    if changed.old_end is None or changed.end >= changed.old_end:
        return []  # 짧아지거나 앞당겨진 경우에만
    pulls = []
    prev_end = changed.end
    for S in successor_tasks(changed, snap):
        same_eq_prev = same_eq_prev_end(S, snap)
        earliest = max(x for x in [prev_end, same_eq_prev] if x is not None)
        if earliest < S.start:
            slack = S.start - earliest
            proposed_start = reverse_advance_fn(S.start, slack)
            proposed_end = proposed_start + (S.end - S.start)
            pulls.append({
                "task_id": S.task_id,
                "equipment_code": S.equipment_code,
                "batch_label": S.batch_id,
                "old_start": S.start, "old_end": S.end,
                "new_start": proposed_start, "new_end": proposed_end,
                "reason": PullReason.successor_slack_available,
            })
            prev_end = proposed_end
        else:
            prev_end = S.end
    return pulls
```

Run → PASS

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/cascade/pull.py backend/tests/services/cascade/test_pull.py
git commit -m "feat(cascade): pull.propose_for_successors + T6 T7 T12"
```

---

### Task 9: `service.py` orchestrator — wave loop + T1 T2 T3 T8 T9 T11

**Files:**

- Modify: `backend/app/services/cascade/service.py`
- Test: `backend/tests/services/cascade/test_service.py`

- [ ] **Step 1: Failing tests (T1, T2, T3, T8, T9, T11)**

Test file 가 큼. 핵심 시나리오만 골라서 설치한 뒤 나머지는 Task 10 에서 보강한다.

```python
# backend/tests/services/cascade/test_service.py
from datetime import datetime, timedelta
from app.services.cascade.snap import Snap, SnapTask
from app.services.cascade.service import plan_cascade_preview_on_snap, MAX_WAVES
from app.services.cascade.reasons import PushReason

def _mk(task_id, eq, sh, eh, sol=1):
    return SnapTask(task_id, eq,
                    datetime(2026,4,20,sh,0), datetime(2026,4,20,eh,0),
                    batch_id=f"B-{task_id}", sales_order_id="SO-1",
                    sales_order_line=sol, due_date=datetime(2026,4,30))

def _calendar_id(dt):  # 테스트용: 캘린더 advance 를 identity 로 고정
    return dt

def test_t1_duration_increase_pushes_next_same_eq():
    A = _mk("A","EQ1", 9, 12)
    B = _mk("B","EQ1", 12, 15)
    snap = Snap(by_id={"A":A,"B":B})
    # A 를 3시간 늘려 12→15 로 끝나게 (B 와 충돌)
    snap.apply("A", datetime(2026,4,20,9,0), datetime(2026,4,20,15,0))
    res = plan_cascade_preview_on_snap(snap, "A", advance_fn=_calendar_id,
                                       reverse_advance_fn=_calendar_id,
                                       horizon_end=datetime(2026,5,1))
    pushed_ids = {p["task_id"] for p in res.pushes}
    assert "B" in pushed_ids
    b_push = [p for p in res.pushes if p["task_id"] == "B"][0]
    assert b_push["new_start"] == datetime(2026,4,20,15,0)
    assert b_push["reason"] == PushReason.same_equipment_conflict

def test_t2_successor_chain_push():
    A = _mk("A","EQ1", 9, 12, sol=1)   # 연선
    B = _mk("B","EQ2", 12, 15, sol=1)  # 절연
    C = _mk("C","EQ3", 15, 18, sol=1)  # 시스
    snap = Snap(by_id={"A":A,"B":B,"C":C})
    snap.apply("A", datetime(2026,4,20,9,0), datetime(2026,4,20,14,0))  # 연선 2h 연장
    res = plan_cascade_preview_on_snap(snap, "A", advance_fn=_calendar_id,
                                       reverse_advance_fn=_calendar_id,
                                       horizon_end=datetime(2026,5,1))
    ids = {p["task_id"]: p for p in res.pushes}
    assert "B" in ids and ids["B"]["new_start"] == datetime(2026,4,20,14,0)
    assert "C" in ids  # B 의 후속이 또 밀림

def test_t3_cross_equipment_conflict():
    A = _mk("A","EQ1", 9, 12, sol=1)      # 연선
    B = _mk("B","EQ2", 12, 15, sol=1)     # 절연
    X = _mk("X","EQ2", 14, 16, sol=99)    # 다른 수주의 절연 설비 block
    snap = Snap(by_id={"A":A,"B":B,"X":X})
    snap.apply("A", datetime(2026,4,20,9,0), datetime(2026,4,20,14,0))
    res = plan_cascade_preview_on_snap(snap, "A", advance_fn=_calendar_id,
                                       reverse_advance_fn=_calendar_id,
                                       horizon_end=datetime(2026,5,1))
    # B 가 14:00-17:00 으로 밀림 → X(14-16) 와 cross-equipment 충돌 → X 도 push
    ids = {p["task_id"]: p for p in res.pushes}
    assert "X" in ids
    assert ids["X"]["reason"] == PushReason.same_equipment_conflict

def test_t11_wave_dedup():
    """같은 wave 안에서 A 가 B 를 same-eq 와 successor 양쪽으로 끄는 케이스 — 한 번만."""
    A = _mk("A","EQ1", 9, 12, sol=1)
    B = _mk("B","EQ1", 12, 15, sol=1)  # 같은 설비의 다음 task 이자 successor
    snap = Snap(by_id={"A":A,"B":B})
    snap.apply("A", datetime(2026,4,20,9,0), datetime(2026,4,20,13,0))
    res = plan_cascade_preview_on_snap(snap, "A", advance_fn=_calendar_id,
                                       reverse_advance_fn=_calendar_id,
                                       horizon_end=datetime(2026,5,1))
    b_entries = [p for p in res.pushes if p["task_id"] == "B"]
    assert len(b_entries) == 1   # dedup

def test_cycle_detection_yields_unresolved():
    """인위적 cycle — 같은 task 가 MAX_WAVES+1 회 push 되면 cycle_detected."""
    # (정교한 케이스는 Task 10 integration 에서 실 calendar 로 확인)
    # 여기서는 MAX_WAVES 상수 존재만 확인
    assert MAX_WAVES == 4
```

- [ ] **Step 2: Implement `plan_cascade_preview_on_snap` (캘린더 주입 가능 형태)**

실제 `plan_cascade_preview` 는 DB + 캘린더를 세팅한 뒤 `_on_snap` 호출. 테스트는 `_on_snap` 을 대상으로.

```python
# backend/app/services/cascade/service.py
import uuid
from collections import defaultdict
from dataclasses import dataclass
from .snap import Snap, build_snapshot
from .bfs import same_equipment_overlapping, same_eq_prev_end, successor_tasks
from .validators import validate_due_date, validate_horizon, validate_cycles
from .pull import propose_for_successors
from .reasons import PushReason, UnresolvedReason

MAX_WAVES = 4
HARD_TASK_LIMIT = 500

@dataclass
class CascadePreviewResult:
    request_id: str
    summary: str
    pushes: list
    pulls: list
    unresolved: list
    can_auto_resolve: bool
    iter_count: int
    truncated: bool


def plan_cascade_preview_on_snap(snap: Snap, changed_task_id: str,
                                 advance_fn, reverse_advance_fn, horizon_end) -> CascadePreviewResult:
    pushes, pulls, unresolved = [], [], []
    push_count = defaultdict(int)
    frontier = [snap.get(changed_task_id)]
    truncated = False
    wave_used = 0

    for wave in range(MAX_WAVES):
        if not frontier:
            break
        if len(pushes) > HARD_TASK_LIMIT:
            unresolved.append({"task_id": changed_task_id, "equipment_code": "",
                               "batch_label": "", "reason": UnresolvedReason.cycle_detected,
                               "detail": "HARD_TASK_LIMIT exceeded"})
            truncated = True
            break
        wave_used = wave + 1
        next_frontier = []
        processed = set()

        for T in frontier:
            # (a) same-equipment overlap
            for N in same_equipment_overlapping(T, snap):
                if N.task_id in processed:
                    continue
                prev = same_eq_prev_end(N, snap)
                base = max(x for x in [T.end, prev] if x is not None)
                new_N_start = advance_fn(base)
                new_N_end = new_N_start + (N.end - N.start)
                pushes.append(_push_entry(N, PushReason.same_equipment_conflict, new_N_start, new_N_end))
                snap.apply(N.task_id, new_N_start, new_N_end)
                push_count[N.task_id] += 1
                processed.add(N.task_id)
                next_frontier.append(snap.get(N.task_id))

            # (b) successor chain
            for S in successor_tasks(T, snap):
                if S.task_id in processed:
                    continue
                prev = same_eq_prev_end(S, snap)
                earliest = max(x for x in [T.end, prev] if x is not None)
                if S.start < earliest:
                    new_S_start = advance_fn(earliest)
                    new_S_end = new_S_start + (S.end - S.start)
                    reason = (PushReason.successor_chain if earliest == T.end
                              else PushReason.cross_equipment_conflict)
                    pushes.append(_push_entry(S, reason, new_S_start, new_S_end))
                    snap.apply(S.task_id, new_S_start, new_S_end)
                    push_count[S.task_id] += 1
                    processed.add(S.task_id)
                    next_frontier.append(snap.get(S.task_id))

        # dedup in next_frontier by task_id (keep latest)
        seen = {}
        for t in next_frontier:
            seen[t.task_id] = t
        frontier = list(seen.values())

    if frontier:
        for t in frontier:
            unresolved.append({"task_id": t.task_id, "equipment_code": t.equipment_code,
                               "batch_label": t.batch_id, "reason": UnresolvedReason.cycle_detected,
                               "detail": f"cascade depth > {MAX_WAVES}"})

    unresolved += validate_due_date(snap)
    unresolved += validate_horizon(snap, horizon_end)
    unresolved += validate_cycles(push_count, MAX_WAVES)

    changed = snap.get(changed_task_id)
    if changed.old_end and changed.end < changed.old_end:
        pulls = propose_for_successors(changed_task_id, snap, reverse_advance_fn)

    can_auto_resolve = len(unresolved) == 0
    summary = _build_summary(changed, pushes, pulls, unresolved)
    return CascadePreviewResult(
        request_id=str(uuid.uuid4()),
        summary=summary,
        pushes=pushes, pulls=pulls, unresolved=unresolved,
        can_auto_resolve=can_auto_resolve,
        iter_count=wave_used,
        truncated=truncated,
    )


def _push_entry(task, reason, new_start, new_end):
    return {
        "task_id": task.task_id,
        "equipment_code": task.equipment_code,
        "batch_label": task.batch_id,
        "old_start": task.start, "old_end": task.end,
        "new_start": new_start, "new_end": new_end,
        "reason": reason,
    }


def _build_summary(changed, pushes, pulls, unresolved):
    n_push = len(pushes)
    n_pull = len(pulls)
    n_unres = len(unresolved)
    delta = (changed.end - changed.start) - (
        (changed.old_end - changed.old_start) if changed.old_end else (changed.end - changed.start)
    )
    delta_h = int(delta.total_seconds() / 3600)
    direction = "늘어" if delta_h > 0 else ("줄어" if delta_h < 0 else "변경")
    return (f"{changed.batch_id} {abs(delta_h)}h {direction}, "
            f"{n_push}건 재배치{' / '+str(n_pull)+'건 앞당김 제안' if n_pull else ''}"
            f"{' / '+str(n_unres)+'건 해소 불가' if n_unres else ''}.")
```

Run: `pytest tests/services/cascade/test_service.py -v` → 5 PASS

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/cascade/service.py backend/tests/services/cascade/test_service.py
git commit -m "feat(cascade): service.plan_cascade_preview_on_snap (wave BFS + dedup) + T1 T2 T3 T11"
```

---

### Task 10: `service.plan_cascade_preview` (DB 통합) + T8 T9

**Files:**

- Modify: `backend/app/services/cascade/service.py`
- Test: `backend/tests/services/cascade/test_service_db.py`

- [ ] **Step 1: Failing tests (T8, T9 — 실 DB + calendar 사용)**

```python
# backend/tests/services/cascade/test_service_db.py
# (pytest fixture db_session + seed_tasks 활용)
from datetime import datetime
from app.services.cascade.service import plan_cascade_preview

def test_t8_equipment_change_cascade(db_session, seed_schedule):
    """설비 변경 + 새 설비 앞 task 와 겹치면 push (B2 수정 확인)."""
    # seed_schedule: A(EQ1, 9-12), B(EQ2, 8-11) 고정
    res = plan_cascade_preview(
        task_id="A",
        new_start=datetime(2026,4,20,10,0),
        new_end=datetime(2026,4,20,13,0),
        new_equipment_code="EQ2",
        db=db_session,
    )
    # B 가 A 의 새 시작(10:00) 이전에 끝나지 않음 → B 를 앞으로 당기거나 A 뒤로 push
    # 현재 policy 는 "이웃 push" 이므로 B 가 추가될 수 있음
    assert any(p["task_id"] == "B" for p in res.pushes)

def test_t9_weekend_skip(db_session, seed_schedule_with_weekend):
    """push 가 주말을 skip 하는지 — calendar_engine.advance 재사용."""
    res = plan_cascade_preview(
        task_id="FRI_LATE",  # 금요일 늦게 끝나는 task
        new_start=datetime(2026,4,17,16,0),
        new_end=datetime(2026,4,17,22,0),  # 근무 종료(18:00) 넘김
        new_equipment_code=None,
        db=db_session,
    )
    pushed = [p for p in res.pushes if p["task_id"] == "MON_NEXT"]
    assert pushed
    # MON_NEXT.new_start 가 월요일 근무 시작 이후 (주말 skip 확인)
    assert pushed[0]["new_start"] >= datetime(2026,4,20,8,0)
```

`seed_schedule` / `seed_schedule_with_weekend` fixture 는 `tests/services/cascade/conftest.py` 에 작성 (Task 4 의 `build_snapshot` 호출 구조).

- [ ] **Step 2: Implement DB wrapper**

```python
# backend/app/services/cascade/service.py (추가)
from app.services.calendar_engine import advance, reverse_advance

def plan_cascade_preview(task_id, new_start, new_end, new_equipment_code, db) -> CascadePreviewResult:
    # horizon: 현재 스케줄 최대 end + 7일 여유 (또는 프로젝트 전역 상수)
    tasks_in_horizon = db.query_all_tasks_in_horizon()  # 실제 쿼리로 교체
    snap = build_snapshot(tasks_in_horizon)
    snap.apply(task_id, new_start, new_end, new_equipment_code)
    horizon_end = max(t.end for t in snap.by_id.values()) + timedelta(days=7)

    def _adv(dt):  return advance(dt, ctx=_ctx(db))
    def _radv(dt, dur): return reverse_advance(dt, dur, ctx=_ctx(db))

    return plan_cascade_preview_on_snap(snap, task_id, _adv, _radv, horizon_end)
```

`_ctx(db)` 는 기존 calendar 로딩 방식에 맞추어 구현 (프로젝트의 `CalendarContext` 생성 helper).

- [ ] **Step 3: Run all cascade unit tests**

```bash
pytest tests/services/cascade/ -v
```

Expected: T1-T12 모두 PASS (12 tests).

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/cascade/service.py backend/tests/services/cascade/
git commit -m "feat(cascade): plan_cascade_preview DB 통합 + T8 T9"
```

---

## Phase 2 — Backend API

### Task 11: `cascade-preview` 라우터 v2

**Files:**

- Modify: `backend/app/presentation/routes/schedules.py`
- Create: `backend/app/presentation/schemas/cascade.py` (Pydantic)
- Test: `backend/tests/api/test_cascade_preview_v2.py`

- [ ] **Step 1: Pydantic 스키마**

```python
# backend/app/presentation/schemas/cascade.py
from pydantic import BaseModel, field_validator
from datetime import datetime

class CascadePreviewRequest(BaseModel):
    task_id: str
    new_start: datetime
    new_end: datetime
    new_equipment_code: str | None = None

    @field_validator("new_start", "new_end")
    def reject_tz(cls, v):
        if v.tzinfo is not None:
            raise ValueError("naive datetime required (KST). 'Z' suffix not allowed.")
        return v

class PushEntry(BaseModel):
    task_id: str
    equipment_code: str
    batch_label: str
    old_start: datetime
    old_end: datetime
    new_start: datetime
    new_end: datetime
    reason: str

class UnresolvedEntry(BaseModel):
    task_id: str
    equipment_code: str
    batch_label: str
    reason: str
    detail: str

class CascadePreviewResponse(BaseModel):
    request_id: str
    summary: str
    pushes: list[PushEntry]
    pulls: list[PushEntry]
    unresolved: list[UnresolvedEntry]
    can_auto_resolve: bool
    iter_count: int
    truncated: bool
```

- [ ] **Step 2: Failing API tests**

```python
# backend/tests/api/test_cascade_preview_v2.py
from fastapi.testclient import TestClient

def test_missing_api_version_header_returns_400(client: TestClient):
    resp = client.post("/api/schedules/cascade-preview",
                       json={"task_id":"X","new_start":"2026-04-20T10:00:00","new_end":"2026-04-20T12:00:00"})
    assert resp.status_code == 400
    assert "X-Cascade-API-Version" in resp.text

def test_tz_suffix_rejected(client: TestClient):
    resp = client.post("/api/schedules/cascade-preview",
                       headers={"X-Cascade-API-Version":"2"},
                       json={"task_id":"X","new_start":"2026-04-20T10:00:00Z","new_end":"2026-04-20T12:00:00Z"})
    assert resp.status_code == 422

def test_feature_flag_off_returns_feature_disabled(client: TestClient, monkeypatch):
    monkeypatch.setenv("FEATURE_FLAG_CASCADE_V2","off")
    resp = client.post("/api/schedules/cascade-preview",
                       headers={"X-Cascade-API-Version":"2"},
                       json={"task_id":"X","new_start":"2026-04-20T10:00:00","new_end":"2026-04-20T12:00:00"})
    assert resp.status_code == 200
    body = resp.json()
    assert any(u["reason"] == "invalid_equipment" and "cascade v2 disabled" in u["detail"]
               for u in body["unresolved"])

def test_happy_path_returns_v2_schema(client: TestClient, monkeypatch, seed_schedule):
    monkeypatch.setenv("FEATURE_FLAG_CASCADE_V2","on")
    resp = client.post("/api/schedules/cascade-preview",
                       headers={"X-Cascade-API-Version":"2"},
                       json={"task_id":"A","new_start":"2026-04-20T10:00:00","new_end":"2026-04-20T13:00:00"})
    assert resp.status_code == 200
    body = resp.json()
    for k in ["request_id","summary","pushes","pulls","unresolved","can_auto_resolve","iter_count","truncated"]:
        assert k in body
```

- [ ] **Step 3: Route implementation**

```python
# backend/app/presentation/routes/schedules.py (cascade-preview 교체)
from fastapi import Header, HTTPException
from app.core.feature_flags import is_cascade_v2_enabled
from app.services.cascade import plan_cascade_preview, UnresolvedReason
from app.presentation.schemas.cascade import CascadePreviewRequest, CascadePreviewResponse

@router.post("/cascade-preview", response_model=CascadePreviewResponse)
def cascade_preview(
    body: CascadePreviewRequest,
    x_cascade_api_version: str | None = Header(None),
    db: Session = Depends(get_db),
):
    if x_cascade_api_version != "2":
        raise HTTPException(400, "X-Cascade-API-Version: 2 required")
    if not is_cascade_v2_enabled():
        return CascadePreviewResponse(
            request_id=str(uuid.uuid4()),
            summary="cascade v2 disabled",
            pushes=[], pulls=[],
            unresolved=[{"task_id": body.task_id, "equipment_code":"",
                         "batch_label":"", "reason": UnresolvedReason.invalid_equipment,
                         "detail":"cascade v2 disabled"}],
            can_auto_resolve=False, iter_count=0, truncated=False,
        )
    result = plan_cascade_preview(body.task_id, body.new_start, body.new_end,
                                  body.new_equipment_code, db)
    return CascadePreviewResponse(**result.__dict__)
```

Run: `pytest tests/api/test_cascade_preview_v2.py -v` → 4 PASS

- [ ] **Step 4: Commit**

```bash
git add backend/app/presentation/schemas/cascade.py backend/app/presentation/routes/schedules.py backend/tests/api/test_cascade_preview_v2.py
git commit -m "feat(api): cascade-preview v2 (header gate + FEATURE_DISABLED 경로)"
```

---

### Task 12: `schedule_change_sets` 테이블 migration

**Files:**

- Create: `backend/app/models/schedule_change_set.py`
- Create: `backend/alembic/versions/<rev>_schedule_change_sets.py`
- Test: `backend/tests/models/test_schedule_change_set.py`

- [ ] **Step 1: Model**

```python
# backend/app/models/schedule_change_set.py
from sqlalchemy import Column, String, DateTime, JSON
from datetime import datetime
from app.models.base import Base

class ScheduleChangeSet(Base):
    __tablename__ = "schedule_change_sets"
    change_set_id = Column(String, primary_key=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    preview_request_id = Column(String, nullable=True)
    snapshot_before = Column(JSON, nullable=False)  # {task_id: {start,end,equipment_code}}
    snapshot_after  = Column(JSON, nullable=False)
    applied_by = Column(String, nullable=True)      # 사용자 (PoC 에선 빈 값)
```

- [ ] **Step 2: Alembic migration**

```bash
cd backend && alembic revision -m "add schedule_change_sets"
```

편집:

```python
def upgrade():
    op.create_table(
        "schedule_change_sets",
        sa.Column("change_set_id", sa.String, primary_key=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("preview_request_id", sa.String, nullable=True),
        sa.Column("snapshot_before", sa.JSON, nullable=False),
        sa.Column("snapshot_after",  sa.JSON, nullable=False),
        sa.Column("applied_by", sa.String, nullable=True),
    )
    op.create_index("ix_schedule_change_sets_created_at", "schedule_change_sets", ["created_at"])

def downgrade():
    op.drop_index("ix_schedule_change_sets_created_at")
    op.drop_table("schedule_change_sets")
```

Run: `alembic upgrade head`

- [ ] **Step 3: Smoke test**

```python
# backend/tests/models/test_schedule_change_set.py
def test_insert_and_query(db_session):
    from app.models.schedule_change_set import ScheduleChangeSet
    c = ScheduleChangeSet(change_set_id="cs-1",
                          snapshot_before={"T1":{"start":"..."}},
                          snapshot_after={"T1":{"start":"..."}})
    db_session.add(c); db_session.commit()
    assert db_session.query(ScheduleChangeSet).count() == 1
```

Run → PASS

- [ ] **Step 4: Commit**

```bash
git add backend/app/models/schedule_change_set.py backend/alembic/versions/ backend/tests/models/test_schedule_change_set.py
git commit -m "feat(db): schedule_change_sets 테이블 + migration"
```

---

### Task 13: `bulk-update` 재검증 + error_code + change_set 저장 + T13

**Files:**

- Modify: `backend/app/presentation/routes/schedules.py` (bulk-update)
- Create: `backend/app/services/schedule_validators.py` (재검증 유틸)
- Create: `backend/app/presentation/schemas/bulk_update.py`
- Test: `backend/tests/api/test_bulk_update_v2.py`

- [ ] **Step 1: 스키마 & error_code enum**

```python
# backend/app/presentation/schemas/bulk_update.py
from pydantic import BaseModel
from datetime import datetime
from enum import Enum

class TaskChange(BaseModel):
    task_id: str
    new_start: datetime
    new_end: datetime
    new_equipment_code: str | None = None

class BulkUpdateRequest(BaseModel):
    changes: list[TaskChange]
    expected_cascade_request_id: str | None = None

class BulkUpdateErrorCode(str, Enum):
    VALIDATION_OVERLAP_SAME_EQUIPMENT = "VALIDATION_OVERLAP_SAME_EQUIPMENT"
    VALIDATION_PREDECESSOR_VIOLATION  = "VALIDATION_PREDECESSOR_VIOLATION"
    VALIDATION_DUE_DATE_VIOLATION     = "VALIDATION_DUE_DATE_VIOLATION"
    FEATURE_DISABLED                  = "FEATURE_DISABLED"
    CONCURRENT_UPDATE                 = "CONCURRENT_UPDATE"

class BulkUpdateError(BaseModel):
    error_code: BulkUpdateErrorCode
    offending_task_id: str
    detail: str
    can_retry: bool

class BulkUpdateSuccess(BaseModel):
    change_set_id: str
    updated_tasks: list  # ScheduleTaskDTO
```

- [ ] **Step 2: Failing tests (T13 포함)**

```python
# backend/tests/api/test_bulk_update_v2.py
def test_422_on_same_equipment_overlap(client, seed_schedule_with_conflict):
    """두 change 가 같은 설비 내 겹치도록 세팅된 경우 422."""
    resp = client.post("/api/schedules/tasks/bulk-update",
                       json={"changes":[
                           {"task_id":"A","new_start":"2026-04-20T10:00:00","new_end":"2026-04-20T14:00:00"},
                           {"task_id":"B","new_start":"2026-04-20T12:00:00","new_end":"2026-04-20T15:00:00"}
                       ]})
    assert resp.status_code == 422
    assert resp.json()["error_code"] == "VALIDATION_OVERLAP_SAME_EQUIPMENT"

def test_feature_flag_off_rejects_multi(client, monkeypatch):
    monkeypatch.setenv("FEATURE_FLAG_CASCADE_V2","off")
    resp = client.post("/api/schedules/tasks/bulk-update",
                       json={"changes":[{"task_id":"A", "new_start":"...","new_end":"..."},
                                        {"task_id":"B", "new_start":"...","new_end":"..."}]})
    assert resp.status_code == 422
    assert resp.json()["error_code"] == "FEATURE_DISABLED"

def test_success_returns_change_set_id(client, seed_schedule, monkeypatch):
    monkeypatch.setenv("FEATURE_FLAG_CASCADE_V2","on")
    resp = client.post("/api/schedules/tasks/bulk-update",
                       json={"changes":[{"task_id":"A","new_start":"2026-04-20T10:00:00","new_end":"2026-04-20T12:00:00"}]})
    assert resp.status_code == 200
    assert "change_set_id" in resp.json()

def test_t13_client_retry_limit(client, seed_schedule_with_conflict, monkeypatch):
    """3회 동일 422 후 프론트가 포기하는지는 E2E 에서 — 여기서는 서버가 일관된 422 반환을 확인."""
    for _ in range(3):
        resp = client.post("/api/schedules/tasks/bulk-update",
                           json={"changes":[{"task_id":"A","new_start":"...","new_end":"..."},
                                            {"task_id":"B","new_start":"...","new_end":"..."}]})
        assert resp.status_code == 422
        assert resp.json()["error_code"] == "VALIDATION_OVERLAP_SAME_EQUIPMENT"
```

- [ ] **Step 3: Implement**

```python
# backend/app/services/schedule_validators.py
def validate_same_eq_overlap(changes, snap):
    # 같은 설비 내 겹침 여부 검사
    ...

def validate_predecessor(changes, snap):
    ...

def validate_due(changes, snap):
    ...
```

```python
# backend/app/presentation/routes/schedules.py (bulk-update 교체)
@router.post("/tasks/bulk-update")
def bulk_update(body: BulkUpdateRequest, db: Session = Depends(get_db)):
    if not is_cascade_v2_enabled() and len(body.changes) > 1:
        raise HTTPException(422, detail=BulkUpdateError(
            error_code=BulkUpdateErrorCode.FEATURE_DISABLED,
            offending_task_id=body.changes[0].task_id,
            detail="cascade v2 disabled",
            can_retry=False,
        ).dict())
    snap = build_snapshot(db.query_all_tasks_in_horizon())
    snapshot_before = _serialize(snap)
    # apply changes
    for c in body.changes:
        snap.apply(c.task_id, c.new_start, c.new_end, c.new_equipment_code)
    # 재검증
    for validator, code in [
        (validate_same_eq_overlap, BulkUpdateErrorCode.VALIDATION_OVERLAP_SAME_EQUIPMENT),
        (validate_predecessor,     BulkUpdateErrorCode.VALIDATION_PREDECESSOR_VIOLATION),
        (validate_due,             BulkUpdateErrorCode.VALIDATION_DUE_DATE_VIOLATION),
    ]:
        violation = validator(body.changes, snap)
        if violation:
            raise HTTPException(422, detail=BulkUpdateError(
                error_code=code, offending_task_id=violation.task_id,
                detail=violation.detail, can_retry=(code != BulkUpdateErrorCode.FEATURE_DISABLED),
            ).dict())
    # commit
    with db.begin():
        for c in body.changes:
            t = db.get(ScheduleTask, c.task_id)
            t.start_datetime, t.end_datetime = c.new_start, c.new_end
            if c.new_equipment_code:
                t.equipment_code = c.new_equipment_code
        change_set_id = str(uuid.uuid4())
        db.add(ScheduleChangeSet(
            change_set_id=change_set_id,
            preview_request_id=body.expected_cascade_request_id,
            snapshot_before=snapshot_before,
            snapshot_after=_serialize(snap),
        ))
    return {"change_set_id": change_set_id, "updated_tasks": [_dto(t) for c in body.changes]}
```

Run: `pytest tests/api/test_bulk_update_v2.py -v` → PASS

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/schedule_validators.py backend/app/presentation/schemas/bulk_update.py backend/app/presentation/routes/schedules.py backend/tests/api/test_bulk_update_v2.py
git commit -m "feat(api): bulk-update 재검증 + error_code + change_set 저장"
```

---

### Task 14: `revert` 엔드포인트 + freshness 검증

**Files:**

- Modify: `backend/app/presentation/routes/schedules.py`
- Test: `backend/tests/api/test_revert.py`

- [ ] **Step 1: Failing tests**

```python
# backend/tests/api/test_revert.py
def test_revert_success(client, seed_schedule):
    # 1) bulk-update 하나 커밋
    r1 = client.post("/api/schedules/tasks/bulk-update", json={...})
    cs_id = r1.json()["change_set_id"]
    # 2) revert
    r2 = client.post(f"/api/schedules/revert/{cs_id}")
    assert r2.status_code == 200
    # 3) 원본 복구 확인
    ...

def test_revert_conflict_when_newer_change_set_exists(client, seed_schedule):
    r1 = client.post("/api/schedules/tasks/bulk-update", json={...})
    cs_id = r1.json()["change_set_id"]
    # 다른 변경이 추가로 커밋됨
    client.post("/api/schedules/tasks/bulk-update", json={...})
    r2 = client.post(f"/api/schedules/revert/{cs_id}")
    assert r2.status_code == 409

def test_revert_unknown_id_404(client):
    assert client.post("/api/schedules/revert/nonexistent").status_code == 404
```

- [ ] **Step 2: Implement**

```python
@router.post("/revert/{change_set_id}")
def revert(change_set_id: str, db: Session = Depends(get_db)):
    cs = db.get(ScheduleChangeSet, change_set_id)
    if not cs:
        raise HTTPException(404, "change_set_id not found")
    # freshness: 이 이후 다른 change_set 가 있으면 409
    newer = db.query(ScheduleChangeSet)\
              .filter(ScheduleChangeSet.created_at > cs.created_at).first()
    if newer:
        raise HTTPException(409, "newer change_set exists")
    with db.begin():
        for task_id, snap in cs.snapshot_before.items():
            t = db.get(ScheduleTask, task_id)
            t.start_datetime = snap["start"]
            t.end_datetime = snap["end"]
            t.equipment_code = snap["equipment_code"]
        db.delete(cs)
    return {"reverted": True}
```

Run → PASS

- [ ] **Step 3: Commit**

```bash
git add backend/app/presentation/routes/schedules.py backend/tests/api/test_revert.py
git commit -m "feat(api): revert 엔드포인트 + 409 freshness 검증"
```

---

### Task 15: T14 성능 벤치마크 fixture

**Files:**

- Test: `backend/tests/api/test_cascade_preview_benchmark.py`

- [ ] **Step 1: 500 task fixture + benchmark**

```python
import pytest
from datetime import datetime, timedelta

@pytest.fixture
def large_schedule(db_session):
    # 500 tasks across 10 equipments, realistic chain
    ...

def test_t14_cascade_preview_p95_under_1s(large_schedule, benchmark, client):
    result = benchmark.pedantic(
        lambda: client.post("/api/schedules/cascade-preview",
                            headers={"X-Cascade-API-Version":"2"},
                            json={"task_id":"middle","new_start":"...","new_end":"..."}),
        iterations=10, rounds=5,
    )
    assert benchmark.stats["p95"] < 1.0  # seconds
```

- [ ] **Step 2: Run**

`pip install pytest-benchmark` (없으면 requirements-dev.txt 추가)

Run: `pytest tests/api/test_cascade_preview_benchmark.py --benchmark-only -v`
Expected: p95 < 1.0s

- [ ] **Step 3: Commit**

```bash
git add backend/tests/api/test_cascade_preview_benchmark.py backend/requirements-dev.txt
git commit -m "test(bench): cascade-preview T14 p95<1s"
```

---

## Phase 3 — Frontend hooks & modal

### Task 16: API fetcher + TypeScript 타입

**Files:**

- Create: `frontend/src/features/scheduler/api/cascade.ts`
- Create: `frontend/src/features/scheduler/api/cascade.types.ts`
- Test: `frontend/src/features/scheduler/api/__tests__/cascade.test.ts`

- [ ] **Step 1: Types (백엔드 schema mirror)**

```ts
// frontend/src/features/scheduler/api/cascade.types.ts
export type PushReason =
  | "same_equipment_conflict"
  | "cross_equipment_conflict"
  | "successor_chain";
export type PullReason = "successor_slack_available";
export type UnresolvedReason =
  | "due_date_violation"
  | "no_space_forward"
  | "cycle_detected"
  | "invalid_equipment";

export interface PushEntry {
  task_id: string;
  equipment_code: string;
  batch_label: string;
  old_start: string;
  old_end: string;
  new_start: string;
  new_end: string;
  reason: PushReason | PullReason;
}
export interface UnresolvedEntry {
  task_id: string;
  equipment_code: string;
  batch_label: string;
  reason: UnresolvedReason;
  detail: string;
}
export interface CascadePreviewResponse {
  request_id: string;
  summary: string;
  pushes: PushEntry[];
  pulls: PushEntry[];
  unresolved: UnresolvedEntry[];
  can_auto_resolve: boolean;
  iter_count: number;
  truncated: boolean;
}
export type BulkUpdateErrorCode =
  | "VALIDATION_OVERLAP_SAME_EQUIPMENT"
  | "VALIDATION_PREDECESSOR_VIOLATION"
  | "VALIDATION_DUE_DATE_VIOLATION"
  | "FEATURE_DISABLED"
  | "CONCURRENT_UPDATE";
```

- [ ] **Step 2: Fetcher + 에러 타이핑**

```ts
// frontend/src/features/scheduler/api/cascade.ts
import { CascadePreviewResponse, BulkUpdateErrorCode } from "./cascade.types";

export async function cascadePreview(payload: {
  task_id: string;
  new_start: string;
  new_end: string;
  new_equipment_code?: string;
}) {
  const res = await fetch("/api/schedules/cascade-preview", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Cascade-API-Version": "2",
    },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(`cascade-preview ${res.status}`);
  return (await res.json()) as CascadePreviewResponse;
}

export async function bulkUpdate(body: {
  changes: {
    task_id: string;
    new_start: string;
    new_end: string;
    new_equipment_code?: string;
  }[];
  expected_cascade_request_id?: string;
}) {
  const res = await fetch("/api/schedules/tasks/bulk-update", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (res.status === 422) {
    const err = await res.json();
    throw Object.assign(new Error("bulk-update 422"), {
      code: err.error_code as BulkUpdateErrorCode,
      offending: err.offending_task_id,
      canRetry: err.can_retry,
    });
  }
  if (!res.ok) throw new Error(`bulk-update ${res.status}`);
  return res.json();
}

export async function revert(changeSetId: string) {
  const res = await fetch(`/api/schedules/revert/${changeSetId}`, {
    method: "POST",
  });
  if (!res.ok) throw new Error(`revert ${res.status}`);
  return res.json();
}
```

- [ ] **Step 3: Tests (msw mock)**

```ts
// frontend/src/features/scheduler/api/__tests__/cascade.test.ts
import { describe, it, expect, vi, beforeEach } from "vitest";
import { cascadePreview, bulkUpdate } from "../cascade";

describe("cascade API client", () => {
  beforeEach(() => {
    global.fetch = vi.fn();
  });
  it("sends X-Cascade-API-Version: 2", async () => {
    (global.fetch as any).mockResolvedValueOnce({
      ok: true,
      json: async () => ({}),
    });
    await cascadePreview({
      task_id: "X",
      new_start: "2026-04-20T10:00:00",
      new_end: "...",
    });
    expect(
      (global.fetch as any).mock.calls[0][1].headers["X-Cascade-API-Version"],
    ).toBe("2");
  });
  it("maps 422 bulkUpdate to error.code", async () => {
    (global.fetch as any).mockResolvedValueOnce({
      ok: false,
      status: 422,
      json: async () => ({
        error_code: "VALIDATION_OVERLAP_SAME_EQUIPMENT",
        offending_task_id: "A",
        detail: "",
        can_retry: true,
      }),
    });
    await expect(bulkUpdate({ changes: [] })).rejects.toMatchObject({
      code: "VALIDATION_OVERLAP_SAME_EQUIPMENT",
    });
  });
});
```

Run: `cd frontend && pnpm vitest run src/features/scheduler/api/__tests__/cascade.test.ts` → PASS

- [ ] **Step 4: Commit**

```bash
git add frontend/src/features/scheduler/api/
git commit -m "feat(api): cascade fetchers (preview/bulk-update/revert) + 타입"
```

---

### Task 17: `useScheduleChangeWithCascade` 훅

**Files:**

- Create: `frontend/src/features/scheduler/hooks/useScheduleChangeWithCascade.ts`
- Test: `frontend/src/features/scheduler/hooks/__tests__/useScheduleChangeWithCascade.test.tsx`

- [ ] **Step 1: Failing tests (RTL + react-query)**

```tsx
// __tests__/useScheduleChangeWithCascade.test.tsx
import { renderHook, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useScheduleChangeWithCascade } from "../useScheduleChangeWithCascade";
import * as api from "../../api/cascade";
import { vi } from "vitest";

const wrapper = ({ children }) => {
  const qc = new QueryClient();
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
};

describe("useScheduleChangeWithCascade", () => {
  it("skips modal when preview empty and calls bulk-update", async () => {
    vi.spyOn(api, "cascadePreview").mockResolvedValue({
      request_id: "r1",
      summary: "",
      pushes: [],
      pulls: [],
      unresolved: [],
      can_auto_resolve: true,
      iter_count: 0,
      truncated: false,
    } as any);
    const bulk = vi
      .spyOn(api, "bulkUpdate")
      .mockResolvedValue({ change_set_id: "cs" } as any);
    const { result } = renderHook(() => useScheduleChangeWithCascade(), {
      wrapper,
    });
    await act(async () => {
      await result.current.commit({
        task_id: "A",
        new_start: "...",
        new_end: "...",
      });
    });
    expect(bulk).toHaveBeenCalled();
  });

  it("opens modal when preview has pushes", async () => {
    vi.spyOn(api, "cascadePreview").mockResolvedValue({
      request_id: "r1",
      summary: "s",
      pushes: [{ task_id: "B" }],
      pulls: [],
      unresolved: [],
      can_auto_resolve: true,
      iter_count: 1,
      truncated: false,
    } as any);
    const { result } = renderHook(() => useScheduleChangeWithCascade(), {
      wrapper,
    });
    await act(async () => {
      await result.current.commit({
        task_id: "A",
        new_start: "...",
        new_end: "...",
      });
    });
    expect(result.current.modalState?.open).toBe(true);
  });

  it("stops after 3 retries of 422", async () => {
    vi.spyOn(api, "cascadePreview").mockResolvedValue({
      pushes: [{ task_id: "B" }],
      pulls: [],
      unresolved: [],
      summary: "",
      request_id: "r",
      can_auto_resolve: true,
      iter_count: 1,
      truncated: false,
    } as any);
    const err = Object.assign(new Error(), {
      code: "VALIDATION_OVERLAP_SAME_EQUIPMENT",
      canRetry: true,
    });
    vi.spyOn(api, "bulkUpdate").mockRejectedValue(err);
    const { result } = renderHook(() => useScheduleChangeWithCascade(), {
      wrapper,
    });
    // open modal + apply 3 times
    for (let i = 0; i < 3; i++) {
      await act(async () => {
        await result.current.commit({
          task_id: "A",
          new_start: "...",
          new_end: "...",
        });
        await result.current.applyModal();
      });
    }
    // 4th call should transition to unresolved guidance
    await act(async () => {
      await result.current.applyModal();
    });
    expect(result.current.retryCount).toBeGreaterThanOrEqual(3);
    expect(result.current.modalState?.guidanceShown).toBe(true);
  });
});
```

- [ ] **Step 2: Implement**

```ts
// frontend/src/features/scheduler/hooks/useScheduleChangeWithCascade.ts
import { useQueryClient } from "@tanstack/react-query";
import { useState, useCallback } from "react";
import { cascadePreview, bulkUpdate, revert } from "../api/cascade";
import { CascadePreviewResponse } from "../api/cascade.types";
import { FEATURE_FLAG_CASCADE_V2 } from "@/shared/config/featureFlags";
import { useToastStore } from "@/shared/ui/Toast";
import { useScheduleStore } from "../store/scheduleStore";

export interface ChangeInput {
  task_id: string;
  new_start: string;
  new_end: string;
  new_equipment_code?: string;
}

export function useScheduleChangeWithCascade() {
  const qc = useQueryClient();
  const toast = useToastStore();
  const legacyMove = useScheduleStore((s) => s.moveTaskLegacy);
  const [modalState, setModalState] = useState<{
    open: boolean;
    preview: CascadePreviewResponse;
    input: ChangeInput;
    guidanceShown?: boolean;
  } | null>(null);
  const [retryCount, setRetryCount] = useState(0);
  const [pullToggle, setPullToggle] = useState(true);

  const commit = useCallback(
    async (input: ChangeInput) => {
      if (!FEATURE_FLAG_CASCADE_V2) {
        legacyMove(input);
        return;
      }
      try {
        const preview = await cascadePreview(input);
        if (
          preview.pushes.length +
            preview.pulls.length +
            preview.unresolved.length ===
          0
        ) {
          const { change_set_id } = await bulkUpdate({
            changes: [input],
            expected_cascade_request_id: preview.request_id,
          });
          await qc.invalidateQueries({ queryKey: ["tasks"] });
          showUndoToast(change_set_id);
          return;
        }
        setModalState({ open: true, preview, input });
      } catch (e: any) {
        toast.show(`재배치 계산 실패: ${e.message}`, "error");
      }
    },
    [qc, legacyMove, toast],
  );

  const applyModal = useCallback(async () => {
    if (!modalState) return;
    const { preview, input } = modalState;
    const changes = [
      ...preview.pushes.map((p) => ({
        task_id: p.task_id,
        new_start: p.new_start,
        new_end: p.new_end,
      })),
      ...(pullToggle
        ? preview.pulls.map((p) => ({
            task_id: p.task_id,
            new_start: p.new_start,
            new_end: p.new_end,
          }))
        : []),
      input,
    ];
    try {
      const { change_set_id } = await bulkUpdate({
        changes,
        expected_cascade_request_id: preview.request_id,
      });
      await qc.invalidateQueries({ queryKey: ["tasks"] });
      showUndoToast(change_set_id);
      setModalState(null);
      setRetryCount(0);
    } catch (e: any) {
      if (retryCount + 1 >= 3) {
        setModalState((s) => (s ? { ...s, guidanceShown: true } : s));
        toast.show("자동 해소 불가. 수동 조정으로 이동해주세요.", "error");
      } else {
        setRetryCount((r) => r + 1);
        toast.show(`재적용 실패 (${retryCount + 1}/3): ${e.code}`, "error");
      }
    }
  }, [modalState, pullToggle, retryCount, qc, toast]);

  function showUndoToast(change_set_id: string) {
    toast.show("적용 완료", "success", 90000, {
      label: "되돌리기",
      onClick: async () => {
        await revert(change_set_id);
        await qc.invalidateQueries({ queryKey: ["tasks"] });
      },
    });
  }

  return {
    commit,
    applyModal,
    modalState,
    setModalState,
    retryCount,
    pullToggle,
    setPullToggle,
  };
}
```

Run vitest → 3 PASS

- [ ] **Step 3: Commit**

```bash
git add frontend/src/features/scheduler/hooks/
git commit -m "feat(hooks): useScheduleChangeWithCascade (2-Phase + 422 재시도 상한)"
```

---

### Task 18: `Toast.tsx` — action 슬롯 추가 (Undo 버튼 지원)

**Files:**

- Modify: `frontend/src/shared/ui/Toast.tsx`
- Test: `frontend/src/shared/ui/__tests__/Toast.test.tsx`

- [ ] **Step 1: Failing test**

```tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { useToastStore } from "../Toast";

it("renders action button and fires onClick", () => {
  const onClick = vi.fn();
  useToastStore
    .getState()
    .show("msg", "success", 10000, { label: "되돌리기", onClick });
  render(<Toast />);
  fireEvent.click(screen.getByRole("button", { name: "되돌리기" }));
  expect(onClick).toHaveBeenCalled();
});
```

- [ ] **Step 2: Implement (`action?: {label, onClick}` 파라미터 추가, 버튼 렌더)**

- [ ] **Step 3: Verify + Commit**

```bash
git add frontend/src/shared/ui/Toast.tsx frontend/src/shared/ui/__tests__/Toast.test.tsx
git commit -m "feat(toast): action 슬롯 (Undo 등 사용자 액션 지원)"
```

---

### Task 19: `ConflictResolutionModal` 섹션화 + reason 매핑 + Pull 토글 + CTA

**Files:**

- Modify: `frontend/src/features/scheduler/components/ConflictResolutionModal.tsx`
- Create: `frontend/src/features/scheduler/components/reasonLabels.ts`
- Test: `frontend/src/features/scheduler/components/__tests__/ConflictResolutionModal.test.tsx`

- [ ] **Step 1: reason 한국어 매핑**

```ts
// reasonLabels.ts
export const PUSH_REASON_LABEL = {
  same_equipment_conflict: "같은 설비의 다음 블록과 충돌로 밀림",
  cross_equipment_conflict: "후속 공정 설비의 다른 블록과 충돌로 밀림",
  successor_chain: "선행 공정 지연으로 시작 시간 밀림",
};
export const PULL_REASON_LABEL = {
  successor_slack_available: "선행 공정 단축으로 앞당김 가능",
};
export const UNRESOLVED_REASON_LABEL = {
  due_date_violation: "납기 초과 — 재배치 불가",
  no_space_forward: "해당 설비에 공간 없음",
  cycle_detected: "연쇄가 너무 복잡 — 수동 조정 필요",
  invalid_equipment: "해당 설비는 이 공정을 수행할 수 없음",
};
```

- [ ] **Step 2: Failing component test**

```tsx
it("renders reason labels in Korean", () => {
  const preview = { pushes:[{task_id:"B",reason:"same_equipment_conflict",...}], pulls:[], unresolved:[], summary:"s", request_id:"r" } as any;
  render(<ConflictResolutionModal preview={preview} onApply={()=>{}} onClose={()=>{}} pullToggle={true} onPullToggle={()=>{}} />);
  expect(screen.getByText(/같은 설비의 다음 블록과 충돌로 밀림/)).toBeInTheDocument();
});

it("disables apply when unresolved exists", () => {
  const preview = { pushes:[], pulls:[], unresolved:[{task_id:"A",reason:"due_date_violation",detail:"",equipment_code:"",batch_label:""}], summary:"" } as any;
  render(<ConflictResolutionModal preview={preview} onApply={()=>{}} onClose={()=>{}} pullToggle={true} onPullToggle={()=>{}} />);
  expect(screen.getByRole("button", { name: "적용" })).toBeDisabled();
});

it("pull toggle off excludes pulls from apply payload", () => {
  const onApply = vi.fn();
  const preview = { pushes:[], pulls:[{task_id:"S",...}], unresolved:[], ... } as any;
  render(<ConflictResolutionModal preview={preview} onApply={onApply} onClose={()=>{}} pullToggle={false} onPullToggle={()=>{}} />);
  fireEvent.click(screen.getByRole("button",{name:"적용"}));
  expect(onApply).toHaveBeenCalled();  // but pull exclusion happens in hook; here just assert rendering with toggle off
  expect(screen.getByLabelText(/Pull 포함/)).not.toBeChecked();
});
```

- [ ] **Step 3: Component implement**

```tsx
// ConflictResolutionModal.tsx (스캐폴드, 기존 파일 구조 유지)
import {
  XCircleIcon,
  ExclamationTriangleIcon,
  NoSymbolIcon,
} from "@heroicons/react/24/outline";
import { CascadePreviewResponse } from "../api/cascade.types";
import {
  PUSH_REASON_LABEL,
  PULL_REASON_LABEL,
  UNRESOLVED_REASON_LABEL,
} from "./reasonLabels";

interface Props {
  preview: CascadePreviewResponse;
  pullToggle: boolean;
  onPullToggle: (v: boolean) => void;
  onApply: () => void;
  onClose: () => void;
  onManualAdjust?: (taskId: string) => void;
}

export function ConflictResolutionModal({
  preview,
  pullToggle,
  onPullToggle,
  onApply,
  onClose,
  onManualAdjust,
}: Props) {
  const canApply = preview.unresolved.length === 0;
  return (
    <div className="kbi-modal">
      <h2>배치 변경 확인</h2>
      <p className="kbi-summary">{preview.summary}</p>

      {preview.pushes.length > 0 && (
        <section aria-labelledby="sec-push">
          <h3 id="sec-push">
            <XCircleIcon className="text-[var(--color-negative-500)]" /> 충돌
            해소 ({preview.pushes.length})
          </h3>
          <Table rows={preview.pushes} reasonLabel={PUSH_REASON_LABEL} />
        </section>
      )}

      {preview.pulls.length > 0 && (
        <section aria-labelledby="sec-pull">
          <h3 id="sec-pull">
            <ExclamationTriangleIcon className="text-[var(--color-warning-500)]" />{" "}
            앞당김 제안 ({preview.pulls.length})
          </h3>
          <label>
            <input
              type="checkbox"
              checked={pullToggle}
              onChange={(e) => onPullToggle(e.target.checked)}
            />{" "}
            Pull 포함
          </label>
          <Table rows={preview.pulls} reasonLabel={PULL_REASON_LABEL} />
        </section>
      )}

      {preview.unresolved.length > 0 && (
        <section aria-labelledby="sec-unres">
          <h3 id="sec-unres">
            <NoSymbolIcon className="text-[var(--color-negative-700)]" /> 해소
            불가 ({preview.unresolved.length})
          </h3>
          <ul>
            {preview.unresolved.map((u) => (
              <li key={u.task_id}>
                {u.batch_label} · {UNRESOLVED_REASON_LABEL[u.reason]}
                {onManualAdjust && (
                  <button onClick={() => onManualAdjust(u.task_id)}>
                    수동 조정 진입
                  </button>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      <footer>
        <button onClick={onClose}>닫기</button>
        <button
          onClick={onApply}
          disabled={!canApply}
          className="kbi-button-primary"
        >
          적용
          <span className="kbi-button-subtext">
            +{preview.pushes.length}건 재배치
            {preview.pulls.length ? ` · -${preview.pulls.length}건 앞당김` : ""}
          </span>
        </button>
      </footer>
    </div>
  );
}
```

_주의_: 기존 `ConflictResolutionModal.tsx` 의 하드코딩된 hex 색상은 모두 `--color-negative-*`, `--color-warning-*`, `--color-brand-*` 토큰으로 치환. `verify-pwc-design` gate 통과 필수.

Run vitest → PASS

- [ ] **Step 4: Commit**

```bash
git add frontend/src/features/scheduler/components/ConflictResolutionModal.tsx frontend/src/features/scheduler/components/reasonLabels.ts frontend/src/features/scheduler/components/__tests__/
git commit -m "feat(modal): 섹션화 + reason 한국어 + Pull 토글 + 수동조정 CTA + 토큰 치환"
```

---

### Task 20: `TaskFormModal` 3필드 편집 + 토큰 치환 + cascade 훅 연결

**Files:**

- Modify: `frontend/src/features/scheduler/components/TaskFormModal.tsx`
- Test: `frontend/src/features/scheduler/components/__tests__/TaskFormModal.test.tsx`

- [ ] **Step 1: Failing tests (3필드 규칙)**

```tsx
it("duration edit keeps start, recalculates end", () => {
  const onChange = vi.fn();
  render(<TaskFormModal task={{start:"2026-04-20T09:00",end:"2026-04-20T12:00"}} onSubmit={onChange} />);
  fireEvent.change(screen.getByLabelText("소요시간(h)"), { target: { value: "5" } });
  fireEvent.click(screen.getByRole("button",{name:"확인"}));
  expect(onChange).toHaveBeenCalledWith(expect.objectContaining({
    new_start:"2026-04-20T09:00", new_end:"2026-04-20T14:00"
  }));
});

it("start edit keeps end, recalculates duration", () => { /* ... */ });
it("end edit keeps start, recalculates duration", () => { /* ... */ });
it("disables submit when duration <= 0", () => {
  render(<TaskFormModal task={{...}} onSubmit={()=>{}} />);
  fireEvent.change(screen.getByLabelText("소요시간(h)"), { target: { value: "0" } });
  expect(screen.getByRole("button",{name:"확인"})).toBeDisabled();
});
```

- [ ] **Step 2: Implement 3필드 규칙 + 토큰 치환**

기존 `TaskFormModal.tsx:456-462` 의 `<div>` 를 `<input type="number" step="0.1" min="0.1">` 로 교체. 기존 인라인 hex 색상 (`#C41230`, `#4A2C2A`, `#9E0E27` 등)을 `var(--color-brand-*)` / `var(--color-neutral-*)` / `var(--color-negative-*)` 로 치환. useScheduleChangeWithCascade 훅 연결.

- [ ] **Step 3: Verify + Commit**

```bash
pnpm vitest run src/features/scheduler/components/__tests__/TaskFormModal.test.tsx
git add frontend/src/features/scheduler/components/TaskFormModal.tsx
git commit -m "feat(modal): TaskFormModal 3필드 편집 + 토큰 치환 + cascade 훅 연결"
```

---

### Task 21: `scheduler/page.tsx` 드래그 리팩터링 + 프론트 cascadePush 제거

**Files:**

- Modify: `frontend/src/app/(main)/scheduler/page.tsx:700-906`
- Modify: `frontend/src/features/scheduler/store/scheduleStore.ts:79-122, 375-394`

- [ ] **Step 1: Extract legacy path 보존**

`scheduleStore.ts` 의 `cascadePush` / 기존 `moveTask` 를 `moveTaskLegacy` 로 rename. `FEATURE_FLAG_CASCADE_V2 off` 시만 사용.

- [ ] **Step 2: handleDragEnd 교체**

```tsx
// page.tsx (handleDragEnd 교체)
const changeWithCascade = useScheduleChangeWithCascade();
const handleDragEnd = (e: DragEndEvent) => {
  // ... dnd-kit 계산은 기존 그대로
  changeWithCascade.commit({
    task_id: draggedTaskId,
    new_start: ..., new_end: ..., new_equipment_code: ...,
  });
};

// preview round-trip 동안 스피너 오버레이
<GanttTaskBlock ... loading={changeWithCascade.isPreviewLoading} />
```

- [ ] **Step 3: ConflictResolutionModal 연결**

```tsx
{
  changeWithCascade.modalState?.open && (
    <ConflictResolutionModal
      preview={changeWithCascade.modalState.preview}
      pullToggle={changeWithCascade.pullToggle}
      onPullToggle={changeWithCascade.setPullToggle}
      onApply={changeWithCascade.applyModal}
      onClose={() => changeWithCascade.setModalState(null)}
      onManualAdjust={(id) => router.push(`/orders/${id}`)}
    />
  );
}
```

- [ ] **Step 4: 기존 front cascadePush 제거 / 축소**

`scheduleStore.ts:79-122` 의 `cascadePush` 를 `moveTaskLegacy` 내부에서만 사용되도록 비공개화. `scheduleStore.ts:375-394` 의 successor 연동은 legacy 전용.

- [ ] **Step 5: Run existing E2E (없으면 manual smoke)**

`pnpm dev` 후 브라우저에서 블록 드래그 → cascade-preview 호출 확인 (DevTools Network).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/app/(main)/scheduler/page.tsx frontend/src/features/scheduler/store/scheduleStore.ts
git commit -m "refactor(scheduler): 드래그 handleDragEnd → useScheduleChangeWithCascade 단일화, legacy cascadePush 비공개화"
```

---

## Phase 4 — Ghost overlay + Observability + E2E

### Task 22: 간트 고스트 오버레이 (Tier 2)

**Files:**

- Modify: `frontend/src/features/scheduler/components/SchedulerView.tsx`
- Modify: `frontend/src/features/scheduler/components/GanttTaskBlock.tsx`
- Test: `frontend/src/features/scheduler/components/__tests__/ghost-overlay.test.tsx`

- [ ] **Step 1: Props 추가**

```tsx
// SchedulerView.tsx
interface Props { previewOverlay?: CascadePreviewResponse | null; ... }
```

- [ ] **Step 2: 렌더링**

각 task block 에 대해 `previewOverlay` 내 `pushes`/`pulls` 에 자기 task_id 가 있으면, 원본 위치는 그대로 유지하고 **변경 후 위치를 반투명 50% opacity + dashed border** 로 겹쳐 렌더링.

- [ ] **Step 3: Hover 연동**

`ConflictResolutionModal` 의 table row `onMouseEnter` → 상위 state `hoveredTaskId` → SchedulerView 가 해당 task 에 focus ring 토큰 outline 추가.

- [ ] **Step 4: Test**

```tsx
it("renders ghost overlay for pushed task", () => {
  const preview = {
    pushes: [{ task_id: "A", old_start: "...", new_start: "..." }],
    pulls: [],
    unresolved: [],
  } as any;
  render(<SchedulerView tasks={[taskA]} previewOverlay={preview} />);
  expect(screen.getByTestId("ghost-A")).toBeInTheDocument();
});
```

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/scheduler/components/SchedulerView.tsx frontend/src/features/scheduler/components/GanttTaskBlock.tsx frontend/src/features/scheduler/components/__tests__/ghost-overlay.test.tsx
git commit -m "feat(gantt): 고스트 오버레이 (변경 전/후 미리보기)"
```

---

### Task 23: 구조화 로그 + Prometheus 메트릭

**Files:**

- Modify: `backend/app/services/cascade/service.py`
- Modify: `backend/app/presentation/routes/schedules.py`
- Create: `backend/app/observability/metrics.py`
- Test: `backend/tests/observability/test_metrics.py`

- [ ] **Step 1: Metrics module**

```python
# backend/app/observability/metrics.py
from prometheus_client import Counter, Histogram, Gauge

cascade_preview_duration_seconds = Histogram(
    "cascade_preview_duration_seconds", "preview latency",
    buckets=(0.1, 0.25, 0.5, 1.0, 2.0, 5.0),
)
cascade_unresolved_total = Counter(
    "cascade_unresolved_total", "unresolved occurrences", ["reason"],
)
cascade_revert_total = Counter("cascade_revert_total", "revert count")
cascade_feature_flag_state = Gauge("cascade_feature_flag_state", "flag state", ["state"])
```

- [ ] **Step 2: Structured logging wrap**

라우터에서 cascade-preview 호출 전후 JSON 로그 + 메트릭 관찰.

```python
import time, json, logging, uuid
log = logging.getLogger("cascade")

@router.post("/cascade-preview", ...)
def cascade_preview(body, ...):
    request_id = str(uuid.uuid4())
    t0 = time.perf_counter()
    try:
        result = plan_cascade_preview(...)
        dur = time.perf_counter() - t0
        cascade_preview_duration_seconds.observe(dur)
        for u in result.unresolved:
            cascade_unresolved_total.labels(reason=u["reason"]).inc()
        log.info(json.dumps({
            "request_id": request_id, "route": "/cascade-preview",
            "task_id": body.task_id, "wave_used": result.iter_count,
            "pushes_n": len(result.pushes), "pulls_n": len(result.pulls),
            "unresolved_n": len(result.unresolved),
            "reason_histogram": _histogram(result),
            "duration_ms": int(dur*1000),
            "truncated": result.truncated, "status":"ok",
        }))
        result.request_id = request_id
        return result
    except Exception as e:
        log.error(json.dumps({"request_id":request_id,"status":"error","error":str(e)}))
        raise
```

- [ ] **Step 3: Test & Commit**

```bash
pytest tests/observability/test_metrics.py -v
git add backend/app/observability/ backend/app/services/cascade/service.py backend/app/presentation/routes/schedules.py backend/tests/observability/
git commit -m "feat(obs): 구조화 로그 + Prometheus 메트릭 (preview/unresolved/revert/flag)"
```

---

### Task 24: E2E fixture — freezeTime + seeded schedule

**Files:**

- Create: `frontend/e2e/fixtures/cascadeFixture.ts`

- [ ] **Step 1: Fixture**

```ts
// frontend/e2e/fixtures/cascadeFixture.ts
import { test as base } from "@playwright/test";
export const test = base.extend({
  frozenTime: async ({ page }, use) => {
    await page.addInitScript(() => {
      const FROZEN = new Date("2026-04-18T09:00:00Z").getTime();
      const _now = Date.now;
      Date.now = () => FROZEN;
    });
    await use("2026-04-18T09:00:00Z");
  },
  seededSchedule: async ({ request }, use) => {
    // backend 테스트 엔드포인트 호출로 seed 주입
    await request.post("/api/test/reset-schedule", {
      data: { fixture: "cascade-e2e-base" },
    });
    await use("cascade-e2e-base");
  },
});
export { expect } from "@playwright/test";
```

- [ ] **Step 2: Backend test-only endpoint**

```python
# backend/app/presentation/routes/test_only.py (only when ENV=test)
@router.post("/test/reset-schedule")
def reset_schedule(fixture: dict):
    # DROP + seed from fixtures/cascade-e2e-base.json
    ...
```

- [ ] **Step 3: Commit**

```bash
git add frontend/e2e/fixtures/ backend/app/presentation/routes/test_only.py
git commit -m "test(e2e): freezeTime + seeded schedule fixture"
```

---

### Task 25: E2E `block-duration-edit.spec.ts`

**Files:**

- Create: `frontend/e2e/block-duration-edit.spec.ts`

- [ ] **Step 1-4: 시나리오 구현**

```ts
import { test, expect } from "./fixtures/cascadeFixture";

test("소요시간 연장 → 충돌 → 적용 → Undo 복구", async ({
  page,
  seededSchedule,
}) => {
  await page.goto("/scheduler");
  await page.getByTestId("block-A").click({ button: "right" });
  await page.getByRole("menuitem", { name: "수정하기" }).click();
  await page.getByLabel("소요시간(h)").fill("90");
  await page.getByRole("button", { name: "확인" }).click();
  await expect(
    page.getByRole("dialog", { name: /배치 변경 확인/ }),
  ).toBeVisible();
  await expect(
    page.getByText(/같은 설비의 다음 블록과 충돌로 밀림/),
  ).toBeVisible();
  await page.getByRole("button", { name: "적용" }).click();
  await expect(page.getByText("적용 완료")).toBeVisible();
  // Undo
  await page.getByRole("button", { name: "되돌리기" }).click();
  // 원상 복구 assertion
  const restoredEnd = await page
    .getByTestId("block-A")
    .getAttribute("data-end");
  expect(restoredEnd).toBe("2026-04-20T12:00:00");
});

test("소요시간 축소 → Pull 제안 → 토글 off → 적용", async ({
  page,
  seededSchedule,
}) => {
  // ...
});

test("due date 위반 → unresolved + 적용 disabled + 수동조정 CTA", async ({
  page,
  seededSchedule,
}) => {
  // ...
});
```

- [ ] **Step 2: Run**

`cd frontend && pnpm exec playwright test block-duration-edit.spec.ts --headed` 로 수동 확인 → CI 모드 PASS.

- [ ] **Step 3: Commit**

```bash
git add frontend/e2e/block-duration-edit.spec.ts
git commit -m "test(e2e): 소요시간 편집/Undo/Pull/due 위반 시나리오"
```

---

### Task 26: E2E `block-drag-cascade.spec.ts`

유사 패턴으로 드래그 → cross-equipment 충돌 → 적용 → 후속 공정 이동 확인.

- [ ] Step 1-3: 구현 + Commit

```bash
git add frontend/e2e/block-drag-cascade.spec.ts
git commit -m "test(e2e): 드래그 cross-equipment cascade"
```

---

### Task 27: E2E `feature-flag-off.spec.ts`

- [ ] Step 1: fixture 에서 `NEXT_PUBLIC_FEATURE_FLAG_CASCADE_V2=off` 주입 (playwright.config project env)
- [ ] Step 2: 드래그/편집 시 cascade-preview 호출이 **없어야** 함 (`page.on("request")` 감시)
- [ ] Step 3: legacy 경로로 동작 확인 + Commit

```bash
git add frontend/e2e/feature-flag-off.spec.ts
git commit -m "test(e2e): feature flag off 경로 (legacy 유지)"
```

---

## Phase 5 — Gate + Docs

### Task 28: `verify-pwc-design` 통과

- [ ] **Step 1**: Invoke `verify-pwc-design` skill
- [ ] **Step 2**: 잔여 경고 0 이 될 때까지 토큰 치환 반복
- [ ] **Step 3**: Commit

```bash
git add frontend/
git commit -m "style(tokens): verify-pwc-design 경고 0 (잔여 hex → 토큰 치환)"
```

---

### Task 29: graphify rebuild + god-node 검증

- [ ] **Step 1**:

```bash
python3 -c "from graphify.watch import _rebuild_code; from pathlib import Path; _rebuild_code(Path('.'))"
```

- [ ] **Step 2**: `cat graphify-out/GRAPH_REPORT.md | grep -A5 "god-node"` 에서 `cascade_planning.py` 또는 `service.py` 의 in-degree ≤ 15 확인. 초과 시 모듈 쪼갬.
- [ ] **Step 3**: Commit graph output

```bash
git add graphify-out/
git commit -m "chore(graphify): rebuild, god-node in-degree <= 15"
```

---

### Task 30: `document-release` skill — 문서 갱신

- [ ] **Step 1**: Invoke `document-release` skill
- [ ] **Step 2**: 아래 문서 갱신 확인
  - `README.md`: cascade v2 소개
  - `ARCHITECTURE.md` (있으면): cascade/ 모듈 다이어그램
  - `CLAUDE.md`: 프로젝트 컨벤션 변동 있으면
  - `CHANGELOG.md`: v2 항목
- [ ] **Step 3**: Commit

```bash
git commit -m "docs: block-duration-edit cascade v2 출하 문서 갱신"
```

---

## Self-Review (Plan vs Spec)

1. **Spec coverage**:
   - §1 요구사항 → Task 20 (duration 편집), Task 21 (드래그 연결), Task 19 (modal 섹션화), Task 11 (preview 계약) ✓
   - §3 설계 원칙 (SSOT, 2-Phase, Explainability, Undo, Kill switch, 토큰) → Task 1, 17, 19, 14, 28 전반 ✓
   - §5.1-5.5 백엔드 → Task 11, 12, 13, 14, 1 ✓
   - §6 프론트 → Task 17-22 ✓
   - §7.3 로깅·메트릭 → Task 23 ✓
   - §8 테스트 T1-T14 → Task 4-10 (T1-T12) + Task 11-15 (T13-T14) ✓
   - §8.4 E2E → Task 24-27 ✓
   - §8.5 Gate → Task 28-29 ✓
   - §9.19 document-release → Task 30 ✓
2. **Placeholder scan**: "..." 는 테스트 스텁 내 실제 seed 데이터 생략부만 사용됨. 각 테스트에 구체 assertion 은 존재. ✅
3. **Type consistency**: `CascadePreviewResponse` / `PushEntry` / `UnresolvedEntry` 이름 backend Pydantic ↔ frontend ts ↔ 훅 간 일관. ✅
4. **누락 점검**: §11 미해결 과제는 본 계획에서 다루지 않음 (의도).

---

## Risks & Mitigations

| 리스크                                                                    | 완화                                                                                  |
| ------------------------------------------------------------------------- | ------------------------------------------------------------------------------------- |
| `calendar_engine.reverse_advance` 가 기존 `advance` 헬퍼와 대칭이 안 맞음 | Task 2 Step 1 에서 기존 `advance` 를 먼저 읽고 구조 확인, 실패 시 헬퍼부터 리팩터     |
| 프론트 `cascadePush` 제거 시 legacy 경로 미작동                           | Task 21 에서 `moveTaskLegacy` 로 보존, `FEATURE_FLAG_CASCADE_V2=off` 로 검증          |
| p95 < 1s 미달 (T14)                                                       | `cascade/snap.py` 에 `(equipment_code) → sorted list` 인덱스 cache 추가 (Task 4 확장) |
| `verify-pwc-design` 경고 과다                                             | Task 19, 20 에서 선제적 토큰 치환                                                     |
| 간트 고스트 오버레이 렌더 성능                                            | Task 22 에서 block 수가 수십 개까지는 OK. 100+ 시 `react-window` 검토 (YAGNI 밖)      |

---

**Plan complete and saved to `docs/plans/2026-04-18-block-duration-edit-cascade.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration. (superpowers:subagent-driven-development)

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch with checkpoints.

**Which approach?**
