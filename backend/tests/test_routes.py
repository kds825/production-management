"""
API 엔드포인트 통합 테스트 — TestClient로 실제 HTTP 요청/응답 검증
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# 헬스체크
# ---------------------------------------------------------------------------


class TestHealth:
    def test_헬스체크_200(self) -> None:
        response = client.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "KBI" in data["service"]


# ---------------------------------------------------------------------------
# 설비 API
# ---------------------------------------------------------------------------


class TestEquipmentRoutes:
    def test_설비_목록_200(self) -> None:
        response = client.get("/api/equipment")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        # 16대 전체 설비 반환
        assert len(data) == 16

    def test_설비_목록_필드_검증(self) -> None:
        response = client.get("/api/equipment")
        eq = response.json()[0]
        required_fields = {
            "id",
            "name",
            "process_type",
            "capabilities",
            "capacity_tons_per_month",
            "status",
        }
        assert required_fields.issubset(eq.keys())

    def test_설비_단건_조회_200(self) -> None:
        response = client.get("/api/equipment/CV_1")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "CV_1"
        assert data["name"] == "CV#1"
        assert data["process_type"] == "hv_insulation"

    def test_설비_단건_존재하지_않음_404(self) -> None:
        response = client.get("/api/equipment/NONEXISTENT")
        assert response.status_code == 404

    def test_kbi_실제_설비명_포함(self) -> None:
        """KBI 현장 실제 설비명 포함 여부 확인"""
        response = client.get("/api/equipment")
        names = [eq["name"] for eq in response.json()]
        expected_names = [
            "54B0#1",
            "54B0#2",
            "T8B0",
            "30B0",
            "AL6B0",
            "44B0",
            "CV#1",
            "CV#2",
            "12B0",
            "4B0",
            "T/P#1",
            "T/P#2",
            "A100EXT",
            "B100EXT",
            "A150EXT",
            "A120EXT",
        ]
        for name in expected_names:
            assert name in names, f"설비 '{name}'이 목록에 없습니다"


# ---------------------------------------------------------------------------
# 수주 API
# ---------------------------------------------------------------------------


class TestOrderRoutes:
    def test_수주_목록_200(self) -> None:
        response = client.get("/api/orders")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 5

    def test_수주_목록_필드_검증(self) -> None:
        response = client.get("/api/orders")
        order = response.json()[0]
        required_fields = {
            "id",
            "voltage",
            "product_group",
            "spec",
            "core_count",
            "customer",
            "delivery_date",
            "total_length_m",
            "priority",
        }
        assert required_fields.issubset(order.keys())

    def test_수주_단건_조회_200(self) -> None:
        response = client.get("/api/orders/ORD-2026-0301")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "ORD-2026-0301"
        assert data["customer"] == "한국전력공사"
        assert data["priority"] == "urgent"

    def test_수주_단건_존재하지_않음_404(self) -> None:
        response = client.get("/api/orders/ORD-9999-0000")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# 스케줄 작업 API
# ---------------------------------------------------------------------------


class TestScheduleRoutes:
    def test_스케줄_목록_200(self) -> None:
        response = client.get("/api/schedules/tasks")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 5

    def test_스케줄_목록_시작시간_오름차순(self) -> None:
        """작업 목록은 시작 시간 오름차순 정렬"""
        response = client.get("/api/schedules/tasks")
        tasks = response.json()
        starts = [t["start"] for t in tasks]
        assert starts == sorted(starts)

    def test_스케줄_작업_생성_201(self) -> None:
        payload = {
            "order_id": "ORD-2026-0301",
            "equipment_id": "4B0",
            "product": "TFR-GV",
            "spec": "95SQ",
            "core_count": 1,
            "color": "흑색",
            "start": "2026-04-01T08:00:00",
            "end": "2026-04-01T16:00:00",
            "volume_m": 5760.0,
            "line_speed_m_per_min": 12.0,
            "priority": "normal",
        }
        response = client.post("/api/schedules/tasks", json=payload)
        assert response.status_code == 201
        data = response.json()
        assert data["product"] == "TFR-GV"
        assert data["spec"] == "95SQ"
        assert data["status"] == "planned"
        assert "duration_hours" in data
        assert data["duration_hours"] == 8.0

    def test_스케줄_작업_생성_존재하지_않는_설비_404(self) -> None:
        payload = {
            "order_id": "ORD-2026-0301",
            "equipment_id": "INVALID_EQ",
            "product": "TFR-GV",
            "spec": "95SQ",
            "core_count": 1,
            "color": "흑색",
            "start": "2026-04-01T08:00:00",
            "end": "2026-04-01T16:00:00",
            "volume_m": 5760.0,
            "line_speed_m_per_min": 12.0,
        }
        response = client.post("/api/schedules/tasks", json=payload)
        assert response.status_code == 404

    def test_스케줄_작업_수정_200(self) -> None:
        # 먼저 기존 작업 하나 확인
        tasks_resp = client.get("/api/schedules/tasks")
        task_id = tasks_resp.json()[0]["id"]

        update_payload = {"notes": "수정된 메모"}
        response = client.put(f"/api/schedules/tasks/{task_id}", json=update_payload)
        assert response.status_code == 200
        assert response.json()["notes"] == "수정된 메모"

    def test_스케줄_작업_삭제_204(self) -> None:
        # 새 작업 생성 후 삭제
        payload = {
            "order_id": "ORD-2026-0302",
            "equipment_id": "A100EXT",
            "product": "HFCO",
            "spec": "35SQ",
            "core_count": 3,
            "color": "흑/적/청",
            "start": "2026-04-05T08:00:00",
            "end": "2026-04-05T14:00:00",
            "volume_m": 3960.0,
            "line_speed_m_per_min": 11.0,
        }
        create_resp = client.post("/api/schedules/tasks", json=payload)
        task_id = create_resp.json()["id"]

        delete_resp = client.delete(f"/api/schedules/tasks/{task_id}")
        assert delete_resp.status_code == 204

        # 삭제 후 수정 시도 → 404
        put_resp = client.put(
            f"/api/schedules/tasks/{task_id}", json={"notes": "없는 작업"}
        )
        assert put_resp.status_code == 404

    def test_스케줄_작업_삭제_없는_작업_404(self) -> None:
        response = client.delete("/api/schedules/tasks/NONEXISTENT-TASK")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# 제약 조건 검증 API
# ---------------------------------------------------------------------------


class TestConstraintRoutes:
    def test_단건_검증_200(self) -> None:
        response = client.post(
            "/api/constraints/validate",
            json={"task_id": "TASK-001", "validate_all": False},
        )
        assert response.status_code == 200
        data = response.json()
        assert "violations" in data
        assert "is_valid" in data
        assert "error_count" in data
        assert "warning_count" in data

    def test_전체_검증_200(self) -> None:
        response = client.post(
            "/api/constraints/validate",
            json={"task_id": "TASK-001", "validate_all": True},
        )
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data["violations"], list)
        assert data["task_id"] is None  # 전체 검증 시 task_id는 None

    def test_존재하지_않는_작업_검증_404(self) -> None:
        response = client.post(
            "/api/constraints/validate",
            json={"task_id": "TASK-NONEXISTENT", "validate_all": False},
        )
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# 공정 경로 API
# ---------------------------------------------------------------------------


class TestProcessRouteRoutes:
    def test_공정_경로_목록_200(self) -> None:
        response = client.get("/api/process-routes")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 6  # 6개 공정 경로

    def test_공정_경로_필드_검증(self) -> None:
        response = client.get("/api/process-routes")
        route = response.json()[0]
        assert "id" in route
        assert "voltage" in route
        assert "steps" in route
        assert isinstance(route["steps"], list)
        assert len(route["steps"]) > 0

    def test_hv_22kv_경로_포함(self) -> None:
        """22.9kV 고압 경로 포함 확인"""
        response = client.get("/api/process-routes")
        voltages = [r["voltage"] for r in response.json()]
        assert "22.9kV" in voltages

    def test_선속도_목록_200(self) -> None:
        response = client.get("/api/line-speeds")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 12  # 16SQ ~ 400SQ 12개 규격

    def test_선속도_필드_검증(self) -> None:
        response = client.get("/api/line-speeds")
        speed = response.json()[0]
        assert "spec" in speed
        assert "speeds" in speed
        assert isinstance(speed["speeds"], dict)
        # 모든 규격에 insulation, jacketing 속도 존재
        assert "insulation" in speed["speeds"]
        assert "jacketing" in speed["speeds"]


# ---------------------------------------------------------------------------
# batch_group 미배정 (Task 2.3) — POST /api/pipeline/batch-group/{bg}/unassign
# ---------------------------------------------------------------------------


class TestBatchGroupUnassignRoutes:
    """POST /api/pipeline/batch-group/{bg}/unassign 단일 트랜잭션 검증.

    Why DI override:
        conftest의 db 픽스처는 teardown에서 rollback하여 실 DB 오염을 막는다.
        라우트가 get_db로 새 세션을 열면 (1) seed된 flush 전용 데이터가 안 보이고,
        (2) 라우트의 db.commit()이 실 DB에 영구 반영되어 rollback이 무의미해진다.
        → app.dependency_overrides[get_db]로 테스트 세션을 주입하고,
          db.commit을 db.flush로 치환하여 conftest rollback 범위 안에서 검증.
    """

    @pytest.fixture(autouse=True)
    def _override_db(self, db):
        """라우트가 쓸 세션을 테스트의 db 픽스처로 강제. commit은 flush로 치환."""
        from app.infrastructure.database import get_db

        # commit을 flush로 치환 — 실 DB 영구반영 차단, 세션 내 가시성은 유지
        db.commit = db.flush  # type: ignore[method-assign]

        def _override():
            yield db

        app.dependency_overrides[get_db] = _override
        try:
            yield
        finally:
            app.dependency_overrides.pop(get_db, None)

    def test_unassign_with_reason_success(self, db):
        from tests.test_batch_group_lifecycle import _seed_planned_group

        _seed_planned_group(db, "route-g1")
        res = client.post(
            "/api/pipeline/batch-group/route-g1/unassign",
            json={"reason": "자재지연"},
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["batch_group"] == "route-g1"
        assert body["reason"] == "자재지연"
        assert body["idempotent"] is False
        assert len(body["affected_batches"]) == 3
        assert len(body["affected_tasks"]) == 3

    def test_unassign_no_body_uses_default_reason(self, db):
        from tests.test_batch_group_lifecycle import _seed_planned_group

        _seed_planned_group(db, "route-g-default")
        res = client.post("/api/pipeline/batch-group/route-g-default/unassign")
        assert res.status_code == 200, res.text
        assert res.json()["reason"] == "기타"

    def test_unassign_invalid_reason_returns_400(self, db):
        from tests.test_batch_group_lifecycle import _seed_planned_group

        _seed_planned_group(db, "route-g-bad")
        res = client.post(
            "/api/pipeline/batch-group/route-g-bad/unassign",
            json={"reason": "해킹시도"},
        )
        assert res.status_code == 400

    def test_unassign_in_progress_returns_400(self, db):
        from app.infrastructure.models.production_batch import ProductionBatch
        from tests.test_batch_group_lifecycle import _seed_planned_group

        _seed_planned_group(db, "route-g2")
        b = (
            db.query(ProductionBatch)
            .filter(ProductionBatch.batch_group == "route-g2")
            .first()
        )
        b.status = "in_progress"
        db.flush()

        res = client.post(
            "/api/pipeline/batch-group/route-g2/unassign",
            json={"reason": "자재지연"},
        )
        assert res.status_code == 400

    def test_unassign_wip_matched_returns_400(self, db):
        from tests.test_batch_group_lifecycle import _seed_planned_group

        _seed_planned_group(db, "route-g-wip", with_wip=True)
        res = client.post(
            "/api/pipeline/batch-group/route-g-wip/unassign",
            json={"reason": "자재지연"},
        )
        assert res.status_code == 400
        assert "WIP" in res.json()["detail"]

    def test_unassign_not_found_returns_404(self, db):
        res = client.post(
            "/api/pipeline/batch-group/doesnotexist-xyz/unassign",
            json={"reason": "자재지연"},
        )
        assert res.status_code == 404


class TestBatchGroupRestoreRoutes:
    """POST /api/pipeline/batch-group/{bg}/restore 단일 트랜잭션 검증.

    DI override 이유는 TestBatchGroupUnassignRoutes와 동일 — conftest rollback이
    실 DB 오염을 막을 수 있도록 테스트 세션을 주입하고 commit을 flush로 치환한다.
    """

    @pytest.fixture(autouse=True)
    def _override_db(self, db):
        from app.infrastructure.database import get_db

        db.commit = db.flush  # type: ignore[method-assign]

        def _override():
            yield db

        app.dependency_overrides[get_db] = _override
        try:
            yield
        finally:
            app.dependency_overrides.pop(get_db, None)

    def test_restore_success(self, db):
        from tests.test_batch_group_lifecycle import _seed_planned_group

        _seed_planned_group(db, "rr1")
        unassign_res = client.post(
            "/api/pipeline/batch-group/rr1/unassign",
            json={"reason": "자재지연"},
        )
        assert unassign_res.status_code == 200, unassign_res.text

        res = client.post("/api/pipeline/batch-group/rr1/restore")
        assert res.status_code == 200, res.text
        body = res.json()
        assert len(body["restored_tasks"]) == 3
        assert body["conflicts"] == []
        assert body["idempotent"] is False

    def test_restore_conflict_returns_409(self, db):
        from app.infrastructure.models.schedule_task import ScheduleTask
        from tests.test_batch_group_lifecycle import _seed_planned_group

        _seed_planned_group(db, "rr2")
        unassign_res = client.post(
            "/api/pipeline/batch-group/rr2/unassign",
            json={"reason": "자재지연"},
        )
        assert unassign_res.status_code == 200, unassign_res.text

        # 다른 group의 task를 rr2의 첫 task 슬롯으로 이동시켜 충돌 유도
        _seed_planned_group(db, "rr3")
        rr2_first_task = (
            db.query(ScheduleTask)
            .filter(ScheduleTask.batch_group == "rr2")
            .order_by(ScheduleTask.task_id)
            .first()
        )
        rr3_task = (
            db.query(ScheduleTask).filter(ScheduleTask.batch_group == "rr3").first()
        )
        rr3_task.equipment_code = rr2_first_task.equipment_code
        rr3_task.start_datetime = rr2_first_task.start_datetime
        rr3_task.end_datetime = rr2_first_task.end_datetime
        db.flush()

        res = client.post("/api/pipeline/batch-group/rr2/restore")
        assert res.status_code == 409, res.text
        detail = res.json().get("detail")
        assert isinstance(detail, dict) and "conflicts" in detail
        assert len(detail["conflicts"]) >= 1

    def test_restore_not_found_returns_404(self, db):
        res = client.post("/api/pipeline/batch-group/nonexistent-rr/restore")
        assert res.status_code == 404


class TestBatchGroupSnapshotsRoute:
    """GET /api/pipeline/batch-group-snapshots — unassigned 목록 + 사유."""

    @pytest.fixture(autouse=True)
    def _override_db(self, db):
        from app.infrastructure.database import get_db

        db.commit = db.flush  # type: ignore[method-assign]

        def _override():
            yield db

        app.dependency_overrides[get_db] = _override
        try:
            yield
        finally:
            app.dependency_overrides.pop(get_db, None)

    def test_list_unassigned_snapshots_includes_reason(self, db):
        from tests.test_batch_group_lifecycle import _seed_planned_group

        _seed_planned_group(db, "snap-s1")
        _seed_planned_group(db, "snap-s2")
        # s1만 unassign
        unassign_res = client.post(
            "/api/pipeline/batch-group/snap-s1/unassign",
            json={"reason": "설비고장"},
        )
        assert unassign_res.status_code == 200, unassign_res.text

        res = client.get("/api/pipeline/batch-group-snapshots")
        assert res.status_code == 200, res.text
        groups = res.json()["groups"]
        snap_s1 = next((g for g in groups if g["batch_group"] == "snap-s1"), None)
        assert snap_s1 is not None, "snap-s1 not in response"
        assert snap_s1["unassign_reason"] == "설비고장"
        assert snap_s1["order_count"] == 3  # 3 batches (연선/절연/시스)
        assert len(snap_s1["processes"]) == 3

        # s2는 planned 상태이므로 응답에 없어야 함
        snap_s2 = next((g for g in groups if g["batch_group"] == "snap-s2"), None)
        assert snap_s2 is None


class TestBatchGroupRestoreAtRoutes:
    """POST /api/pipeline/batch-group/{bg}/restore-at — anchor 기준 재배치 preview."""

    @pytest.fixture(autouse=True)
    def _override_db(self, db):
        from app.infrastructure.database import get_db

        db.commit = db.flush  # type: ignore[method-assign]

        def _override():
            yield db

        app.dependency_overrides[get_db] = _override
        try:
            yield
        finally:
            app.dependency_overrides.pop(get_db, None)

    def test_restore_at_success(self, db):
        from tests.test_batch_group_lifecycle import _seed_planned_group

        _seed_planned_group(db, "ra-1")
        client.post(
            "/api/pipeline/batch-group/ra-1/unassign",
            json={"reason": "자재지연"},
        )
        res = client.post(
            "/api/pipeline/batch-group/ra-1/restore-at",
            json={
                "anchor_equipment_code": "DS-C11D",
                "anchor_start": "2026-04-25T09:00:00",
            },
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["batch_group"] == "ra-1"
        assert len(body["task_positions"]) == 3
        assert body["task_positions"][0]["is_anchor"] is True

    def test_restore_at_status_error_returns_400(self, db):
        from tests.test_batch_group_lifecycle import _seed_planned_group

        # planned 상태(미unassign) 그룹에 restore_at 시도 → BatchGroupStatusError → 400
        _seed_planned_group(db, "ra-2")
        res = client.post(
            "/api/pipeline/batch-group/ra-2/restore-at",
            json={
                "anchor_equipment_code": "DS-C11D",
                "anchor_start": "2026-04-25T09:00:00",
            },
        )
        assert res.status_code == 400

    def test_restore_at_not_found_returns_404(self):
        res = client.post(
            "/api/pipeline/batch-group/nonexistent-ra/restore-at",
            json={
                "anchor_equipment_code": "DS-C11D",
                "anchor_start": "2026-04-25T09:00:00",
            },
        )
        assert res.status_code == 404

    def test_restore_at_bad_body_returns_422(self, db):
        from tests.test_batch_group_lifecycle import _seed_planned_group

        _seed_planned_group(db, "ra-3")
        client.post(
            "/api/pipeline/batch-group/ra-3/unassign",
            json={"reason": "자재지연"},
        )
        # anchor_start 누락 → Pydantic 422
        res = client.post(
            "/api/pipeline/batch-group/ra-3/restore-at",
            json={"anchor_equipment_code": "DS-C11D"},
        )
        assert res.status_code == 422
