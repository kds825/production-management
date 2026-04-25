"""``base_date`` (스케줄 시작 기준일시) 결정 로직.

원래 ``optimization_loop._run_optimization_once`` 의 lines 152-172 블록.

핵심:
  - ``override`` 가 명시되면 그대로 반환.
  - 그 외에는 ``run_label`` (YYYYMMDD_HHMMSS 형식) 의 앞 8자리를 날짜로 파싱
    → 해당 날짜의 08:00:00 KST.
  - 파싱 실패 시 KST 당일 08:00:00 으로 폴백.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


def resolve_base_date(run_label: str, override: datetime | None) -> datetime:
    """run_label 또는 KST-now 기반 base_date 결정.

    Returns naive datetime (timezone 정보 없음 — caller 가 KST 로 해석).
    """
    if override is not None:
        return override
    try:
        date_part = run_label.split("_")[0]  # "20260406"
        return datetime(
            int(date_part[:4]),
            int(date_part[4:6]),
            int(date_part[6:8]),
            8,
            0,
            0,
        )
    except Exception:
        kst_now = datetime.now(ZoneInfo("Asia/Seoul"))
        return kst_now.replace(hour=8, minute=0, second=0, microsecond=0).replace(
            tzinfo=None
        )
