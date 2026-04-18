"""시스 색상 묶음(cluster) 스케줄링 헬퍼.

'같은 설비 카테고리(A100/A120/저압시스/고압시스) + 같은 주차 반버킷 + 같은 색상'
인 그룹들을 하나의 묶음으로 취급해 latest_start 역산 + 연속 배치를 지원한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SheathCluster:
    """시스 색상 묶음. 같은 설비/주차/색상 그룹들의 메타 집계."""

    cluster_id: str
    equipment_category: str  # "A100" | "A120" | "저압시스" | "고압시스"
    color: str
    due_week_int: int  # schedule_optimizer._sheath_group_due_week_int 규격
    group_keys: list[str] = field(default_factory=list)
