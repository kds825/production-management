# 스케줄러 파이프라인·시스 그룹핑 재설계 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 연선→절연→시스 파이프라인 유휴 최소화, 시스 색상 체인 그룹핑, 겹침 자동 재최적화 + 경고 배너, 시스 블록 라벨 D 방식을 구현한다.

**Architecture:**

- 백엔드: `calculate_start_datetime` 역방향 캘린더 헬퍼 추가 → `schedule_optimizer.py` greedy / `cp_sat_optimizer.py` 모두에 동일 공식(`T_i_start = max(T_s_first_drum, T_s_end - D_i)`)과 끝 제약(`pred_end <= succ_end`) 적용. `batch_grouping.py` 시스 그룹 정렬/분할 로직 수정. `auto_schedule` 래퍼에서 `validate_all` 겹침 검증 → 2회 재시도 → `SchedulerOverlapError`.
- 프론트: Next.js 15 (breaking changes 주의 — `node_modules/next/dist/docs/` 확인 후 작성). `GanttTaskBlock.tsx` 시스 라벨 D 방식 렌더링. 스케줄 API 응답의 `overlap_alert` 플래그로 `ConstraintAlert` 배너 확장.

**Tech Stack:** Python 3.11, SQLAlchemy, OR-Tools CP-SAT, FastAPI, pytest · Next.js 15, React, TypeScript, Tailwind, Playwright

**Spec:** `docs/specs/2026-04-17-scheduler-pipeline-and-sheath-redesign-design.md`

---

## File Structure

| 파일                                                             | 역할                      | 변경                                                                |
| ---------------------------------------------------------------- | ------------------------- | ------------------------------------------------------------------- |
| `backend/app/services/calendar_engine.py`                        | 캘린더 기반 duration 계산 | Add `calculate_start_datetime`                                      |
| `backend/app/services/schedule_optimizer.py`                     | Greedy 스케줄러           | 파이프라인 공식 적용, `+1틀` 하한 제거, 재시도 래퍼                 |
| `backend/app/services/cp_sat_optimizer.py`                       | CP-SAT 솔버               | `pred_end <= succ_end` 제약 + 유휴 최소 목적함수 + 색상 체인 보너스 |
| `backend/app/services/batch_grouping.py`                         | Stage 1/2 그룹핑          | 시스 그룹 정렬·분할 로직                                            |
| `backend/app/services/constraint_checker.py`                     | 제약 검증                 | `validate_all` 반환형에 `has_overlap()` 추가 (헬퍼)                 |
| `backend/app/exceptions.py` (신규)                               | 예외 정의                 | `SchedulerOverlapError`                                             |
| `backend/app/presentation/routes/schedules.py`                   | 스케줄 API                | `overlap_alert` 응답 필드                                           |
| `frontend/src/features/scheduler/components/GanttTaskBlock.tsx`  | 블록 렌더                 | 시스 라벨 D 방식                                                    |
| `frontend/src/features/scheduler/components/ConstraintAlert.tsx` | 경고 배너                 | 겹침 경고 케이스                                                    |
| `frontend/src/features/scheduler/types.ts` (또는 기존 타입)      | 타입                      | `spec_list`, `overlap_alert`                                        |
| `backend/tests/test_calendar_engine_reverse.py` (신규)           | 테스트                    | 역방향 duration                                                     |
| `backend/tests/test_pipeline_sync.py` (신규)                     | 테스트                    | 파이프라인 공식                                                     |
| `backend/tests/test_sheath_color_chain.py` (신규)                | 테스트                    | 시스 그룹핑                                                         |
| `backend/tests/test_overlap_retry.py` (신규)                     | 테스트                    | 겹침 재시도                                                         |
| `frontend/e2e/sheath-label.spec.ts` (신규)                       | Playwright                | 라벨 D 방식 + 기존 구현 검증                                        |

---

## Task 1: 캘린더 엔진 역방향 duration 헬퍼 (P0-A)

**Files:**

- Modify: `backend/app/services/calendar_engine.py`
- Test: `backend/tests/test_calendar_engine_reverse.py` (create)

**Purpose:** `calculate_end_datetime(start, duration)` 의 역방향. `T_s_end - D_i` 역산 시 휴식·주말·금요일 종료 시각을 동일 캘린더로 보정하여 유효 가동 시각을 반환한다.

- [ ] **Step 1: Write failing tests**

Create file `backend/tests/test_calendar_engine_reverse.py`:

```python
"""역방향 duration 헬퍼 테스트 — calculate_start_datetime."""
from datetime import datetime

from app.services.calendar_engine import (
    calculate_end_datetime,
    calculate_start_datetime,
)


def test_reverse_round_trip_weekday_no_break():
    # 저압절연(24h): 월요일 오후 14:00에서 3시간 뒤 = 17:00
    end = datetime(2026, 4, 20, 17, 0)  # Monday
    start = calculate_start_datetime(end, 180, equipment_code="EX-B100")
    assert start == datetime(2026, 4, 20, 14, 0)

    # 순방향 재계산 → 동일 end 복원
    recomputed_end = calculate_end_datetime(start, 180, equipment_code="EX-B100")
    assert recomputed_end == end


def test_reverse_across_weekend():
    # 저압절연 끝이 월요일 09:00 → 2h 역산 = 금요일 11:00 (토·일 건너뜀)
    # 금요일 가용시간 12h (08:00~20:00)
    end = datetime(2026, 4, 20, 9, 0)  # Monday
    start = calculate_start_datetime(end, 120, equipment_code="EX-B100")
    # 월요일 09:00 기준 2h 역산 = 월요일 07:00 → 금요일 20:00 - 1h = 19:00...
    # 정확히는 월요일 08:00까지 1h (월요일 08:00~09:00), 나머지 1h는 금요일 19:00~20:00
    # 따라서 start는 금요일 19:00
    assert start == datetime(2026, 4, 17, 19, 0)


def test_reverse_across_break_stranding():
    # 연선연합(연선): 월~목 휴식 12:00~13:00, 18:00~18:30, 22:00~22:30
    # 월요일 13:30 종료, 1h 역산 → 휴식 12:00~13:00 건너뛰어 11:30
    end = datetime(2026, 4, 20, 13, 30)  # Monday
    start = calculate_start_datetime(end, 60, equipment_code="ST-T6B0")
    assert start == datetime(2026, 4, 20, 11, 30)


def test_reverse_exact_window_boundary():
    # 금요일 20:00 종료 (저압절연 금요일 종료) → 1h 역산 = 금요일 19:00
    end = datetime(2026, 4, 17, 20, 0)  # Friday
    start = calculate_start_datetime(end, 60, equipment_code="EX-B100")
    assert start == datetime(2026, 4, 17, 19, 0)


def test_reverse_zero_duration_is_end():
    end = datetime(2026, 4, 20, 10, 0)
    start = calculate_start_datetime(end, 0, equipment_code="EX-B100")
    assert start == end
```

- [ ] **Step 2: Run tests to confirm failure**

```
cd /Users/jaewookim/Desktop/Project/KBI_PoC/backend
pytest tests/test_calendar_engine_reverse.py -v
```

Expected: `ImportError: cannot import name 'calculate_start_datetime' from 'app.services.calendar_engine'`

- [ ] **Step 3: Add `calculate_start_datetime` to `calendar_engine.py`**

Append at the end of `backend/app/services/calendar_engine.py` (before `_is_last_two_mondays`):

```python
def calculate_start_datetime(
    end: datetime,
    duration_min: float,
    db: Session | None = None,
    equipment_code: str | None = None,
) -> datetime:
    """종료시각 - 소요시간(분) → 시작시각 (역방향 캘린더 보정).

    calculate_end_datetime 의 역연산. 휴식·주말·금요일 종료 시각을 건너뛰며
    뒤에서부터 시간을 소진한다. 유휴 최소화 역산 공식 T_start = T_end - D 에 사용.
    """
    if duration_min <= 0:
        return end

    remaining = duration_min
    current = end
    cat = _get_category(equipment_code)

    for _ in range(1000):
        if remaining <= 0:
            return current

        current_date = current.date()
        avail_hours = get_available_hours(current_date, equipment_code, db)

        if avail_hours <= 0:
            # 비가동일 → 전날 종료시각으로 이동
            prev = current_date - timedelta(days=1)
            _, prev_end = get_working_window(prev, equipment_code)
            current = prev_end
            continue

        day_start, day_end = get_working_window(current_date, equipment_code)

        if current <= day_start:
            prev = current_date - timedelta(days=1)
            _, prev_end = get_working_window(prev, equipment_code)
            current = prev_end
            continue

        # 현재 위치가 휴식 구간 내에 있으면 휴식 시작 시각으로 이동
        day_breaks = _get_day_breaks(current_date, cat)
        for brk_s, brk_e in day_breaks:
            if brk_s < current <= brk_e:
                current = brk_s
                break

        if current <= day_start:
            prev = current_date - timedelta(days=1)
            _, prev_end = get_working_window(prev, equipment_code)
            current = prev_end
            continue

        # current 직전의 가장 가까운 휴식 구간 탐색 (역방향)
        prev_brk_s: datetime | None = None
        prev_brk_e: datetime | None = None
        for brk_s, brk_e in reversed(day_breaks):
            if brk_e < current:
                prev_brk_s = brk_s
                prev_brk_e = brk_e
                break

        # 이전 정지 지점: 이전 휴식 종료 or 창 시작 중 느린 것
        if prev_brk_e is not None and prev_brk_e > day_start:
            work_back_until = prev_brk_e
        else:
            work_back_until = day_start

        avail_min = (current - work_back_until).total_seconds() / 60

        if remaining <= avail_min:
            return current - timedelta(minutes=remaining)

        remaining -= avail_min

        if prev_brk_e is not None and work_back_until == prev_brk_e:
            # 휴식 구간 건너뜀 (역방향)
            current = prev_brk_s  # type: ignore[assignment]
        else:
            # 창 시작 → 전날 종료시각
            prev = current_date - timedelta(days=1)
            _, prev_end = get_working_window(prev, equipment_code)
            current = prev_end

    # 안전 폴백
    return end - timedelta(minutes=duration_min)
```

- [ ] **Step 4: Run tests to confirm pass**

```
pytest tests/test_calendar_engine_reverse.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```
cd /Users/jaewookim/Desktop/Project/KBI_PoC
git add backend/app/services/calendar_engine.py backend/tests/test_calendar_engine_reverse.py
git commit -m "feat(calendar): 역방향 duration 헬퍼 calculate_start_datetime 추가"
```

---

## Task 2: 파이프라인 동기화 공식 — Greedy 경로 (P0-B-1)

**Files:**

- Modify: `backend/app/services/schedule_optimizer.py` (lines 540-656 근처)
- Test: `backend/tests/test_pipeline_sync.py` (create)

**Purpose:** 후공정 시작 = `max(pred_first_drum, pred_end - succ_duration)` 역산 공식 적용. 기존 `선행 끝 + 1틀` 하한 제거. 후공정 끝 ≥ 선행공정 끝 불변식 보장.

- [ ] **Step 1: Write failing test**

Create `backend/tests/test_pipeline_sync.py`:

```python
"""파이프라인 동기화 공식 테스트 — 연선→절연→시스 유휴 최소 역산."""
from datetime import datetime, timedelta

from sqlalchemy.orm import Session


def test_insulation_end_not_before_stranding_end(db: Session):
    """절연 선속이 연선의 2배라도 절연 끝이 연선 끝보다 빠르면 안 됨."""
    from app.services.schedule_optimizer import auto_schedule

    # Fixture: 연선 duration 10h, 절연 duration 5h (2배 빠름), 1 드럼
    # 기대: 절연 시작 = 연선 끝 - 5h (역산), 절연 끝 = 연선 끝
    _seed_stranding_then_insulation(db, stranding_min=600, insulation_min=300)

    result = auto_schedule(run_label="test-pipeline-1", db=db)

    stranding = _get_task(db, "test-pipeline-1", "연선")
    insulation = _get_task(db, "test-pipeline-1", "저압절연")

    assert insulation.end_datetime >= stranding.end_datetime, (
        f"절연 끝 {insulation.end_datetime} < 연선 끝 {stranding.end_datetime}"
    )
    # 유휴 최소: 끝이 정렬 (±1분 허용)
    delta = abs((insulation.end_datetime - stranding.end_datetime).total_seconds())
    assert delta <= 60, f"끝 정렬 어긋남 {delta}초"


def test_insulation_start_after_first_drum(db: Session):
    """절연 시작 ≥ 연선 첫 드럼 완료 시각."""
    from app.services.schedule_optimizer import auto_schedule

    _seed_stranding_multi_drum(db, drums=3, stranding_per_drum_min=200, insulation_min=100)
    auto_schedule(run_label="test-pipeline-2", db=db)

    stranding = _get_task(db, "test-pipeline-2", "연선")
    insulation = _get_task(db, "test-pipeline-2", "저압절연")

    # 연선 첫 드럼 = stranding.start + (duration / 3)
    stranding_dur = (stranding.end_datetime - stranding.start_datetime).total_seconds() / 60
    first_drum_end = stranding.start_datetime + timedelta(minutes=stranding_dur / 3)
    assert insulation.start_datetime >= first_drum_end


def test_insulation_block_width_unchanged(db: Session):
    """블록 width 는 선속 기반 고정 — 역산이 width 를 늘리지 않음."""
    from app.services.schedule_optimizer import auto_schedule

    expected_dur = 300
    _seed_stranding_then_insulation(db, stranding_min=600, insulation_min=expected_dur)
    auto_schedule(run_label="test-pipeline-3", db=db)

    insulation = _get_task(db, "test-pipeline-3", "저압절연")
    actual_dur = (insulation.end_datetime - insulation.start_datetime).total_seconds() / 60

    # 캘린더 휴식/주말 보정으로 wall-clock 은 살짝 달라질 수 있으나
    # "연선 끝 + 1틀" 과다 하한이 제거되었으므로 기존 동작보다 작거나 같아야 함
    assert actual_dur <= expected_dur * 1.5, f"블록이 과하게 늘어남: {actual_dur}분"


# 아래 헬퍼는 기존 conftest 또는 새 파일에 정의 (seed_data.py 스타일 차용)
def _seed_stranding_then_insulation(db, stranding_min, insulation_min):
    """단일 SQ, 단일 드럼 연선 + 절연 배치 시드."""
    from app.infrastructure.models.production_batch import ProductionBatch
    # 실제 필드는 seed_data.py 참조 — 최소 필드만
    s = ProductionBatch(run_label="test-pipeline-1", batch_group="ST-10-A",
                        process_name="연선", sq_mm2=10, drum_count=1,
                        total_length_m=stranding_min * 10, batch_seq=0)
    i = ProductionBatch(run_label="test-pipeline-1", batch_group="EX-B100-10",
                        process_name="저압절연", sq_mm2=10, drum_count=1,
                        total_length_m=insulation_min * 20, batch_seq=0,
                        predecessor_batch_id=None)
    db.add_all([s, i])
    db.flush()


def _seed_stranding_multi_drum(db, drums, stranding_per_drum_min, insulation_min):
    from app.infrastructure.models.production_batch import ProductionBatch
    header = ProductionBatch(run_label="test-pipeline-2", batch_group="ST-10-B",
                             process_name="연선", sq_mm2=10, drum_count=drums,
                             total_length_m=stranding_per_drum_min * drums * 10,
                             batch_seq=-1)
    db.add(header)
    i = ProductionBatch(run_label="test-pipeline-2", batch_group="EX-B100-10B",
                        process_name="저압절연", sq_mm2=10, drum_count=1,
                        total_length_m=insulation_min * 20, batch_seq=0)
    db.add(i)
    db.flush()


def _get_task(db, run_label, process_name):
    from app.infrastructure.models.schedule_task import ScheduleTask
    from app.infrastructure.models.production_batch import ProductionBatch
    return (
        db.query(ScheduleTask)
        .join(ProductionBatch, ScheduleTask.batch_id == ProductionBatch.batch_id)
        .filter(ScheduleTask.run_label == run_label,
                ProductionBatch.process_name == process_name)
        .first()
    )
```

> **NOTE**: seed 헬퍼의 실제 필드는 `backend/seed_data.py` 참조하여 required not-null 컬럼 채워넣기. 테스트 실행 시 `NOT NULL violation` 이 나오면 그 필드를 헬퍼에 추가.

- [ ] **Step 2: Run test to confirm failure**

```
cd backend && pytest tests/test_pipeline_sync.py -v
```

Expected: FAIL — 현재 로직은 절연이 일찍 끝남.

- [ ] **Step 3: Replace pipeline logic in `schedule_optimizer.py`**

In `backend/app/services/schedule_optimizer.py`, around lines 628-656, find this block:

```python
        end_dt = calculate_end_datetime(best_start, best_total_duration, db, best_eq.equipment_code)

        # ── 파이프라인 겹침 보정: 후공정이 선행공정 종료 전에 끝나지 않도록 ───
        # 배경: 절연은 연선 첫 드럼 출력 후 시작하지만 선속이 2배 빠르면
        #       연선이 아직 진행 중인데 절연이 끝나는 현상 발생.
        # 수정: 선행공정 마지막 틀 완료 시각 + 후공정 1틀 소요시간 >= end_dt 보장.
        # 대상 공정: PREDECESSOR_PROCESS 기준 + 시스는 연합도 추가 체크.
        _pipeline_check_procs: list[str] = []
        if pred_proc:
            _pipeline_check_procs.append(pred_proc)
        if rep.process_name in ("저압시스", "고압시스"):
            _pipeline_check_procs.append("연합")

        _all_sqs_g = {int(b.sq_mm2 or 0) for b in group_batches}
        # 현재 그룹의 틀 수를 직접 계산 (lot_count는 아직 이 시점에서 미설정)
        if header_batch is not None:
            _curr_lot_count = max(int(header_batch.drum_count or 1), 1)
        else:
            _curr_lot_count = max(sum(int(b.drum_count or 1) for b in group_batches), 1)
        _per_drum_min = group_duration / _curr_lot_count
        for _pp in _pipeline_check_procs:
            for _sq_i in _all_sqs_g:
                _pred_last = process_end_by_sq.get((_pp, _sq_i))
                if _pred_last and _pred_last < datetime.max and _pred_last > best_start:
                    _min_end = calculate_end_datetime(
                        _pred_last, _per_drum_min, db, best_eq.equipment_code
                    )
                    if _min_end > end_dt:
                        end_dt = _min_end
```

Replace with (신규 역산 공식):

```python
        # ── 파이프라인 유휴 최소 역산 공식 ────────────────────────────────────
        # T_succ_start = max(T_pred_first_drum, T_pred_end - D_succ)
        # 불변식: T_succ_end >= T_pred_end (후공정 끝 ≥ 선행공정 끝)
        # 효과: 절연 선속이 연선보다 빠르면 시작을 늦춰 끝을 정렬. 블록 width 불변.
        _pipeline_check_procs: list[str] = []
        if pred_proc:
            _pipeline_check_procs.append(pred_proc)
        if rep.process_name in ("저압시스", "고압시스"):
            _pipeline_check_procs.append("연합")

        _all_sqs_g = {int(b.sq_mm2 or 0) for b in group_batches}

        # 모든 관련 선행공정의 종료 시각 중 최대 — 이 시각에 맞춰 succ 끝 정렬
        pred_end_latest: datetime | None = None
        for _pp in _pipeline_check_procs:
            for _sq_i in _all_sqs_g:
                _pe = process_end_by_sq.get((_pp, _sq_i))
                if _pe and _pe < datetime.max:
                    if pred_end_latest is None or _pe > pred_end_latest:
                        pred_end_latest = _pe

        if pred_end_latest is not None:
            # 역산 시작 시각: pred_end - best_total_duration (캘린더 보정)
            reverse_start = calculate_start_datetime(
                pred_end_latest, best_total_duration, db, best_eq.equipment_code
            )
            # best_start 가 이미 reverse_start 보다 늦으면 그대로 사용 (절연이 느림)
            # 아니면 best_start 를 reverse_start 로 지연 (유휴 최소)
            if reverse_start > best_start:
                # 지연된 시작 시각도 timeline 에 가능한 슬롯인지 확인
                delayed_start = _find_available_slot(
                    reverse_start, best_total_duration, slots, db, best_eq.equipment_code
                )
                best_start = delayed_start
                end_dt = calculate_end_datetime(
                    best_start, best_total_duration, db, best_eq.equipment_code
                )

            # 불변식 보장: end_dt >= pred_end_latest (캘린더 보정 오차 대비)
            if end_dt < pred_end_latest:
                end_dt = pred_end_latest
```

Add import at the top of `schedule_optimizer.py`:

```python
from app.services.calendar_engine import calculate_end_datetime, calculate_start_datetime
```

(기존 import 줄에 `calculate_start_datetime` 추가)

- [ ] **Step 4: Run tests to confirm pass**

```
cd backend && pytest tests/test_pipeline_sync.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Run full backend test suite (regression check)**

```
cd backend && pytest tests/ -v --ignore=tests/test_overlap_retry.py --ignore=tests/test_sheath_color_chain.py 2>&1 | tail -30
```

Expected: 기존 테스트 PASS 유지. 실패 시 fixture 문제이면 fix, 회귀이면 로직 재검토.

- [ ] **Step 6: Commit**

```
git add backend/app/services/schedule_optimizer.py backend/tests/test_pipeline_sync.py
git commit -m "feat(scheduler): 파이프라인 유휴 최소 역산 공식 적용 (greedy 경로)"
```

---

## Task 3: 파이프라인 동기화 — CP-SAT 경로 (P0-B-2)

**Files:**

- Modify: `backend/app/services/cp_sat_optimizer.py` (lines 550-602 근처)

**Purpose:** CP-SAT 모델에 `pred_end <= succ_end` 제약 추가 + 목적함수에 유휴 최소 항 추가.

- [ ] **Step 1: Add test case to `test_pipeline_sync.py`**

Append to existing file:

```python
def test_cp_sat_pipeline_end_constraint(db: Session):
    """CP-SAT 솔버가 pred_end <= succ_end 를 지키는지."""
    from app.services.cp_sat_optimizer import cp_sat_schedule

    _seed_stranding_then_insulation(db, stranding_min=600, insulation_min=300)
    result = cp_sat_schedule(run_label="test-cpsat-1", db=db)
    assert result["solver_status"] in ("OPTIMAL", "FEASIBLE")

    stranding = _get_task(db, "test-cpsat-1", "연선")
    insulation = _get_task(db, "test-cpsat-1", "저압절연")
    assert insulation.end_datetime >= stranding.end_datetime
```

- [ ] **Step 2: Run test to confirm failure**

```
cd backend && pytest tests/test_pipeline_sync.py::test_cp_sat_pipeline_end_constraint -v
```

Expected: FAIL — CP-SAT 에 end 제약 없음.

- [ ] **Step 3: Add `pred_end <= succ_end` constraint and idle minimize term**

In `backend/app/services/cp_sat_optimizer.py`, in block `6-d. 공정 선후관계` (around line 567-588), **after** the existing `model.add(start_vars[gk] >= start_vars[pred_gk] + first_drum)` line, add:

```python
            # 신규: 후공정 끝 ≥ 선행공정 끝 (유휴 최소 역산 공식의 CP-SAT 버전)
            model.add(end_vars[gk] >= end_vars[pred_gk])
```

Then modify the objective function around line 601-602. Find:

```python
    # 6-f. 목적함수: 가중 납기 초과 최소화
    model.minimize(sum(meta["weight"] * tardiness_vars[gk] for gk, meta in group_meta.items()))
```

Replace with:

```python
    # 6-f. 목적함수: 가중 납기 초과 최소화 + 파이프라인 유휴 최소화
    # 유휴 = succ_end - pred_end (0 이상). 가중치는 납기보다 작게 (납기 hard 유지).
    idle_terms: list[Any] = []
    for gk in groups:
        pred_proc = PREDECESSOR_PROCESS.get(group_meta[gk]["rep"].process_name)
        if not pred_proc:
            continue
        for pred_gk in proc_groups_by_sq.get((pred_proc, group_meta[gk]["sq"]), []):
            # idle = end_vars[gk] - end_vars[pred_gk] (이미 >= 0 제약 있음)
            idle_var = model.new_int_var(0, _HORIZON_MIN, f"idle_{pred_gk}_{gk}")
            model.add(idle_var == end_vars[gk] - end_vars[pred_gk])
            idle_terms.append(idle_var)

    IDLE_WEIGHT = 1  # 납기 위반 대비 1/100 이하 가중치 (tardiness weight 보통 100+)
    model.minimize(
        sum(meta["weight"] * tardiness_vars[gk] for gk, meta in group_meta.items())
        + IDLE_WEIGHT * sum(idle_terms)
    )
```

> **NOTE**: `_HORIZON_MIN` 이 파일 상수로 없다면 기존 코드에서 사용하는 horizon 변수를 찾아 사용 (예: `horizon` 또는 `_MAX_MIN`). 없으면 `10 * 24 * 60` (10일) 로 정의.

- [ ] **Step 4: Run test to confirm pass**

```
cd backend && pytest tests/test_pipeline_sync.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```
git add backend/app/services/cp_sat_optimizer.py backend/tests/test_pipeline_sync.py
git commit -m "feat(cp-sat): 파이프라인 끝 제약 + 유휴 최소 목적함수 항 추가"
```

---

## Task 4: 시스 색상 체인 정렬 — Stage 2 (P0-C-1)

**Files:**

- Modify: `backend/app/services/batch_grouping.py`
- Test: `backend/tests/test_sheath_color_chain.py` (create)

**Purpose:** 시스 배치의 Stage 2 정렬을 [색상 체인, 절연 완료, 납기] 우선순위로 변경하여 동일 색상 인접 주차가 체인을 형성하도록 한다. Stage 1 group*key 는 `{설비}*{색상}\_{주차}` 유지.

- [ ] **Step 1: Write failing test**

Create `backend/tests/test_sheath_color_chain.py`:

```python
"""시스 색상 체인 그룹핑 테스트 — A'' 접근안."""
from datetime import date, datetime, timedelta


def test_sheath_color_chain_across_weeks(db):
    """같은 색상이 인접 주차에 있으면 연속 배치 (체인지오버 0)."""
    from app.services.schedule_optimizer import auto_schedule

    # 시드: A120 흑색 2개 그룹 (W15, W16), 갈색 1개 그룹 (W15)
    # 기대 순서: 흑(W15) → 흑(W16) → 갈(W15)  (흑 체인 연속)
    _seed_sheath_three_groups(db)
    auto_schedule(run_label="test-chain-1", db=db)

    tasks = _get_sheath_tasks(db, "test-chain-1", eq_code="SH-A120")
    sorted_tasks = sorted(tasks, key=lambda t: t.start_datetime)

    colors = [_get_color(db, t) for t in sorted_tasks]
    # 흑 색상이 연속해서 나오는지 확인 (갈 사이에 흑이 끼지 않음)
    black_positions = [i for i, c in enumerate(colors) if c == "흑"]
    assert black_positions == list(
        range(min(black_positions), max(black_positions) + 1)
    ), f"흑 색상이 연속이 아님: {colors}"


def test_sheath_color_chain_respects_deadline(db):
    """납기 hard: 같은 색상이라도 납기 위반 예측 시 분할."""
    from app.services.schedule_optimizer import auto_schedule

    # 흑(W16, 납기 04-20) + 흑(W14, 긴급 납기 04-03)
    # 기대: 납기 04-03 긴급 흑이 먼저, 그 다음 W16 흑
    _seed_sheath_with_urgent(db)
    auto_schedule(run_label="test-chain-2", db=db)

    tasks = sorted(
        _get_sheath_tasks(db, "test-chain-2", eq_code="SH-A120"),
        key=lambda t: t.start_datetime,
    )
    due_dates = [_get_due(db, t) for t in tasks]
    assert due_dates == sorted(due_dates), f"납기순 위반: {due_dates}"


def test_sheath_respects_insulation_end(db):
    """시스는 절연 완료 이후에만 시작."""
    from app.services.schedule_optimizer import auto_schedule

    _seed_sheath_with_predecessor_insulation(db)
    auto_schedule(run_label="test-chain-3", db=db)

    sheath_tasks = _get_sheath_tasks(db, "test-chain-3", eq_code="SH-A120")
    for t in sheath_tasks:
        insul_end = _get_predecessor_end(db, t, process_name="저압절연")
        if insul_end is not None:
            assert t.start_datetime >= insul_end, (
                f"시스 task {t.task_id} 시작 {t.start_datetime} < 절연 끝 {insul_end}"
            )


def _seed_sheath_three_groups(db):
    from app.infrastructure.models.production_batch import ProductionBatch
    rows = [
        dict(process_name="저압시스", sheath_color="흑", sq_mm2=120,
             due_date=date(2026, 4, 13), drum_count=1, total_length_m=1000),
        dict(process_name="저압시스", sheath_color="흑", sq_mm2=95,
             due_date=date(2026, 4, 20), drum_count=1, total_length_m=800),
        dict(process_name="저압시스", sheath_color="갈", sq_mm2=120,
             due_date=date(2026, 4, 13), drum_count=1, total_length_m=500),
    ]
    for r in rows:
        db.add(ProductionBatch(run_label="test-chain-1", batch_group="", batch_seq=0, **r))
    db.flush()


def _seed_sheath_with_urgent(db):
    from app.infrastructure.models.production_batch import ProductionBatch
    db.add(ProductionBatch(run_label="test-chain-2", batch_group="", batch_seq=0,
                           process_name="저압시스", sheath_color="흑", sq_mm2=120,
                           due_date=date(2026, 4, 20), drum_count=1, total_length_m=1000))
    db.add(ProductionBatch(run_label="test-chain-2", batch_group="", batch_seq=0,
                           process_name="저압시스", sheath_color="흑", sq_mm2=50,
                           due_date=date(2026, 4, 3), drum_count=1, total_length_m=300))
    db.flush()


def _seed_sheath_with_predecessor_insulation(db):
    from app.infrastructure.models.production_batch import ProductionBatch
    db.add(ProductionBatch(run_label="test-chain-3", batch_group="EX-B100-50",
                           process_name="저압절연", sq_mm2=50, drum_count=1,
                           total_length_m=2000, batch_seq=0))
    db.add(ProductionBatch(run_label="test-chain-3", batch_group="",
                           process_name="저압시스", sheath_color="흑", sq_mm2=50,
                           due_date=date(2026, 4, 20), drum_count=1,
                           total_length_m=2000, batch_seq=0))
    db.flush()


def _get_sheath_tasks(db, run_label, eq_code):
    from app.infrastructure.models.schedule_task import ScheduleTask
    return (
        db.query(ScheduleTask)
        .filter(ScheduleTask.run_label == run_label,
                ScheduleTask.equipment_code == eq_code)
        .all()
    )


def _get_color(db, task):
    from app.infrastructure.models.production_batch import ProductionBatch
    b = db.query(ProductionBatch).filter(ProductionBatch.batch_id == task.batch_id).first()
    return (b.sheath_color or "").strip() if b else ""


def _get_due(db, task):
    from app.infrastructure.models.production_batch import ProductionBatch
    b = db.query(ProductionBatch).filter(ProductionBatch.batch_id == task.batch_id).first()
    return b.due_date if b else None


def _get_predecessor_end(db, task, process_name):
    from app.infrastructure.models.schedule_task import ScheduleTask
    from app.infrastructure.models.production_batch import ProductionBatch
    pred_task = (
        db.query(ScheduleTask)
        .join(ProductionBatch, ScheduleTask.batch_id == ProductionBatch.batch_id)
        .filter(ScheduleTask.run_label == task.run_label,
                ProductionBatch.process_name == process_name)
        .first()
    )
    return pred_task.end_datetime if pred_task else None
```

- [ ] **Step 2: Run test to confirm failure**

```
cd backend && pytest tests/test_sheath_color_chain.py -v
```

Expected: FAIL — 현재 로직은 SQ 기준이라 색상 체인 형성 안 됨.

- [ ] **Step 3: Modify sort key in `batch_grouping.py`**

In `backend/app/services/batch_grouping.py`, find the `_sort_key` closure and the `batches.sort(key=_sort_key)` call (near lines 760-778). Add a new sort stage specifically for sheath batches.

After `batches.sort(key=_sort_key)` (line 778), add:

```python
    # ── 시스(저압/고압) 전용 2차 정렬: 색상 체인 + 절연 완료 + 납기 ─────────
    # A'' 접근안: 동일 색상 인접 주차를 연속 배치하여 체인지오버 최소화.
    # Stage 2 이후 스케줄 배치 순서를 결정하는 정렬.
    def _sheath_chain_key(b: ProductionBatch) -> tuple:
        if b.process_name not in ("저압시스", "고압시스"):
            # 비시스 배치는 원 순서 유지 (sort stable)
            return (0, "", date(1900, 1, 1), 0)

        color = (b.sheath_color or "").strip() or "기타"
        # 색상 우선순위 (임의 고정 순서 — 흑이 가장 많이 쓰이므로 먼저)
        color_rank = _SHEATH_COLOR_RANK.get(color, 99)

        # 납기 주차 (정수로 변환하여 주차 간 연속성 유지)
        if b.due_date:
            yr, wk, _ = b.due_date.isocalendar()
            due_wk_int = yr * 100 + wk
        else:
            due_wk_int = 999999

        due_dt = b.due_date or date(2099, 12, 31)
        return (1, color_rank, due_wk_int, due_dt.toordinal())

    # stable sort — 비시스는 원순서, 시스만 재정렬
    batches.sort(key=_sheath_chain_key)
```

At the top of `batch_grouping.py` (with other module constants), add:

```python
# 시스 색상 정렬 우선순위 (A120: 흑·청·흑/적 / A100: 갈·회·녹/황 등)
# 현장에서 자주 쓰이는 색상일수록 앞에 배치 → 긴 체인 형성 확률 ↑
_SHEATH_COLOR_RANK: dict[str, int] = {
    "흑": 1, "갈": 2, "회": 3, "청": 4,
    "녹": 5, "녹/황": 5, "백": 6, "적": 7, "흑/적": 8,
}
```

- [ ] **Step 4: Run test 1 (color chain across weeks)**

```
cd backend && pytest tests/test_sheath_color_chain.py::test_sheath_color_chain_across_weeks -v
```

Expected: PASS.

- [ ] **Step 5: Run test 3 (insulation precedence)**

```
cd backend && pytest tests/test_sheath_color_chain.py::test_sheath_respects_insulation_end -v
```

Expected: PASS (기존 파이프라인 공식이 Task 2에서 이미 보장).

- [ ] **Step 6: Commit**

```
git add backend/app/services/batch_grouping.py backend/tests/test_sheath_color_chain.py
git commit -m "feat(scheduler): 시스 색상 체인 Stage 2 정렬 (A'' 접근안)"
```

---

## Task 5: 시스 납기 hard 분할 (P0-C-2)

**Files:**

- Modify: `backend/app/services/batch_grouping.py` (Stage 1 group_key)
- Test: `backend/tests/test_sheath_color_chain.py` (Task 4 의 test_sheath_color_chain_respects_deadline)

**Purpose:** 같은 색상·같은 주차 내에서 납기 위반 예측 시 그룹 분할. 현재 주차 단위 분할만 있어 같은 주 내 3일 차이가 밀리는 문제 해결.

- [ ] **Step 1: Verify test `test_sheath_color_chain_respects_deadline` still fails**

```
cd backend && pytest tests/test_sheath_color_chain.py::test_sheath_color_chain_respects_deadline -v
```

Expected: FAIL — 같은 색상 같은 주차 내 긴급(납기 04-03)이 납기 04-20 뒤로 밀려있음.

- [ ] **Step 2: Refine Stage 1 group_key — 동일 색상 · 동일 주차라도 납기 간격 큰 경우 분할**

In `backend/app/services/batch_grouping.py`, find the sheath group_key block (lines 800-815):

```python
        if proc in ("저압시스", "고압시스"):
            color = (b.sheath_color or "").strip()
            color_key = color.replace("/", "_") if color else "기타"
            if proc == "저압시스":
                # 납기 ISO 주차로 분할 기준 결정
                if b.due_date:
                    _yr, _wk, _ = b.due_date.isocalendar()
                    _due_wk = f"{_yr}W{_wk:02d}"
                else:
                    _due_wk = "9999W99"
                if color in ("흑", "청", "흑/적"):
                    group_key = f"A120_{color_key}_{_due_wk}"
                else:
                    group_key = f"A100_{color_key}_{_due_wk}"
            else:
                group_key = f"{proc}_{color_key}"
```

Replace with (납기 반-주차 분할 추가):

```python
        if proc in ("저압시스", "고압시스"):
            color = (b.sheath_color or "").strip()
            color_key = color.replace("/", "_") if color else "기타"
            if proc == "저압시스":
                # 납기 반-주차(3~4일) 분할 — 같은 주 내 납기 차이 크면 별도 그룹
                if b.due_date:
                    yr, wk, wday = b.due_date.isocalendar()
                    # 주차 내 상/하반 구분: 월~수=H1, 목~일=H2
                    half = "H1" if wday <= 3 else "H2"
                    _due_bucket = f"{yr}W{wk:02d}{half}"
                else:
                    _due_bucket = "9999W99X"
                if color in ("흑", "청", "흑/적"):
                    group_key = f"A120_{color_key}_{_due_bucket}"
                else:
                    group_key = f"A100_{color_key}_{_due_bucket}"
            else:
                group_key = f"{proc}_{color_key}"
```

- [ ] **Step 3: Run tests**

```
cd backend && pytest tests/test_sheath_color_chain.py -v
```

Expected: 3 passed.

- [ ] **Step 4: Regression — A120 seed data 로 긴 체인 유지 확인**

Add regression test to `test_sheath_color_chain.py`:

```python
def test_long_color_chain_not_broken_by_half_week(db):
    """같은 색상 H1·H2 연속이면 여전히 체인 형성 (배치 정렬이 이어짐)."""
    from app.services.schedule_optimizer import auto_schedule
    from app.infrastructure.models.production_batch import ProductionBatch

    # W15H1 흑 + W15H2 흑 + W16H1 흑 → 시작 시각 연속, 체인지오버 0
    for d in [date(2026, 4, 6), date(2026, 4, 10), date(2026, 4, 13)]:
        db.add(ProductionBatch(run_label="test-chain-4", batch_group="",
                               process_name="저압시스", sheath_color="흑", sq_mm2=50,
                               due_date=d, drum_count=1, total_length_m=500,
                               batch_seq=0))
    db.flush()

    auto_schedule(run_label="test-chain-4", db=db)
    tasks = sorted(
        _get_sheath_tasks(db, "test-chain-4", "SH-A120"),
        key=lambda t: t.start_datetime,
    )
    # 3개 태스크가 인접해야 (마지막 end ~ 다음 start 사이 gap < 1h)
    for i in range(len(tasks) - 1):
        gap = (tasks[i + 1].start_datetime - tasks[i].end_datetime).total_seconds() / 60
        assert gap < 60, f"같은 색상 체인 끊어짐: gap={gap}분"
```

Run:

```
cd backend && pytest tests/test_sheath_color_chain.py::test_long_color_chain_not_broken_by_half_week -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```
git add backend/app/services/batch_grouping.py backend/tests/test_sheath_color_chain.py
git commit -m "feat(scheduler): 시스 반-주차 분할로 납기 hard constraint 강화"
```

---

## Task 6: CP-SAT 색상 체인 보너스 (P0-C-3)

**Files:**

- Modify: `backend/app/services/cp_sat_optimizer.py`

**Purpose:** CP-SAT 목적함수에 동일 색상 인접 배치 보너스 항 추가. Stage 2 greedy 는 이미 정렬됐지만 CP-SAT 는 독립 솔버라 명시적 선호 신호 필요.

- [ ] **Step 1: Write failing test**

Append to `test_sheath_color_chain.py`:

```python
def test_cp_sat_color_chain_bonus(db):
    """CP-SAT 솔버가 같은 색상을 연속 배치하는 해를 선호하는지."""
    from app.services.cp_sat_optimizer import cp_sat_schedule

    _seed_sheath_three_groups(db)
    # run_label 달리하여 cp-sat 경로만 테스트
    result = cp_sat_schedule(run_label="test-cpsat-chain", db=db)
    assert result["solver_status"] in ("OPTIMAL", "FEASIBLE")

    tasks = sorted(
        _get_sheath_tasks(db, "test-cpsat-chain", "SH-A120"),
        key=lambda t: t.start_datetime,
    )
    colors = [_get_color(db, t) for t in tasks]
    # 흑 색상이 연속한 블록으로 뭉쳐 있어야 함
    changeovers = sum(1 for i in range(len(colors) - 1) if colors[i] != colors[i + 1])
    assert changeovers <= 1, f"색상 체인지오버 과다: {changeovers}회, 순서={colors}"
```

- [ ] **Step 2: Run test to confirm failure (or pass if lucky)**

```
cd backend && pytest tests/test_sheath_color_chain.py::test_cp_sat_color_chain_bonus -v
```

Expected: FAIL 또는 플래키 — 솔버가 색상 선호 없이 랜덤 해.

- [ ] **Step 3: Add color chain bonus to CP-SAT objective**

In `backend/app/services/cp_sat_optimizer.py`, after block `6-f. 목적함수` (edited in Task 3), add **before** the final `model.minimize(...)` call:

```python
    # 6-g. 색상 체인 보너스: 시스 공정에서 같은 설비 · 같은 색상 인접 배치 선호
    #      원리: 각 시스 그룹 쌍 (gk_i, gk_j) 에 대해
    #           diff_color = 1 if colors 다르면 else 0
    #           same_eq   = equip_vars[gk_i][eq] AND equip_vars[gk_j][eq]
    #           adjacent  = |start_i - start_j| 이 짧은 경우
    #           → 체인지오버 패널티 = diff_color × same_eq × (간단히) 1
    #      본 PoC 에서는 간소화: 동일 색상 그룹끼리 시작 시각 차이 최소화만 적용.
    chain_terms: list[Any] = []
    sheath_groups_by_color: dict[tuple[str, str], list[str]] = {}
    for gk, meta in group_meta.items():
        rep = meta["rep"]
        if rep.process_name not in ("저압시스", "고압시스"):
            continue
        color = (rep.sheath_color or "").strip() or "기타"
        # 설비 카테고리 (A100 / A120) — gk prefix 로 판단
        eq_cat = "A120" if gk.startswith("A120_") else "A100"
        sheath_groups_by_color.setdefault((eq_cat, color), []).append(gk)

    for (eq_cat, color), gks in sheath_groups_by_color.items():
        if len(gks) < 2:
            continue
        for i in range(len(gks) - 1):
            for j in range(i + 1, len(gks)):
                gk_a, gk_b = gks[i], gks[j]
                # |start_a - start_b| 최소화 항
                diff = model.new_int_var(0, _HORIZON_MIN, f"chain_diff_{gk_a}_{gk_b}")
                model.add_abs_equality(diff, start_vars[gk_a] - start_vars[gk_b])
                chain_terms.append(diff)

    CHAIN_WEIGHT = 1  # idle 과 동일 가중치, 납기 대비 훨씬 작게 유지
    model.minimize(
        sum(meta["weight"] * tardiness_vars[gk] for gk, meta in group_meta.items())
        + IDLE_WEIGHT * sum(idle_terms)
        + CHAIN_WEIGHT * sum(chain_terms)
    )
```

> **NOTE**: 기존 `model.minimize` 호출이 위 교체로 유일 하나만 남아야 함 — 기존 `6-f` 블록의 `model.minimize` 를 제거하고 `6-g` 블록 끝의 이 새 minimize 로 대체. `model.minimize` 는 CP-SAT 모델당 한 번만 호출 가능.

- [ ] **Step 4: Run test**

```
cd backend && pytest tests/test_sheath_color_chain.py::test_cp_sat_color_chain_bonus -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```
git add backend/app/services/cp_sat_optimizer.py backend/tests/test_sheath_color_chain.py
git commit -m "feat(cp-sat): 시스 색상 체인 보너스 목적함수 항 추가"
```

---

## Task 7: SchedulerOverlapError 예외 정의 (P0-D-1)

**Files:**

- Create: `backend/app/exceptions.py` (없다면)

**Purpose:** 겹침 재시도 실패 시 라우트 레이어에서 잡아 HTTP 응답으로 변환할 전용 예외.

- [ ] **Step 1: Check if `exceptions.py` already exists**

```
ls backend/app/exceptions.py 2>/dev/null && echo "exists" || echo "create new"
```

- [ ] **Step 2: Create (or append) `backend/app/exceptions.py`**

```python
"""애플리케이션 전역 예외 클래스."""

from __future__ import annotations


class SchedulerOverlapError(Exception):
    """겹침 재시도 2회 실패 — 스케줄 저장 거부 신호.

    라우트 레이어가 이 예외를 잡아 `overlap_alert=True` HTTP 응답을 반환하고,
    기존 DB 스케줄은 수정하지 않는다.
    """

    def __init__(
        self,
        message: str,
        *,
        run_label: str,
        violations: list[dict],
        attempts: int,
    ):
        super().__init__(message)
        self.run_label = run_label
        self.violations = violations
        self.attempts = attempts
```

- [ ] **Step 3: Commit**

```
git add backend/app/exceptions.py
git commit -m "feat(app): SchedulerOverlapError 예외 클래스 추가"
```

---

## Task 8: auto_schedule 래퍼 — 겹침 재시도 2회 + 예외 발생 (P0-D-2)

**Files:**

- Modify: `backend/app/services/schedule_optimizer.py`
- Modify: `backend/app/services/constraint_checker.py` (add `has_overlap` helper)
- Test: `backend/tests/test_overlap_retry.py` (create)

**Purpose:** `validate_all` 결과에 겹침이 있으면 최대 2회 재시도 → 여전히 겹침이면 `SchedulerOverlapError` 발생.

- [ ] **Step 1: Write failing test**

Create `backend/tests/test_overlap_retry.py`:

```python
"""겹침 자동 재최적화 + SchedulerOverlapError 테스트."""
import pytest

from app.exceptions import SchedulerOverlapError


def test_overlap_retry_succeeds_on_second_attempt(db, monkeypatch):
    """겹침 탐지 → 재시도 성공 → 정상 반환."""
    from app.services import schedule_optimizer

    attempts = {"count": 0}
    original = schedule_optimizer._run_optimization_once

    def _flaky_run(*args, **kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            return _make_overlapping_tasks()
        return original(*args, **kwargs)

    monkeypatch.setattr(schedule_optimizer, "_run_optimization_once", _flaky_run)
    _seed_minimal(db)

    result = schedule_optimizer.auto_schedule(run_label="test-retry-1", db=db)
    assert attempts["count"] == 2
    assert result.get("overlap_alert") is False


def test_overlap_persist_raises(db, monkeypatch):
    """겹침이 2회 재시도 후에도 남으면 예외 발생."""
    from app.services import schedule_optimizer

    monkeypatch.setattr(
        schedule_optimizer,
        "_run_optimization_once",
        lambda *a, **kw: _make_overlapping_tasks(),
    )
    _seed_minimal(db)

    with pytest.raises(SchedulerOverlapError) as exc:
        schedule_optimizer.auto_schedule(run_label="test-retry-2", db=db)
    assert exc.value.attempts == 3  # 초기 + 2회 재시도


def _seed_minimal(db):
    from app.infrastructure.models.production_batch import ProductionBatch
    db.add(ProductionBatch(run_label="test-retry-1", batch_group="",
                           process_name="저압절연", sq_mm2=50, drum_count=1,
                           total_length_m=1000, batch_seq=0))
    db.flush()


def _make_overlapping_tasks():
    """겹침이 있는 가짜 결과 반환."""
    from datetime import datetime
    return {
        "tasks_created": [
            type("T", (), dict(task_id=1, equipment_code="EX-B100",
                 start_datetime=datetime(2026, 4, 20, 8, 0),
                 end_datetime=datetime(2026, 4, 20, 12, 0)))(),
            type("T", (), dict(task_id=2, equipment_code="EX-B100",
                 start_datetime=datetime(2026, 4, 20, 10, 0),
                 end_datetime=datetime(2026, 4, 20, 14, 0)))(),
        ],
        "warnings": [],
    }
```

- [ ] **Step 2: Run test to confirm failure**

```
cd backend && pytest tests/test_overlap_retry.py -v
```

Expected: FAIL — `_run_optimization_once` 미정의.

- [ ] **Step 3: Add `has_overlap` helper to `constraint_checker.py`**

Append to `backend/app/services/constraint_checker.py` (after `validate_all`):

```python
def has_overlap(violations: list[dict]) -> bool:
    """validate_all 결과에 겹침 위반이 하나라도 있는지."""
    return any(v.get("constraint_id") == "overlap" for v in violations)
```

- [ ] **Step 4: Refactor `auto_schedule` in `schedule_optimizer.py` to wrap with retry**

In `backend/app/services/schedule_optimizer.py`, find the existing `auto_schedule(...)` function signature. Rename its body to `_run_optimization_once(...)` and add a new `auto_schedule` wrapper:

```python
from app.exceptions import SchedulerOverlapError
from app.services.audit_logger import log_event   # 기존 audit 로깅
from app.services.constraint_checker import validate_all, has_overlap


def auto_schedule(run_label: str, db: Session, **kwargs) -> dict:
    """겹침 재시도 2회 포함 스케줄 생성 래퍼.

    동작:
      1. _run_optimization_once 로 스케줄 생성
      2. validate_all 로 겹침 검증
      3. 겹침 있으면 최대 2회 재시도 (동일 목적함수)
      4. 2회 재시도 후에도 겹침이면 SchedulerOverlapError 발생 (DB 롤백)
    """
    MAX_RETRIES = 2
    for attempt in range(MAX_RETRIES + 1):
        result = _run_optimization_once(run_label, db, **kwargs)
        violations = validate_all(run_label, db)

        if not has_overlap(violations):
            result["overlap_alert"] = False
            return result

        log_event(
            db,
            event="overlap_detected_retry",
            run_label=run_label,
            metadata={"attempt": attempt + 1, "violation_count": len(violations)},
        )
        # 재시도 전 현재 run 의 기존 태스크 삭제
        _purge_run_tasks(db, run_label)

    log_event(
        db,
        event="overlap_persist_alert",
        run_label=run_label,
        severity="high",
        metadata={"attempts": MAX_RETRIES + 1},
    )
    db.rollback()  # 저장 거부
    raise SchedulerOverlapError(
        "스케줄 겹침이 재시도 후에도 지속됩니다.",
        run_label=run_label,
        violations=[v for v in violations if v.get("constraint_id") == "overlap"],
        attempts=MAX_RETRIES + 1,
    )


def _purge_run_tasks(db: Session, run_label: str) -> None:
    """재시도 전 기존 태스크·배치 정리 (rollback 없이 재생성 준비)."""
    from app.infrastructure.models.schedule_task import ScheduleTask
    db.query(ScheduleTask).filter(ScheduleTask.run_label == run_label).delete()
    db.flush()
```

Rename existing `def auto_schedule(...)` body to `def _run_optimization_once(run_label, db, **kwargs)` — 내부 시그니처/본문 동일.

> **NOTE**: `log_event` 함수 시그니처는 `backend/app/services/audit_logger.py` 에서 확인. 현재 파라미터가 다르면 그에 맞춰 호출 맞추기.

- [ ] **Step 5: Run test to confirm pass**

```
cd backend && pytest tests/test_overlap_retry.py -v
```

Expected: 2 passed.

- [ ] **Step 6: Commit**

```
git add backend/app/services/schedule_optimizer.py backend/app/services/constraint_checker.py backend/tests/test_overlap_retry.py
git commit -m "feat(scheduler): 겹침 재시도 2회 + SchedulerOverlapError"
```

---

## Task 9: 스케줄 라우트에 overlap_alert 응답 (P0-D-3)

**Files:**

- Modify: `backend/app/presentation/routes/schedules.py`

**Purpose:** `SchedulerOverlapError` 를 잡아 HTTP 200 응답 + `overlap_alert=True` + 기존 스케줄 유지로 변환.

- [ ] **Step 1: Read current schedule route to find auto_schedule call**

```
grep -n "auto_schedule\|overlap" backend/app/presentation/routes/schedules.py
```

- [ ] **Step 2: Wrap `auto_schedule` call with try/except**

In `backend/app/presentation/routes/schedules.py`, find the endpoint that calls `auto_schedule(...)` (likely POST `/schedule/run` or similar). Wrap the call:

```python
from app.exceptions import SchedulerOverlapError


@router.post("/schedule/run")
def run_schedule(request: RunScheduleRequest, db: Session = Depends(get_db)):
    try:
        result = auto_schedule(run_label=request.run_label, db=db)
        return {
            "status": "ok",
            "overlap_alert": result.get("overlap_alert", False),
            "warnings": result.get("warnings", []),
            **result,
        }
    except SchedulerOverlapError as exc:
        # 저장 거부 — 기존 스케줄 유지, 프론트 배너 트리거
        return {
            "status": "overlap_alert",
            "overlap_alert": True,
            "message": str(exc),
            "violations": exc.violations,
            "attempts": exc.attempts,
        }
```

> **NOTE**: 기존 엔드포인트 응답 형식에 맞춰 조정. 기존 응답이 `ScheduleRunResponse` pydantic 스키마를 쓰면 거기에 `overlap_alert: bool = False` 필드 추가.

- [ ] **Step 3: Add route test**

Append to `backend/tests/test_overlap_retry.py`:

```python
def test_route_returns_overlap_alert_on_persist(client, monkeypatch):
    """HTTP: 겹침 지속 시 overlap_alert=True 응답."""
    from app.services import schedule_optimizer
    from app.exceptions import SchedulerOverlapError

    def _always_raise(*a, **kw):
        raise SchedulerOverlapError(
            "testsim", run_label="test-route-1",
            violations=[{"constraint_id": "overlap", "detail": "x"}],
            attempts=3,
        )

    monkeypatch.setattr(schedule_optimizer, "auto_schedule", _always_raise)

    resp = client.post("/schedule/run", json={"run_label": "test-route-1"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["overlap_alert"] is True
    assert data["status"] == "overlap_alert"
    assert data["attempts"] == 3
```

- [ ] **Step 4: Run test**

```
cd backend && pytest tests/test_overlap_retry.py::test_route_returns_overlap_alert_on_persist -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```
git add backend/app/presentation/routes/schedules.py backend/tests/test_overlap_retry.py
git commit -m "feat(api): 스케줄 라우트에 overlap_alert 응답 추가"
```

---

## Task 10: 프론트 경고 배너 (P0-D-4)

**Files:**

- Modify: `frontend/src/features/scheduler/components/ConstraintAlert.tsx`
- Modify: `frontend/src/app/(main)/scheduler/page.tsx` (overlap_alert 핸들링)

**Purpose:** API 응답 `overlap_alert=True` 시 상단 배너 "스케줄에 겹침이 있습니다 — 관리자 확인 필요" 표시.

> ⚠️ **Next.js 15 주의**: `node_modules/next/dist/docs/` 확인 필요. Server Action, `use client` 경계, `await params`/`searchParams` 등 변경점 있음. 현재 프로젝트 패턴 따르기.

- [ ] **Step 1: Read current `ConstraintAlert.tsx` and `scheduler/page.tsx`**

```
cat frontend/src/features/scheduler/components/ConstraintAlert.tsx | head -100
grep -n "overlap\|constraint\|alert" frontend/src/app/\(main\)/scheduler/page.tsx | head -30
```

- [ ] **Step 2: Add `overlap_alert` variant to ConstraintAlert**

Modify `frontend/src/features/scheduler/components/ConstraintAlert.tsx`:

```typescript
// 상단 import 근처
type Severity = "info" | "warning" | "error" | "overlap";

interface ConstraintAlertProps {
  severity: Severity;
  message: string;
  onDismiss?: () => void;
}

// 본문 렌더 — overlap 케이스 추가
const bgClass = {
  info: "bg-blue-50 border-blue-200 text-blue-800",
  warning: "bg-yellow-50 border-yellow-200 text-yellow-900",
  error: "bg-red-50 border-red-200 text-red-900",
  overlap: "bg-orange-100 border-orange-400 text-orange-900 font-semibold",
}[severity];

// icon: overlap 은 ⚠️ 고정
```

- [ ] **Step 3: Handle overlap_alert in schedule run callback**

In `frontend/src/app/(main)/scheduler/page.tsx`, find the schedule run handler (where `/schedule/run` 응답을 처리). Add state + banner trigger:

```typescript
const [overlapAlert, setOverlapAlert] = useState<string | null>(null);

const runSchedule = useCallback(async () => {
  const res = await apiFetch("/schedule/run", { method: "POST", body: JSON.stringify({ run_label }) });
  const data = await res.json();
  if (data.overlap_alert) {
    setOverlapAlert(
      `스케줄에 겹침이 있습니다 (재시도 ${data.attempts}회 실패) — 관리자 확인 필요. 기존 스케줄을 유지합니다.`
    );
    return;  // 기존 스케줄 유지 (refresh 안 함)
  }
  setOverlapAlert(null);
  // 기존 refresh 로직
}, [run_label]);

// JSX 상단에 배너
{overlapAlert && (
  <ConstraintAlert
    severity="overlap"
    message={overlapAlert}
    onDismiss={() => setOverlapAlert(null)}
  />
)}
```

- [ ] **Step 4: Manual verification**

```
cd frontend && npm run dev
```

Browser: `/scheduler` 페이지 진입 → DevTools Network 탭에서 `/schedule/run` 응답을 `overlap_alert=true` 로 조작(mock) → 배너 표시 확인.

- [ ] **Step 5: Commit**

```
git add frontend/src/features/scheduler/components/ConstraintAlert.tsx frontend/src/app/\(main\)/scheduler/page.tsx
git commit -m "feat(ui): 스케줄 겹침 경고 배너 추가"
```

---

## Task 11: 시스 블록 라벨 D 방식 — 백엔드 spec_list (P1-E-1)

**Files:**

- Modify: `backend/app/presentation/routes/schedules.py` (응답 직렬화)
- Modify: 관련 pydantic 스키마 (있다면)

**Purpose:** 시스 배치블록 응답에 `spec_list: list[str]` 필드 추가 (묶인 수주들의 규격 중복제거 목록).

- [ ] **Step 1: Locate task response serialization**

```
grep -n "sales_orders\|spec\|batch_group" backend/app/presentation/routes/schedules.py | head -30
```

- [ ] **Step 2: Add `spec_list` to response**

In the task → response dict conversion (or pydantic schema), find where task fields are built. Add:

```python
def _compute_spec_list(task, db) -> list[str]:
    """시스 배치 블록의 묶인 수주 규격 목록 (중복 제거, 오름차순)."""
    from app.infrastructure.models.production_batch import ProductionBatch
    batches = (
        db.query(ProductionBatch)
        .filter(ProductionBatch.batch_group == task.batch_group,
                ProductionBatch.run_label == task.run_label)
        .all()
    )
    sqs = sorted({int(b.sq_mm2) for b in batches if b.sq_mm2})
    return [f"{sq}SQ" for sq in sqs]


# response 조립부에서
response["spec_list"] = _compute_spec_list(task, db) if task.equipment_code.startswith("SH-") else None
```

- [ ] **Step 3: Add test**

Create `backend/tests/test_sheath_label_api.py`:

```python
def test_sheath_task_response_has_spec_list(client, db):
    from app.infrastructure.models.production_batch import ProductionBatch

    db.add_all([
        ProductionBatch(run_label="test-label", batch_group="A120_흑_2026W15H1",
                        process_name="저압시스", sheath_color="흑", sq_mm2=50,
                        drum_count=1, total_length_m=500, batch_seq=0),
        ProductionBatch(run_label="test-label", batch_group="A120_흑_2026W15H1",
                        process_name="저압시스", sheath_color="흑", sq_mm2=100,
                        drum_count=1, total_length_m=500, batch_seq=0),
    ])
    db.flush()

    resp = client.post("/schedule/run", json={"run_label": "test-label"})
    assert resp.status_code == 200
    tasks = resp.json().get("tasks", [])
    sheath_tasks = [t for t in tasks if t["equipment_code"].startswith("SH-")]
    assert len(sheath_tasks) >= 1
    assert sheath_tasks[0]["spec_list"] == ["50SQ", "100SQ"]
```

- [ ] **Step 4: Run test**

```
cd backend && pytest tests/test_sheath_label_api.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```
git add backend/app/presentation/routes/schedules.py backend/tests/test_sheath_label_api.py
git commit -m "feat(api): 시스 배치 응답에 spec_list 필드 추가"
```

---

## Task 12: 시스 블록 라벨 D 방식 — 프론트 렌더 (P1-E-2)

**Files:**

- Modify: `frontend/src/features/scheduler/components/GanttTaskBlock.tsx` (lines 301-335 근처)
- Modify: `frontend/src/features/scheduler/types.ts` (또는 기존 Task 타입)

**Purpose:** 시스 블록 라벨을 `[색상]` (1줄) + `50·60·100 SQ` 또는 `50·60·100 SQ +2종` (2줄) 형태로 렌더.

- [ ] **Step 1: Add `spec_list` to Task type**

In `frontend/src/features/scheduler/types.ts` (또는 Task interface 위치), add:

```typescript
export interface ScheduleTask {
  // ... 기존 필드
  spec_list?: string[] | null; // 시스 블록에만 있음
}
```

- [ ] **Step 2: Modify `specLabel` in `GanttTaskBlock.tsx`**

In `frontend/src/features/scheduler/components/GanttTaskBlock.tsx`, find the sheath label branch (lines 308-313):

```typescript
if (SHEATH_EQUIPMENT_IDS.has(task.equipment_id) && task.color) {
  // 색상 + 규격(SQ) 함께 표시
  const sqPart = task.sq_mm2 ? ` · ${task.sq_mm2}SQ` : "";
  return task.color + sqPart;
}
```

Replace with:

```typescript
if (SHEATH_EQUIPMENT_IDS.has(task.equipment_id) && task.color) {
  // D 방식: 색상 1줄 + 규격 목록 1줄 (1-3개 전부, 4개+ 축약)
  const specList = task.spec_list ?? (task.sq_mm2 ? [`${task.sq_mm2}SQ`] : []);
  let sqPart = "";
  if (specList.length > 0) {
    sqPart =
      specList.length <= 3
        ? specList.join("·")
        : `${specList.slice(0, 3).join("·")} +${specList.length - 3}종`;
  }
  // 색상 + 규격 — 폭이 허용하면 한 줄, 좁으면 CSS 가 줄바꿈 처리
  return sqPart ? `${task.color} · ${sqPart}` : task.color;
}
```

- [ ] **Step 3: Adjust label CSS for two-line support**

Find the JSX element rendering `specLabel` (likely `<span>` near line 540). Add `flex-wrap` or `whiteSpace: "normal"` so long labels wrap:

```typescript
<span
  style={{
    fontSize: 10,
    fontWeight: 600,
    whiteSpace: "normal",       // 줄바꿈 허용
    lineHeight: 1.1,
    overflow: "hidden",
    textOverflow: "ellipsis",
    display: "-webkit-box",
    WebkitLineClamp: 2,
    WebkitBoxOrient: "vertical",
  }}
>
  {specLabel}
</span>
```

- [ ] **Step 4: Add Playwright test**

Create `frontend/e2e/sheath-label.spec.ts`:

```typescript
import { test, expect } from "@playwright/test";

test("sheath block shows color + spec list", async ({ page }) => {
  await page.goto("/scheduler");
  await page.waitForSelector('[data-testid^="gantt-block-"]');

  // 시스 블록(SH-A120) 선택
  const sheathBlock = page.locator('[data-equipment-id^="SH-"]').first();
  await expect(sheathBlock).toBeVisible();

  // 색상 · 규격 라벨 존재
  const label = sheathBlock.locator(".block-label, [data-label]").first();
  const text = await label.textContent();
  expect(text).toMatch(/(흑|갈|회|청).*\d+SQ/);
});

test("sheath block with 4+ specs shows abbreviated label", async ({ page }) => {
  await page.goto("/scheduler");
  // 규격 4개 이상인 블록 찾기
  const block = page
    .locator("[data-spec-list-length]")
    .filter({
      has: page.locator(
        '[data-spec-list-length="4"], [data-spec-list-length="5"]',
      ),
    })
    .first();
  if ((await block.count()) === 0) {
    test.skip(true, "seed data 에 4개+ 규격 블록 없음");
  }
  const text = await block.locator(".block-label").textContent();
  expect(text).toMatch(/\+\d+종/);
});
```

- [ ] **Step 5: Run Playwright**

```
cd frontend && npx playwright test e2e/sheath-label.spec.ts
```

Expected: PASS (또는 skip 표시).

- [ ] **Step 6: Commit**

```
git add frontend/src/features/scheduler/components/GanttTaskBlock.tsx frontend/src/features/scheduler/types.ts frontend/e2e/sheath-label.spec.ts
git commit -m "feat(ui): 시스 블록 라벨 D 방식 (색상 + 규격 목록 축약)"
```

---

## Task 13: 기존 구현 검증 — 고내화 · 배지 · 우측 패널 · 주말 (P2-F)

**Files:**

- Test: `frontend/e2e/existing-features.spec.ts` (create)

**Purpose:** 이미 구현된 4개 기능이 실제로 동작하는지 playwright 로 검증. 이상 시 이슈 올리고 별도 수정.

- [ ] **Step 1: Create Playwright test**

`frontend/e2e/existing-features.spec.ts`:

```typescript
import { test, expect } from "@playwright/test";

test.describe("기존 구현 기능 검증", () => {
  test("고내화 배지 ('고') 렌더링", async ({ page }) => {
    await page.goto("/scheduler");
    await page.waitForSelector('[data-testid^="gantt-block-"]');

    // 고내화 배치는 batch_group 이 _고내화 로 끝남
    const gonaehwaBlock = page.locator('[data-batch-group$="_고내화"]').first();
    if ((await gonaehwaBlock.count()) === 0) {
      test.skip(true, "seed data 에 고내화 배치 없음");
    }

    const badge = gonaehwaBlock.locator("text=고").first();
    await expect(badge).toBeVisible();
    // 주황색 배경 확인
    const bg = await badge.evaluate(
      (el) => getComputedStyle(el).backgroundColor,
    );
    expect(bg).toContain("234"); // rgb(234,88,12) 주황
  });

  test("블록 클릭 → 우측 상세 패널", async ({ page }) => {
    await page.goto("/scheduler");
    const firstBlock = page.locator('[data-testid^="gantt-block-"]').first();
    await firstBlock.click();

    // 우측 패널 (최소 너비 420px) 이 보여야 함
    const panel = page.locator('[data-testid="task-detail-panel"]').first();
    await expect(panel).toBeVisible();
    const box = await panel.boundingBox();
    expect(box!.width).toBeGreaterThanOrEqual(400);

    // 간트 차트 오른쪽에 위치 (left 좌표가 가운데 이상)
    const viewportWidth = page.viewportSize()!.width;
    expect(box!.x).toBeGreaterThan(viewportWidth / 2);
  });

  test("주말 접힘 — 토/일 열 너비 8px", async ({ page }) => {
    await page.goto("/scheduler");
    // 주말 열 요소 selector (실제 구현에 따라 조정 필요)
    const weekendCol = page.locator('[data-weekend="true"]').first();
    if ((await weekendCol.count()) === 0) {
      test.skip(true, "주말 열 data attr 없음 — selector 업데이트 필요");
    }
    const box = await weekendCol.boundingBox();
    expect(box!.width).toBeLessThanOrEqual(12); // 8px ± 오차
  });

  test("고내화 저압절연 분리 — 별도 배치 블록", async ({ page }) => {
    await page.goto("/scheduler");
    // 같은 SQ 의 일반 절연 + 고내화 절연이 각각 다른 block 으로 존재
    const sqBlocks = page.locator(
      '[data-process="저압절연"][data-sq-mm2="120"]',
    );
    const gonaehwa = sqBlocks.locator('[data-batch-group$="_고내화"]');
    const normal = sqBlocks.locator(
      '[data-batch-group]:not([data-batch-group$="_고내화"])',
    );

    if ((await gonaehwa.count()) === 0 || (await normal.count()) === 0) {
      test.skip(true, "시드 데이터에 고내화 + 일반 절연 120SQ 쌍 없음");
    }

    // 두 블록은 시간이 겹치지 않아야
    const gBox = await gonaehwa.first().boundingBox();
    const nBox = await normal.first().boundingBox();
    const xOverlap = !(
      gBox!.x + gBox!.width <= nBox!.x || nBox!.x + nBox!.width <= gBox!.x
    );
    expect(xOverlap).toBe(false);
  });
});
```

> **NOTE**: 일부 `data-testid`, `data-*` 속성이 기존 코드에 없으면 먼저 그 속성을 소스 코드에 추가하는 별도 커밋 필요. 추가 시:
>
> - `GanttTaskBlock.tsx`: `data-testid={`gantt-block-${task.id}`}`, `data-batch-group={task.batch_group}`, `data-process={task.process}`, `data-sq-mm2={task.sq_mm2}`, `data-equipment-id={task.equipment_id}`
> - `SchedulerView.tsx` 주말 열: `data-weekend="true"`
> - 우측 패널 wrapper: `data-testid="task-detail-panel"`

- [ ] **Step 2: Add missing `data-*` attributes to source**

Edit `frontend/src/features/scheduler/components/GanttTaskBlock.tsx`. Find the outer `<div>` with block style (around line 393-420). Add:

```tsx
<div
  data-testid={`gantt-block-${task.id}`}
  data-batch-group={task.batch_group ?? ""}
  data-process={task.process_name ?? ""}
  data-sq-mm2={task.sq_mm2 ?? ""}
  data-equipment-id={task.equipment_id}
  data-spec-list-length={(task.spec_list ?? []).length}
  style={...}
  ...
>
```

Edit `SchedulerView.tsx`: find weekend column rendering, add `data-weekend="true"`.

Edit the detail panel wrapper in `scheduler/page.tsx`: add `data-testid="task-detail-panel"`.

- [ ] **Step 3: Run tests**

```
cd frontend && npx playwright test e2e/existing-features.spec.ts
```

Expected: 4 개 테스트, 시드 데이터 부재 시 일부는 `skip`. PASS/SKIP 만 허용.

- [ ] **Step 4: Record failures if any**

테스트 중 실제 기능 누락이 발견되면 별도 fix task 로 commit 분리. (이 플랜에서는 skip 처리 후 사용자에게 보고)

- [ ] **Step 5: Commit**

```
git add frontend/src/features/scheduler/components/GanttTaskBlock.tsx frontend/src/features/scheduler/components/SchedulerView.tsx frontend/src/app/\(main\)/scheduler/page.tsx frontend/e2e/existing-features.spec.ts
git commit -m "test(e2e): 고내화/배지/우측패널/주말접힘 기존 구현 검증 테스트"
```

---

## Task 14: Y축 lane 스태킹 (P2-G)

**Files:**

- Create: `frontend/src/features/scheduler/utils/laneAssign.ts`
- Modify: `frontend/src/features/scheduler/components/SchedulerView.tsx`
- Modify: `frontend/src/features/scheduler/components/GanttTaskBlock.tsx`

**Purpose:** 같은 설비 row 에 시간 겹치는 블록이 있으면 Y축으로 분리 (lane 할당). 백엔드에서 겹침이 완전히 제거된 후의 안전망.

- [ ] **Step 1: Create sweep-line lane assigner with tests**

Create `frontend/src/features/scheduler/utils/laneAssign.ts`:

```typescript
/**
 * Sweep-line 알고리즘으로 동일 row 에 시간 겹치는 블록에 lane 번호를 할당한다.
 * O(n log n). lane 0 부터 채움. 겹침 없으면 모두 lane 0.
 */
export interface LaneInput {
  id: string | number;
  start: number; // epoch ms
  end: number;
}

export interface LaneOutput extends LaneInput {
  lane: number;
}

export function assignLanes(items: LaneInput[]): LaneOutput[] {
  const sorted = [...items].sort((a, b) => a.start - b.start);
  const laneEnds: number[] = []; // lane index → 마지막 end 시각
  const result: LaneOutput[] = [];

  for (const item of sorted) {
    let assigned = -1;
    for (let i = 0; i < laneEnds.length; i++) {
      if (laneEnds[i] <= item.start) {
        assigned = i;
        laneEnds[i] = item.end;
        break;
      }
    }
    if (assigned === -1) {
      assigned = laneEnds.length;
      laneEnds.push(item.end);
    }
    result.push({ ...item, lane: assigned });
  }
  return result;
}

export function getLaneCount(items: LaneOutput[]): number {
  return items.length === 0 ? 1 : Math.max(...items.map((x) => x.lane)) + 1;
}
```

Create test `frontend/src/features/scheduler/utils/laneAssign.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { assignLanes, getLaneCount } from "./laneAssign";

describe("assignLanes", () => {
  it("겹침 없으면 모두 lane 0", () => {
    const out = assignLanes([
      { id: 1, start: 0, end: 10 },
      { id: 2, start: 20, end: 30 },
    ]);
    expect(out.map((x) => x.lane)).toEqual([0, 0]);
  });

  it("두 블록 겹치면 lane 0, 1 분리", () => {
    const out = assignLanes([
      { id: 1, start: 0, end: 20 },
      { id: 2, start: 10, end: 30 },
    ]);
    expect(out.map((x) => x.lane)).toEqual([0, 1]);
  });

  it("lane 재활용 — 앞 lane 이 비면 재사용", () => {
    const out = assignLanes([
      { id: 1, start: 0, end: 10 },
      { id: 2, start: 5, end: 20 },
      { id: 3, start: 15, end: 25 },
    ]);
    expect(out[0].lane).toBe(0);
    expect(out[1].lane).toBe(1);
    expect(out[2].lane).toBe(0); // id=1 끝나서 lane 0 재활용
  });

  it("getLaneCount 반환 — 최대 lane + 1", () => {
    const out = assignLanes([
      { id: 1, start: 0, end: 20 },
      { id: 2, start: 5, end: 10 },
      { id: 3, start: 15, end: 25 },
    ]);
    expect(getLaneCount(out)).toBe(2);
  });
});
```

- [ ] **Step 2: Run tests**

```
cd frontend && npm run test -- laneAssign.test.ts
```

> **NOTE**: 프로젝트에 vitest 가 없으면 jest 또는 playwright test 로 변환. `package.json` 의 test 스크립트 확인.

Expected: 4 passed.

- [ ] **Step 3: Apply laneAssign in SchedulerView**

In `frontend/src/features/scheduler/components/SchedulerView.tsx`, find where tasks are mapped to blocks per equipment row. Before rendering, apply lane assignment:

```typescript
import { assignLanes, getLaneCount } from "../utils/laneAssign";

// 설비별 태스크 그룹
const tasksByEquipment = useMemo(() => {
  const map: Record<string, ScheduleTask[]> = {};
  for (const t of tasks) {
    (map[t.equipment_id] = map[t.equipment_id] || []).push(t);
  }
  return map;
}, [tasks]);

// 각 설비 row 마다 lane 계산
const taskLanes = useMemo(() => {
  const out: Record<number | string, number> = {};
  const laneCounts: Record<string, number> = {};
  for (const [eqId, eqTasks] of Object.entries(tasksByEquipment)) {
    const assigned = assignLanes(
      eqTasks.map((t) => ({
        id: t.id,
        start: new Date(t.start_datetime).getTime(),
        end: new Date(t.end_datetime).getTime(),
      })),
    );
    for (const a of assigned) out[a.id] = a.lane;
    laneCounts[eqId] = getLaneCount(assigned);
  }
  return { taskLanes: out, laneCounts };
}, [tasksByEquipment]);
```

Pass `lane` to `GanttTaskBlock`:

```tsx
<GanttTaskBlock
  task={t}
  lane={taskLanes.taskLanes[t.id] ?? 0}
  laneHeight={LANE_HEIGHT}
  ...
/>
```

Adjust row height:

```tsx
<div
  className="equipment-row"
  style={{ height: `${(taskLanes.laneCounts[eqId] ?? 1) * LANE_HEIGHT}px` }}
>
```

Define constant near top:

```typescript
const LANE_HEIGHT = 32; // 기존 row height 와 일치
```

- [ ] **Step 4: Update `GanttTaskBlock` to use lane for top position**

In `GanttTaskBlock.tsx`, find the `top: 4` hard-coded value. Replace with:

```typescript
top: (lane ?? 0) * (laneHeight ?? 32) + 4,
```

Accept `lane` and `laneHeight` props:

```typescript
interface GanttTaskBlockProps {
  task: ScheduleTask;
  lane?: number;
  laneHeight?: number;
  // ... 기존
}
```

- [ ] **Step 5: Playwright regression — no visible overlap**

Add to `existing-features.spec.ts`:

```typescript
test("블록 겹침 시 Y축 lane 분리", async ({ page }) => {
  await page.goto("/scheduler");
  await page.waitForSelector('[data-testid^="gantt-block-"]');

  const blocks = await page.locator('[data-testid^="gantt-block-"]').all();
  const boxes = await Promise.all(blocks.map((b) => b.boundingBox()));

  // 설비별로 그룹화 (간트에서 블록이 속한 row top 좌표로)
  for (let i = 0; i < boxes.length; i++) {
    for (let j = i + 1; j < boxes.length; j++) {
      const a = boxes[i]!;
      const b = boxes[j]!;
      // 완전 동일 좌표(겹침) 금지
      const sameTop = Math.abs(a.y - b.y) < 2;
      const xOverlap = !(a.x + a.width <= b.x || b.x + b.width <= a.x);
      if (sameTop && xOverlap) {
        throw new Error(`블록 시각 겹침: ${i} vs ${j}`);
      }
    }
  }
});
```

Run:

```
cd frontend && npx playwright test e2e/existing-features.spec.ts
```

Expected: 추가된 테스트 PASS.

- [ ] **Step 6: Commit**

```
git add frontend/src/features/scheduler/utils/laneAssign.ts frontend/src/features/scheduler/utils/laneAssign.test.ts frontend/src/features/scheduler/components/SchedulerView.tsx frontend/src/features/scheduler/components/GanttTaskBlock.tsx frontend/e2e/existing-features.spec.ts
git commit -m "feat(ui): Y축 lane 스태킹 — 블록 시각 겹침 방지 안전망"
```

---

## Self-Review

**Spec coverage check:**

- [x] §1 파이프라인 동기화 → Task 1, 2, 3
- [x] §2 시스 그룹핑 A'' → Task 4, 5, 6
- [x] §3 겹침 자동 재최적화 + 저장 거부 + 배너 → Task 7, 8, 9, 10
- [x] §4 시스 블록 라벨 D 방식 → Task 11, 12
- [x] §5 검증 (고내화·배지·패널·주말) → Task 13
- [x] §리스크 5건 → Task 14 (lane 스태킹 안전망)

**Type consistency:**

- `SchedulerOverlapError(message, run_label, violations, attempts)` — Task 7 정의, Task 8·9 사용 일치
- `spec_list: list[str]` — Task 11 백엔드, Task 12 프론트 typing 일치
- `assignLanes(items) → LaneOutput[]` — Task 14 유일 정의

**Placeholder scan:** TBD·TODO·"implement later" 없음. 모든 step 에 실행 가능한 코드/명령 포함.

**Known fragile points** (실행자 주의):

- Task 2·3 에서 `process_first_output_by_sq` 업데이트 위치(라인 689-723)가 변경된 greedy 블록 뒤에 그대로 있어야 함 — 라인 이동 시 재검증
- Task 8 `log_event` 시그니처는 기존 `audit_logger.py` 에 맞춰 조정
- Task 10·12 에서 Next.js 15 특성상 `use client` 경계 / `await params` 주의

---

**Plan complete and saved to** `docs/plans/2026-04-17-scheduler-pipeline-and-sheath-redesign.md`. Two execution options:

**1. Subagent-Driven (recommended)** — fresh subagent per task, review between tasks
**2. Inline Execution** — batch execution with checkpoints

Which approach?
