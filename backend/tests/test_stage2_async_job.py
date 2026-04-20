"""Stage 2 비동기 job queue 스모크 테스트.

async 엔드포인트:
  - POST /api/pipeline/stage2/async  → { job_id, status: 'running' }
  - GET  /api/pipeline/stage2/status/{job_id}  → { status, result|error }

왜 이 테스트 범위:
  job queue 자체의 스레드 거동 + HTTP 바인딩 검증. CP-SAT 실 솔버는
  runner 를 monkeypatch 해서 결정론적으로 '성공' 또는 'overlap_alert' 를
  시뮬레이트 (실솔버 wall-time 을 피해 결정론 확보).
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.exceptions import SchedulerOverlapError
from app.main import app
from app.services import stage2_job_queue


@pytest.fixture(autouse=True)
def _reset_queue():
    """각 테스트 시작 시 in-memory 큐 초기화."""
    stage2_job_queue._reset_for_tests()
    yield
    stage2_job_queue._reset_for_tests()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _wait_until_done(client: TestClient, job_id: str, timeout_s: float = 5.0) -> dict:
    """폴링 헬퍼 — done/overlap_alert/error 가 될 때까지 짧게 대기."""
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        resp = client.get(f"/api/pipeline/stage2/status/{job_id}")
        assert resp.status_code == 200, resp.text
        last = resp.json()
        if last["status"] != "running":
            return last
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} 타임아웃: last={last}")


def test_async_job_submit_returns_job_id(monkeypatch, client):
    """POST /stage2/async → 즉시 { job_id, status: 'running' } 반환."""
    from app.presentation.routes import plan_pipeline

    # runner 를 즉시 성공하도록 교체 (실 auto_schedule 우회)
    def _ok_runner(req, db):
        return {
            "run_label": req.run_label,
            "schedule": {"engine": "greedy", "total_tasks": 0},
            "violations": [],
            "total_violations": 0,
            "overlap_alert": False,
        }

    monkeypatch.setattr(
        plan_pipeline,
        "_execute_stage2_core",
        lambda *a, **k: _ok_runner(type("R", (), {"run_label": a[0]})(), None),
    )

    resp = client.post(
        "/api/pipeline/stage2/async",
        json={"run_label": "test-async-ok", "optimizer": "greedy"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "job_id" in data
    assert data["status"] == "running"
    assert data["run_label"] == "test-async-ok"


def test_async_job_status_transitions_to_done(monkeypatch, client):
    """submit → polling 으로 status=done 로 전이되고 result 구조가 sync 와 동치."""
    from app.presentation.routes import plan_pipeline

    expected_result = {
        "run_label": "test-async-done",
        "schedule": {"engine": "greedy", "total_tasks": 0},
        "violations": [],
        "total_violations": 0,
        "overlap_alert": False,
    }

    monkeypatch.setattr(
        plan_pipeline,
        "_execute_stage2_core",
        lambda run_label, base_date_dt, optimizer, db: expected_result,
    )

    resp = client.post(
        "/api/pipeline/stage2/async",
        json={"run_label": "test-async-done", "optimizer": "greedy"},
    )
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]

    final = _wait_until_done(client, job_id)
    assert final["status"] == "done"
    assert final["result"] == expected_result
    assert final["finished_at"] is not None


def test_async_job_overlap_alert_mapping(monkeypatch, client):
    """runner 가 SchedulerOverlapError → status=overlap_alert + result 페이로드."""
    from app.presentation.routes import plan_pipeline

    def _raise_overlap(run_label, base_date_dt, optimizer, db):
        raise SchedulerOverlapError(
            "mock overlap",
            run_label=run_label,
            violations=[{"constraint_id": "overlap", "detail": "mock"}],
            attempts=3,
        )

    monkeypatch.setattr(plan_pipeline, "_execute_stage2_core", _raise_overlap)

    resp = client.post(
        "/api/pipeline/stage2/async",
        json={"run_label": "test-async-overlap", "optimizer": "cpsat"},
    )
    job_id = resp.json()["job_id"]

    final = _wait_until_done(client, job_id)
    assert final["status"] == "overlap_alert"
    assert final["result"]["overlap_alert"] is True
    assert final["result"]["attempts"] == 3
    assert final["result"]["total_violations"] == 1


def test_async_job_status_404_when_unknown(client):
    """존재하지 않는 job_id → 404."""
    resp = client.get("/api/pipeline/stage2/status/does-not-exist")
    assert resp.status_code == 404


def test_async_job_missing_run_label_422(client):
    """run_label 없으면 400 (job 생성 전 body 검증)."""
    resp = client.post("/api/pipeline/stage2/async", json={})
    assert resp.status_code == 400
