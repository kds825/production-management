"""decision_card ❷ Impact / ❹ Equipment-day / ❺ Bundle / ❻ Alternatives / placement_calc.

build_card.py 분할 (Task 1.10, B-5.3).
원본 import path 보존 — build_card 가 본 모듈에서 re-export.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from app.application.decisions._card_why import _extract_metric
from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.schedule_task import ScheduleTask


def _build_impact_block(batch: ProductionBatch, audit_rows: list, phrasing):
    """audit row 에서 metric 추출 → ImpactCell N개 + severity."""
    from app.presentation.schemas.decision_card import ImpactBlock, ImpactCell

    cells: list[ImpactCell] = []

    # 표준 4 metric 시도 — audit row 의 constraints_applied 에서
    metric_extractors = [
        ("color_change", "🎨 색상교체"),
        ("spec_change", "📏 규격교체"),
        ("predecessor_gap", "⏱️ 전공정과 갭"),
        ("due_slack_days", "📅 납기 여유"),
    ]
    for kind, label in metric_extractors:
        value = _extract_metric(audit_rows, kind)
        if value is None:
            continue
        sev = phrasing.severity_for(kind=kind, value=float(value))
        text = phrasing.impact_line(kind=kind, params={"value": value})
        cells.append(
            ImpactCell(
                label=label,
                value=text,
                severity=sev,
                kind=kind,
            )
        )

    # 소요시간 분해 — Step 3c-2 wire-up: setup + production + gap 합산
    duration_breakdown: list[dict] = []
    if batch.estimated_duration_min:
        duration_breakdown.append(
            {
                "label": "본 작업",
                "minutes": int(float(batch.estimated_duration_min) or 0),
                "source": "batch.estimated_duration_min",
            }
        )

    return ImpactBlock(cells=cells, duration_breakdown=duration_breakdown)


def _build_equipment_day(
    db: Session,
    batch: ProductionBatch,
    task: Optional[ScheduleTask],
    gantt_builder,
) -> list:
    """본 batch 의 같은 설비 + 같은 날짜의 ScheduleTask row 집계 + 정렬."""
    from app.presentation.schemas.decision_card import GanttRow

    if task is None or not task.equipment_code or not task.start_datetime:
        return []

    same_day_start = task.start_datetime.replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    next_day = same_day_start.replace(hour=23, minute=59, second=59)
    same_day_tasks = (
        db.query(ScheduleTask)
        .filter(
            ScheduleTask.equipment_code == task.equipment_code,
            ScheduleTask.start_datetime >= same_day_start,
            ScheduleTask.start_datetime <= next_day,
            ScheduleTask.run_label == batch.run_label,
        )
        .all()
    )

    # gantt_builder.sort_key 로 정렬 (sheath 의 경우 cluster_sort_key 미러)
    rows_view: list = []
    for t in same_day_tasks:
        rows_view.append(
            GanttRow(
                task_id=t.task_id,
                batch_id=t.batch_id,
                batch_group=t.batch_group or "",
                label=t.batch_group or f"task-{t.task_id}",
                start_at=t.start_datetime,
                end_at=t.end_datetime,
                is_self=(t.batch_id == batch.batch_id),
            )
        )
    # 시각 단순 정렬 — 더 정교한 cluster_sort_key 미러는 build_card 가 cluster_meta
    # 를 가지지 않으므로 시간 순으로. 정렬 라벨은 sheath 의 경우 memory 표기.
    rows_view.sort(key=lambda r: r.start_at)
    return rows_view


def _post_hoc_bundle_metrics(
    db: Session,
    batch: ProductionBatch,
    task: Optional[ScheduleTask],
    audit_rows: list,
) -> tuple[Optional[dict], list[dict]]:
    """orchestrator 손대지 않고 본 묶음 + 인접 cluster N=5 score 합성.

    chosen 은 본 batch_group 의 metric, alternatives 는 같은 run_label 안의
    다른 batch_group 들. 솔버 결과 위에서 phrasing-only rebuild.
    """
    if not batch.batch_group:
        return None, []

    chosen = {
        "label": f"본 묶음 ({batch.batch_group})",
        "color_change_min": _extract_metric(audit_rows, "color_change") or 0,
        "spec_change_min": _extract_metric(audit_rows, "spec_change") or 0,
        "duration_min": int(float(batch.estimated_duration_min or 0)),
        "score": 0.0,  # chosen 의 score 는 표시용 — alternative 들의 비교 기준
    }

    # 인접 cluster — 같은 run_label, 다른 batch_group 의 ScheduleTask
    same_run_tasks = (
        db.query(ScheduleTask)
        .filter(
            ScheduleTask.run_label == batch.run_label,
            ScheduleTask.batch_group != batch.batch_group,
        )
        .limit(20)  # 한도 — 50ms 보장
        .all()
    )
    seen_groups: set = set()
    alts: list[dict] = []
    for t in same_run_tasks:
        if t.batch_group in seen_groups or not t.batch_group:
            continue
        seen_groups.add(t.batch_group)
        # 단순 score — duration 차이를 score 로 (실제 phrasing-only post-hoc)
        dur_min = int(
            (t.end_datetime - t.start_datetime).total_seconds() / 60
            if t.end_datetime and t.start_datetime
            else 0
        )
        alts.append(
            {
                "label": f"대안 묶음 {t.batch_group}",
                "color_change_min": 0,
                "spec_change_min": 0,
                "duration_min": dur_min,
                "score": float(dur_min) - chosen["duration_min"],
            }
        )
        if len(alts) >= 5:
            break
    return chosen, alts


def _build_alternatives(audit_rows: list, phrasing) -> list:
    """audit_log.action_type='filter_out' row → phrasing.filter_out_reason."""
    from app.presentation.schemas.decision_card import Alternative

    out: list[Alternative] = []
    for r in audit_rows:
        if r.action_type != "filter_out":
            continue
        cs = r.constraints_applied or []
        for c in cs:
            if not isinstance(c, dict):
                continue
            code = c.get("id", "")
            params = (c.get("params") or {}) | {"detail": c.get("detail", "")}
            text = phrasing.filter_out_reason(code=code, params=params)
            out.append(
                Alternative(
                    candidate_label=str(params.get("equipment", code)),
                    rejected_reason=text,
                    severity="fail",
                    code=code,
                )
            )
    return out


def _build_placement_calc(task: Optional[ScheduleTask], audit_rows: list) -> dict:
    """⓵ '시작 = max(예정ready, 직전묶음끝+셋업, 캘린더가용)' popover 데이터."""
    if task is None:
        return {}
    return {
        "start_at": (task.start_datetime.isoformat() if task.start_datetime else None),
        "end_at": task.end_datetime.isoformat() if task.end_datetime else None,
        "duration_minutes": (
            int((task.end_datetime - task.start_datetime).total_seconds() / 60)
            if (task.start_datetime and task.end_datetime)
            else 0
        ),
        "rationale": (
            "시작 = max(예정ready, 직전묶음끝+셋업, 캘린더가용) — "
            "ScheduleTask.start_datetime source."
        ),
    }
