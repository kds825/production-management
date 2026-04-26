"""감사 추적 기록 — 모든 스케줄링 결정의 근거를 기록.

Phase 6 (decision_card) 추가: `log_wip_match` / `log_filter_out` 두 thin wrapper.
- `log_wip_match`: 재공 매칭 결정 (decision_card ❸ 재공 활용 섹션 출처)
- `log_filter_out`: 사전 필터링 단계 탈락 (decision_card ❻ 다른 설비/시간 탈락 사유 출처)

audit_log.stage 컬럼은 NOT NULL 이라 caller 가 항상 "stage1"/"stage2" 명시 (S1).
"""

from sqlalchemy.orm import Session

from app.infrastructure.models.audit_log import AuditLog


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
