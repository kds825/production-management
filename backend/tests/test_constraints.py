"""
제약 조건 단위 테스트 — 5가지 제약 규칙 KBI 실제 시나리오 기반 검증
"""

from datetime import datetime

import pytest

from app.domain.constraints import (
    check_delivery_date,
    check_equipment_capability,
    check_overlap,
    check_precedence,
    check_process_route,
    validate_task,
)
from app.domain.entities import (
    Equipment,
    ProcessRoute,
    ProcessStep,
    ProcessType,
    ScheduleTask,
    TaskPriority,
    TaskStatus,
)


# ---------------------------------------------------------------------------
# 공통 픽스처
# ---------------------------------------------------------------------------


def make_task(
    task_id: str,
    equipment_id: str,
    spec: str,
    start: datetime,
    end: datetime,
    product: str = "TFR-GV",
    predecessors: list[str] | None = None,
    delivery_date: datetime | None = None,
    process_step: int | None = None,
) -> ScheduleTask:
    return ScheduleTask(
        id=task_id,
        order_id="ORD-TEST",
        equipment_id=equipment_id,
        product=product,
        spec=spec,
        core_count=1,
        color="흑색",
        start=start,
        end=end,
        volume_m=1000.0,
        line_speed_m_per_min=10.0,
        priority=TaskPriority.NORMAL,
        status=TaskStatus.PLANNED,
        predecessors=predecessors or [],
        delivery_date=delivery_date,
        process_step=process_step,
    )


@pytest.fixture
def lv_insulator_4b0() -> Equipment:
    """4B0 저압 절연기 — 1.5~70SQ 지원"""
    return Equipment(
        id="4B0",
        name="4B0",
        process_type=ProcessType.LV_INSULATION,
        capabilities=[
            "1.5SQ",
            "2.5SQ",
            "4SQ",
            "6SQ",
            "10SQ",
            "16SQ",
            "25SQ",
            "35SQ",
            "50SQ",
            "70SQ",
        ],
        capacity_tons_per_month=50.0,
        max_diameter_mm=15.0,
    )


@pytest.fixture
def cv_insulator() -> Equipment:
    """CV#1 고압 절연기 — 16~240SQ 지원"""
    return Equipment(
        id="CV_1",
        name="CV#1",
        process_type=ProcessType.HV_INSULATION,
        capabilities=[
            "16SQ",
            "25SQ",
            "35SQ",
            "50SQ",
            "70SQ",
            "95SQ",
            "120SQ",
            "150SQ",
            "185SQ",
            "240SQ",
        ],
        capacity_tons_per_month=30.0,
        max_diameter_mm=30.0,
    )


@pytest.fixture
def hv_extruder() -> Equipment:
    """A120EXT 고압 피복기 — 25~400SQ 지원"""
    return Equipment(
        id="A120EXT",
        name="A120EXT",
        process_type=ProcessType.HV_JACKETING,
        capabilities=[
            "25SQ",
            "35SQ",
            "50SQ",
            "70SQ",
            "95SQ",
            "120SQ",
            "150SQ",
            "185SQ",
            "240SQ",
            "300SQ",
            "400SQ",
        ],
        capacity_tons_per_month=45.0,
        max_diameter_mm=50.0,
    )


@pytest.fixture
def sample_route() -> ProcessRoute:
    """LV 0.6/1kV 단심 공정 경로 (테스트용)"""
    return ProcessRoute(
        id="route_lv_1c",
        voltage="0.6/1kV",
        core_count_range="1",
        description="LV 단심 테스트 경로",
        steps=[
            ProcessStep(
                order=1, process_type=ProcessType.DRAWING, equipment_ids=["54B0_1"]
            ),
            ProcessStep(
                order=2, process_type=ProcessType.STRANDING, equipment_ids=["44B0"]
            ),
            ProcessStep(
                order=3,
                process_type=ProcessType.LV_INSULATION,
                equipment_ids=["12B0", "4B0"],
            ),
            ProcessStep(
                order=4,
                process_type=ProcessType.LV_JACKETING,
                equipment_ids=["A100EXT", "B100EXT"],
            ),
        ],
    )


# ---------------------------------------------------------------------------
# 1. check_overlap 테스트
# ---------------------------------------------------------------------------


class TestCheckOverlap:
    def test_동일_설비_시간_겹침_에러(self) -> None:
        """CV#1에 동시간대 작업 2개 배정 시 overlap 에러"""
        task_a = make_task(
            "T-A",
            "CV_1",
            "150SQ",
            datetime(2026, 3, 25, 8, 0),
            datetime(2026, 3, 25, 14, 0),
        )
        task_b = make_task(
            "T-B",
            "CV_1",
            "95SQ",
            datetime(2026, 3, 25, 12, 0),
            datetime(2026, 3, 25, 18, 0),
        )
        violations = check_overlap(task_a, [task_a, task_b])
        assert len(violations) == 1
        assert violations[0].type == "overlap"
        assert violations[0].severity == "error"
        assert violations[0].related_task_id == "T-B"

    def test_다른_설비_시간_겹쳐도_위반_없음(self) -> None:
        """다른 설비(CV_1 vs CV_2)는 시간이 겹쳐도 위반 없음"""
        task_a = make_task(
            "T-A",
            "CV_1",
            "150SQ",
            datetime(2026, 3, 25, 8, 0),
            datetime(2026, 3, 25, 16, 0),
        )
        task_b = make_task(
            "T-B",
            "CV_2",
            "150SQ",
            datetime(2026, 3, 25, 8, 0),
            datetime(2026, 3, 25, 16, 0),
        )
        violations = check_overlap(task_a, [task_a, task_b])
        assert violations == []

    def test_동일_설비_연속_작업_위반_없음(self) -> None:
        """연속으로 배치된 작업(끝 = 다음 시작)은 겹침 없음"""
        task_a = make_task(
            "T-A",
            "4B0",
            "35SQ",
            datetime(2026, 3, 25, 8, 0),
            datetime(2026, 3, 25, 14, 0),
        )
        task_b = make_task(
            "T-B",
            "4B0",
            "35SQ",
            datetime(2026, 3, 25, 14, 0),
            datetime(2026, 3, 25, 20, 0),
        )
        violations = check_overlap(task_a, [task_a, task_b])
        assert violations == []

    def test_자기_자신과는_겹침_미검사(self) -> None:
        """동일 task_id 자기 자신과의 비교는 스킵"""
        task = make_task(
            "T-SELF",
            "CV_1",
            "150SQ",
            datetime(2026, 3, 25, 8, 0),
            datetime(2026, 3, 25, 16, 0),
        )
        violations = check_overlap(task, [task])
        assert violations == []


# ---------------------------------------------------------------------------
# 2. check_equipment_capability 테스트
# ---------------------------------------------------------------------------


class TestCheckEquipmentCapability:
    def test_지원_규격_위반_없음(self, lv_insulator_4b0: Equipment) -> None:
        """4B0이 지원하는 35SQ 배정 — 위반 없음"""
        task = make_task(
            "T",
            "4B0",
            "35SQ",
            datetime(2026, 3, 25, 8, 0),
            datetime(2026, 3, 25, 16, 0),
        )
        assert check_equipment_capability(task, lv_insulator_4b0) == []

    def test_미지원_대단면적_에러(self, lv_insulator_4b0: Equipment) -> None:
        """4B0은 95SQ 이상 대단면적 미지원 — 에러"""
        task = make_task(
            "T",
            "4B0",
            "95SQ",
            datetime(2026, 3, 25, 8, 0),
            datetime(2026, 3, 25, 16, 0),
        )
        violations = check_equipment_capability(task, lv_insulator_4b0)
        assert len(violations) == 1
        assert violations[0].type == "equipment_capability"
        assert violations[0].severity == "error"
        assert "4B0" in violations[0].message
        assert "95SQ" in violations[0].message

    def test_고압_절연기에_저압_규격_배정_에러(self, cv_insulator: Equipment) -> None:
        """CV#1 고압 절연기에 1.5SQ 초소단면 배정 — 에러"""
        task = make_task(
            "T",
            "CV_1",
            "1.5SQ",
            datetime(2026, 3, 25, 8, 0),
            datetime(2026, 3, 25, 16, 0),
        )
        violations = check_equipment_capability(task, cv_insulator)
        assert len(violations) == 1
        assert "CV#1" in violations[0].message
        assert "1.5SQ" in violations[0].message

    def test_고압_피복기_지원_규격_정상(self, hv_extruder: Equipment) -> None:
        """A120EXT에 240SQ 고압 케이블 배정 — 정상"""
        task = make_task(
            "T",
            "A120EXT",
            "240SQ",
            datetime(2026, 3, 25, 8, 0),
            datetime(2026, 3, 25, 16, 0),
        )
        assert check_equipment_capability(task, hv_extruder) == []


# ---------------------------------------------------------------------------
# 3. check_delivery_date 테스트
# ---------------------------------------------------------------------------


class TestCheckDeliveryDate:
    def test_납기_이전_완료_위반_없음(self) -> None:
        """작업 완료가 납기 전 — 위반 없음"""
        task = make_task(
            "T",
            "4B0",
            "35SQ",
            start=datetime(2026, 3, 25, 8, 0),
            end=datetime(2026, 3, 25, 16, 0),
            delivery_date=datetime(2026, 4, 15),
        )
        assert check_delivery_date(task) == []

    def test_납기_초과_경고(self) -> None:
        """작업 완료가 납기 후 — 경고"""
        task = make_task(
            "T",
            "4B0",
            "35SQ",
            start=datetime(2026, 4, 16, 8, 0),
            end=datetime(2026, 4, 16, 16, 0),
            delivery_date=datetime(2026, 4, 15),
        )
        violations = check_delivery_date(task)
        assert len(violations) == 1
        assert violations[0].type == "delivery"
        assert violations[0].severity == "warning"  # 에러가 아닌 경고
        assert "2026-04-15" in violations[0].message

    def test_납기일_없으면_위반_없음(self) -> None:
        """납기일 미설정 작업 — 검사 생략"""
        task = make_task(
            "T",
            "4B0",
            "35SQ",
            start=datetime(2026, 3, 25, 8, 0),
            end=datetime(2026, 3, 25, 16, 0),
            delivery_date=None,
        )
        assert check_delivery_date(task) == []

    def test_납기_당일_완료_위반_없음(self) -> None:
        """납기 당일 완료 (end == delivery_date 경계값)"""
        delivery = datetime(2026, 4, 15, 23, 59)
        task = make_task(
            "T",
            "4B0",
            "35SQ",
            start=datetime(2026, 4, 15, 8, 0),
            end=delivery,
            delivery_date=delivery,
        )
        assert check_delivery_date(task) == []


# ---------------------------------------------------------------------------
# 4. check_precedence 테스트
# ---------------------------------------------------------------------------


class TestCheckPrecedence:
    def test_선행_공정_완료_후_시작_정상(self) -> None:
        """선행 공정이 완료된 후 시작 — 정상"""
        pred = make_task(
            "PRED",
            "44B0",
            "95SQ",
            datetime(2026, 3, 25, 6, 0),
            datetime(2026, 3, 25, 12, 0),
        )
        task = make_task(
            "T",
            "4B0",
            "95SQ",
            datetime(2026, 3, 25, 13, 0),
            datetime(2026, 3, 25, 20, 0),
            predecessors=["PRED"],
        )
        assert check_precedence(task, [pred, task]) == []

    def test_선행_공정_미완료_에러(self) -> None:
        """선행 공정이 아직 진행 중 — 에러"""
        pred = make_task(
            "PRED",
            "44B0",
            "95SQ",
            datetime(2026, 3, 25, 8, 0),
            datetime(2026, 3, 25, 18, 0),
        )
        task = make_task(
            "T",
            "4B0",
            "95SQ",
            datetime(2026, 3, 25, 14, 0),
            datetime(2026, 3, 25, 22, 0),
            predecessors=["PRED"],
        )
        violations = check_precedence(task, [pred, task])
        assert len(violations) == 1
        assert violations[0].type == "precedence"
        assert violations[0].severity == "error"
        assert violations[0].related_task_id == "PRED"

    def test_선행_공정_없으면_위반_없음(self) -> None:
        """선행 공정 목록 비어있음 — 검사 생략"""
        task = make_task(
            "T",
            "4B0",
            "35SQ",
            datetime(2026, 3, 25, 8, 0),
            datetime(2026, 3, 25, 16, 0),
        )
        assert check_precedence(task, [task]) == []

    def test_선행_공정_id_없으면_위반_없음(self) -> None:
        """선행 작업 ID가 존재하지 않으면 검사 건너뜀"""
        task = make_task(
            "T",
            "4B0",
            "35SQ",
            datetime(2026, 3, 25, 8, 0),
            datetime(2026, 3, 25, 16, 0),
            predecessors=["NONEXISTENT-ID"],
        )
        # 존재하지 않는 선행 작업은 검증 대상 아님
        assert check_precedence(task, [task]) == []

    def test_다중_선행_공정_모두_완료(self) -> None:
        """다중 선행 공정 모두 완료된 경우 — 정상 (다심 케이블 시나리오)"""
        pred1 = make_task(
            "PRED-1",
            "44B0",
            "35SQ",
            datetime(2026, 3, 25, 6, 0),
            datetime(2026, 3, 25, 10, 0),
        )
        pred2 = make_task(
            "PRED-2",
            "44B0",
            "35SQ",
            datetime(2026, 3, 25, 10, 0),
            datetime(2026, 3, 25, 14, 0),
        )
        task = make_task(
            "T",
            "A100EXT",
            "35SQ",
            datetime(2026, 3, 25, 15, 0),
            datetime(2026, 3, 25, 22, 0),
            predecessors=["PRED-1", "PRED-2"],
        )
        assert check_precedence(task, [pred1, pred2, task]) == []


# ---------------------------------------------------------------------------
# 5. check_process_route 테스트
# ---------------------------------------------------------------------------


class TestCheckProcessRoute:
    def test_올바른_설비_공정_단계_정상(
        self, lv_insulator_4b0: Equipment, sample_route: ProcessRoute
    ) -> None:
        """4B0이 LV 절연 3단계에 배정됨 — 공정 경로 정상"""
        task = make_task(
            "T",
            "4B0",
            "35SQ",
            datetime(2026, 3, 25, 8, 0),
            datetime(2026, 3, 25, 16, 0),
            process_step=3,
        )
        violations = check_process_route(task, lv_insulator_4b0, [sample_route])
        assert violations == []

    def test_잘못된_설비_공정_단계_에러(
        self, hv_extruder: Equipment, sample_route: ProcessRoute
    ) -> None:
        """고압 피복기(A120EXT)가 LV 절연 3단계에 배정됨 — 공정 경로 위반"""
        task = make_task(
            "T",
            "A120EXT",
            "35SQ",
            datetime(2026, 3, 25, 8, 0),
            datetime(2026, 3, 25, 16, 0),
            process_step=3,
        )
        violations = check_process_route(task, hv_extruder, [sample_route])
        assert len(violations) >= 1
        assert violations[0].type == "process_route"
        assert violations[0].severity == "error"

    def test_공정_단계_없으면_검사_생략(
        self, hv_extruder: Equipment, sample_route: ProcessRoute
    ) -> None:
        """process_step=None이면 공정 경로 검사 생략"""
        task = make_task(
            "T",
            "A120EXT",
            "35SQ",
            datetime(2026, 3, 25, 8, 0),
            datetime(2026, 3, 25, 16, 0),
            process_step=None,
        )
        violations = check_process_route(task, hv_extruder, [sample_route])
        assert violations == []


# ---------------------------------------------------------------------------
# 통합 테스트: validate_task
# ---------------------------------------------------------------------------


class TestValidateTask:
    def test_정상_작업_위반_없음(
        self, lv_insulator_4b0: Equipment, sample_route: ProcessRoute
    ) -> None:
        """모든 제약 조건을 만족하는 정상 작업"""
        task = make_task(
            "T-OK",
            "4B0",
            "35SQ",
            start=datetime(2026, 3, 25, 8, 0),
            end=datetime(2026, 3, 25, 16, 0),
            delivery_date=datetime(2026, 4, 20),
            process_step=3,
        )
        violations = validate_task(task, lv_insulator_4b0, [task], [sample_route])
        assert violations == []

    def test_복합_위반_수집(self, lv_insulator_4b0: Equipment) -> None:
        """납기 초과 + 설비 미지원 규격 — 복수 위반 동시 감지"""
        # 납기 초과
        task = make_task(
            "T-BAD",
            "4B0",
            "95SQ",  # 4B0은 95SQ 미지원
            start=datetime(2026, 4, 16, 8, 0),
            end=datetime(2026, 4, 16, 16, 0),
            delivery_date=datetime(2026, 4, 15),  # 납기 초과
        )
        violations = validate_task(task, lv_insulator_4b0, [task])
        types = {v.type for v in violations}
        assert "equipment_capability" in types
        assert "delivery" in types
