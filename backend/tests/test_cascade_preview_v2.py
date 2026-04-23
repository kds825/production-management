"""Task 11: v2 cascade-preview 엔드포인트 계약 테스트.

- 헤더 가드 (`X-Cascade-API-Version: 2`) — 400 on missing/wrong
- TZ guard — 422 on 'Z' suffix
- FEATURE_FLAG off — invalid_equipment unresolved
- 응답 스키마 키 계약
"""

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.services.cascade import CascadePreviewResult

client = TestClient(app)


def _mock_preview_result() -> CascadePreviewResult:
    """DB 를 거치지 않는 empty preview — 스키마/flow 검증 전용."""
    return CascadePreviewResult(
        request_id="test-uuid-1234",
        summary="no change",
        pushes=[],
        pulls=[],
        unresolved=[],
        can_auto_resolve=True,
        iter_count=0,
        truncated=False,
    )


class TestCascadePreviewV2HeaderGate:
    def test_missing_header_returns_400(self):
        resp = client.post(
            "/api/schedules/cascade-preview",
            json={
                "task_id": "X",
                "new_start": "2026-04-20T10:00:00",
                "new_end": "2026-04-20T12:00:00",
            },
        )
        assert resp.status_code == 400
        assert "X-Cascade-API-Version" in resp.text

    def test_wrong_version_header_returns_400(self):
        resp = client.post(
            "/api/schedules/cascade-preview",
            headers={"X-Cascade-API-Version": "1"},
            json={
                "task_id": "X",
                "new_start": "2026-04-20T10:00:00",
                "new_end": "2026-04-20T12:00:00",
            },
        )
        assert resp.status_code == 400


class TestCascadePreviewV2TZGuard:
    def test_z_suffix_rejected(self):
        resp = client.post(
            "/api/schedules/cascade-preview",
            headers={"X-Cascade-API-Version": "2"},
            json={
                "task_id": "X",
                "new_start": "2026-04-20T10:00:00Z",
                "new_end": "2026-04-20T12:00:00Z",
            },
        )
        assert resp.status_code == 422

    def test_offset_suffix_rejected(self):
        resp = client.post(
            "/api/schedules/cascade-preview",
            headers={"X-Cascade-API-Version": "2"},
            json={
                "task_id": "X",
                "new_start": "2026-04-20T10:00:00+09:00",
                "new_end": "2026-04-20T12:00:00+09:00",
            },
        )
        assert resp.status_code == 422


class TestCascadePreviewV2FeatureFlag:
    def test_feature_flag_off_returns_invalid_equipment(self, monkeypatch):
        monkeypatch.setenv("FEATURE_FLAG_CASCADE_V2", "off")
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
        body = resp.json()
        assert body["can_auto_resolve"] is False
        assert body["pushes"] == []
        assert body["pulls"] == []
        assert len(body["unresolved"]) == 1
        u = body["unresolved"][0]
        assert u["reason"] == "invalid_equipment"
        assert "cascade v2 disabled" in u["detail"].lower()


class TestCascadePreviewV2ResponseSchema:
    def test_response_has_all_v2_keys(self, monkeypatch):
        """FEATURE_FLAG on + plan_cascade_preview mock — 계약 필드 모두 존재."""
        monkeypatch.setenv("FEATURE_FLAG_CASCADE_V2", "on")
        with patch(
            "app.presentation.routes.schedules.plan_cascade_preview",
            return_value=_mock_preview_result(),
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
        body = resp.json()
        for key in [
            "request_id",
            "summary",
            "pushes",
            "pulls",
            "unresolved",
            "can_auto_resolve",
            "iter_count",
            "truncated",
        ]:
            assert key in body, f"missing key: {key}"
        assert isinstance(body["pushes"], list)
        assert isinstance(body["pulls"], list)
        assert isinstance(body["unresolved"], list)
        assert body["request_id"]
        assert body["iter_count"] == 0
        assert body["truncated"] is False
