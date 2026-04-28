"""cp_sat_schedule() 의 진단 스냅샷 단계 — Phase 2 Task 2.10a 추출.

원본: ``orchestrator.py:431-454`` 본문 그대로. solver 입력(group_meta) +
출력(start/end/equip/tardiness) + 주요 objective 항의 실제 기여값을
JSON 한 벌로 저장 — "어떤 그룹이 왜 그 위치에 갔는가" 를 사후에 재현
가능하도록.

Why thin wrapper:
    write_snapshot 자체는 이미 ``snapshot.py`` 에 분리되어 있다. 본 모듈은
    orchestrator 가 호출하던 keyword args 묶음을 그대로 전달하는 use-case
    helper. 5 sub-step 분할의 일관성 (모든 §8 sub-step 이 별도 모듈) 을 위해.
"""

from __future__ import annotations

from datetime import datetime

from app.application.scheduling.cp_sat.helpers import (
    _MAX_HORIZON_MIN,
    _build_snapshot_weights,
)
from app.application.scheduling.cp_sat.model_builder import BuiltModel
from app.application.scheduling.cp_sat.snapshot import write_snapshot
from app.domain.constants import _WORK_MIN_PER_DAY


def write_diagnostic_snapshot(
    *,
    run_label: str,
    base_date: datetime,
    group_meta: dict,
    frozen_group_keys: set[str] | None,
    solver,
    status_name: str,
    built: BuiltModel,
) -> None:
    """원본: orchestrator.py:431-454 본문 그대로.

    BuiltModel 의 vars/terms 를 풀어 write_snapshot 에 전달.
    """
    # ── 진단 스냅샷: solver 입력(group_meta) + 출력(start/end/equip/tardiness) +
    # 주요 objective 항의 실제 기여값을 JSON 한 벌로 저장. 원인 분석 시
    # "어떤 그룹이 왜 그 위치에 갔는가" 를 사후에 재현할 수 있도록.
    write_snapshot(
        run_label=run_label,
        base_date=base_date,
        group_meta=group_meta,
        frozen_group_keys=frozen_group_keys,
        solver=solver,
        solver_status_name=status_name,
        start_vars=built.start_vars,
        end_vars=built.end_vars,
        equip_vars=built.equip_vars,
        tardiness_vars=built.tardiness_vars,
        edd_pair_vars=built.edd_pair_terms,
        slack_terms_meta=built.slack_terms_meta,
        weights=_build_snapshot_weights(),
        work_min_per_day=_WORK_MIN_PER_DAY,
        max_horizon_min=_MAX_HORIZON_MIN,
        idle_terms=built.idle_terms,
        transition_terms=built.transition_terms,
        sheath_end_terms=built.sheath_end_terms,
        edd_mixed_pastdue_vars=built.edd_mixed_pastdue_terms,
    )
