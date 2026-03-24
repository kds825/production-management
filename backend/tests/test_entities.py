"""
도메인 엔티티 단위 테스트
실제 KBI 현장 시나리오 기반 검증
"""

from datetime import datetime

import pytest

from app.domain.entities import (
    Equipment,
    ProcessType,
    ScheduleTask,
    TaskPriority,
    TaskStatus,
)


# ---------------------------------------------------------------------------
# 테스트 픽스처
# ---------------------------------------------------------------------------


@pytest.fixture
def cv_insulator() -> Equipment:
    """고압 절연기 CV#1 — HV 절연 공정 담당"""
    return Equipment(
        id="CV_1",
        name="CV#1",
        process_type=ProcessType.HV_INSULATION,
        capabilities=["95SQ", "150SQ", "185SQ", "240SQ"],
        capacity_tons_per_month=30.0,
        max_diameter_mm=30.0,
        status="available",
    )


@pytest.fixture
def lv_extruder() -> Equipment:
    """저압 피복 압출기 A100EXT — LV 피복 공정 담당"""
    return Equipment(
        id="A100EXT",
        name="A100EXT",
        process_type=ProcessType.LV_JACKETING,
        capabilities=["16SQ", "25SQ", "35SQ", "50SQ", "70SQ", "95SQ"],
        capacity_tons_per_month=55.0,
        max_diameter_mm=30.0,
        status="available",
    )


@pytest.fixture
def base_task() -> ScheduleTask:
    """기본 스케줄 작업 (TFR-GV 95SQ, 8시간)"""
    return ScheduleTask(
        id="TASK-TEST-001",
        order_id="ORD-TEST",
        equipment_id="4B0",
        product="TFR-GV",
        spec="95SQ",
        core_count=1,
        color="흑색",
        start=datetime(2026, 3, 25, 8, 0),
        end=datetime(2026, 3, 25, 16, 0),
        volume_m=5760.0,
        line_speed_m_per_min=12.0,
        priority=TaskPriority.NORMAL,
        status=TaskStatus.PLANNED,
    )


# ---------------------------------------------------------------------------
# Equipment.supports_spec 테스트
# ---------------------------------------------------------------------------


class TestEquipmentSupportsSpec:
    def test_지원_규격_반환_true(self, cv_insulator: Equipment) -> None:
        """CV#1이 지원하는 규격은 True 반환"""
        assert cv_insulator.supports_spec("95SQ") is True
        assert cv_insulator.supports_spec("150SQ") is True
        assert cv_insulator.supports_spec("240SQ") is True

    def test_미지원_규격_반환_false(self, cv_insulator: Equipment) -> None:
        """CV#1이 지원하지 않는 소단면적 규격은 False 반환"""
        assert cv_insulator.supports_spec("1.5SQ") is False
        assert cv_insulator.supports_spec("10SQ") is False
        assert cv_insulator.supports_spec("300SQ") is False

    def test_빈_규격_문자열_반환_false(self, lv_extruder: Equipment) -> None:
        """빈 문자열 규격 검색 시 False 반환 (방어적 처리)"""
        assert lv_extruder.supports_spec("") is False

    def test_대소문자_구분_정확히_매칭(self, lv_extruder: Equipment) -> None:
        """규격명은 대소문자 구분하여 정확히 일치해야 함"""
        assert lv_extruder.supports_spec("95sq") is False
        assert lv_extruder.supports_spec("95SQ") is True

    def test_lv_extruder_중간_규격_지원(self, lv_extruder: Equipment) -> None:
        """A100EXT는 16SQ~95SQ 범위 지원"""
        for spec in ["16SQ", "25SQ", "35SQ", "50SQ", "70SQ", "95SQ"]:
            assert lv_extruder.supports_spec(spec) is True

    def test_설비_없는_규격_지원_안함(self, lv_extruder: Equipment) -> None:
        """A100EXT는 120SQ 이상 대단면적 미지원"""
        for spec in ["120SQ", "150SQ", "240SQ"]:
            assert lv_extruder.supports_spec(spec) is False


# ---------------------------------------------------------------------------
# ScheduleTask.duration_hours 테스트
# ---------------------------------------------------------------------------


class TestScheduleTaskDurationHours:
    def test_정확한_8시간_계산(self, base_task: ScheduleTask) -> None:
        """08:00 ~ 16:00 = 정확히 8시간"""
        assert base_task.duration_hours == 8.0

    def test_분_단위_포함_소수점_계산(self) -> None:
        """30분 포함 시 0.5시간으로 환산"""
        task = ScheduleTask(
            id="T",
            order_id="O",
            equipment_id="E",
            product="CV",
            spec="150SQ",
            core_count=1,
            color="흑색",
            start=datetime(2026, 3, 25, 8, 0),
            end=datetime(2026, 3, 25, 16, 30),
            volume_m=3000.0,
            line_speed_m_per_min=6.0,
        )
        assert task.duration_hours == 8.5

    def test_야간_작업_24시간_초과(self) -> None:
        """24시간 이상 장기 작업도 정확히 계산"""
        task = ScheduleTask(
            id="T",
            order_id="O",
            equipment_id="E",
            product="TFR-GV",
            spec="25SQ",
            core_count=4,
            color="흑/적/청/녹황",
            start=datetime(2026, 3, 25, 8, 0),
            end=datetime(2026, 3, 27, 8, 0),
            volume_m=17280.0,
            line_speed_m_per_min=6.0,
        )
        assert task.duration_hours == 48.0

    def test_선속도_기반_소요시간_일치(self) -> None:
        """volume_m / line_speed = duration 검증 (실제 생산 계획 정합성)"""
        volume_m = 3300.0
        speed_m_per_min = 11.0
        expected_hours = (volume_m / speed_m_per_min) / 60  # 5시간

        task = ScheduleTask(
            id="T",
            order_id="O",
            equipment_id="A100EXT",
            product="HFCO",
            spec="35SQ",
            core_count=3,
            color="흑/적/청",
            start=datetime(2026, 3, 26, 8, 0),
            end=datetime(2026, 3, 26, 13, 0),
            volume_m=volume_m,
            line_speed_m_per_min=speed_m_per_min,
        )
        assert task.duration_hours == pytest.approx(expected_hours, rel=1e-3)


# ---------------------------------------------------------------------------
# ScheduleTask.overlaps 테스트
# ---------------------------------------------------------------------------


class TestScheduleTaskOverlaps:
    def test_완전_겹침(self, base_task: ScheduleTask) -> None:
        """동일 시간대 완전 겹침"""
        other = ScheduleTask(
            id="TASK-TEST-002",
            order_id="ORD-002",
            equipment_id="4B0",
            product="HFCO",
            spec="95SQ",
            core_count=1,
            color="회색",
            start=datetime(2026, 3, 25, 8, 0),
            end=datetime(2026, 3, 25, 16, 0),
            volume_m=5760.0,
            line_speed_m_per_min=12.0,
        )
        assert base_task.overlaps(other) is True

    def test_부분_겹침_앞부분(self, base_task: ScheduleTask) -> None:
        """다른 작업이 앞쪽에서 일부 겹침 (06:00~10:00 vs 08:00~16:00)"""
        other = ScheduleTask(
            id="TASK-TEST-003",
            order_id="ORD-003",
            equipment_id="4B0",
            product="TFR-CV",
            spec="70SQ",
            core_count=1,
            color="흑색",
            start=datetime(2026, 3, 25, 6, 0),
            end=datetime(2026, 3, 25, 10, 0),
            volume_m=2880.0,
            line_speed_m_per_min=12.0,
        )
        assert base_task.overlaps(other) is True

    def test_부분_겹침_뒷부분(self, base_task: ScheduleTask) -> None:
        """다른 작업이 뒤쪽에서 일부 겹침 (14:00~20:00 vs 08:00~16:00)"""
        other = ScheduleTask(
            id="TASK-TEST-004",
            order_id="ORD-004",
            equipment_id="4B0",
            product="CV",
            spec="95SQ",
            core_count=1,
            color="흑색",
            start=datetime(2026, 3, 25, 14, 0),
            end=datetime(2026, 3, 25, 20, 0),
            volume_m=4320.0,
            line_speed_m_per_min=12.0,
        )
        assert base_task.overlaps(other) is True

    def test_완전_포함_내부(self, base_task: ScheduleTask) -> None:
        """다른 작업이 내부에 완전히 포함됨 (10:00~12:00 in 08:00~16:00)"""
        other = ScheduleTask(
            id="TASK-TEST-005",
            order_id="ORD-005",
            equipment_id="4B0",
            product="TFR-GV",
            spec="95SQ",
            core_count=1,
            color="흑색",
            start=datetime(2026, 3, 25, 10, 0),
            end=datetime(2026, 3, 25, 12, 0),
            volume_m=1440.0,
            line_speed_m_per_min=12.0,
        )
        assert base_task.overlaps(other) is True

    def test_겹침_없음_연속(self, base_task: ScheduleTask) -> None:
        """연속 배치 (16:00~24:00) — 겹침 없음"""
        other = ScheduleTask(
            id="TASK-TEST-006",
            order_id="ORD-006",
            equipment_id="4B0",
            product="TFR-GV",
            spec="95SQ",
            core_count=1,
            color="흑색",
            start=datetime(2026, 3, 25, 16, 0),
            end=datetime(2026, 3, 26, 0, 0),
            volume_m=5760.0,
            line_speed_m_per_min=12.0,
        )
        assert base_task.overlaps(other) is False

    def test_겹침_없음_이전(self, base_task: ScheduleTask) -> None:
        """완전 이전 작업 (00:00~08:00) — 겹침 없음"""
        other = ScheduleTask(
            id="TASK-TEST-007",
            order_id="ORD-007",
            equipment_id="4B0",
            product="TFR-GV",
            spec="95SQ",
            core_count=1,
            color="흑색",
            start=datetime(2026, 3, 25, 0, 0),
            end=datetime(2026, 3, 25, 8, 0),
            volume_m=5760.0,
            line_speed_m_per_min=12.0,
        )
        assert base_task.overlaps(other) is False

    def test_다른_날짜_겹침_없음(self, base_task: ScheduleTask) -> None:
        """다음 날 동일 시간대 — 다른 날이므로 겹침 없음"""
        other = ScheduleTask(
            id="TASK-TEST-008",
            order_id="ORD-008",
            equipment_id="4B0",
            product="TFR-GV",
            spec="95SQ",
            core_count=1,
            color="흑색",
            start=datetime(2026, 3, 26, 8, 0),
            end=datetime(2026, 3, 26, 16, 0),
            volume_m=5760.0,
            line_speed_m_per_min=12.0,
        )
        assert base_task.overlaps(other) is False
