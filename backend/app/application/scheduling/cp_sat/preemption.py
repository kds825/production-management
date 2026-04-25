"""Urgent-batch preemption — drum-split or defer blockers on a chosen equipment.

Extracted from ``cp_sat_optimizer._try_preempt_for_urgent`` (Week 9 SRP cleanup).

The preemption logic is invoked by the calendar-greedy pass after CP-SAT
has decided the global order. It owns three responsibilities that
historically lived inline in ``cp_sat_optimizer.py``:

1. **Drum boundary partitioning** (multi-drum strategy) — when a
   blocking task can complete ``k > 0`` drums before the urgent window
   opens, shorten the original ``ScheduleTask`` and emit a remainder
   ``ProductionBatch`` (status='planned') for re-scheduling later.

2. **Single-drum deferral** (single-drum strategy) — when the blocker
   can't even finish 1 drum before urgent, delete the ``ScheduleTask``
   and reset the batch to 'planned' so the greedy loop replans it.

3. **Predecessor-map repair** — every deletion may invalidate a
   ``predecessor_task_id`` reference; the caller passes ``predecessor_map``
   so we can drop dangling entries before they FK-violate on INSERT.

Why a separate module: this is pure scheduling/DB logic with zero
solver coupling — no IntVars, no objective. Keeping it inline made
``cp_sat_optimizer.py`` 200 lines longer than the actual CP-SAT entry
deserves.

Why we still hand the caller a list of ``ProductionBatch`` (not just
booleans): downstream greedy must re-enqueue the remainders into the
same scheduling pass. Returning the batches preserves caller-owned
control flow.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.schedule_task import ScheduleTask
from app.infrastructure.calendar_engine import calculate_end_datetime
from app.application._shared.db_ops import _delete_task_safely

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _drums_completable(
    task_start: datetime,
    preempt_at: datetime,
    setup_min: float,
    work_dur_min: float,
    total_drums: int,
    eq_code: str | None,
    db: "Session",
) -> int:
    """Binary-search: how many drums finish before ``preempt_at``?

    Returns 0 if even the setup can't complete in time. Pure I/O against
    ``calculate_end_datetime`` for calendar arithmetic — no DB writes.
    """
    if total_drums <= 0 or preempt_at <= task_start:
        return 0
    drum_min = work_dur_min / max(total_drums, 1)
    lo, hi = 0, total_drums
    while lo < hi:
        mid = (lo + hi + 1) // 2
        end_mid = calculate_end_datetime(
            task_start, setup_min + mid * drum_min, db, eq_code
        )
        if end_mid <= preempt_at:
            lo = mid
        else:
            hi = mid - 1
    return lo


def try_preempt_for_urgent(
    earliest: datetime,
    chosen_eq_code: str,
    run_label: str,
    timeline: dict[str, list],
    db: "Session",
    urgent_priority: int = 7,
    predecessor_map: dict[tuple, int] | None = None,
) -> list[ProductionBatch]:
    """Free a slot at ``earliest`` on ``chosen_eq_code`` for an urgent batch.

    Two strategies, picked per blocking slot:

    * **A — multi-drum split** (``drum_count >= 2``): trim the existing
      ``ScheduleTask.end_datetime`` to a drum boundary that ends before
      ``earliest`` and create a remainder ``ProductionBatch`` (status
      ='planned') for the unfinished drums.
    * **B — single-drum defer** (``drum_count < 2``): delete the task,
      reset the batch to 'planned', and return it so the caller can
      reschedule after the urgent batch lands.

    Both strategies update ``timeline`` in-place and return remainder
    batches the caller must enqueue.

    ``urgent_priority`` is the urgent batch's ``customer_priority``;
    blockers at this priority or higher are *not* displaced (mutual
    urgency stalemate).
    """
    slots = list(timeline.get(chosen_eq_code, []))
    if not slots:
        return []

    remainder_batches: list[ProductionBatch] = []

    for slot_start, slot_end in sorted(slots, key=lambda s: s[0]):
        if slot_end <= earliest:
            continue  # already-finished block before urgent window
        if slot_start >= earliest:
            break  # later block — urgent can fit before it

        # slot_start < earliest < slot_end → in-progress block crosses urgent
        task = (
            db.query(ScheduleTask)
            .filter(
                ScheduleTask.run_label == run_label,
                ScheduleTask.equipment_code == chosen_eq_code,
                ScheduleTask.start_datetime == slot_start,
                ScheduleTask.end_datetime == slot_end,
            )
            .first()
        )
        if task is None:
            break

        src_batch = (
            db.query(ProductionBatch)
            .filter(ProductionBatch.batch_id == task.batch_id)
            .first()
        )
        if src_batch is None:
            break

        total_drums = int(src_batch.drum_count or 1)
        setup_min = float(task.setup_time_min or 0)
        work_dur_min = float(src_batch.estimated_duration_min or 0)

        blocking_priority = int(src_batch.customer_priority or 99)

        if total_drums < 2:
            # Strategy B — single-drum defer
            if blocking_priority <= urgent_priority:
                break  # mutual urgency — can't displace
            deleted_id = _delete_task_safely(db, task)
            if predecessor_map is not None:
                # Drop predecessor entries pointing at the now-deleted task —
                # otherwise the next INSERT will FK-violate.
                for _k in [k for k, v in predecessor_map.items() if v == deleted_id]:
                    predecessor_map.pop(_k, None)
            db.flush()

            tl = timeline[chosen_eq_code]
            tl.remove((slot_start, slot_end))

            src_batch.status = "planned"
            src_batch.equipment_code = chosen_eq_code
            db.flush()
            remainder_batches.append(src_batch)
            break

        # Strategy A — multi-drum split
        k = _drums_completable(
            slot_start,
            earliest,
            setup_min,
            work_dur_min,
            total_drums,
            chosen_eq_code,
            db,
        )
        if k == 0:
            # Even setup can't finish — fall back to deferral if not urgent
            if blocking_priority <= urgent_priority:
                break
            deleted_id = _delete_task_safely(db, task)
            if predecessor_map is not None:
                for _k in [k2 for k2, v in predecessor_map.items() if v == deleted_id]:
                    predecessor_map.pop(_k, None)
            db.flush()
            tl = timeline[chosen_eq_code]
            tl.remove((slot_start, slot_end))
            src_batch.status = "planned"
            src_batch.equipment_code = chosen_eq_code
            db.flush()
            remainder_batches.append(src_batch)
            break

        # k > 0: split at drum boundary k
        drum_min = work_dur_min / total_drums
        trim_dur = setup_min + k * drum_min
        new_end = calculate_end_datetime(slot_start, trim_dur, db, chosen_eq_code)

        task.end_datetime = new_end

        tl = timeline[chosen_eq_code]
        tl.remove((slot_start, slot_end))
        tl.append((slot_start, new_end))

        remain_drums = total_drums - k
        remain_dur = remain_drums * drum_min
        remain_len = float(src_batch.total_length_m or 0) * remain_drums / total_drums
        new_bg = (
            f"{src_batch.batch_group}_REMAIN"
            if src_batch.batch_group
            else f"REMAIN_{src_batch.batch_id}"
        )

        rem_b = ProductionBatch(
            run_label=run_label,
            sales_order_id=src_batch.sales_order_id,
            sales_order_line=src_batch.sales_order_line,
            item_code=src_batch.item_code,
            routing_code=src_batch.routing_code,
            process_name=src_batch.process_name,
            equipment_code=chosen_eq_code,
            batch_seq=src_batch.batch_seq,
            drum_length_m=src_batch.drum_length_m,
            drum_count=remain_drums,
            total_length_m=remain_len,
            extra_length_m=src_batch.extra_length_m,
            sq_mm2=src_batch.sq_mm2,
            core_count=src_batch.core_count,
            core_colors=src_batch.core_colors,
            sheath_color=src_batch.sheath_color,
            customer_name=src_batch.customer_name,
            due_date=src_batch.due_date,
            customer_priority=src_batch.customer_priority,
            line_speed_mpm=src_batch.line_speed_mpm,
            setup_time_min=0,
            estimated_duration_min=remain_dur,
            status="planned",
            remarks=f"[선점분할 잔여] 원배치={src_batch.batch_id} ({k}/{total_drums}드럼 선점)",
            product_group=src_batch.product_group,
            voltage=src_batch.voltage,
            conductor_material=src_batch.conductor_material,
            stranding_type=src_batch.stranding_type,
            batch_group=new_bg,
            spec_raw=src_batch.spec_raw,
        )
        db.add(rem_b)
        db.flush()
        remainder_batches.append(rem_b)

        orig_total_m = float(src_batch.total_length_m or 0)
        src_batch.drum_count = k
        src_batch.total_length_m = orig_total_m * k / total_drums
        src_batch.estimated_duration_min = trim_dur - setup_min
        db.flush()

        break  # one slot processed per call

    return remainder_batches
