# 파이프라인 End-Alignment 누락 수정 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 멀티설비 분배 경로와 CP-SAT 단일 경로에 파이프라인 end-alignment 로직을 적용해 후공정 종료가 선행공정 종료보다 빠르지 않도록 보장한다. 블록 폭(소요시간)은 고정, 시작만 지연.

**Architecture:** `schedule_optimizer.py`에 공통 helper `align_start_to_predecessor_end`를 추출하고, 세 경로(단일 greedy·멀티·CP-SAT 단일)에서 호출한다. 기존 단일 greedy 경로의 인라인 로직은 helper 호출로 리팩토링(동작 불변). CP-SAT 단일 경로의 "end_dt 확장" 블록은 helper 호출로 교체(블록 폭 유지).

**Tech Stack:** Python 3 · SQLAlchemy · pytest · FastAPI (scheduler backend)

**Spec:** `docs/specs/2026-04-18-pipeline-end-alignment-multi-equipment-design.md`

---

## File Structure

**New file:**

- `backend/tests/test_pipeline_alignment.py` — helper 단위 테스트 + 고압 멀티설비 통합 테스트

**Modified:**

- `backend/app/services/schedule_optimizer.py` — helper 추가 + 단일 경로 리팩토링 + 멀티설비 경로에 호출 삽입
- `backend/app/services/cp_sat_optimizer.py` — helper import + 단일 경로 "end_dt 확장" 블록 교체

**No placeholders / dead code removed:** CP-SAT 단일 경로 라인 1027-1056의 "파이프라인 겹침 보정(end_dt 확장)" 블록은 삭제 — helper가 대체.

---

### Task 1: helper `align_start_to_predecessor_end` + 단위 테스트

**Files:**

- Modify: `backend/app/services/schedule_optimizer.py` (helper 추가, 위치: `_find_available_slot` 정의 바로 위 = 기존 라인 1510 근처)
- Create: `backend/tests/test_pipeline_alignment.py`

- [ ] **Step 1: 실패 테스트 작성**

`backend/tests/test_pipeline_alignment.py`:

```python
"""End-alignment helper 단위 테스트 — DB 없이 순수 함수 시나리오.

의도: calculate_start_datetime/calculate_end_datetime/_find_available_slot 은
DB·캘린더에 의존하므로 이 테스트는 *helper 동작의 논리적 계약*만 검증한다.
실제 스케줄러 경로 테스트는 test_pipeline_sync.py 참조.
"""

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest


def _mock_db():
    """calculate_start/end 가 호출되면 canonical 24h/일 캘린더처럼 동작하는 mock."""
    return MagicMock()


def test_returns_input_when_no_predecessor_end_recorded(monkeypatch):
    """process_end_by_sq 에 예상 선행공정 종료가 없으면 start/end 변경 없음."""
    from app.services import schedule_optimizer

    current_start = datetime(2026, 4, 10, 8, 0)
    current_end = datetime(2026, 4, 15, 8, 0)

    aligned_start, aligned_end = schedule_optimizer.align_start_to_predecessor_end(
        process_name="고압시스",
        pred_proc="고압절연",
        group_sqs={300},
        process_end_by_sq={},  # 비어있음
        current_start=current_start,
        current_end=current_end,
        duration_min=5 * 24 * 60,
        slots=[],
        db=_mock_db(),
        equipment_code="SH-A150",
    )

    assert aligned_start == current_start
    assert aligned_end == current_end


def test_returns_input_when_already_aligned(monkeypatch):
    """현재 end >= pred_end_latest 이면 변경 없음 (reverse_start ≤ current_start)."""
    from app.services import schedule_optimizer

    # calculate_start_datetime(pred_end=4/15, duration=5일)=4/10 → current_start(=4/10)와 같음
    monkeypatch.setattr(
        schedule_optimizer,
        "calculate_start_datetime",
        lambda end, dur, db, eq: end - timedelta(minutes=dur),
    )
    monkeypatch.setattr(
        schedule_optimizer,
        "calculate_end_datetime",
        lambda start, dur, db, eq: start + timedelta(minutes=dur),
    )
    monkeypatch.setattr(
        schedule_optimizer,
        "_find_available_slot",
        lambda earliest, dur, slots, db, eq: earliest,
    )

    current_start = datetime(2026, 4, 10, 8, 0)
    current_end = datetime(2026, 4, 15, 8, 0)

    aligned_start, aligned_end = schedule_optimizer.align_start_to_predecessor_end(
        process_name="고압시스",
        pred_proc="고압절연",
        group_sqs={300},
        process_end_by_sq={("고압절연", 300): datetime(2026, 4, 15, 8, 0)},
        current_start=current_start,
        current_end=current_end,
        duration_min=5 * 24 * 60,
        slots=[],
        db=_mock_db(),
        equipment_code="SH-A150",
    )

    assert aligned_start == current_start
    assert aligned_end == current_end


def test_delays_start_when_predecessor_ends_later(monkeypatch):
    """pred_end > current_end 이면 reverse_start = pred_end - duration, start 지연.
    불변식: aligned_end == pred_end (블록 폭 유지)."""
    from app.services import schedule_optimizer

    monkeypatch.setattr(
        schedule_optimizer,
        "calculate_start_datetime",
        lambda end, dur, db, eq: end - timedelta(minutes=dur),
    )
    monkeypatch.setattr(
        schedule_optimizer,
        "calculate_end_datetime",
        lambda start, dur, db, eq: start + timedelta(minutes=dur),
    )
    monkeypatch.setattr(
        schedule_optimizer,
        "_find_available_slot",
        lambda earliest, dur, slots, db, eq: earliest,
    )

    current_start = datetime(2026, 4, 9, 8, 0)
    duration_min = 7 * 24 * 60  # 7일
    current_end = current_start + timedelta(minutes=duration_min)  # 4/16 08:00

    aligned_start, aligned_end = schedule_optimizer.align_start_to_predecessor_end(
        process_name="고압시스",
        pred_proc="고압절연",
        group_sqs={300},
        process_end_by_sq={("고압절연", 300): datetime(2026, 4, 17, 8, 0)},
        current_start=current_start,
        current_end=current_end,
        duration_min=duration_min,
        slots=[],
        db=_mock_db(),
        equipment_code="SH-A150",
    )

    # pred_end=4/17, reverse_start = 4/17 - 7일 = 4/10 08:00
    assert aligned_start == datetime(2026, 4, 10, 8, 0)
    # 블록 폭 유지: end - start == duration_min
    assert (aligned_end - aligned_start).total_seconds() / 60 == duration_min
    # 불변식: end >= pred_end
    assert aligned_end == datetime(2026, 4, 17, 8, 0)


def test_mixed_sq_uses_max_pred_end(monkeypatch):
    """혼합 SQ 그룹(고압시스 색상별) — 모든 SQ의 pred_end 중 최대값 사용."""
    from app.services import schedule_optimizer

    monkeypatch.setattr(
        schedule_optimizer,
        "calculate_start_datetime",
        lambda end, dur, db, eq: end - timedelta(minutes=dur),
    )
    monkeypatch.setattr(
        schedule_optimizer,
        "calculate_end_datetime",
        lambda start, dur, db, eq: start + timedelta(minutes=dur),
    )
    monkeypatch.setattr(
        schedule_optimizer,
        "_find_available_slot",
        lambda earliest, dur, slots, db, eq: earliest,
    )

    duration_min = 24 * 60
    current_start = datetime(2026, 4, 9, 8, 0)
    current_end = current_start + timedelta(minutes=duration_min)

    aligned_start, aligned_end = schedule_optimizer.align_start_to_predecessor_end(
        process_name="고압시스",
        pred_proc="고압절연",
        group_sqs={240, 300},
        process_end_by_sq={
            ("고압절연", 240): datetime(2026, 4, 15, 8, 0),
            ("고압절연", 300): datetime(2026, 4, 17, 8, 0),  # 더 늦음
        },
        current_start=current_start,
        current_end=current_end,
        duration_min=duration_min,
        slots=[],
        db=_mock_db(),
        equipment_code="SH-A150",
    )

    # 최대값인 4/17 기준으로 정렬
    assert aligned_end == datetime(2026, 4, 17, 8, 0)
    assert aligned_start == datetime(2026, 4, 16, 8, 0)


def test_sheath_also_checks_assembly_end(monkeypatch):
    """시스(저압/고압)는 절연 외에 연합 종료도 선행으로 고려."""
    from app.services import schedule_optimizer

    monkeypatch.setattr(
        schedule_optimizer,
        "calculate_start_datetime",
        lambda end, dur, db, eq: end - timedelta(minutes=dur),
    )
    monkeypatch.setattr(
        schedule_optimizer,
        "calculate_end_datetime",
        lambda start, dur, db, eq: start + timedelta(minutes=dur),
    )
    monkeypatch.setattr(
        schedule_optimizer,
        "_find_available_slot",
        lambda earliest, dur, slots, db, eq: earliest,
    )

    duration_min = 24 * 60
    current_start = datetime(2026, 4, 9, 8, 0)
    current_end = current_start + timedelta(minutes=duration_min)

    aligned_start, aligned_end = schedule_optimizer.align_start_to_predecessor_end(
        process_name="저압시스",
        pred_proc="저압절연",
        group_sqs={50},
        process_end_by_sq={
            ("저압절연", 50): datetime(2026, 4, 12, 8, 0),
            ("연합", 50): datetime(2026, 4, 14, 8, 0),  # 연합이 더 늦음
        },
        current_start=current_start,
        current_end=current_end,
        duration_min=duration_min,
        slots=[],
        db=_mock_db(),
        equipment_code="SH-A100",
    )

    assert aligned_end == datetime(2026, 4, 14, 8, 0)
```

- [ ] **Step 2: 테스트 실행 — 실패 확인**

Run: `cd backend && python -m pytest tests/test_pipeline_alignment.py -v`

Expected: 모든 테스트 FAIL (`AttributeError: module ... has no attribute 'align_start_to_predecessor_end'`)

- [ ] **Step 3: helper 구현**

`backend/app/services/schedule_optimizer.py` 의 `def _find_available_slot(` 선언 바로 위(기존 라인 1510 근처)에 삽입:

```python
def align_start_to_predecessor_end(
    *,
    process_name: str,
    pred_proc: str | None,
    group_sqs: set[int],
    process_end_by_sq: dict[tuple[str, int], datetime],
    current_start: datetime,
    current_end: datetime,
    duration_min: float,
    slots: list,
    db: "Session",
    equipment_code: str,
) -> tuple[datetime, datetime]:
    """후공정 종료가 선행공정 종료 이상이 되도록 시작을 지연한다.

    불변식:
      - aligned_end >= pred_end_latest (후공정 끝 ≥ 선행공정 끝)
      - aligned_end - aligned_start == duration_min (블록 폭 유지, 캘린더 보정 오차 허용)

    시스 공정(저압시스/고압시스)은 pred_proc 외에 "연합" 종료도 함께 고려.
    pred_proc 가 None 이거나 process_end_by_sq 에 기록이 없으면 입력 그대로 반환.

    반환: (aligned_start, aligned_end)
    """
    pipeline_procs: list[str] = []
    if pred_proc:
        pipeline_procs.append(pred_proc)
    if process_name in ("저압시스", "고압시스"):
        pipeline_procs.append("연합")

    if not pipeline_procs:
        return current_start, current_end

    pred_end_latest: datetime | None = None
    for pp in pipeline_procs:
        for sq_i in group_sqs:
            pe = process_end_by_sq.get((pp, sq_i))
            if pe and pe < datetime.max:
                if pred_end_latest is None or pe > pred_end_latest:
                    pred_end_latest = pe

    if pred_end_latest is None:
        return current_start, current_end

    aligned_start = current_start
    aligned_end = current_end

    reverse_start = calculate_start_datetime(
        pred_end_latest, duration_min, db, equipment_code
    )
    if reverse_start > current_start:
        aligned_start = _find_available_slot(
            reverse_start, duration_min, slots, db, equipment_code
        )
        aligned_end = calculate_end_datetime(
            aligned_start, duration_min, db, equipment_code
        )

    # 캘린더 보정 오차 대비: end < pred_end_latest 면 bump
    if aligned_end < pred_end_latest:
        aligned_end = pred_end_latest

    return aligned_start, aligned_end
```

- [ ] **Step 4: 테스트 재실행 — PASS 확인**

Run: `cd backend && python -m pytest tests/test_pipeline_alignment.py -v`

Expected: 5 passed

- [ ] **Step 5: 전체 스케줄러 회귀 테스트**

Run: `cd backend && python -m pytest tests/test_pipeline_sync.py tests/test_schedule_optimizer.py -v 2>&1 | tail -30`

Expected: 기존 테스트 모두 PASS (helper 추가만 했을 뿐 경로는 변경 전)

- [ ] **Step 6: 커밋**

```bash
cd /Users/jaewookim/Desktop/Project/KBI_PoC
git add backend/app/services/schedule_optimizer.py backend/tests/test_pipeline_alignment.py
git commit -m "feat(scheduler): align_start_to_predecessor_end helper + 단위 테스트

파이프라인 end-alignment 로직을 공통 helper 로 추출.
아직 호출처는 없음 — 다음 커밋에서 단일/멀티/CP-SAT 경로에 적용."
```

---

### Task 2: 단일 greedy 경로 리팩토링 (helper 호출로 교체, 동작 불변)

**Files:**

- Modify: `backend/app/services/schedule_optimizer.py:869-910`

- [ ] **Step 1: 기존 인라인 블록을 helper 호출로 교체**

`backend/app/services/schedule_optimizer.py` 라인 869-910 (기존 "파이프라인 유휴 최소 역산 공식" 블록)을 다음으로 교체:

```python
        # ── 파이프라인 유휴 최소 역산 공식 ────────────────────────────────────
        # T_succ_start = max(T_pred_first_drum, T_pred_end - D_succ)
        # 불변식: T_succ_end >= T_pred_end (후공정 끝 ≥ 선행공정 끝)
        # 효과: 절연 선속이 연선보다 빠르면 시작을 늦춰 끝을 정렬. 블록 width 불변.
        best_start, end_dt = align_start_to_predecessor_end(
            process_name=rep.process_name,
            pred_proc=pred_proc,
            group_sqs={int(b.sq_mm2 or 0) for b in group_batches},
            process_end_by_sq=process_end_by_sq,
            current_start=best_start,
            current_end=end_dt,
            duration_min=best_total_duration,
            slots=timeline.get(best_eq.equipment_code, []),
            db=db,
            equipment_code=best_eq.equipment_code,
        )
```

(기존 `_pipeline_check_procs`/`_all_sqs_g`/`pred_end_latest`/`reverse_start`/`delayed_start` 로컬 변수 모두 제거. `slots` 조회는 `timeline.get(best_eq.equipment_code, [])` 로 복원 — 기존 코드에서 루프 밖이라 `slots` 가 루프 마지막 반복의 eligible 이었던 잠재 버그 수정.)

- [ ] **Step 2: 기존 파이프라인 회귀 테스트 실행**

Run: `cd backend && python -m pytest tests/test_pipeline_sync.py -v`

Expected: `test_insulation_end_aligns_with_stranding_end`, `test_insulation_block_width_unchanged`, `test_assembly_to_sheath_pipeline_end_constraint` 모두 PASS (단일 경로는 저압이 주로 탐)

- [ ] **Step 3: 전체 backend 단위 테스트**

Run: `cd backend && python -m pytest -x 2>&1 | tail -20`

Expected: 모두 PASS

- [ ] **Step 4: 커밋**

```bash
git add backend/app/services/schedule_optimizer.py
git commit -m "refactor(scheduler): 단일설비 greedy 경로 end-alignment를 helper 호출로 교체

동작 변경 없음. 기존 인라인 _pipeline_check_procs/reverse_start 블록을
align_start_to_predecessor_end helper 호출로 리팩토링. 루프 밖의 slots
참조가 마지막 eligible 것이던 잠재 버그도 timeline 재조회로 해소."
```

---

### Task 3: 멀티설비 경로에 end-alignment 적용 (주요 버그 수정)

**Files:**

- Modify: `backend/app/services/schedule_optimizer.py:1272-1350` (`_schedule_multi_equipment` 서브태스크 루프 내부)

- [ ] **Step 1: 먼저 실패하는 통합 테스트 작성**

`backend/tests/test_pipeline_alignment.py` 하단에 추가:

```python
# ---- 통합 테스트 (실제 스케줄러 + DB) ----


def test_high_voltage_sheath_ends_after_high_voltage_insulation(db):
    """고압시스 최종 종료가 고압절연 최종 종료 이상 (멀티설비 분배 경로).

    재현: 2026-04-18 자동배열에서 고압시스(A150+B100)가 고압절연(CV#1+CV#2)
    보다 하루 먼저 끝나던 버그. _schedule_multi_equipment 에 end-alignment
    역산이 없어서 발생.
    """
    from datetime import date

    from app.infrastructure.models.production_batch import ProductionBatch
    from app.infrastructure.models.schedule_task import ScheduleTask
    from app.services.schedule_optimizer import auto_schedule

    run_label = "test-hv-align"

    # 고압절연 2드럼 (CV#1+CV#2 분배 발동 조건: drums≥2, eligible≥2)
    # 고압시스 2드럼 (A150+B100 분배 발동)
    for i in range(2):
        db.add(
            ProductionBatch(
                run_label=run_label,
                batch_seq=i,
                process_name="고압절연",
                sq_mm2=300,
                drum_count=1,
                drum_length_m=8000,  # 길이 크게 → 절연 duration 길어짐
                total_length_m=8000,
                conductor_material="CU",
                voltage="22.9kV",
                sales_order_id=f"SO-HV-{i + 1}",
                sales_order_line=1,
                batch_group="",
                status="planned",
            )
        )
        db.add(
            ProductionBatch(
                run_label=run_label,
                batch_seq=i,
                process_name="고압시스",
                sheath_color="흑",
                sq_mm2=300,
                due_date=date(2026, 4, 30),
                drum_count=1,
                drum_length_m=2000,  # 길이 작게 → 시스 duration 짧음 (버그 조건)
                total_length_m=2000,
                conductor_material="CU",
                voltage="22.9kV",
                sales_order_id=f"SO-HV-{i + 1}",
                sales_order_line=1,
                batch_group="",
                status="planned",
            )
        )
    db.flush()

    auto_schedule(run_label=run_label, db=db)

    insul_tasks = (
        db.query(ScheduleTask)
        .join(ProductionBatch, ScheduleTask.batch_id == ProductionBatch.batch_id)
        .filter(
            ScheduleTask.run_label == run_label,
            ProductionBatch.process_name == "고압절연",
        )
        .all()
    )
    sheath_tasks = (
        db.query(ScheduleTask)
        .join(ProductionBatch, ScheduleTask.batch_id == ProductionBatch.batch_id)
        .filter(
            ScheduleTask.run_label == run_label,
            ProductionBatch.process_name == "고압시스",
        )
        .all()
    )
    assert insul_tasks, "고압절연 task 생성 실패 (시드 부족?)"
    assert sheath_tasks, "고압시스 task 생성 실패 (시드 부족?)"

    insul_last_end = max(t.end_datetime for t in insul_tasks)
    sheath_last_end = max(t.end_datetime for t in sheath_tasks)

    # 불변식: 고압시스 최종 종료 >= 고압절연 최종 종료
    assert sheath_last_end >= insul_last_end, (
        f"고압시스 최종 종료 {sheath_last_end} < 고압절연 최종 종료 {insul_last_end} "
        f"— end-alignment 실패 (버그 회귀)"
    )


def test_high_voltage_sheath_block_width_preserved(db):
    """멀티설비 경로 end-alignment 후에도 블록 폭(end-start)이 커지지 않음."""
    from datetime import date, timedelta

    from app.infrastructure.models.production_batch import ProductionBatch
    from app.infrastructure.models.schedule_task import ScheduleTask
    from app.services.schedule_optimizer import auto_schedule

    run_label = "test-hv-width"
    for i in range(2):
        db.add(
            ProductionBatch(
                run_label=run_label,
                batch_seq=i,
                process_name="고압절연",
                sq_mm2=300,
                drum_count=1,
                drum_length_m=8000,
                total_length_m=8000,
                conductor_material="CU",
                voltage="22.9kV",
                sales_order_id=f"SO-W-{i + 1}",
                sales_order_line=1,
                batch_group="",
                status="planned",
            )
        )
        db.add(
            ProductionBatch(
                run_label=run_label,
                batch_seq=i,
                process_name="고압시스",
                sheath_color="흑",
                sq_mm2=300,
                due_date=date(2026, 4, 30),
                drum_count=1,
                drum_length_m=2000,
                total_length_m=2000,
                conductor_material="CU",
                voltage="22.9kV",
                sales_order_id=f"SO-W-{i + 1}",
                sales_order_line=1,
                batch_group="",
                status="planned",
            )
        )
    db.flush()

    # alignment 전 기대 폭 = 2000m / SpeedMaster(A150/SH-B100 선속) — 상한만 검증
    auto_schedule(run_label=run_label, db=db)

    sheath_tasks = (
        db.query(ScheduleTask)
        .join(ProductionBatch, ScheduleTask.batch_id == ProductionBatch.batch_id)
        .filter(
            ScheduleTask.run_label == run_label,
            ProductionBatch.process_name == "고압시스",
        )
        .all()
    )
    assert sheath_tasks

    for t in sheath_tasks:
        width_min = (t.end_datetime - t.start_datetime).total_seconds() / 60
        # 블록 폭이 24h(= 1일) 이내 — 2000m는 시스 선속상 수 시간 내 작업. 극단적 확장 방지.
        assert width_min <= 24 * 60, (
            f"고압시스 블록 폭 {width_min}분 (> 24h). 확장 버그 회귀 의심."
        )
```

- [ ] **Step 2: 테스트 실행 — 통합 테스트 실패 확인**

Run: `cd backend && python -m pytest tests/test_pipeline_alignment.py::test_high_voltage_sheath_ends_after_high_voltage_insulation -v`

Expected: FAIL — 고압시스 최종 종료가 고압절연보다 앞서 (버그 재현)

- [ ] **Step 3: `_schedule_multi_equipment` 서브태스크 루프에 helper 호출 삽입**

`backend/app/services/schedule_optimizer.py:1311-1321` 에서 `slot_start = _find_available_slot(...)` 직후·`end_dt = calculate_end_datetime(...)` 호출 블록을 다음으로 교체:

```python
        slots = timeline.get(eq_code, [])
        slot_start = _find_available_slot(
            earliest, eq_total_duration, slots, db, eq_code
        )
        end_dt = calculate_end_datetime(slot_start, eq_total_duration, db, eq_code)

        # ── 파이프라인 유휴 최소 역산 — 서브태스크별 독립 적용 ────────────────
        # duration(= eq_total_duration)은 드럼 수 비례이므로 서브태스크마다 다름.
        # 각 서브태스크가 선행공정 종료 이상에서 끝나도록 개별 정렬.
        slot_start, end_dt = align_start_to_predecessor_end(
            process_name=rep.process_name,
            pred_proc=pred_proc,
            group_sqs={int(b.sq_mm2 or 0) for b in group_batches},
            process_end_by_sq=process_end_by_sq,
            current_start=slot_start,
            current_end=end_dt,
            duration_min=eq_total_duration,
            slots=slots,
            db=db,
            equipment_code=eq_code,
        )

        # 시간 올림 — 간트 블록은 정각 단위
```

(기존 `# 시간 올림 —` 주석 이후 코드는 그대로 유지.)

- [ ] **Step 4: 통합 테스트 재실행 — PASS 확인**

Run: `cd backend && python -m pytest tests/test_pipeline_alignment.py -v`

Expected: 7 passed (helper 단위 5 + 통합 2)

- [ ] **Step 5: 전체 스케줄러 관련 테스트 실행**

Run: `cd backend && python -m pytest tests/test_pipeline_sync.py tests/test_schedule_optimizer.py tests/test_sheath_sq_split.py tests/test_sheath_color_chain.py -v 2>&1 | tail -30`

Expected: 모두 PASS

- [ ] **Step 6: 커밋**

```bash
git add backend/app/services/schedule_optimizer.py backend/tests/test_pipeline_alignment.py
git commit -m "fix(scheduler): 멀티설비 경로 end-alignment 누락 — 고압시스 조기 종료 수정

_schedule_multi_equipment 서브태스크 루프에 align_start_to_predecessor_end
호출 추가. 서브태스크별 duration 에 대해 개별 역산하여 블록 폭 유지하면서
종료만 선행공정 종료 이상으로 정렬.

재현 케이스: 고압절연(CV#1+CV#2) → 고압시스(A150+B100), 시스 블록이
절연보다 하루 먼저 끝나던 현상. 통합 테스트 2건으로 보장."
```

---

### Task 4: CP-SAT 단일 경로의 "end_dt 확장" 블록을 helper로 교체

**Files:**

- Modify: `backend/app/services/cp_sat_optimizer.py` (import 추가 + 라인 1020-1056 교체)

- [ ] **Step 1: 기존 CP-SAT 파이프라인 테스트 확인 (현재 PASS 여야 정상)**

Run: `cd backend && python -m pytest tests/test_pipeline_sync.py::test_cp_sat_pipeline_end_constraint -v`

Expected: PASS (기존 "end_dt 확장" 방식이 불변식 `pred_end <= succ_end` 는 만족. 블록 폭은 커짐.)

- [ ] **Step 2: 블록 폭 회귀 테스트 추가 (실패 확인용)**

`backend/tests/test_pipeline_alignment.py` 하단에 추가:

```python
def test_cp_sat_single_path_block_width_preserved(db):
    """CP-SAT 단일 경로: end-alignment 후 블록 폭(end-start)이 duration과 거의 같음.

    기존 "end_dt 확장" 방식은 best_start 유지 + end_dt 만 pred_end+1드럼으로
    늘려 블록 폭이 크게 증가했다. helper 교체 후 블록 폭은 duration 에 고정.
    """
    from app.infrastructure.models.production_batch import ProductionBatch
    from app.infrastructure.models.schedule_task import ScheduleTask
    from app.services.cp_sat_optimizer import cp_sat_schedule

    run_label = "test-cpsat-width"
    db.add(
        ProductionBatch(
            run_label=run_label,
            batch_seq=0,
            process_name="연선",
            sq_mm2=50,
            drum_count=1,
            drum_length_m=6000,
            total_length_m=6000,
            sales_order_id="SO-CPSAT-W",
            sales_order_line=1,
            batch_group="",
            status="planned",
            conductor_material="CU",
        )
    )
    db.add(
        ProductionBatch(
            run_label=run_label,
            batch_seq=0,
            process_name="저압절연",
            sq_mm2=50,
            drum_count=1,
            drum_length_m=6000,  # 선속 차이로 절연 duration << 연선 duration
            total_length_m=6000,
            sales_order_id="SO-CPSAT-W",
            sales_order_line=1,
            batch_group="",
            status="planned",
            conductor_material="CU",
        )
    )
    db.flush()

    result = cp_sat_schedule(run_label=run_label, db=db)
    if result.get("solver_status") not in ("OPTIMAL", "FEASIBLE"):
        import pytest

        pytest.skip(f"CP-SAT solver failed: {result.get('solver_status')}")

    insul = (
        db.query(ScheduleTask)
        .join(ProductionBatch, ScheduleTask.batch_id == ProductionBatch.batch_id)
        .filter(
            ScheduleTask.run_label == run_label,
            ProductionBatch.process_name == "저압절연",
        )
        .first()
    )
    assert insul
    width_min = (insul.end_datetime - insul.start_datetime).total_seconds() / 60
    # 절연 duration 은 선속상 수십 분. 블록 폭이 24h 를 넘으면 "end_dt 확장" 회귀.
    assert width_min <= 24 * 60, (
        f"저압절연 블록 폭 {width_min}분 (> 24h). CP-SAT end_dt 확장 회귀."
    )
```

- [ ] **Step 3: 새 테스트가 현재 실패하는지 확인**

Run: `cd backend && python -m pytest tests/test_pipeline_alignment.py::test_cp_sat_single_path_block_width_preserved -v`

Expected: FAIL — 블록 폭이 24h 초과 (end_dt 확장 방식 회귀 증명)

- [ ] **Step 4: `cp_sat_optimizer.py` import 추가**

파일 상단 import 섹션(`from app.services.schedule_optimizer import` 문이 있는 곳 근처)에:

```python
from app.services.schedule_optimizer import (
    _schedule_multi_equipment,
    align_start_to_predecessor_end,
)
```

(기존에 `from app.services.schedule_optimizer import _schedule_multi_equipment` 만 있었다면 두 번째 줄 추가. 여러 줄이면 alphabetical.)

- [ ] **Step 5: 라인 1020-1056 블록 교체**

`backend/app/services/cp_sat_optimizer.py` 에서 다음 범위:

```python
        # 캘린더 인식 슬롯 탐색 — 겹침 완전 방지
        slots = timeline.get(chosen_eq_code, [])
        best_start = _find_available_slot(
            earliest, total_dur, slots, db, chosen_eq_code
        )
        end_dt = calculate_end_datetime(best_start, total_dur, db, chosen_eq_code)

        # ── 파이프라인 겹침 보정: 후공정이 선행공정 종료 전에 끝나지 않도록 ───
        # ... (기존 _pipeline_procs/_min_end/end_dt 확장 블록 전체)
```

를 다음으로 교체:

```python
        # 캘린더 인식 슬롯 탐색 — 겹침 완전 방지
        slots = timeline.get(chosen_eq_code, [])
        best_start = _find_available_slot(
            earliest, total_dur, slots, db, chosen_eq_code
        )
        end_dt = calculate_end_datetime(best_start, total_dur, db, chosen_eq_code)

        # ── 파이프라인 유휴 최소 역산 — start 지연 방식 ──────────────────────
        # 기존 "end_dt 확장" 방식은 블록 폭이 늘어나 소요시간 고정 요건을 위반.
        # helper 는 reverse_start = pred_end - duration 으로 start 만 지연.
        best_start, end_dt = align_start_to_predecessor_end(
            process_name=rep.process_name,
            pred_proc=pred_proc,
            group_sqs={int(b.sq_mm2 or 0) for b in gb},
            process_end_by_sq=process_end_by_sq,
            current_start=best_start,
            current_end=end_dt,
            duration_min=total_dur,
            slots=slots,
            db=db,
            equipment_code=chosen_eq_code,
        )
```

- [ ] **Step 6: 블록 폭 테스트 재실행 — PASS 확인**

Run: `cd backend && python -m pytest tests/test_pipeline_alignment.py::test_cp_sat_single_path_block_width_preserved -v`

Expected: PASS

- [ ] **Step 7: 기존 CP-SAT 파이프라인 제약 테스트도 PASS 확인**

Run: `cd backend && python -m pytest tests/test_pipeline_sync.py::test_cp_sat_pipeline_end_constraint -v`

Expected: PASS (end-alignment 방식만 바뀌었지 불변식은 동일)

- [ ] **Step 8: 전체 backend 테스트**

Run: `cd backend && python -m pytest -x --timeout=120 2>&1 | tail -30`

Expected: 전부 PASS

- [ ] **Step 9: 커밋**

```bash
git add backend/app/services/cp_sat_optimizer.py backend/tests/test_pipeline_alignment.py
git commit -m "fix(cp-sat): 단일경로 파이프라인 정렬 end_dt 확장 → start 지연 방식으로 교체

기존 로직은 best_start 유지하고 end_dt 를 pred_end+per_drum_duration 까지
늘리는 방식이라 블록 폭이 크게 커졌다(소요시간 고정 요건 위반).
align_start_to_predecessor_end helper 를 호출해 start 만 지연하도록 변경.

회귀 테스트: test_cp_sat_single_path_block_width_preserved"
```

---

### Task 5: E2E 검증 — 브라우저에서 간트 확인

**Files:** (코드 변경 없음. 실제 스케줄러 API 호출 + UI 확인)

- [ ] **Step 1: 백엔드 실행**

Run (백그라운드): `cd /Users/jaewookim/Desktop/Project/KBI_PoC/backend && . venv/bin/activate && uvicorn app.main:app --reload --port 8000`

Expected: FastAPI 기동

- [ ] **Step 2: 프론트엔드 실행**

Run (백그라운드): `cd /Users/jaewookim/Desktop/Project/KBI_PoC/frontend && npm run dev`

Expected: Next.js dev server 기동 (보통 포트 3000)

- [ ] **Step 3: 브라우저로 스케줄러 페이지 접근 + 자동배열 실행**

`browse` 스킬 또는 Playwright 로:

1. `http://localhost:3000/scheduler` 접근
2. "고압만" 필터 클릭
3. "자동배열" 버튼 클릭
4. 기다린 후 스크린샷 캡처

- [ ] **Step 4: Gantt 종료일 검증**

스크린샷 또는 DOM 조회로 확인:

- 고압절연(CV 1호, CV 2호)의 마지막 블록 end date
- 고압시스(A150EXT, B150EXT)의 마지막 블록 end date
- **불변식**: 고압시스 end >= 고압절연 end (하루 이상 앞서는 현상 없음)

- [ ] **Step 5: 기록 + 커밋 (스크린샷 파일이 있으면)**

스크린샷을 `docs/specs/assets/2026-04-18-high-voltage-aligned.png` 등 경로로 저장 후:

```bash
git add docs/specs/assets/
git commit -m "docs(spec): 고압 파이프라인 end-alignment 수정 E2E 검증 스크린샷"
```

(스크린샷 저장이 번거로우면 이 단계는 생략 가능. 필수는 아니지만 사용자 CLAUDE.md에 'UI 기능 Playwright 검증 필수' 가이드 있음.)

---

## Self-Review 결과

**Spec coverage:**

- 2.1 멀티설비 누락 → Task 3 ✅
- 2.2 CP-SAT 블록 폭 확장 → Task 4 ✅
- 3.2 공통 helper 추출 → Task 1 ✅
- 3.3 멀티 경로 적용 → Task 3 ✅
- 3.4 CP-SAT 단일 경로 적용 → Task 4 ✅
- 4.1 단위 테스트 4건 → Task 1 (5건으로 확장) ✅
- 4.2 통합 테스트 → Task 3 (고압 재현 시드) ✅
- 4.2 Playwright E2E → Task 5 ✅
- 4.3 회귀 방어 (저압 단일 경로) → Task 2 실행 시 `test_pipeline_sync.py` 통과로 검증 ✅

**Placeholder scan:** "TBD"/"TODO"/"implement later" 없음.

**Type consistency:** helper signature 는 모든 호출처(Task 2/3/4)에서 동일한 키워드 인자(`process_name`, `pred_proc`, `group_sqs`, `process_end_by_sq`, `current_start`, `current_end`, `duration_min`, `slots`, `db`, `equipment_code`)로 일치.

**Task 2의 미묘한 변화** — 기존 단일 경로에서 `slots` 가 마지막 eligible 루프의 것을 참조하던 것을 `timeline.get(best_eq.equipment_code, [])` 로 재조회하도록 바꿨음. 이것은 잠재 버그 수정이지 동작 불변 리팩토링은 아님. 회귀 테스트(`test_pipeline_sync`)가 통과하면 안전. 통과 안 하면 원래 `slots` 로 복원(`slots` 는 루프 밖으로 노출되지 않으므로 `timeline` 재조회가 semantic 으로 더 명확).
