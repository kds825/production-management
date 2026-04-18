"""Observability — cascade 엔드포인트의 구조화 로그와 Prometheus 메트릭.

Task 23 (2026-04-18-block-duration-edit-cascade.md):
- `metrics` 모듈: prometheus_client Histogram/Counter/Gauge 정의.
- `cascade_logging` 모듈: request_id correlation 용 JSON 로그 컨텍스트 매니저.
라우터(`schedules.py`) 가 3개 엔드포인트(cascade-preview / bulk-update / revert) 에서
직접 import 해 래핑한다.
"""
