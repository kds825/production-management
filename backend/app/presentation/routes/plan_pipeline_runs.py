"""파이프라인 실행 이력 / 비교 / 삭제 API.

원본: ``plan_pipeline.py`` 의 /runs* endpoint 들을 sub-router 로 분리 (Task 1.2).
외부 import path 보존: ``plan_pipeline.py`` 끝에서 명시적 re-export
(``from app.presentation.routes.plan_pipeline_runs import compare_runs, list_runs,
delete_run``) — Appendix A.2 patch 적용.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.infrastructure.database import get_db
from app.infrastructure.models.production_batch import ProductionBatch

router = APIRouter()


@router.delete("/runs/{run_label}", summary="특정 계획 실행 삭제")
def delete_run(run_label: str, db: Session = Depends(get_db)):
    """특정 run_label의 실행 데이터를 삭제한다.

    - audit_log, schedule_task, production_batch, sales_order: run_label 행 삭제
    - wip_inventory: run_label 행 삭제 + 해당 run에서 매칭된 다른 WIP 행 상태 초기화
      (matched_order_id → NULL, status → 사용가능)
    """
    counts = {}

    # production_batch / wip_inventory / sales_order 는 상호 FK 로 얽혀 있다:
    #   - wip_inventory.source_batch_id → production_batch.batch_id  (RESTRICT)
    #   - production_batch.wip_matched_id → wip_inventory.wip_id      (RESTRICT)
    #   - sales_order.wip_id              → wip_inventory.wip_id      (RESTRICT)
    # 어느 쪽을 먼저 지워도 다른 쪽이 막는다. 따라서
    #  (a) 삭제 대상 run 바깥에서 들어오는 FK 는 모두 NULL 로 끊어두고,
    #  (b) 자식 → 부모 순서로 삭제한다.

    # (a-1) 이 run 의 production_batch → wip_inventory 참조 해제 (동일 run 내부 순환)
    db.execute(
        text("UPDATE production_batch SET wip_matched_id = NULL WHERE run_label = :rl"),
        {"rl": run_label},
    )

    # (a-2) 다른 run 의 wip_inventory 가 이 run 의 production_batch 를 참조 중이면 NULL 로 끊기
    db.execute(
        text(
            """
            UPDATE wip_inventory
            SET source_batch_id = NULL
            WHERE source_batch_id IN (
                SELECT batch_id FROM production_batch WHERE run_label = :rl
            )
            """
        ),
        {"rl": run_label},
    )

    # (a-3) 다른 run 의 sales_order 가 이 run 의 wip_inventory 를 참조 중이면 NULL 로 끊기
    db.execute(
        text(
            """
            UPDATE sales_order
            SET wip_id = NULL
            WHERE wip_id IN (
                SELECT wip_id FROM wip_inventory WHERE run_label = :rl
            )
            """
        ),
        {"rl": run_label},
    )

    # (b) 자식 → 부모 순으로 삭제한다.
    #     audit_log / schedule_task / sales_order 는 production_batch·wip_inventory 를 참조하므로 먼저,
    #     그 다음 wip_inventory, 마지막으로 production_batch.
    for table in ["audit_log", "schedule_task", "sales_order"]:
        result = db.execute(
            text(f"DELETE FROM {table} WHERE run_label = :rl"),
            {"rl": run_label},
        )
        counts[table] = result.rowcount

    wip_del = db.execute(
        text("DELETE FROM wip_inventory WHERE run_label = :rl"),
        {"rl": run_label},
    )
    counts["wip_inventory"] = wip_del.rowcount

    pb_del = db.execute(
        text("DELETE FROM production_batch WHERE run_label = :rl"),
        {"rl": run_label},
    )
    counts["production_batch"] = pb_del.rowcount

    # wip_inventory: 이 run에서 매칭(사용완료)됐지만 다른 run_label을 가진 WIP 상태 초기화.
    # production_batch가 이미 삭제됐으므로 wip_matched_id 역참조가 깨진 WIP를 정리한다.
    # matched_order_id는 "order_id:order_line" 형식 — 해당 run의 sales_order가 지워졌으므로
    # 더 이상 유효하지 않다. status를 사용가능으로 되돌리고 매칭 정보를 초기화한다.
    wip_reset = db.execute(
        text("""
            UPDATE wip_inventory
            SET status = '사용가능',
                matched_order_id = NULL
            WHERE status = '사용완료'
              AND run_label != :rl
              AND matched_order_id IS NOT NULL
        """),
        {"rl": run_label},
    )
    counts["wip_inventory_reset"] = wip_reset.rowcount

    db.commit()
    total = counts["wip_inventory"] + sum(
        v
        for k, v in counts.items()
        if k not in ("wip_inventory", "wip_inventory_reset")
    )
    if total == 0 and counts.get("wip_inventory_reset", 0) == 0:
        raise HTTPException(status_code=404, detail=f"run_label '{run_label}' 없음")
    return {"run_label": run_label, "deleted": counts, "total": total}


@router.get("/runs", summary="계획 실행 이력 목록")
def list_runs(db: Session = Depends(get_db)) -> list[dict]:
    """저장된 모든 run_label 목록을 배치 수 및 최초 생성 시각과 함께 반환한다.

    최신 실행이 상단에 오도록 created_at 내림차순 정렬.
    outsource_count: ERP 외주 플래그(is_outsourced=True) 수주 건수.
    parent_run_label: stage1/update 로 파생된 경우 어느 이전 run 의 후속인지.
    """
    from app.infrastructure.models.sales_order import SalesOrder

    rows = (
        db.query(
            ProductionBatch.run_label,
            func.count(ProductionBatch.batch_id).label("batch_count"),
            func.min(ProductionBatch.created_at).label("created_at"),
            # 같은 run 내에서 parent_run_label 은 모두 동일 (stage1/update 에서 일괄 설정).
            # NULL 과 non-NULL 이 섞일 경우 MAX 로 비-NULL 우선. 최초 run 은 NULL 유지.
            func.max(ProductionBatch.parent_run_label).label("parent_run_label"),
        )
        # `test-` prefix 는 pytest fixture 가 SAVEPOINT 밖에서 commit 되어 leak
        # 됐을 때만 등장. 운영 UI 가 test fixture 를 최신 run 으로 골라 0배치
        # 화면을 띄우는 회귀를 막기 위해 응답에서 제외.
        .filter(~ProductionBatch.run_label.like("test-%"))
        .group_by(ProductionBatch.run_label)
        .order_by(func.min(ProductionBatch.created_at).desc())
        .all()
    )

    # 런별 외주 건수 — ProductionBatch.sales_order_id 기준으로 SalesOrder 조인.
    # SalesOrder.run_label 은 forward-roll 로 최신 run 을 가리킬 수 있으므로
    # pb.run_label 기준으로 집계해야 버전별 정확한 외주 건수를 얻는다.
    outsource_counts: dict[str, int] = {}
    if rows:
        run_labels = [r.run_label for r in rows]
        outsource_rows = (
            db.query(
                ProductionBatch.run_label,
                func.count(func.distinct(ProductionBatch.sales_order_id)).label("cnt"),
            )
            .join(
                SalesOrder,
                ProductionBatch.sales_order_id == SalesOrder.order_id,
            )
            .filter(
                ProductionBatch.run_label.in_(run_labels),
                SalesOrder.is_outsourced == True,  # noqa: E712
            )
            .group_by(ProductionBatch.run_label)
            .all()
        )
        outsource_counts = {r.run_label: r.cnt for r in outsource_rows}

    return [
        {
            "run_label": row.run_label,
            "batch_count": row.batch_count,
            "created_at": row.created_at,
            "parent_run_label": row.parent_run_label,
            "outsource_count": outsource_counts.get(row.run_label, 0),
        }
        for row in rows
    ]


@router.get("/runs/compare", summary="두 run 간 배치/스케줄 diff")
def compare_runs(
    before: str,
    after: str,
    db: Session = Depends(get_db),
) -> dict:
    """두 run_label 의 ProductionBatch + ScheduleTask 를 비교해 added/removed/
    moved/unchanged 로 분류한다.

    stable key = (sales_order_id, sales_order_line, process_name, batch_seq).
    batch_id 는 run 마다 autoincrement 로 달라지므로 논리적 식별자가 필요.
    응답 스키마는 ScheduleDiffResponse (types/diff.ts) 와 호환 — task_id 필드에
    stable key 의 문자열 표현을 넣어 UI 에서 dedup 하기 편하도록 함.
    """
    from app.infrastructure.models.schedule_task import ScheduleTask

    if before == after:
        raise HTTPException(status_code=400, detail="before 와 after 는 달라야 합니다")

    # 두 run 의 배치 + task 를 한 번에 로드 (N+1 방지)
    def _load(run_label: str) -> dict[tuple, dict]:
        """stable_key → {batch, task, meta} 매핑."""
        rows = (
            db.query(ProductionBatch, ScheduleTask)
            .outerjoin(
                ScheduleTask,
                (ScheduleTask.batch_id == ProductionBatch.batch_id)
                & (ScheduleTask.run_label == ProductionBatch.run_label),
            )
            .filter(ProductionBatch.run_label == run_label)
            .all()
        )
        result: dict[tuple, dict] = {}
        for b, t in rows:
            key = (
                b.sales_order_id or "",
                b.sales_order_line if b.sales_order_line is not None else 0,
                b.process_name or "",
                b.batch_seq if b.batch_seq is not None else 0,
            )
            # batch_seq=-1 (연선 헤더) 가 여러 수주를 묶은 경우 sales_order_id 가
            # 비어 있을 수 있음 — batch_group 으로 보강.
            if not key[0]:
                key = (b.batch_group or f"UNK_{b.batch_id}",) + key[1:]
            start_iso = t.start_datetime.isoformat() if t and t.start_datetime else None
            end_iso = t.end_datetime.isoformat() if t and t.end_datetime else None
            result[key] = {
                "batch_id": b.batch_id,
                "equipment_code": (t.equipment_code if t else None) or b.equipment_code,
                "start": start_iso,
                "end": end_iso,
                "process_name": b.process_name,
                "batch_group": b.batch_group,
                "sales_order_id": b.sales_order_id,
                "sales_order_line": b.sales_order_line,
                "customer_name": b.customer_name,
                "status": b.status,
                "sheath_color": b.sheath_color,
                "sq_mm2": float(b.sq_mm2) if b.sq_mm2 is not None else None,
                "due_date": b.due_date.isoformat() if b.due_date else None,
            }
        return result

    before_map = _load(before)
    after_map = _load(after)

    if not before_map and not after_map:
        raise HTTPException(
            status_code=404,
            detail=f"두 run 모두 배치가 없습니다: before={before}, after={after}",
        )

    before_keys = set(before_map.keys())
    after_keys = set(after_map.keys())
    added_keys = after_keys - before_keys
    removed_keys = before_keys - after_keys
    common_keys = before_keys & after_keys

    def _delta_hours(a_iso: str | None, b_iso: str | None) -> float | None:
        if not a_iso or not b_iso:
            return None
        try:
            a_dt = datetime.fromisoformat(a_iso)
            b_dt = datetime.fromisoformat(b_iso)
            return (b_dt - a_dt).total_seconds() / 3600.0
        except Exception:
            return None

    def _key_str(k: tuple) -> str:
        return "|".join(str(x) for x in k)

    moved_tasks: list[dict] = []
    unchanged_task_ids: list[str] = []

    for key in sorted(common_keys, key=_key_str):
        b_row = before_map[key]
        a_row = after_map[key]
        start_changed = b_row["start"] != a_row["start"]
        end_changed = b_row["end"] != a_row["end"]
        eq_changed = b_row["equipment_code"] != a_row["equipment_code"]

        if not (start_changed or end_changed or eq_changed):
            unchanged_task_ids.append(_key_str(key))
            continue

        moved_tasks.append(
            {
                "task_id": _key_str(key),
                "old_start": b_row["start"],
                "old_end": b_row["end"],
                "old_equipment": b_row["equipment_code"],
                "new_start": a_row["start"],
                "new_end": a_row["end"],
                "new_equipment": a_row["equipment_code"],
                "start_delta_hours": _delta_hours(b_row["start"], a_row["start"]),
                "end_delta_hours": _delta_hours(b_row["end"], a_row["end"]),
                "equipment_changed": eq_changed,
                "batch_group": a_row["batch_group"],
                "process_name": a_row["process_name"],
                "sales_order_id": a_row["sales_order_id"],
                "customer_name": a_row["customer_name"],
                "sheath_color": a_row["sheath_color"],
                "cross_section": a_row["sq_mm2"],
            }
        )

    added_tasks: list[dict] = []
    for key in sorted(added_keys, key=_key_str):
        a_row = after_map[key]
        added_tasks.append(
            {
                "task_id": _key_str(key),
                "start": a_row["start"],
                "end": a_row["end"],
                "equipment": a_row["equipment_code"],
                "batch_group": a_row["batch_group"],
                "process_name": a_row["process_name"],
                "sales_order_id": a_row["sales_order_id"],
                "customer_name": a_row["customer_name"],
                "sheath_color": a_row["sheath_color"],
                "cross_section": a_row["sq_mm2"],
                "due_date": a_row["due_date"],
            }
        )

    removed_tasks: list[dict] = []
    for key in sorted(removed_keys, key=_key_str):
        b_row = before_map[key]
        removed_tasks.append(
            {
                "task_id": _key_str(key),
                "start": b_row["start"],
                "end": b_row["end"],
                "equipment": b_row["equipment_code"],
                "batch_group": b_row["batch_group"],
                "process_name": b_row["process_name"],
                "sales_order_id": b_row["sales_order_id"],
                "customer_name": b_row["customer_name"],
                "sheath_color": b_row["sheath_color"],
                "cross_section": b_row["sq_mm2"],
                "due_date": b_row["due_date"],
            }
        )

    return {
        "run_label_before": before,
        "run_label_after": after,
        "kind": "run_compare",
        "created_at": datetime.now().isoformat(),
        "summary": {
            "moved": len(moved_tasks),
            "added": len(added_tasks),
            "removed": len(removed_tasks),
            "unchanged": len(unchanged_task_ids),
            "total_before": len(before_keys),
            "total_after": len(after_keys),
        },
        "moved_tasks": moved_tasks,
        "added_tasks": added_tasks,
        "removed_tasks": removed_tasks,
        "unchanged_task_ids": unchanged_task_ids,
    }
