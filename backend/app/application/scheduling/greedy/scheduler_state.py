"""SchedulerState — greedy `_run_optimization_once` per-call mutable state container.

Phase 4 (architecture-target.md §4 Phase 4): `_run_optimization_once` 가
runtime 동안 mutate 하는 ~10 개의 in-progress dict / list / scalar 를
하나의 dataclass 로 묶어 (a) 의도(이 변수들이 함께 진화한다)를 명시하고
(b) retry 사이 cross-contamination 을 명문화한다.

## EM NTH#2 — 부분 frozen 컨벤션

`@dataclass(frozen=True)` 는 nested field (dict 안의 entry 등) 를 막지 못
하므로 frozen 화의 의미가 제한적. 대신 **convention 으로**:

- `timeline / predecessor_map / sq_to_equip / process_end_by_sq /
  process_first_output_by_sq / core_first_drum_by_main_sq /
  last_batch_on_equip / tasks_created / preempted_remainder /
  first_insul_output` 는 **runtime mutate** 됨 (default_factory 로 fresh
  per-call 보장).
- master data (`equipment_by_process / speed_map / constraint_params /
  welding_min`) 는 **`__post_init__` 이후 read-only 컨벤션** — caller 가
  채우고 함수 본문은 절대 mutate 하지 않는다.

retry 마다 SchedulerState() 를 **새로** 생성해야 cross-contamination 을
회피 (Phase 4 step 3). `tests/test_scheduler_state_isolation.py` (Phase 4
step 4) 가 이 invariant 를 freezes — 두 retry 의 state.timeline 이 서로
다른 dict 객체임을 assert.

## 사용

```python
state = SchedulerState()
state.timeline.setdefault("EX-B100", []).append((start_dt, end_dt))
state.predecessor_map[("SO-01", 1)] = 42
# ... 계산 끝 ...
return {"total_tasks": len(state.tasks_created), ...}
```
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.application._shared.constraint_params import ConstraintParams
    from app.infrastructure.models.production_batch import ProductionBatch
    from app.infrastructure.models.schedule_task import ScheduleTask


@dataclass
class SchedulerState:
    """`_run_optimization_once` 의 per-call mutable state.

    Mutable (per-call fresh):
        timeline                       — equipment_code → [(start, end), ...]
        predecessor_map                — (sales_order_id, line) → last task_id
        last_batch_on_equip            — equipment_code → last placed batch
        sq_to_equip                    — (process_name, sq) → equipment_code
        tasks_created                  — list of ScheduleTask (committed via flush)
        process_end_by_sq              — (process_name, sq) → max end_datetime
        process_first_output_by_sq     — (process_name, sq) → first drum output
        core_first_drum_by_main_sq     — int main_sq → first drum output
        first_insul_output             — earliest insul output (single scalar)
        preempted_remainder            — list of ProductionBatch (urgent split tail)

    Read-only after __post_init__ (convention only — Python 은 강제 못함):
        equipment_by_process           — process_name → list[EquipmentMaster]
        speed_map                      — (eq_code, sq_float) → SpeedMaster
        constraint_params              — ConstraintParams
        welding_min                    — float (4-4 용접 분)
    """

    # ── 1. Runtime mutable (per-call fresh) ────────────────────────────────
    timeline: dict[str, list[tuple[datetime, datetime]]] = field(default_factory=dict)
    predecessor_map: dict[tuple[str, int], int] = field(default_factory=dict)
    last_batch_on_equip: dict[str, "ProductionBatch"] = field(default_factory=dict)
    sq_to_equip: dict[tuple[str, int], str] = field(default_factory=dict)
    tasks_created: list["ScheduleTask"] = field(default_factory=list)
    process_end_by_sq: dict[tuple[str, int], datetime] = field(default_factory=dict)
    process_first_output_by_sq: dict[tuple[str, int], datetime] = field(
        default_factory=dict
    )
    core_first_drum_by_main_sq: dict[int, datetime] = field(default_factory=dict)
    first_insul_output: datetime | None = None
    preempted_remainder: list["ProductionBatch"] = field(default_factory=list)

    # ── 2. Master data (read-only after construction by convention) ────────
    equipment_by_process: dict = field(default_factory=dict)
    speed_map: dict = field(default_factory=dict)
    constraint_params: "ConstraintParams | None" = None
    welding_min: float = 30.0
