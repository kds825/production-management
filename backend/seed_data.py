"""
KBI Cosmolink 실제 생산 현장 데이터 기반 시드 데이터
- 설비 16대 (신선/연선/절연/테이핑/성缆/피복)
- 공정 경로 6종
- 선속도 테이블
- 샘플 수주 5건
"""

from datetime import datetime

from app.domain.entities import (
    Equipment,
    Order,
    ProcessRoute,
    ProcessStep,
    ProcessType,
    ScheduleTask,
    TaskPriority,
    TaskStatus,
)

# ---------------------------------------------------------------------------
# 설비 목록 (16대)
# ---------------------------------------------------------------------------
EQUIPMENT: list[Equipment] = [
    # ── 신선기 (Drawing) ──────────────────────────────────────────────────
    Equipment(
        id="54B0_1",
        name="54B0#1",
        process_type=ProcessType.DRAWING,
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
        ],
        capacity_tons_per_month=40.0,
        max_diameter_mm=9.5,
        status="available",
    ),
    Equipment(
        id="54B0_2",
        name="54B0#2",
        process_type=ProcessType.DRAWING,
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
        ],
        capacity_tons_per_month=40.0,
        max_diameter_mm=9.5,
        status="available",
    ),
    Equipment(
        id="T8B0",
        name="T8B0",
        process_type=ProcessType.DRAWING,
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
            "95SQ",
        ],
        capacity_tons_per_month=50.0,
        max_diameter_mm=12.0,
        status="available",
    ),
    Equipment(
        id="30B0",
        name="30B0",
        process_type=ProcessType.DRAWING,
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
        capacity_tons_per_month=60.0,
        max_diameter_mm=16.0,
        status="available",
    ),
    Equipment(
        id="AL6B0",
        name="AL6B0",
        process_type=ProcessType.DRAWING,
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
            "300SQ",
            "400SQ",
        ],
        capacity_tons_per_month=70.0,
        max_diameter_mm=22.0,
        status="available",
    ),
    # ── 연선기 (Stranding) ────────────────────────────────────────────────
    Equipment(
        id="44B0",
        name="44B0",
        process_type=ProcessType.STRANDING,
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
            "300SQ",
            "400SQ",
        ],
        capacity_tons_per_month=80.0,
        max_diameter_mm=25.0,
        status="available",
    ),
    # ── 고압 절연기 (HV Insulation) ───────────────────────────────────────
    Equipment(
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
        status="available",
    ),
    Equipment(
        id="CV_2",
        name="CV#2",
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
            "300SQ",
            "400SQ",
        ],
        capacity_tons_per_month=35.0,
        max_diameter_mm=35.0,
        status="available",
    ),
    # ── 저압 절연기 (LV Insulation) ───────────────────────────────────────
    Equipment(
        id="12B0",
        name="12B0",
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
        ],
        capacity_tons_per_month=45.0,
        max_diameter_mm=12.0,
        status="available",
    ),
    Equipment(
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
        status="available",
    ),
    # ── 테이핑기 (Taping) ─────────────────────────────────────────────────
    Equipment(
        id="TP_1",
        name="T/P#1",
        process_type=ProcessType.TAPING,
        capabilities=[
            "16SQ",
            "25SQ",
            "35SQ",
            "50SQ",
            "70SQ",
            "95SQ",
            "120SQ",
            "150SQ",
        ],
        capacity_tons_per_month=25.0,
        max_diameter_mm=25.0,
        status="available",
    ),
    Equipment(
        id="TP_2",
        name="T/P#2",
        process_type=ProcessType.TAPING,
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
        ],
        capacity_tons_per_month=30.0,
        max_diameter_mm=30.0,
        status="available",
    ),
    # ── 저압 피복 압출기 (LV Jacketing) ──────────────────────────────────
    Equipment(
        id="A100EXT",
        name="A100EXT",
        process_type=ProcessType.LV_JACKETING,
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
            "95SQ",
        ],
        capacity_tons_per_month=55.0,
        max_diameter_mm=30.0,
        status="available",
    ),
    Equipment(
        id="B100EXT",
        name="B100EXT",
        process_type=ProcessType.LV_JACKETING,
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
            "95SQ",
            "120SQ",
        ],
        capacity_tons_per_month=60.0,
        max_diameter_mm=35.0,
        status="available",
    ),
    # ── 고압 피복 압출기 (HV Jacketing) ──────────────────────────────────
    Equipment(
        id="A150EXT",
        name="A150EXT",
        process_type=ProcessType.HV_JACKETING,
        capabilities=[
            "50SQ",
            "70SQ",
            "95SQ",
            "120SQ",
            "150SQ",
            "185SQ",
            "240SQ",
            "300SQ",
        ],
        capacity_tons_per_month=40.0,
        max_diameter_mm=45.0,
        status="available",
    ),
    Equipment(
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
        status="available",
    ),
]

# ---------------------------------------------------------------------------
# 공정 경로 (6종)
# ---------------------------------------------------------------------------
PROCESS_ROUTES: list[ProcessRoute] = [
    # 1. LV 0.6/1kV 단심
    ProcessRoute(
        id="route_lv_1c",
        voltage="0.6/1kV",
        core_count_range="1",
        description="LV 0.6/1kV 단심: 신선 → 연선 → 저압절연 → 저압피복",
        steps=[
            ProcessStep(
                order=1,
                process_type=ProcessType.DRAWING,
                equipment_ids=["54B0_1", "54B0_2", "T8B0", "30B0", "AL6B0"],
            ),
            ProcessStep(
                order=2,
                process_type=ProcessType.STRANDING,
                equipment_ids=["44B0"],
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
    ),
    # 2. LV 0.6/1kV 다심 (2~4심)
    ProcessRoute(
        id="route_lv_mc",
        voltage="0.6/1kV",
        core_count_range="2-4",
        description="LV 0.6/1kV 다심: 신선 → 연선 → 저압절연 → 성缆 → 저압피복",
        steps=[
            ProcessStep(
                order=1,
                process_type=ProcessType.DRAWING,
                equipment_ids=["54B0_1", "54B0_2", "T8B0", "30B0", "AL6B0"],
            ),
            ProcessStep(
                order=2,
                process_type=ProcessType.STRANDING,
                equipment_ids=["44B0"],
            ),
            ProcessStep(
                order=3,
                process_type=ProcessType.LV_INSULATION,
                equipment_ids=["12B0", "4B0"],
            ),
            ProcessStep(
                order=4,
                process_type=ProcessType.CABLING,
                equipment_ids=["44B0"],
            ),
            ProcessStep(
                order=5,
                process_type=ProcessType.LV_JACKETING,
                equipment_ids=["A100EXT", "B100EXT"],
            ),
        ],
    ),
    # 3. MV 6/10kV 단심
    ProcessRoute(
        id="route_mv_1c",
        voltage="6/10kV",
        core_count_range="1",
        description="MV 6/10kV 단심: 신선 → 연선 → 고압절연 → 테이핑 → 저압피복",
        steps=[
            ProcessStep(
                order=1,
                process_type=ProcessType.DRAWING,
                equipment_ids=["54B0_1", "54B0_2", "T8B0", "30B0"],
            ),
            ProcessStep(
                order=2,
                process_type=ProcessType.STRANDING,
                equipment_ids=["44B0"],
            ),
            ProcessStep(
                order=3,
                process_type=ProcessType.HV_INSULATION,
                equipment_ids=["CV_1", "CV_2"],
            ),
            ProcessStep(
                order=4,
                process_type=ProcessType.TAPING,
                equipment_ids=["TP_1", "TP_2"],
            ),
            ProcessStep(
                order=5,
                process_type=ProcessType.LV_JACKETING,
                equipment_ids=["A100EXT", "B100EXT"],
            ),
        ],
    ),
    # 4. MV 6/10kV 다심 (2~4심)
    ProcessRoute(
        id="route_mv_mc",
        voltage="6/10kV",
        core_count_range="2-4",
        description="MV 6/10kV 다심: 신선 → 연선 → 고압절연 → 테이핑 → 성缆 → 저압피복",
        steps=[
            ProcessStep(
                order=1,
                process_type=ProcessType.DRAWING,
                equipment_ids=["54B0_1", "54B0_2", "T8B0", "30B0"],
            ),
            ProcessStep(
                order=2,
                process_type=ProcessType.STRANDING,
                equipment_ids=["44B0"],
            ),
            ProcessStep(
                order=3,
                process_type=ProcessType.HV_INSULATION,
                equipment_ids=["CV_1", "CV_2"],
            ),
            ProcessStep(
                order=4,
                process_type=ProcessType.TAPING,
                equipment_ids=["TP_1", "TP_2"],
            ),
            ProcessStep(
                order=5,
                process_type=ProcessType.CABLING,
                equipment_ids=["44B0"],
            ),
            ProcessStep(
                order=6,
                process_type=ProcessType.LV_JACKETING,
                equipment_ids=["A100EXT", "B100EXT"],
            ),
        ],
    ),
    # 5. HV 22.9kV 단심
    ProcessRoute(
        id="route_hv_1c",
        voltage="22.9kV",
        core_count_range="1",
        description="HV 22.9kV 단심: 신선 → 연선 → 고압절연 → 중성선 → 고압피복",
        steps=[
            ProcessStep(
                order=1,
                process_type=ProcessType.DRAWING,
                equipment_ids=["30B0", "AL6B0"],
            ),
            ProcessStep(
                order=2,
                process_type=ProcessType.STRANDING,
                equipment_ids=["44B0"],
            ),
            ProcessStep(
                order=3,
                process_type=ProcessType.HV_INSULATION,
                equipment_ids=["CV_1", "CV_2"],
            ),
            ProcessStep(
                order=4,
                process_type=ProcessType.NEUTRAL_WIRE,
                equipment_ids=["44B0"],
            ),
            ProcessStep(
                order=5,
                process_type=ProcessType.HV_JACKETING,
                equipment_ids=["A150EXT", "A120EXT"],
            ),
        ],
    ),
    # 6. HV 22.9kV ACSR (알루미늄 강심)
    ProcessRoute(
        id="route_hv_acsr",
        voltage="22.9kV",
        core_count_range="ACSR",
        description="HV 22.9kV ACSR: 신선 → 연선 (피복 없음)",
        steps=[
            ProcessStep(
                order=1,
                process_type=ProcessType.DRAWING,
                equipment_ids=["AL6B0"],
            ),
            ProcessStep(
                order=2,
                process_type=ProcessType.STRANDING,
                equipment_ids=["44B0"],
            ),
        ],
    ),
]

# ---------------------------------------------------------------------------
# 선속도 테이블 (m/min) — 현장 실측 데이터
# 키: 도체 규격, 값: 공정별 선속도
# ---------------------------------------------------------------------------
LINE_SPEEDS: dict[str, dict[str, float]] = {
    "16SQ": {
        "insulation": 35.0,
        "jacketing": 30.0,
        "1C": 35.0,
        "2C": 18.0,
        "3C": 15.0,
        "4C": 12.0,
        "HFCO": 20.0,
        "TFR-GV": 28.0,
    },
    "25SQ": {
        "insulation": 30.0,
        "jacketing": 26.0,
        "1C": 30.0,
        "2C": 15.0,
        "3C": 13.0,
        "4C": 10.0,
        "HFCO": 18.0,
        "TFR-GV": 24.0,
    },
    "35SQ": {
        "insulation": 26.0,
        "jacketing": 22.0,
        "1C": 26.0,
        "2C": 13.0,
        "3C": 11.0,
        "4C": 9.0,
        "HFCO": 16.0,
        "TFR-GV": 20.0,
    },
    "50SQ": {
        "insulation": 22.0,
        "jacketing": 19.0,
        "1C": 22.0,
        "2C": 11.0,
        "3C": 9.5,
        "4C": 8.0,
        "HFCO": 14.0,
        "TFR-GV": 17.0,
    },
    "70SQ": {
        "insulation": 18.0,
        "jacketing": 16.0,
        "1C": 18.0,
        "2C": 9.0,
        "3C": 8.0,
        "4C": 6.5,
        "HFCO": 11.0,
        "TFR-GV": 14.0,
    },
    "95SQ": {
        "insulation": 15.0,
        "jacketing": 13.0,
        "1C": 15.0,
        "2C": 7.5,
        "3C": 6.5,
        "4C": 5.5,
        "HFCO": 9.0,
        "TFR-GV": 12.0,
    },
    "120SQ": {
        "insulation": 13.0,
        "jacketing": 11.0,
        "1C": 13.0,
        "2C": 6.5,
        "3C": 5.5,
        "4C": 4.5,
        "HFCO": 8.0,
        "TFR-GV": 10.0,
    },
    "150SQ": {
        "insulation": 11.0,
        "jacketing": 9.5,
        "1C": 11.0,
        "2C": 5.5,
        "3C": 5.0,
        "4C": 4.0,
        "HFCO": 7.0,
        "TFR-GV": 8.5,
    },
    "185SQ": {
        "insulation": 9.0,
        "jacketing": 8.0,
        "1C": 9.0,
        "2C": 4.5,
        "3C": 4.0,
        "4C": 3.5,
        "HFCO": 5.5,
        "TFR-GV": 7.0,
    },
    "240SQ": {
        "insulation": 7.5,
        "jacketing": 6.5,
        "1C": 7.5,
        "2C": 3.8,
        "3C": 3.3,
        "4C": 2.8,
        "HFCO": 4.5,
        "TFR-GV": 6.0,
    },
    "300SQ": {
        "insulation": 6.0,
        "jacketing": 5.5,
        "1C": 6.0,
        "2C": 3.0,
        "3C": 2.7,
        "4C": 2.3,
        "HFCO": 3.8,
        "TFR-GV": 5.0,
    },
    "400SQ": {
        "insulation": 5.0,
        "jacketing": 4.5,
        "1C": 5.0,
        "2C": 2.5,
        "3C": 2.2,
        "4C": 1.9,
        "HFCO": 3.2,
        "TFR-GV": 4.0,
    },
}


def get_line_speed(spec: str, process_key: str) -> float:
    """선속도 테이블에서 속도를 조회한다.
    알 수 없는 규격/공정 키에는 10.0 m/min 을 기본값으로 반환한다."""
    spec_speeds = LINE_SPEEDS.get(spec, {})
    return spec_speeds.get(process_key, 10.0)


# ---------------------------------------------------------------------------
# 샘플 수주 (5건) — ERP 현장 실사 데이터 기반
# ---------------------------------------------------------------------------
SAMPLE_ORDERS: list[Order] = [
    Order(
        id="ORD-2026-0301",
        voltage="0.6/1kV",
        product_group="TFR-GV",
        spec="95SQ",
        core_count=1,
        core_color="흑색",
        sheath_color="흑색",
        customer="한국전력공사",
        delivery_date=datetime(2026, 4, 15),
        length_m=5000.0,
        quantity=1,
        total_length_m=5000.0,
        cu_weight_kg=4275.0,
        packaging="목드럼",
        priority=TaskPriority.URGENT,
    ),
    Order(
        id="ORD-2026-0302",
        voltage="0.6/1kV",
        product_group="HFCO",
        spec="35SQ",
        core_count=3,
        core_color="흑/적/청",
        sheath_color="회색",
        customer="현대건설",
        delivery_date=datetime(2026, 4, 20),
        length_m=2000.0,
        quantity=3,
        total_length_m=6000.0,
        cu_weight_kg=1998.0,
        packaging="목드럼",
        priority=TaskPriority.NORMAL,
    ),
    Order(
        id="ORD-2026-0303",
        voltage="6/10kV",
        product_group="CV",
        spec="150SQ",
        core_count=1,
        core_color="흑색",
        sheath_color="흑색",
        customer="삼성전자",
        delivery_date=datetime(2026, 4, 10),
        length_m=3000.0,
        quantity=1,
        total_length_m=3000.0,
        cu_weight_kg=4050.0,
        packaging="철드럼",
        priority=TaskPriority.CRITICAL,
    ),
    Order(
        id="ORD-2026-0304",
        voltage="22.9kV",
        product_group="CV",
        spec="240SQ",
        core_count=1,
        core_color="흑색",
        sheath_color="흑색",
        customer="한전KPS",
        delivery_date=datetime(2026, 5, 1),
        length_m=1500.0,
        quantity=1,
        total_length_m=1500.0,
        cu_weight_kg=3240.0,
        packaging="철드럼",
        priority=TaskPriority.NORMAL,
    ),
    Order(
        id="ORD-2026-0305",
        voltage="0.6/1kV",
        product_group="TFR-GV",
        spec="25SQ",
        core_count=4,
        core_color="흑/적/청/녹황",
        sheath_color="회색",
        customer="GS건설",
        delivery_date=datetime(2026, 4, 25),
        length_m=1000.0,
        quantity=5,
        total_length_m=5000.0,
        cu_weight_kg=1350.0,
        packaging="목드럼",
        priority=TaskPriority.NORMAL,
    ),
]

# ---------------------------------------------------------------------------
# 샘플 스케줄 작업 (초기 데이터)
# ---------------------------------------------------------------------------
SAMPLE_TASKS: list[ScheduleTask] = [
    ScheduleTask(
        id="TASK-001",
        order_id="ORD-2026-0301",
        equipment_id="4B0",
        product="TFR-GV",
        spec="95SQ",
        core_count=1,
        color="흑색",
        start=datetime(2026, 3, 25, 8, 0),
        end=datetime(2026, 3, 25, 15, 57),  # 5000m / 12m/min = ~417min
        volume_m=5000.0,
        line_speed_m_per_min=get_line_speed("95SQ", "TFR-GV"),
        priority=TaskPriority.URGENT,
        status=TaskStatus.PLANNED,
        delivery_date=datetime(2026, 4, 15),
        process_step=3,
        notes="한국전력 긴급 수주",
        changeover_min=30,
    ),
    ScheduleTask(
        id="TASK-002",
        order_id="ORD-2026-0302",
        equipment_id="A100EXT",
        product="HFCO",
        spec="35SQ",
        core_count=3,
        color="흑/적/청",
        start=datetime(2026, 3, 26, 8, 0),
        end=datetime(2026, 3, 26, 19, 30),  # 6000m / 11m/min = ~545min
        volume_m=6000.0,
        line_speed_m_per_min=get_line_speed("35SQ", "3C"),
        priority=TaskPriority.NORMAL,
        status=TaskStatus.PLANNED,
        delivery_date=datetime(2026, 4, 20),
        process_step=5,
        notes="현대건설 3심 케이블",
        changeover_min=20,
    ),
    ScheduleTask(
        id="TASK-003",
        order_id="ORD-2026-0303",
        equipment_id="CV_1",
        product="CV",
        spec="150SQ",
        core_count=1,
        color="흑색",
        start=datetime(2026, 3, 27, 6, 0),
        end=datetime(
            2026, 3, 27, 11, 42
        ),  # 3000m / get_line_speed("150SQ", "insulation") = ~333min
        volume_m=3000.0,
        line_speed_m_per_min=get_line_speed("150SQ", "insulation"),
        priority=TaskPriority.CRITICAL,
        status=TaskStatus.PLANNED,
        delivery_date=datetime(2026, 4, 10),
        process_step=3,
        notes="삼성전자 긴급 — 납기 엄수",
        changeover_min=60,
    ),
    ScheduleTask(
        id="TASK-004",
        order_id="ORD-2026-0304",
        equipment_id="CV_2",
        product="CV",
        spec="240SQ",
        core_count=1,
        color="흑색",
        start=datetime(2026, 3, 28, 8, 0),
        end=datetime(
            2026, 3, 28, 13, 20
        ),  # 1500m / get_line_speed("240SQ", "insulation") = 200min
        volume_m=1500.0,
        line_speed_m_per_min=get_line_speed("240SQ", "insulation"),
        priority=TaskPriority.NORMAL,
        status=TaskStatus.PLANNED,
        delivery_date=datetime(2026, 5, 1),
        process_step=3,
        notes="한전KPS 22.9kV 고압",
        changeover_min=90,
    ),
    ScheduleTask(
        id="TASK-005",
        order_id="ORD-2026-0305",
        equipment_id="B100EXT",
        product="TFR-GV",
        spec="25SQ",
        core_count=4,
        color="흑/적/청/녹황",
        start=datetime(2026, 3, 29, 8, 0),
        end=datetime(
            2026, 3, 29, 22, 0
        ),  # 5000m / get_line_speed("25SQ", "4C") = ~833min
        volume_m=5000.0,
        line_speed_m_per_min=get_line_speed("25SQ", "4C"),
        priority=TaskPriority.NORMAL,
        status=TaskStatus.PLANNED,
        delivery_date=datetime(2026, 4, 25),
        process_step=5,
        notes="GS건설 4심 케이블 5드럼",
        changeover_min=20,
    ),
]


def get_equipment_map() -> dict[str, Equipment]:
    """설비 ID → Equipment 객체 매핑"""
    return {eq.id: eq for eq in EQUIPMENT}


def get_order_map() -> dict[str, Order]:
    """수주 ID → Order 객체 매핑"""
    return {o.id: o for o in SAMPLE_ORDERS}
