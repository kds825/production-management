"""Central feature flag registry.

각 플래그는 환경변수 한 개로 제어되며, 배포 후 재시작 없이 ON/OFF 가 바뀌어도
요청 단위로 즉시 반영되도록 런타임 lookup 을 한다.
"""

import os

_TRUTHY = {"1", "on", "true", "yes"}


def is_cascade_v2_enabled() -> bool:
    return os.environ.get("FEATURE_FLAG_CASCADE_V2", "").strip().lower() in _TRUTHY
