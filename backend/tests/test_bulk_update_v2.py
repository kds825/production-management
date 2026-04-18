"""Task 13: bulk-update v2 계약 + 재검증 smoke.

- FEATURE_FLAG off 시 multi-change 는 `FEATURE_DISABLED` (422).
- TZ-aware datetime 은 Pydantic validator 로 거절 (422).
- 실 validation(overlap/predecessor/due_date) 은 DB seed 부담으로 E2E 성격이 강해
  서비스 단위(`test_schedule_validators.py`) 에 위임.
"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


class TestBulkUpdateV2FeatureFlag:
    def test_flag_off_rejects_multi(self, monkeypatch):
        """flag off + multi-change → FEATURE_DISABLED 422."""
        monkeypatch.setenv("FEATURE_FLAG_CASCADE_V2", "off")
        resp = client.post(
            "/api/schedules/tasks/bulk-update",
            json={
                "changes": [
                    {
                        "task_id": "1",
                        "new_start": "2026-04-20T10:00:00",
                        "new_end": "2026-04-20T12:00:00",
                    },
                    {
                        "task_id": "2",
                        "new_start": "2026-04-20T12:00:00",
                        "new_end": "2026-04-20T14:00:00",
                    },
                ]
            },
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body["detail"]["error_code"] == "FEATURE_DISABLED"
        assert body["detail"]["offending_task_id"] == "1"
        assert body["detail"]["can_retry"] is False

    def test_flag_off_single_change_not_feature_disabled(self, monkeypatch):
        """단일 change 는 legacy 호환 경로로 허용되어야 함 — flag 로 막히지 않음.

        실제 task 유무는 DB seed 에 의존하므로 여기선 "`FEATURE_DISABLED` 가 아닌
        결과" 만 확인.
        """
        monkeypatch.setenv("FEATURE_FLAG_CASCADE_V2", "off")
        resp = client.post(
            "/api/schedules/tasks/bulk-update",
            json={
                "changes": [
                    {
                        "task_id": "999999",  # 없는 id — 404 가 나도 OK, flag 가 아니면 통과.
                        "new_start": "2026-04-20T10:00:00",
                        "new_end": "2026-04-20T12:00:00",
                    }
                ]
            },
        )
        if resp.status_code == 422:
            # 422 면 flag error 는 아닌지 — schema/validation 다른 이유일 수 있음.
            detail = resp.json().get("detail")
            if isinstance(detail, dict):
                assert detail.get("error_code") != "FEATURE_DISABLED"


class TestBulkUpdateV2Schema:
    def test_tz_suffix_rejected(self, monkeypatch):
        """Z 접미사 — Pydantic field_validator 로 422."""
        monkeypatch.setenv("FEATURE_FLAG_CASCADE_V2", "on")
        resp = client.post(
            "/api/schedules/tasks/bulk-update",
            json={
                "changes": [
                    {
                        "task_id": "X",
                        "new_start": "2026-04-20T10:00:00Z",
                        "new_end": "2026-04-20T12:00:00Z",
                    }
                ]
            },
        )
        assert resp.status_code == 422

    def test_empty_changes_returns_noop_success(self, monkeypatch):
        """빈 changes 는 no-op 성공 — change_set_id 는 빈 문자열."""
        monkeypatch.setenv("FEATURE_FLAG_CASCADE_V2", "on")
        resp = client.post(
            "/api/schedules/tasks/bulk-update",
            json={"changes": []},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["change_set_id"] == ""
        assert body["updated_task_ids"] == []
