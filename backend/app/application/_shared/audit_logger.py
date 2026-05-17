"""감사 추적 기록 — 모든 스케줄링 결정의 근거를 기록.

Phase 6 (decision_card) 추가: `log_wip_match` / `log_filter_out` 두 thin wrapper.
- `log_wip_match`: 재공 매칭 결정 (decision_card ❸ 재공 활용 섹션 출처)
- `log_filter_out`: 사전 필터링 단계 탈락 (decision_card ❻ 다른 설비/시간 탈락 사유 출처)

audit_log.stage 컬럼은 NOT NULL 이라 caller 가 항상 "stage1"/"stage2" 명시 (S1).

Phase 6 step 14 (2026-05) 추가: ``AuditLogBuffer`` — apply_calendar_greedy 의
``log_decision()`` 호출이 매 batch placement 후 ``db.add(AuditLog(...))`` →
``_calendar_apply.py:605`` 의 explicit ``db.flush()`` 와 함께 ~3s INSERT
round-trip 누적. buffer 가 끝에서 1회 bulk_insert_mappings 으로 묶음.
"""

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.infrastructure.models.audit_log import AuditLog


@dataclass
class AuditLogBuffer:
    """AuditLog INSERT 의 deferred bulk-insert buffer (Phase 6 step 14).

    Why:
        ``log_decision()`` 이 매 호출마다 ``db.add(AuditLog(...))`` 호출.
        apply_calendar_greedy 의 main loop 안에서 호출되므로 ``_calendar_
        apply.py:605`` 의 explicit ``db.flush()`` 가 같이 INSERT 로 emit
        → ~3s 누적. 끝에서 한 번에 묶으면 1 round-trip.

    Safety:
        AuditLog 는 FK 가 ScheduleTask.task_id 로 향한다. ``buffer.append()``
        는 ``task.task_id`` 가 확정된 _후_ (db.flush() 후 PK return 시점)
        호출. FK 무결성 유지.

        ``bulk_insert_mappings(AuditLog, ...)`` 는 ``database.py:66-102``
        의 guard 가 ProductionBatch 한정이라 AuditLog 에는 영향 없음.
        AuditLog 에 ``after_insert`` listener 도 없음 (grep 검증).

    Caller-managed:
        ``cp_sat_schedule()`` 가 buffer 생성, ``apply_calendar_greedy`` 에
        인자로 전달, ``state_buffer.flush`` 직후 ``audit_buffer.flush(db)``
        1회 호출.
    """

    entries: list[dict[str, Any]] = field(default_factory=list)

    def append(self, **fields: Any) -> None:
        """``log_decision`` 시그니처와 1:1 매핑 (run_label, stage, action_type,
        batch_id, task_id, reason, constraints_applied, alternatives).
        """
        self.entries.append(fields)

    def flush(self, db) -> int:
        """``bulk_insert_mappings`` 으로 1회 commit. 반환: INSERT 된 row 수."""
        if not self.entries:
            return 0
        # log_decision 의 dict → AuditLog column 매핑.
        # 'reason' → 'decision_reason', 'alternatives' → 'alternatives_considered'.
        payload = []
        for e in self.entries:
            row = {
                "run_label": e["run_label"],
                "stage": e["stage"],
                "action_type": e["action_type"],
                "batch_id": e.get("batch_id"),
                "task_id": e.get("task_id"),
                "constraints_applied": e.get("constraints_applied") or [],
                "decision_reason": e.get("reason", ""),
                "alternatives_considered": e.get("alternatives"),
            }
            payload.append(row)
        db.bulk_insert_mappings(AuditLog, payload)
        db.flush()
        n = len(payload)
        self.entries.clear()
        return n


def log_decision(
    db: Session,
    run_label: str,
    stage: str,
    action_type: str,
    batch_id: int | None = None,
    task_id: int | None = None,
    constraints_applied: list[dict] | None = None,
    reason: str = "",
    alternatives: list[dict] | None = None,
):
    """감사 로그 1건 기록"""
    entry = AuditLog(
        run_label=run_label,
        stage=stage,
        batch_id=batch_id,
        task_id=task_id,
        action_type=action_type,
        constraints_applied=constraints_applied or [],
        decision_reason=reason,
        alternatives_considered=alternatives,
    )
    db.add(entry)


def log_wip_match(
    db: Session,
    *,
    run_label: str,
    stage: str,
    batch_id: int,
    matched_wip_id: int | None,
    candidates_evaluated: list[dict] | None = None,
    applied_rule: str | None = "2-1",
    params_used: dict | None = None,
    override_reason: str | None = None,
    detail: str = "",
) -> None:
    """재공(WIP) 매칭 결정 근거를 기록한다.

    `audit_log.action_type='wip_match'` 행으로 저장. matched_wip_id 가 None
    이면 매칭 실패 (full WIP shortage 또는 후보 0건).

    decision_card ❸ 재공 활용 섹션 + ❶ 적합성 줄 (재공 활용된 batch) 의
    데이터 출처. caller 는 stage='stage1' 을 명시해야 함 (S1).
    """
    constraints = [
        {
            "id": applied_rule or "2-1",
            "name": "재공 활용",
            "result": "pass" if matched_wip_id else "fail",
            "params": params_used or {},
            "override_reason": override_reason,
            "detail": detail,
        }
    ]
    log_decision(
        db=db,
        run_label=run_label,
        stage=stage,
        action_type="wip_match",
        batch_id=batch_id,
        constraints_applied=constraints,
        reason=(
            f"wip_match: matched_wip_id={matched_wip_id}"
            if matched_wip_id
            else "wip_match: no match (shortage or 0 candidates)"
        ),
        alternatives=candidates_evaluated,
    )


def log_filter_out(
    db: Session,
    *,
    run_label: str,
    stage: str,
    batch_id: int | None,
    reason_code: str,
    reason_detail: str,
    excluded_candidates: list[dict] | None = None,
) -> None:
    """후보 (배치/설비/시간슬롯) 가 사전 필터링 단계에서 탈락한 사유 기록.

    `audit_log.action_type='filter_out'` 행으로 저장. decision_card ❻
    '다른 설비/시간 탈락 사유' 섹션의 데이터 출처. caller 는 stage='stage1'
    을 명시해야 함 (S1).
    """
    log_decision(
        db=db,
        run_label=run_label,
        stage=stage,
        action_type="filter_out",
        batch_id=batch_id,
        constraints_applied=[
            {
                "id": reason_code,
                "name": "filter_out",
                "result": "fail",
                "detail": reason_detail,
            }
        ],
        reason=reason_detail,
        alternatives=excluded_candidates,
    )


def get_audit_trail(
    run_label: str,
    db: Session,
    batch_id: int | None = None,
    task_id: int | None = None,
) -> list[dict]:
    """감사 로그 조회"""
    query = db.query(AuditLog).filter(AuditLog.run_label == run_label)
    if batch_id:
        query = query.filter(AuditLog.batch_id == batch_id)
    if task_id:
        query = query.filter(AuditLog.task_id == task_id)

    logs = query.order_by(AuditLog.created_at.asc()).all()

    return [
        {
            "log_id": log.log_id,
            "stage": log.stage,
            "batch_id": log.batch_id,
            "task_id": log.task_id,
            "action_type": log.action_type,
            "constraints_applied": log.constraints_applied,
            "decision_reason": log.decision_reason,
            "alternatives_considered": log.alternatives_considered,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        }
        for log in logs
    ]
