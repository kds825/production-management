"""Prometheus metrics — cascade preview / bulk-update / revert.

왜 prometheus_client: Task 23 계약. 이미 `backend/requirements` 간접 의존성으로
설치돼 있고 (버전 0.25+) `generate_latest(...)` 로 `/metrics` 엔드포인트를
노출하면 기존 모니터링 파이프라인에 그대로 태울 수 있음.

왜 모듈 전역으로 정의: prometheus_client 는 동일 이름의 메트릭을 두 번 등록하면
`ValueError` — 테스트에서 `TestClient(app)` 를 여러 번 생성해도 싱글톤이
유지되도록 import 시 한 번만 등록한다.
"""

from prometheus_client import Counter, Gauge, Histogram

# ---------------------------------------------------------------------------
# cascade-preview 요청 지연 — 버킷은 p50 수 ms ~ p99 2s 를 가정.
# 5s 는 상한 — BFS timeout / iter_count hard limit (100) 도달 시의 최악값.
# ---------------------------------------------------------------------------
cascade_preview_duration_seconds = Histogram(
    "cascade_preview_duration_seconds",
    "cascade-preview request latency (seconds)",
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0),
)

# ---------------------------------------------------------------------------
# cascade-preview 가 반환하는 unresolved 엔트리 counter — reason 별 집계.
# UnresolvedReason enum 값을 그대로 label 로 사용하므로 카드inality 는 low (~5).
# ---------------------------------------------------------------------------
cascade_unresolved_total = Counter(
    "cascade_unresolved_total",
    "Count of unresolved entries returned by cascade-preview",
    ["reason"],
)

# ---------------------------------------------------------------------------
# revert 호출 counter — status = success/conflict/not_found.
# 왜 status label: 성공/409/404 비율이 사용자 retry 전략과 직결되어 대시보드 필수 지표.
# ---------------------------------------------------------------------------
cascade_revert_total = Counter(
    "cascade_revert_total",
    "Count of revert operations by outcome",
    ["status"],  # "success" | "conflict" | "not_found"
)

# ---------------------------------------------------------------------------
# FEATURE_FLAG_CASCADE_V2 상태 — 요청 시점 환경변수 snapshot.
# Gauge 선택 이유: on/off 는 "현재 상태" 의 성격이라 Counter 부적합.
# 1 = on, 0 = off.
# ---------------------------------------------------------------------------
cascade_feature_flag_state = Gauge(
    "cascade_feature_flag_state",
    "Current state of FEATURE_FLAG_CASCADE_V2 (1=on, 0=off)",
)
