"""Structured JSON logging for cascade endpoints — request_id correlation.

왜 컨텍스트 매니저: 요청 진입 시 start time 기록, 종료 시 duration_ms + status
를 합쳐 단일 INFO 레코드로 flush. 호출부는 yield 된 `extra` dict 에 추가 필드를
써 넣기만 하면 됨 — 중간 raise 가 발생해도 finally 에서 로그가 보장된다.

왜 JSON line: 표준 logging handler 가 받아 structlog/loki 로 바로 파싱 가능.
`json.dumps(..., default=str)` 로 datetime/enum 등 non-JSON 타입도 안전 처리.
"""

import json
import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

log = logging.getLogger("cascade")


@contextmanager
def log_cascade_request(
    request_id: str, route: str, **ctx: Any
) -> Iterator[dict[str, Any]]:
    """Cascade 엔드포인트 래핑용 로그 컨텍스트.

    Args:
        request_id: 요청별 correlation id (uuid4 등).
        route: 엔드포인트 식별자 (e.g. "/cascade-preview").
        **ctx: 초기 로그 필드 (task_id, new_start, …).

    Yields:
        dict — 호출자가 mutate 하면 종료 시 병합되어 로그에 포함.
    """
    start = time.perf_counter()
    extra: dict[str, Any] = {"request_id": request_id, "route": route, **ctx}
    try:
        yield extra
        # 호출자가 status 를 명시적으로 설정하지 않은 정상 경로 → "ok".
        extra.setdefault("status", "ok")
    except Exception as e:  # pragma: no cover — raise 보장용
        extra["status"] = "error"
        extra["error_type"] = type(e).__name__
        extra["error"] = str(e)
        raise
    finally:
        extra["duration_ms"] = int((time.perf_counter() - start) * 1000)
        # default=str 로 datetime / Enum / UUID 등도 안전 직렬화.
        log.info(json.dumps(extra, default=str))
