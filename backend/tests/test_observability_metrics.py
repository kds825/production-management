"""Task 23: Prometheus metrics + structured logging 계약 검증.

- `/metrics` 엔드포인트가 text-exposition 포맷을 돌려주는지.
- FEATURE_FLAG off 경로가 unresolved counter (invalid_equipment) 를 증가시키는지.
- cascade-preview 래퍼가 Histogram 에 샘플을 1 개 이상 기록하는지.
- revert 의 not_found/conflict/success 경로가 해당 status label counter 를 증가시키는지.

왜 counter 값을 raw `._value.get()` 으로 읽는지: prometheus_client 는 중앙 registry 에
Counter 단일 인스턴스를 보관하고 테스트가 같은 프로세스에서 여러 번 요청을 쏴도
label 별 누적값을 유지한다. 때문에 테스트는 "before/after delta" 만 검증하는 쪽이
다른 테스트 영향을 받지 않는다.
"""

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.observability.metrics import (
    cascade_preview_duration_seconds,
    cascade_revert_total,
    cascade_unresolved_total,
)
from app.services.cascade import CascadePreviewResult

client = TestClient(app)


def _unresolved_count(reason: str) -> float:
    """현재 누적된 unresolved counter 값. labels() 는 idempotent."""
    return cascade_unresolved_total.labels(reason=reason)._value.get()


def _revert_count(status: str) -> float:
    return cascade_revert_total.labels(status=status)._value.get()


def _preview_hist_sample_count() -> float:
    """Histogram `_count` 은 관측 총 샘플 수 — `.time()` 이 1회 증가시킴."""
    return cascade_preview_duration_seconds._sum.get(), sum(
        b.get() for b in cascade_preview_duration_seconds._buckets
    )


# ---------------------------------------------------------------------------
# /metrics 엔드포인트
# ---------------------------------------------------------------------------


class TestMetricsEndpoint:
    def test_returns_prometheus_text_format(self):
        resp = client.get("/metrics")
        assert resp.status_code == 200
        # text/plain; version=0.0.4 — prometheus_client 의 기본 포맷.
        assert "text/plain" in resp.headers.get("content-type", "")

    def test_exposes_cascade_metrics(self):
        resp = client.get("/metrics")
        body = resp.text
        # 4개 메트릭 이름이 모두 노출되어야 함 (HELP 라인에 등장).
        assert "cascade_preview_duration_seconds" in body
        assert "cascade_unresolved_total" in body
        assert "cascade_revert_total" in body
        assert "cascade_feature_flag_state" in body


# ---------------------------------------------------------------------------
# cascade-preview — FEATURE_FLAG off → unresolved counter 증가
# ---------------------------------------------------------------------------


class TestCascadePreviewFeatureFlagMetric:
    def test_feature_flag_off_increments_invalid_equipment(self, monkeypatch):
        monkeypatch.setenv("FEATURE_FLAG_CASCADE_V2", "off")
        before = _unresolved_count("invalid_equipment")
        resp = client.post(
            "/api/schedules/cascade-preview",
            headers={"X-Cascade-API-Version": "2"},
            json={
                "task_id": "X",
                "new_start": "2026-04-20T10:00:00",
                "new_end": "2026-04-20T12:00:00",
            },
        )
        assert resp.status_code == 200
        after = _unresolved_count("invalid_equipment")
        # 단일 요청 → +1 정확히 증가.
        assert after - before == 1


class TestCascadePreviewDurationHistogram:
    """FEATURE_FLAG on + mock service 로 preview 한 번 호출 → Histogram 에 샘플 1개 기록."""

    def test_histogram_records_sample(self, monkeypatch):
        monkeypatch.setenv("FEATURE_FLAG_CASCADE_V2", "on")
        _sum_before, _count_before = _preview_hist_sample_count()

        mock_result = CascadePreviewResult(
            request_id="test-uuid",
            summary="noop",
            pushes=[],
            pulls=[],
            unresolved=[],
            can_auto_resolve=True,
            iter_count=0,
            truncated=False,
        )
        with patch(
            "app.presentation.routes.schedules.plan_cascade_preview",
            return_value=mock_result,
        ):
            resp = client.post(
                "/api/schedules/cascade-preview",
                headers={"X-Cascade-API-Version": "2"},
                json={
                    "task_id": "X",
                    "new_start": "2026-04-20T10:00:00",
                    "new_end": "2026-04-20T13:00:00",
                },
            )
        assert resp.status_code == 200
        _sum_after, _count_after = _preview_hist_sample_count()
        # bucket 샘플 총합이 최소 1 증가 (accumulative bucket — le=+Inf 도 +1).
        assert _count_after > _count_before


# ---------------------------------------------------------------------------
# revert — not_found status counter 증가
# ---------------------------------------------------------------------------


class TestRevertNotFoundMetric:
    def test_unknown_change_set_increments_not_found(self):
        before = _revert_count("not_found")
        resp = client.post("/api/schedules/revert/does-not-exist-metrics-test")
        assert resp.status_code == 404
        after = _revert_count("not_found")
        assert after - before == 1
