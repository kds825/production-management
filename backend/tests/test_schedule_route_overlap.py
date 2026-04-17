"""스케줄 API 라우트 overlap_alert 응답 테스트.

왜 필요: SchedulerOverlapError 는 '스케줄 저장 거부' 신호이므로
HTTP 500이 아닌 200 + overlap_alert=True 로 프론트에 경고 배너를
노출할 수 있도록 라우트에서 잡아야 한다.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.exceptions import SchedulerOverlapError
from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_stage2_route_returns_overlap_alert_on_persist(monkeypatch, client):
    """HTTP 응답: SchedulerOverlapError → 200 + overlap_alert=True."""
    # 라우트가 import-time 에 바인딩한 심볼을 교체해야 실제 호출이 훅됨
    from app.presentation.routes import plan_pipeline

    def _raise_overlap(run_label, db, **kwargs):
        raise SchedulerOverlapError(
            "test overlap",
            run_label=run_label,
            violations=[{"constraint_id": "overlap", "detail": "mock"}],
            attempts=3,
        )

    # greedy 경로를 타도록 optimizer=greedy 로 호출하고,
    # 라우트 파일 내 이름(auto_schedule) 을 패치한다.
    monkeypatch.setattr(plan_pipeline, "auto_schedule", _raise_overlap)

    resp = client.post(
        "/api/pipeline/stage2",
        json={"run_label": "test-route-overlap", "optimizer": "greedy"},
    )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data.get("overlap_alert") is True
    assert data.get("attempts") == 3
    assert data.get("run_label") == "test-route-overlap"
    # 기존 위반 리스트도 함께 전달
    assert (
        data.get("violations") and data["violations"][0]["constraint_id"] == "overlap"
    )


def test_stage2_route_overlap_alert_via_cpsat_fallback(monkeypatch, client):
    """CP-SAT 경로(optimizer=cpsat) 에서도 overlap_alert 이 반환된다.

    Fix P0-4A 이후: plan_pipeline 은 cp_sat_schedule 을 직접 호출하지 않고
    ``auto_schedule(use_cpsat=True)`` 로 통합됨. CP-SAT 미해결 → greedy 폴백은
    auto_schedule 내부에서 처리되며, 어느 경로에서든 ``SchedulerOverlapError``
    가 발생하면 라우트가 동일하게 200 + overlap_alert=True 로 응답해야 한다.
    """
    from app.presentation.routes import plan_pipeline

    def _raise_overlap(run_label, db, **kwargs):
        raise SchedulerOverlapError(
            "fallback overlap",
            run_label=run_label,
            violations=[{"constraint_id": "overlap"}],
            attempts=3,
        )

    # 라우트가 import 한 단일 entry 를 패치 — use_cpsat 플래그 포함 kwargs 는 무시
    monkeypatch.setattr(plan_pipeline, "auto_schedule", _raise_overlap)

    resp = client.post(
        "/api/pipeline/stage2",
        json={"run_label": "test-cpsat-fallback", "optimizer": "cpsat"},
    )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data.get("overlap_alert") is True
    assert data.get("attempts") == 3
