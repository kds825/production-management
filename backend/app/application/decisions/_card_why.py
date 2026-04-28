"""decision_card ❶ Why lines + verdict_summary helper.

build_card.py 분할 (Task 1.9, B-5.2).
원본 import path 보존 — build_card 가 본 모듈에서 re-export.
"""

from __future__ import annotations

from typing import Optional

from app.application.decisions._card_helpers import _task_view
from app.infrastructure.models.audit_log import AuditLog
from app.infrastructure.models.equipment_master import EquipmentMaster
from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.schedule_task import ScheduleTask
from app.infrastructure.models.solver_run import SolverRun


def _build_why_lines(
    batch: ProductionBatch,
    equipment: Optional[EquipmentMaster],
    audit_rows: list,
    phrasing,
) -> list:
    """audit pass 룰 + equipment 적합성 룰을 자연어로 합성.

    audit_rows 의 constraints_applied 에서 result='pass' 룰을 anchor 로 사용.
    equipment 가 있으면 5-1 (SQ 적합성) + 10-3 (재질) + 3-3 (색상묶음) 도 추가.
    """
    from app.presentation.schemas.decision_card import DecisionLine

    lines: list[DecisionLine] = []

    # Equipment 기반 ❶ 자연어 (sheath / stranding / insulation)
    if equipment is not None:
        eq_name = equipment.equipment_name or equipment.equipment_code
        sq = int(float(batch.sq_mm2 or 0))

        # 5-1: SQ 작업범위
        if equipment.range_min and equipment.range_max:
            text = phrasing.adequacy_line(
                anchor="5-1",
                params={
                    "equipment": eq_name,
                    "min": int(equipment.range_min),
                    "max": int(equipment.range_max),
                    "sq": sq,
                },
            )
            if not text.startswith("["):  # 폴백 마커 제외
                lines.append(
                    DecisionLine(
                        anchor="why_line_5_1",
                        natural=text,
                        constraint_id="#5-1",
                        severity="ok",
                    )
                )

        # 10-3: 시스재질 (sheath only — Stranding/Insulation 은 다른 anchor)
        if (
            phrasing.process_key == "sheath"
            and equipment.material_limit
            and equipment.material_limit != "ALL"
        ):
            text = phrasing.adequacy_line(
                anchor="10-3",
                params={
                    "equipment": eq_name,
                    "allowed": equipment.material_limit,
                    "current": batch.conductor_material or "CU",
                },
            )
            if not text.startswith("["):
                lines.append(
                    DecisionLine(
                        anchor="why_line_10_3",
                        natural=text,
                        constraint_id="#10-3",
                        severity="ok",
                    )
                )

        # 3-3: 색상묶음 (sheath only)
        if phrasing.process_key == "sheath" and equipment.color_group:
            text = phrasing.adequacy_line(
                anchor="3-3",
                params={
                    "equipment": eq_name,
                    "eq_category": equipment.color_group,
                    "color": batch.sheath_color or "",
                },
            )
            if not text.startswith("["):
                lines.append(
                    DecisionLine(
                        anchor="why_line_3_3",
                        natural=text,
                        constraint_id="#3-3",
                        severity="ok",
                    )
                )

    # 4-2: 색상교체 — audit row 의 constraint_id='4-2' 보고
    for r in audit_rows:
        cs = r.constraints_applied or []
        for c in cs:
            cid = c.get("id") if isinstance(c, dict) else None
            if cid != "4-2":
                continue
            params = c.get("params") or {}
            minutes = int(params.get("minutes", 0) or 0)
            if minutes == 0:
                anchor_key = "4-2_save"
                anchor_params = {"color": batch.sheath_color or ""}
            else:
                anchor_key = "4-2_warn"
                anchor_params = {
                    "prev_color": params.get("prev_color", "?"),
                    "minutes": minutes,
                }
            text = phrasing.adequacy_line(anchor=anchor_key, params=anchor_params)
            if not text.startswith("["):
                lines.append(
                    DecisionLine(
                        anchor="why_line_4_2",
                        natural=text,
                        constraint_id="#4-2",
                        severity=phrasing.severity_for(
                            kind="color_change", value=float(minutes)
                        ),
                    )
                )

    return lines


def _extract_metric(audit_rows: list, kind: str):
    """audit_log.constraints_applied 에서 kind 에 해당하는 value 추출."""
    for r in audit_rows:
        cs = r.constraints_applied or []
        for c in cs:
            if not isinstance(c, dict):
                continue
            params = c.get("params") or {}
            if kind == "color_change" and c.get("id") == "4-2":
                return params.get("minutes", 0)
            if kind == "spec_change" and c.get("id") in ("5-2-spec", "spec-change"):
                return params.get("minutes", 0)
            if kind == "predecessor_gap" and c.get("id") == "predecessor_gap":
                return params.get("minutes", 0)
            if kind == "due_slack_days" and c.get("id") == "due_slack":
                return params.get("days", 0.0)
    return None


def _scenario_key_hint(batch: ProductionBatch, audit_rows: list[AuditLog]) -> str:
    """간단한 휴리스틱 — Step 3c-2 에서 더 정교한 로직.

    A: 정상 / B: 색상교체 / C: 규격교체 / D: D-day / E: 외주 /
    F: 연선 재공 / G: TFR-GV / H: 묶음 비교
    """
    pg_upper = (batch.product_group or "").upper()
    if "TFR-GV" in pg_upper and float(batch.sq_mm2 or 0) > 25:
        return "G"
    if batch.process_name == "연선":
        return "F"
    return "A"


def _safe_verdict_summary(
    phrasing,
    *,
    batch: ProductionBatch,
    audit_rows: list[AuditLog],
    solver_run: Optional[SolverRun],
    task: Optional[ScheduleTask],
) -> str:
    """phrasing.verdict_summary 안전 호출 — 미데이터 시 폴백."""
    try:
        return phrasing.verdict_summary(
            batch=batch,
            audit=_collect_audit_metrics(audit_rows),
            solver=solver_run,
            schedule_task=_task_view(task),
        )
    except Exception as e:  # noqa: BLE001 — 안전 폴백 우선
        return f"ⓘ verdict_summary 합성 실패 ({e!s})"


def _collect_audit_metrics(audit_rows: list[AuditLog]):
    """audit_rows 에서 verdict_summary 가 보는 metric snapshot 추출.

    Step 3c-2 wire-up 에서 정교화. skeleton 단계는 SimpleNamespace 폴백.
    """
    from types import SimpleNamespace

    color_min = 0
    spec_min = 0
    slack = 0.0
    loss_pct = 0.0
    for r in audit_rows:
        if r.action_type == "color_change" and r.constraints_applied:
            for c in r.constraints_applied:
                color_min = int(c.get("minutes", color_min) or color_min)
        if r.action_type == "spec_change" and r.constraints_applied:
            for c in r.constraints_applied:
                spec_min = int(c.get("minutes", spec_min) or spec_min)
    return SimpleNamespace(
        color_change_min=color_min,
        spec_change_min=spec_min,
        due_slack_days=slack,
        wip_loss_pct=loss_pct,
        outsource_lead_days=0,
    )
