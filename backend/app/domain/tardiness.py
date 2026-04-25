"""납기 초과(tardiness) 전 공정 집계 메트릭.

`count_color_transitions` 와 동일한 패턴 — solver 결과를 DB 에서 읽어 운영
지표를 독립 계산한다 (관찰자 효과 격리). CP-SAT objective 가 tardiness 를
minimize 하지만 past-due / capacity-infeasible 케이스는 최종 결과에도 남으므로,
UI/운영자에게 전체 납기초과 현황을 보여주기 위한 지표가 필요하다.

Why 독립 모듈:
- sheath_cluster 는 시스 전용. tardiness 는 전 공정 대상이므로 분리.
- schedule_optimizer 순환 의존 회피 (schedule_optimizer 가 tardiness_metrics 를
  호출하지 반대 방향 없음).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

# 근무 분 기준 하루(8시간 2교대 기준이 아닌 단순 분)
_MINUTES_PER_DAY: int = 24 * 60


def _priority_label(customer_priority: int | None) -> str:
    """customer_priority (1=가장 높음 ~ 99=낮음) → 우선순위 레이블.

    cp_sat_optimizer 의 _priority_label 과 동일 규칙. 임포트 시 순환 의존
    때문에 여기 복제 — 변경 시 두 곳 동기화 필요.
    """
    if customer_priority is None:
        return "normal"
    if customer_priority <= 3:
        return "critical"
    if customer_priority <= 7:
        return "urgent"
    return "normal"


def count_tardiness(run_label: str, db) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    """지정 run 의 전 공정 ScheduleTask 를 납기(ProductionBatch.due_date) 와
    비교해 초과 현황 집계.

    Args:
        run_label: 조회 대상 run 식별자. None/빈값이면 0-집계 반환.
        db: SQLAlchemy Session.

    Returns:
        {
            "total_tardy_count": int,         # 납기 초과 task 수
            "total_tardy_minutes": int,       # 초과 분 합계 (모든 task 합)
            "max_tardy_days": float,          # 가장 심한 초과(일 단위)
            "per_priority": {                 # 고객 우선순위별 breakdown
                "critical": {"count": int, "minutes": int},
                "urgent":   {"count": int, "minutes": int},
                "normal":   {"count": int, "minutes": int},
            },
            "per_process": {                  # 공정별 breakdown
                process_name: {"count": int, "minutes": int}
            },
            "worst_tasks": [                  # 상위 N 건 상세 (기본 10)
                {
                    "task_id": int,
                    "batch_group": str,
                    "equipment_code": str,
                    "process_name": str,
                    "due_date": "YYYY-MM-DD",
                    "end_datetime": "YYYY-MM-DD HH:MM",
                    "tardy_days": float,
                    "customer_priority": int | None,
                },
                ...
            ],
        }

    Rule:
        - 초과 판정: end_datetime.date() > due_date  (시간대 없이 날짜 기준)
        - 초과 분: (end_datetime - due_date_end_of_day) in minutes, 정수 반올림
          due_date 를 해당 일자 23:59:59 기준으로 봐 "당일 내 완료" 는 초과 0.
        - due_date 가 없으면 (None) 초과 판정 skip — 집계에서 제외
        - end_datetime 이 None 인 task (배치 실패) 는 집계에서 제외, 단
          warnings 로 반환하지 않음 (호출측에서 별도 점검 필요)
        - 정렬: worst_tasks 는 tardy_days 내림차순 (가장 심한 것부터)
    """
    from app.infrastructure.models.production_batch import ProductionBatch
    from app.infrastructure.models.schedule_task import ScheduleTask

    empty: dict[str, Any] = {
        "total_tardy_count": 0,
        "total_tardy_minutes": 0,
        "max_tardy_days": 0.0,
        "per_priority": {
            "critical": {"count": 0, "minutes": 0},
            "urgent": {"count": 0, "minutes": 0},
            "normal": {"count": 0, "minutes": 0},
        },
        "per_process": {},
        "worst_tasks": [],
    }

    if not run_label:
        return empty

    # JOIN — 필요한 컬럼만 투영
    rows = (
        db.query(
            ScheduleTask.task_id,
            ScheduleTask.equipment_code,
            ScheduleTask.end_datetime,
            ProductionBatch.batch_group,
            ProductionBatch.process_name,
            ProductionBatch.due_date,
            ProductionBatch.customer_priority,
        )
        .join(ProductionBatch, ScheduleTask.batch_id == ProductionBatch.batch_id)
        .filter(ScheduleTask.run_label == run_label)
        .filter(ScheduleTask.end_datetime.isnot(None))
        .all()
    )

    total_count = 0
    total_minutes = 0
    max_days = 0.0
    per_priority: dict[str, dict[str, int]] = {
        "critical": {"count": 0, "minutes": 0},
        "urgent": {"count": 0, "minutes": 0},
        "normal": {"count": 0, "minutes": 0},
    }
    per_process: dict[str, dict[str, int]] = {}
    tardy_list: list[dict[str, Any]] = []

    for (
        task_id,
        equipment_code,
        end_dt,
        batch_group,
        process_name,
        due_date,
        customer_priority,
    ) in rows:
        if due_date is None or end_dt is None:
            continue
        # 당일 마감 허용: due_date 23:59:59 기준
        due_dt_end = datetime.combine(
            due_date, datetime.max.time().replace(microsecond=0)
        )
        if end_dt <= due_dt_end:
            continue  # on-time
        # 초과
        delta_min = int(round((end_dt - due_dt_end).total_seconds() / 60))
        if delta_min <= 0:
            continue
        total_count += 1
        total_minutes += delta_min
        tardy_days = delta_min / _MINUTES_PER_DAY
        if tardy_days > max_days:
            max_days = tardy_days

        prio_label = _priority_label(customer_priority)
        per_priority[prio_label]["count"] += 1
        per_priority[prio_label]["minutes"] += delta_min

        proc = process_name or "(미지정)"
        per_process.setdefault(proc, {"count": 0, "minutes": 0})
        per_process[proc]["count"] += 1
        per_process[proc]["minutes"] += delta_min

        tardy_list.append(
            {
                "task_id": task_id,
                "batch_group": batch_group or "",
                "equipment_code": equipment_code or "",
                "process_name": proc,
                "due_date": due_date.isoformat(),
                "end_datetime": end_dt.strftime("%Y-%m-%d %H:%M"),
                "tardy_days": round(tardy_days, 2),
                "customer_priority": customer_priority,
            }
        )

    # worst_tasks: 상위 10건만 (DoS 방지)
    tardy_list.sort(key=lambda t: t["tardy_days"], reverse=True)
    worst_tasks = tardy_list[:10]

    return {
        "total_tardy_count": total_count,
        "total_tardy_minutes": total_minutes,
        "max_tardy_days": round(max_days, 2),
        "per_priority": per_priority,
        "per_process": per_process,
        "worst_tasks": worst_tasks,
    }
