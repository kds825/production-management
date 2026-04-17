"""
KBI 공장 마스터 데이터 시드 스크립트
-------------------------------------
모든 14개 테이블 중 마스터/설정 테이블 8개를 시드한다.
(sales_order, wip_inventory, production_batch, schedule_task, audit_log, item_master 는
 트랜잭션 테이블이므로 시드 대상 아님)

실행: cd backend && python seed_db.py
멱등성: customer_master에 데이터가 이미 있으면 스킵한다.
"""

import sys

sys.path.insert(0, ".")

from app.infrastructure.database import SessionLocal
from app.infrastructure.models import (
    ConstraintConfig,
    CustomerMaster,
    DecisionCriteria,
    DrumLotMaster,
    EquipmentMaster,
    OperationCalendar,
    ProcessRouting,
    SpeedMaster,
)


# ---------------------------------------------------------------------------
# 1. customer_master (4 rows)
# ---------------------------------------------------------------------------
def _customers():
    return [
        CustomerMaster(
            customer_code="C-001",
            customer_name="아이마켓코리아",
            priority=1,
            due_type="도착기준",
            due_strictness="최우선-무조건",
            require_sample=True,
            urgency_frequency="매우잦음",
        ),
        CustomerMaster(
            customer_code="C-002",
            customer_name="특판거래처",
            priority=2,
            due_type="출하기준",
            due_strictness="특판우선",
            require_sample=False,
            urgency_frequency="보통",
        ),
        CustomerMaster(
            customer_code="C-003",
            customer_name="일반시판",
            priority=3,
            due_type="출하기준",
            due_strictness="여유있음",
            require_sample=False,
            urgency_frequency="낮음",
        ),
        CustomerMaster(
            customer_code="C-099",
            customer_name="케이비아이코스모링크",
            priority=99,
            due_type="출하기준",
            due_strictness="최후순위",
            require_sample=False,
            urgency_frequency="낮음",
        ),
    ]


# ---------------------------------------------------------------------------
# 2. process_routing (9 rows)
# ---------------------------------------------------------------------------
def _routings():
    return [
        ProcessRouting(
            routing_code="RT-001",
            routing_name="저압CV 단심",
            process_1="신선",
            process_2="연선",
            process_3="저압절연",
            process_4="저압시스",
            process_5=None,
            process_6=None,
        ),
        ProcessRouting(
            routing_code="RT-002",
            routing_name="저압CV 다심(2C+)",
            process_1="신선",
            process_2="연선",
            process_3="저압절연",
            process_4="연합",
            process_5="저압시스",
            process_6=None,
        ),
        ProcessRouting(
            routing_code="RT-TFR",
            routing_name="TFR-GV(절연생략)",
            process_1="신선",
            process_2="연선",
            process_3="저압시스",
            process_4=None,
            process_5=None,
            process_6=None,
        ),
        ProcessRouting(
            routing_code="RT-BARE",
            routing_name="연동선/나동선(연선만)",
            process_1="신선",
            process_2="연선",
            process_3=None,
            process_4=None,
            process_5=None,
            process_6=None,
        ),
        ProcessRouting(
            routing_code="RT-003",
            routing_name="HFCO 단심",
            process_1="신선",
            process_2="연선",
            process_3="저압절연",
            process_4="HFCO시스",
            process_5=None,
            process_6=None,
        ),
        ProcessRouting(
            routing_code="RT-004",
            routing_name="HFCO 다심",
            process_1="신선",
            process_2="연선",
            process_3="저압절연",
            process_4="연합",
            process_5="HFCO시스",
            process_6=None,
        ),
        ProcessRouting(
            routing_code="RT-HP1",
            routing_name="6/10kV 고압",
            process_1="신선",
            process_2="연선",
            process_3="고압절연",
            process_4="저압시스",
            process_5=None,
            process_6=None,
        ),
        ProcessRouting(
            routing_code="RT-HP2",
            routing_name="22.9kV TR-CNCO",
            process_1="신선",
            process_2="연선",
            process_3="고압절연",
            process_4="고압시스",
            process_5=None,
            process_6=None,
        ),
        ProcessRouting(
            routing_code="RT-HP3",
            routing_name="35kV 고압",
            process_1="신선",
            process_2="연선",
            process_3="고압절연",
            process_4="고압시스",
            process_5=None,
            process_6=None,
        ),
        ProcessRouting(
            routing_code="RT-EXT",
            routing_name="외주전용",
            process_1="외주",
            process_2=None,
            process_3=None,
            process_4=None,
            process_5=None,
            process_6=None,
        ),
        # ── TFR-8 일반 (고내화 제외) ──
        # 신선 → 연선 → 저압절연 → 저압시스 (단심)
        ProcessRouting(
            routing_code="RT-005",
            routing_name="TFR-8 단심",
            process_1="신선",
            process_2="연선",
            process_3="저압절연",
            process_4="저압시스",
            process_5=None,
            process_6=None,
        ),
        # 신선 → 연선 → 저압절연 → 연합 → 저압시스 (다심)
        ProcessRouting(
            routing_code="RT-006",
            routing_name="TFR-8 다심(2C+)",
            process_1="신선",
            process_2="연선",
            process_3="저압절연",
            process_4="연합",
            process_5="저압시스",
            process_6=None,
        ),
        # ── TFR-8 고내화 (T/P 공정 추가) ──
        # 신선 → 연선 → T/P → 저압절연 → 저압시스 (단심)
        ProcessRouting(
            routing_code="RT-007",
            routing_name="TFR-8 고내화 단심",
            process_1="신선",
            process_2="연선",
            process_3="T/P",
            process_4="저압절연",
            process_5="저압시스",
            process_6=None,
        ),
        # 신선 → 연선 → T/P → 저압절연 → 연합 → 저압시스 (다심)
        ProcessRouting(
            routing_code="RT-008",
            routing_name="TFR-8 고내화 다심(2C+)",
            process_1="신선",
            process_2="연선",
            process_3="T/P",
            process_4="저압절연",
            process_5="연합",
            process_6="저압시스",
        ),
    ]


# ---------------------------------------------------------------------------
# 3. equipment_master (26 rows)
# ---------------------------------------------------------------------------
def _equipment():
    return [
        # --- 신선 ---
        EquipmentMaster(
            equipment_code="DS-C11D",
            equipment_name="C11D",
            process_name="신선",
            material_limit="CU",
            range_min=1.60,
            range_max=3.80,
            range_unit="mm",
            shift_type="2교대",
            base_working_hours=20,
            calendar_rule_code="CAL-STD",
        ),
        EquipmentMaster(
            equipment_code="DS-N11D",
            equipment_name="N11D",
            process_name="신선",
            material_limit="CU",
            range_min=1.60,
            range_max=4.00,
            range_unit="mm",
            shift_type="2교대",
            base_working_hours=20,
            calendar_rule_code="CAL-STD",
        ),
        EquipmentMaster(
            equipment_code="DS-A11D",
            equipment_name="A11D",
            process_name="신선",
            material_limit="CU",
            range_min=1.80,
            range_max=4.85,
            range_unit="mm",
            shift_type="2교대",
            base_working_hours=20,
            calendar_rule_code="CAL-STD",
        ),
        EquipmentMaster(
            equipment_code="DS-A11D2",
            equipment_name="A11D 2호기",
            process_name="신선",
            material_limit="AL",
            range_min=None,
            range_max=None,
            range_unit="mm",
            shift_type="2교대",
            base_working_hours=20,
            calendar_rule_code="CAL-STD",
        ),
        EquipmentMaster(
            equipment_code="DS-17D",
            equipment_name="17D",
            process_name="신선",
            material_limit="CU",
            range_min=0.32,
            range_max=1.20,
            range_unit="mm",
            shift_type="2교대",
            base_working_hours=20,
            calendar_rule_code="CAL-STD",
        ),
        # --- 연선 ---
        EquipmentMaster(
            equipment_code="ST-T6B0",
            equipment_name="T6B0",
            process_name="연선",
            material_limit="CU",
            range_min=25,
            range_max=50,
            range_unit="SQ",
            stranding_method="7연선",
            shift_type="2교대",
            base_working_hours=20,
            calendar_rule_code="CAL-STD",
        ),
        EquipmentMaster(
            equipment_code="ST-AL6BO",
            equipment_name="AL6BO",
            process_name="연선",
            material_limit="AL",
            range_min=25,
            range_max=50,
            range_unit="SQ",
            stranding_method="7연선",
            shift_type="2교대",
            base_working_hours=20,
            calendar_rule_code="CAL-STD",
        ),
        EquipmentMaster(
            equipment_code="ST-1150BC",
            equipment_name="1150 B/C",
            process_name="연선",
            material_limit="AL",
            range_min=4,
            range_max=16,
            range_unit="SQ",
            shift_type="2교대",
            base_working_hours=20,
            calendar_rule_code="CAL-STD",
        ),
        EquipmentMaster(
            equipment_code="ST-54BO1",
            equipment_name="54BO 1호",
            process_name="연선",
            material_limit="CU",
            range_min=70,
            range_max=800,
            range_unit="SQ",
            stranding_method="61연선/19연선/37연선",
            shift_type="2교대",
            base_working_hours=20,
            calendar_rule_code="CAL-STD",
        ),
        EquipmentMaster(
            equipment_code="ST-54BO2",
            equipment_name="54BO 2호",
            process_name="연선",
            material_limit="AL",
            range_min=70,
            range_max=800,
            range_unit="SQ",
            stranding_method="61연선",
            shift_type="2교대",
            base_working_hours=20,
            calendar_rule_code="CAL-STD",
        ),
        EquipmentMaster(
            equipment_code="ST-54BO3",
            equipment_name="54BO 3호",
            process_name="연선",
            material_limit="AL",
            range_min=70,
            range_max=800,
            range_unit="SQ",
            stranding_method="61연선",
            shift_type="2교대",
            base_working_hours=20,
            calendar_rule_code="CAL-STD",
        ),
        EquipmentMaster(
            equipment_code="ST-30BO",
            equipment_name="30BO",
            process_name="연선",
            material_limit="AL",
            range_min=70,
            range_max=120,
            range_unit="SQ",
            stranding_method="19연선",
            shift_type="2교대",
            base_working_hours=20,
            calendar_rule_code="CAL-STD",
        ),
        EquipmentMaster(
            equipment_code="ST-44BO",
            equipment_name="44BO",
            process_name="연선",
            material_limit="AL",
            range_min=70,
            range_max=240,
            range_unit="SQ",
            stranding_method="19연선",
            shift_type="2교대",
            base_working_hours=20,
            calendar_rule_code="CAL-STD",
        ),
        # --- 고압절연 ---
        EquipmentMaster(
            equipment_code="EX-CV1",
            equipment_name="CV 1호",
            process_name="고압절연",
            material_limit=None,
            range_min=25,
            range_max=325,
            range_unit="SQ",
            shift_type="2교대",
            base_working_hours=20,
            calendar_rule_code="CAL-STD",
        ),
        EquipmentMaster(
            equipment_code="EX-CV2",
            equipment_name="CV 2호",
            process_name="고압절연",
            material_limit=None,
            range_min=25,
            range_max=800,
            range_unit="SQ",
            shift_type="2교대",
            base_working_hours=20,
            calendar_rule_code="CAL-STD",
        ),
        # --- 저압절연 ---
        EquipmentMaster(
            equipment_code="EX-B100",
            equipment_name="B100EXT",
            process_name="저압절연",
            material_limit="CU",
            range_min=None,
            range_max=None,
            range_unit="SQ",
            shift_type="2교대",
            base_working_hours=20,
            calendar_rule_code="CAL-STD",
        ),
        # --- 연합 ---
        EquipmentMaster(
            equipment_code="CA-12BO",
            equipment_name="小4BO(12BO)",
            process_name="연합",
            material_limit=None,
            range_min=None,
            range_max=30,
            range_unit="Ø",
            shift_type="2교대",
            base_working_hours=20,
            calendar_rule_code="CAL-STD",
        ),
        EquipmentMaster(
            equipment_code="CA-4BO",
            equipment_name="4BO",
            process_name="연합",
            material_limit=None,
            range_min=35,
            range_max=110,
            range_unit="Ø",
            shift_type="2교대",
            base_working_hours=20,
            calendar_rule_code="CAL-STD",
        ),
        # --- T/P ---
        EquipmentMaster(
            equipment_code="TP-1",
            equipment_name="T/P 1호",
            process_name="T/P",
            material_limit=None,
            range_min=None,
            range_max=60,
            range_unit="Ø",
            shift_type="2교대",
            base_working_hours=20,
            calendar_rule_code="CAL-STD",
        ),
        EquipmentMaster(
            equipment_code="TP-2",
            equipment_name="T/P 2호",
            process_name="T/P",
            material_limit=None,
            range_min=None,
            range_max=60,
            range_unit="Ø",
            shift_type="2교대",
            base_working_hours=20,
            calendar_rule_code="CAL-STD",
        ),
        EquipmentMaster(
            equipment_code="TP-GD",
            equipment_name="강대 T/P",
            process_name="T/P",
            material_limit=None,
            range_min=None,
            range_max=100,
            range_unit="Ø",
            shift_type="2교대",
            base_working_hours=20,
            calendar_rule_code="CAL-STD",
        ),
        EquipmentMaster(
            equipment_code="CA-LU",
            equipment_name="Laying Up",
            process_name="연합",
            material_limit=None,
            range_min=20,
            range_max=120,
            range_unit="Ø",
            shift_type="2교대",
            base_working_hours=20,
            calendar_rule_code="CAL-STD",
        ),
        # --- 저압시스(피복) ---
        EquipmentMaster(
            equipment_code="SH-A100",
            equipment_name="A100EXT",
            process_name="저압시스",
            material_limit="CU",
            range_min=None,
            range_max=50,
            range_unit="Ø",
            color_group="전색상",
            shift_type="2교대",
            base_working_hours=20,
            calendar_rule_code="CAL-STD",
        ),
        EquipmentMaster(
            equipment_code="SH-A120",
            equipment_name="A120EXT",
            process_name="저압시스",
            material_limit="CU",
            range_min=None,
            range_max=70,
            range_unit="Ø",
            color_group="흑/청",
            shift_type="2교대",
            base_working_hours=20,
            calendar_rule_code="CAL-STD",
        ),
        # --- 고압시스 ---
        EquipmentMaster(
            equipment_code="SH-B100",
            equipment_name="B100EXT",
            process_name="고압시스",
            material_limit=None,
            range_min=None,
            range_max=50,
            range_unit="Ø",
            shift_type="2교대",
            base_working_hours=20,
            calendar_rule_code="CAL-STD",
        ),
        EquipmentMaster(
            equipment_code="SH-A150",
            equipment_name="A150EXT",
            process_name="고압시스",
            material_limit=None,
            range_min=None,
            range_max=110,
            range_unit="Ø",
            shift_type="2교대",
            base_working_hours=20,
            calendar_rule_code="CAL-STD",
        ),
    ]


# ---------------------------------------------------------------------------
# 4. operation_calendar (4 rules)
# ---------------------------------------------------------------------------
def _calendars():
    return [
        OperationCalendar(
            rule_code="CAL-STD",
            rule_name="표준 2교대",
            day_of_week="Mon~Thu,Sat,Sun",
            working_hours=22,
            start_time="08:00",
            end_time="08:00",
            deduction_hours=2,
            notes="24hr - 부동2hr = 22hr",
        ),
        OperationCalendar(
            rule_code="CAL-FRI",
            rule_name="금요일 단축",
            day_of_week="Fri",
            working_hours=14,
            start_time="08:00",
            end_time="24:00",
            deduction_hours=2,
            notes="16hr - 부동2hr = 14hr",
        ),
        OperationCalendar(
            rule_code="CAL-MON-EDU",
            rule_name="안전교육 월요일",
            day_of_week="Mon(last2weeks)",
            working_hours=20,
            start_time="08:00",
            end_time="08:00",
            deduction_hours=4,
            notes="표준22hr - 안전교육2hr = 20hr",
        ),
        OperationCalendar(
            rule_code="CAL-HOL",
            rule_name="공휴일/휴무",
            day_of_week="Holiday",
            working_hours=0,
            start_time=None,
            end_time=None,
            deduction_hours=0,
            notes="가동정지",
        ),
    ]


# ---------------------------------------------------------------------------
# 5. drum_lot_master (14 rows)
# ---------------------------------------------------------------------------
def _drum_lots():
    return [
        DrumLotMaster(
            cross_section=16,
            wire_diameter=1.75,
            wire_count=7,
            lot_wire_drawing=20000,
            lot_stranding=22000,
            daily_production=30000,
            setup_time_min=180,
            drum_weight_ton=3,
        ),
        DrumLotMaster(
            cross_section=25,
            wire_diameter=2.21,
            wire_count=7,
            lot_wire_drawing=12000,
            lot_stranding=13500,
            daily_production=30000,
            setup_time_min=180,
            drum_weight_ton=3,
        ),
        DrumLotMaster(
            cross_section=35,
            wire_diameter=2.64,
            wire_count=7,
            lot_wire_drawing=8500,
            lot_stranding=9700,
            daily_production=30000,
            setup_time_min=180,
            drum_weight_ton=3,
        ),
        DrumLotMaster(
            cross_section=50,
            wire_diameter=3.02,
            wire_count=7,
            lot_wire_drawing=6000,
            lot_stranding=6900,
            daily_production=16000,
            setup_time_min=180,
            drum_weight_ton=5,
        ),
        DrumLotMaster(
            cross_section=70,
            wire_diameter=2.21,
            wire_count=19,
            lot_wire_drawing=10000,
            lot_stranding=11000,
            daily_production=14000,
            setup_time_min=180,
            drum_weight_ton=5,
        ),
        DrumLotMaster(
            cross_section=95,
            wire_diameter=2.64,
            wire_count=19,
            lot_wire_drawing=8500,
            lot_stranding=9700,
            daily_production=14000,
            setup_time_min=180,
            drum_weight_ton=10,
        ),
        DrumLotMaster(
            cross_section=120,
            wire_diameter=2.92,
            wire_count=19,
            lot_wire_drawing=7000,
            lot_stranding=8000,
            daily_production=14000,
            setup_time_min=180,
            drum_weight_ton=10,
        ),
        DrumLotMaster(
            cross_section=150,
            wire_diameter=2.46,
            wire_count=37,
            lot_wire_drawing=8500,
            lot_stranding=9500,
            daily_production=12000,
            setup_time_min=180,
            drum_weight_ton=15,
        ),
        DrumLotMaster(
            cross_section=185,
            wire_diameter=2.64,
            wire_count=37,
            lot_wire_drawing=8500,
            lot_stranding=9500,
            daily_production=12000,
            setup_time_min=180,
            drum_weight_ton=15,
        ),
        DrumLotMaster(
            cross_section=200,
            wire_diameter=2.75,
            wire_count=37,
            lot_wire_drawing=8500,
            lot_stranding=9500,
            daily_production=12000,
            setup_time_min=180,
            drum_weight_ton=15,
        ),
        DrumLotMaster(
            cross_section=240,
            wire_diameter=3.02,
            wire_count=37,
            lot_wire_drawing=8500,
            lot_stranding=9500,
            daily_production=12000,
            setup_time_min=180,
            drum_weight_ton=20,
        ),
        DrumLotMaster(
            cross_section=300,
            wire_diameter=2.64,
            wire_count=61,
            lot_wire_drawing=8500,
            lot_stranding=9500,
            daily_production=12000,
            setup_time_min=180,
            drum_weight_ton=25,
        ),
        DrumLotMaster(
            cross_section=400,
            wire_diameter=2.92,
            wire_count=None,
            lot_wire_drawing=7000,
            lot_stranding=8000,
            daily_production=10000,
            setup_time_min=180,
            drum_weight_ton=30,
        ),
        # 1250kcmil (633SQ) — 고압 35kV 압축연선, 소선경 1.2mm × 499본
        # lot_stranding=1120m ≈ 드럼 1개 용량(1067m) + 트림 여유
        # daily_production=8400m: 7.0 m/min × 60 × 20hr
        DrumLotMaster(
            cross_section=633,
            wire_diameter=1.2,
            wire_count=499,
            lot_wire_drawing=5000,
            lot_stranding=1120,
            daily_production=8400,
            setup_time_min=210,
            drum_weight_ton=35,
        ),
    ]


# ---------------------------------------------------------------------------
# 6. decision_criteria (8 rows)
# ---------------------------------------------------------------------------
def _decision_criteria():
    return [
        DecisionCriteria(
            criteria_name="Loss 허용 한도",
            criteria_value="8",
            criteria_unit="%",
            description="재공 대체 시 loss 8% 이내만 허용",
        ),
        DecisionCriteria(
            criteria_name="최소 잔여 조장 보유",
            criteria_value="50",
            criteria_unit="m",
            description="대체 후 잔량 50m 미만 → 스크랩",
        ),
        DecisionCriteria(
            criteria_name="조장 부족 허용율",
            criteria_value="5",
            criteria_unit="%",
            description="재공 길이가 주문 대비 5% 이내 부족 허용",
        ),
        DecisionCriteria(
            criteria_name="색상 일치 필수",
            criteria_value="Y",
            criteria_unit="-",
            description="선심 색상 정확 일치",
        ),
        DecisionCriteria(
            criteria_name="전압 일치 필수",
            criteria_value="Y",
            criteria_unit="-",
            description="동일 전압등급만",
        ),
        DecisionCriteria(
            criteria_name="재질 일치 필수",
            criteria_value="Y",
            criteria_unit="-",
            description="CU/AL 구분",
        ),
        DecisionCriteria(
            criteria_name="SQ 허용 오차",
            criteria_value="0",
            criteria_unit="mm²",
            description="정확 SQ 일치",
        ),
        DecisionCriteria(
            criteria_name="잔량 소진 방향",
            criteria_value="흑색",
            criteria_unit="-",
            description="잔량 ~200m → 흑색 시스로 소진",
        ),
    ]


# ---------------------------------------------------------------------------
# 7. speed_master (저압절연 + 시스 + 고압)
# ---------------------------------------------------------------------------
def _speed_master():
    rows: list[SpeedMaster] = []

    # ── 7-A. B100EXT 저압절연 (12 rows) ──
    b100_insulation = [
        # (cross_section, line_speed_mpm, setup_spec_min)
        (16, 55, 60),
        (25, 55, 60),
        (35, 55, 60),
        (50, 55, 60),
        (70, 42, 60),
        (95, 30, 60),
        (120, 28, 60),
        (150, 25, 60),
        (185, 23, 60),
        (240, 20, 60),
        (300, 17, 60),
        (400, 14, 60),
    ]
    for sq, speed, setup in b100_insulation:
        rows.append(
            SpeedMaster(
                equipment_code="EX-B100",
                product_type="저압절연",
                cross_section=sq,
                line_speed_mpm=speed,
                line_speed_hr=speed * 60,
                setup_spec_min=setup,
            )
        )

    # ── 7-B. 시스 속도 데이터 (A100=전색상, A120=흑/청 1C only) ──
    # 시스 속도표: (SQ, 1C, 2C, 3C, 4C, HFCO, TFR-GV)
    #   None 은 해당 조합 없음
    sheath_speeds = [
        (16, 25, 20, 20, 18, 17, 25),
        (25, 25, 20, 14, 13, 17, 21),
        (35, 25, None, 14, 13, 17, 19),
        (50, 25, None, 12, 11, 17, 17),
        (70, 22, None, 10, 10, 15, 15),
        (95, 22, None, 10, 10, 15, 12),
        (120, 20, None, None, None, 14, 11),
        (150, 18, None, None, None, 12, None),
        (185, 16, None, None, None, 11, None),
        (240, 13, None, None, None, 9, None),
        (300, 11, None, None, None, 8, None),
        (400, 9, None, None, None, 7, None),
    ]

    # product_type 매핑: (index, product_type, equipment_codes)
    # A100 (전색상): 모든 product_type
    # A120 (흑/청):  1C 계열만
    product_map = [
        (1, "TFR-CV 1C", ["SH-A100", "SH-A120"]),  # 1C → 양쪽 설비
        (2, "TFR-CV 2C", ["SH-A100"]),
        (3, "TFR-CV 3C", ["SH-A100"]),
        (4, "TFR-CV 4C", ["SH-A100"]),
        (5, "HFCO", ["SH-A100"]),
        (6, "TFR-GV", ["SH-A100", "SH-A120"]),  # 1C 계열 → 양쪽 설비
    ]

    for sq, spd_1c, spd_2c, spd_3c, spd_4c, spd_hfco, spd_gv in sheath_speeds:
        speed_by_idx = {
            1: spd_1c,
            2: spd_2c,
            3: spd_3c,
            4: spd_4c,
            5: spd_hfco,
            6: spd_gv,
        }
        for idx, ptype, equip_codes in product_map:
            spd = speed_by_idx[idx]
            if spd is None:
                continue
            for eq_code in equip_codes:
                rows.append(
                    SpeedMaster(
                        equipment_code=eq_code,
                        product_type=ptype,
                        cross_section=sq,
                        line_speed_mpm=spd,
                        line_speed_hr=spd * 60,
                        setup_spec_min=30,
                        setup_color_min=120,
                    )
                )

    # ── 7-C. 고압절연 CV1/CV2 (5+4=9 rows) ──
    # (kcmil_label, sq, cv1_speed, cv2_speed)
    cv_insulation = [
        ("4/0AWG", 107, 6.3, None),
        ("500", 253, 4.3, 4.1),
        ("750", 380, 3.8, 3.7),
        ("1000", 507, 3.7, 3.4),
        ("1250", 633, 3.25, 3.1),
    ]
    for label, sq, cv1_spd, cv2_spd in cv_insulation:
        if cv1_spd is not None:
            rows.append(
                SpeedMaster(
                    equipment_code="EX-CV1",
                    product_type=f"고압CV {label}",
                    cross_section=sq,
                    line_speed_mpm=cv1_spd,
                    line_speed_hr=round(cv1_spd * 60, 1),
                    setup_spec_min=300,
                )
            )
        if cv2_spd is not None:
            rows.append(
                SpeedMaster(
                    equipment_code="EX-CV2",
                    product_type=f"고압CV {label}",
                    cross_section=sq,
                    line_speed_mpm=cv2_spd,
                    line_speed_hr=round(cv2_spd * 60, 1),
                    setup_spec_min=300,
                )
            )

    # ── 7-D. 고압시스 A150EXT ──
    # (kcmil_label, sq, sheath_speed)
    hp_sheath = [
        ("500", 253, 7.0),
        ("750", 380, 6.5),
        ("1000", 507, 6.0),
        ("1250", 633, 5.5),
    ]
    for label, sq, spd in hp_sheath:
        rows.append(
            SpeedMaster(
                equipment_code="SH-A150",
                product_type=f"고압시스 {label}",
                cross_section=sq,
                line_speed_mpm=spd,
                line_speed_hr=round(spd * 60, 1),
                setup_spec_min=30,
            )
        )

    # ── 7-G. T/P 공정 선속 (TFR-8 고내화) ──
    # T/P(Tape/Padding): 내화층 권포 공정. TP-1/TP-2(일반, 최대 60Ø), TP-GD(강대, 최대 100Ø)
    # 선속은 드럼 외경에 따라 달라지나 SQ 기준 근사값 사용. setup 180분(규격교체).
    tp_data = [
        # (cross_section, line_speed_mpm, setup_spec_min)
        (16, 40, 180),
        (25, 38, 180),
        (35, 35, 180),
        (50, 32, 180),
        (70, 28, 180),
        (95, 25, 180),
        (120, 22, 180),
        (150, 20, 180),
        (185, 18, 180),
        (240, 15, 180),
        (300, 12, 180),
        (400, 10, 180),
    ]
    # TP-1(일반 60Ø) / TP-GD(강대 100Ø) 동일 속도표 공유. TP-2 는 운영 DB 에
    # 역사적으로 EX-B100 값이 들어가 있으나 스케줄러가 미배정하므로 seed 레벨에서는
    # 정상값(tp_data)을 제공. fresh seed 시 TP-2 값이 정상화된다.
    for tp_eq in ("TP-1", "TP-2", "TP-GD"):
        for sq, speed, setup in tp_data:
            rows.append(
                SpeedMaster(
                    equipment_code=tp_eq,
                    product_type="T/P",
                    cross_section=sq,
                    line_speed_mpm=speed,
                    line_speed_hr=speed * 60,
                    setup_spec_min=setup,
                )
            )

    # ── 7-H. 연선(ST-*) 선속 ──
    # Why: fix_speed_master_option_b.py 와 동기화된 canonical. 값은 PDF 레퍼런스
    # 기반 PoC 초기 추정 — 생산팀 실측 검증 필요. setup=210분(SQ 교체 policy).
    stranding_speeds = [
        # ST-54BO1: CU 61연선 70~800SQ
        (
            "ST-54BO1",
            "61연선 CU",
            [
                (70, 30),
                (95, 28),
                (120, 25),
                (150, 22),
                (185, 20),
                (240, 18),
                (300, 15),
                (400, 12),
                (500, 10),
                (630, 8),
                (800, 6),
            ],
        ),
        # ST-54BO2: AL 61연선 70~800SQ
        (
            "ST-54BO2",
            "61연선 AL",
            [
                (70, 32),
                (95, 30),
                (120, 27),
                (150, 24),
                (185, 22),
                (240, 20),
                (300, 17),
                (380, 15),
                (400, 14),
                (500, 12),
                (507, 12),
                (630, 10),
                (633, 10),
                (800, 8),
            ],
        ),
        # ST-54BO3: AL 61연선 (ST-54BO2 병렬 설비)
        (
            "ST-54BO3",
            "61연선 AL",
            [
                (70, 32),
                (95, 30),
                (107, 28),
                (120, 27),
                (150, 24),
                (185, 22),
                (240, 20),
                (300, 17),
                (380, 15),
                (400, 14),
                (500, 12),
                (630, 10),
                (633, 10),
                (800, 8),
            ],
        ),
        # ST-30BO: AL 19연선 70~120SQ
        ("ST-30BO", "19연선 AL", [(70, 25), (95, 22), (120, 20)]),
        # ST-44BO: AL 19연선 70~240SQ
        (
            "ST-44BO",
            "19연선 AL",
            [(70, 25), (95, 22), (120, 20), (150, 18), (185, 16), (240, 14)],
        ),
        # ST-1150BC: AL B/C 4~16SQ
        ("ST-1150BC", "B/C AL", [(4, 40), (6, 38), (10, 35), (16, 30)]),
        # ST-T6B0: CU 7연선 25~50SQ
        ("ST-T6B0", "7연선 CU", [(25, 25), (35, 25), (50, 20)]),
        # ST-AL6BO: AL 7연선 25~50SQ
        ("ST-AL6BO", "7연선 AL", [(25, 22), (35, 20), (50, 17)]),
    ]
    for eq_code, ptype, sq_speeds in stranding_speeds:
        for sq, speed in sq_speeds:
            rows.append(
                SpeedMaster(
                    equipment_code=eq_code,
                    product_type=ptype,
                    cross_section=sq,
                    line_speed_mpm=speed,
                    line_speed_hr=speed * 60,
                    setup_spec_min=210,
                )
            )

    # ── 7-I. 연합(CA-*) 선속 ──
    # 연합 공정: 다심 꼬기. 설비 range_unit 이 Ø 이지만 SpeedMaster cross_section
    # 은 SQ 공통 키로 저장 (스케줄러 조회 키와 일치).
    coupling_speeds = [
        ("CA-12BO", [(1.5, 35), (2.5, 32), (4, 30), (6, 28)]),
        ("CA-4BO", [(35, 28), (50, 25), (70, 22), (95, 20)]),
        ("CA-LU", [(35, 30), (50, 28), (70, 25), (95, 22), (120, 20), (150, 18)]),
    ]
    for eq_code, sq_speeds in coupling_speeds:
        for sq, speed in sq_speeds:
            rows.append(
                SpeedMaster(
                    equipment_code=eq_code,
                    product_type="연합",
                    cross_section=sq,
                    line_speed_mpm=speed,
                    line_speed_hr=speed * 60,
                    setup_spec_min=120,
                )
            )

    # ── 7-J. 고압시스 SH-B100 (저용량, range_max=50Ø) ──
    b100_hp_sheath = [(16, 8.0), (25, 7.5), (35, 7.0), (50, 6.5)]
    for sq, spd in b100_hp_sheath:
        rows.append(
            SpeedMaster(
                equipment_code="SH-B100",
                product_type="고압시스 저용량",
                cross_section=sq,
                line_speed_mpm=spd,
                line_speed_hr=round(spd * 60, 1),
                setup_spec_min=30,
            )
        )

    return rows


# ---------------------------------------------------------------------------
# 8. constraint_config (38 rows)
# ---------------------------------------------------------------------------
def _constraints():
    return [
        ConstraintConfig(
            constraint_id="1-1",
            constraint_name="거래처 우선순위",
            category="납기/우선순위",
            is_enabled=True,
            priority=1,
            impact_level="★★★",
            params_json={},
            applicable_processes=["전체"],
            implementation_type="table_param",
        ),
        ConstraintConfig(
            constraint_id="1-2",
            constraint_name="납기 기준(도착/출하)",
            category="납기/우선순위",
            is_enabled=True,
            priority=2,
            impact_level="★★★",
            params_json={"transport_days": 1},
            applicable_processes=["전체"],
            implementation_type="table_param",
        ),
        ConstraintConfig(
            constraint_id="1-3",
            constraint_name="긴급 변경 대응",
            category="납기/우선순위",
            is_enabled=True,
            priority=3,
            impact_level="★★★",
            params_json={},
            applicable_processes=["전체"],
            implementation_type="hybrid",
        ),
        ConstraintConfig(
            constraint_id="2-1",
            constraint_name="재공 활용(연선/절연 재고우선)",
            category="SM수량/재고",
            is_enabled=True,
            priority=4,
            impact_level="★★★",
            params_json={
                "loss_limit_pct": 8,
                "min_remainder_m": 50,
                "shortage_tolerance_pct": 5,
            },
            applicable_processes=["연선", "저압절연", "고압절연"],
            implementation_type="hybrid",
        ),
        ConstraintConfig(
            constraint_id="2-2",
            constraint_name="외주 조건(≤10SQ, 고내화16)",
            category="SM수량/재고",
            is_enabled=True,
            priority=5,
            impact_level="★★★",
            params_json={},
            applicable_processes=["전체"],
            implementation_type="table_param",
        ),
        ConstraintConfig(
            constraint_id="2-3",
            constraint_name="틀단위 기준 생산",
            category="SM수량/재고",
            is_enabled=True,
            priority=6,
            impact_level="★★★",
            params_json={},
            applicable_processes=["연선"],
            implementation_type="table_param",
        ),
        ConstraintConfig(
            constraint_id="2-4",
            constraint_name="61연선 분리",
            category="SM수량/재고",
            is_enabled=True,
            priority=7,
            impact_level="★★★",
            params_json={},
            applicable_processes=["연선"],
            implementation_type="code_logic",
        ),
        ConstraintConfig(
            constraint_id="3-1",
            constraint_name="색상별 여척 추가",
            category="색상관리",
            is_enabled=True,
            priority=10,
            impact_level="★★",
            params_json={"extra_length_m": 7, "sample_extra_m": 10},
            applicable_processes=["연선", "저압절연"],
            implementation_type="table_param",
        ),
        ConstraintConfig(
            constraint_id="3-2",
            constraint_name="색상 묶음 배치",
            category="색상관리",
            is_enabled=True,
            priority=11,
            impact_level="★★★",
            params_json={},
            applicable_processes=["저압시스"],
            implementation_type="code_logic",
        ),
        ConstraintConfig(
            constraint_id="3-3",
            constraint_name="설비별 색상그룹 제한",
            category="색상관리",
            is_enabled=True,
            priority=12,
            impact_level="★★★",
            params_json={},
            applicable_processes=["저압시스"],
            implementation_type="table_param",
        ),
        ConstraintConfig(
            constraint_id="3-4",
            constraint_name="잔량 흑색 소진",
            category="색상관리",
            is_enabled=True,
            priority=13,
            impact_level="★",
            params_json={"remnant_threshold_m": 200},
            applicable_processes=["저압시스"],
            implementation_type="table_param",
        ),
        ConstraintConfig(
            constraint_id="4-1",
            constraint_name="규격교체 시간",
            category="셋업/교체",
            is_enabled=True,
            priority=20,
            impact_level="★★",
            params_json={
                "stranding_min": 210,
                "insulation_min": 60,
                "sheath_min": 30,
                "cv_min": 300,
            },
            applicable_processes=["연선", "저압절연", "고압절연", "저압시스"],
            implementation_type="table_param",
        ),
        ConstraintConfig(
            constraint_id="4-2",
            constraint_name="색상교체 시간",
            category="셋업/교체",
            is_enabled=True,
            priority=21,
            impact_level="★★",
            params_json={"sheath_color_min": 120},
            applicable_processes=["저압시스"],
            implementation_type="table_param",
        ),
        ConstraintConfig(
            constraint_id="4-3",
            constraint_name="드럼 권취 시간",
            category="셋업/교체",
            is_enabled=True,
            priority=22,
            impact_level="★★",
            params_json={},
            applicable_processes=["전체"],
            implementation_type="table_param",
        ),
        ConstraintConfig(
            constraint_id="4-4",
            constraint_name="용접 시간",
            category="셋업/교체",
            is_enabled=True,
            priority=23,
            impact_level="★★",
            params_json={"welding_min": 30},
            applicable_processes=["연선"],
            implementation_type="table_param",
        ),
        ConstraintConfig(
            constraint_id="4-5",
            constraint_name="테이핑 속도 제한",
            category="셋업/교체",
            is_enabled=True,
            priority=24,
            impact_level="★★",
            params_json={},
            applicable_processes=["T/P"],
            implementation_type="table_param",
        ),
        ConstraintConfig(
            constraint_id="5-1",
            constraint_name="SQ 기준 설비 배정",
            category="품명/규격",
            is_enabled=True,
            priority=30,
            impact_level="★★★",
            params_json={},
            applicable_processes=["연선", "저압절연", "고압절연"],
            implementation_type="table_param",
        ),
        ConstraintConfig(
            constraint_id="5-2",
            constraint_name="연선방식 구분(압축/원형/수밀)",
            category="품명/규격",
            is_enabled=True,
            priority=31,
            impact_level="★★★",
            params_json={},
            applicable_processes=["연선"],
            implementation_type="table_param",
        ),
        ConstraintConfig(
            constraint_id="5-3",
            constraint_name="다심 우선배치",
            category="품명/규격",
            is_enabled=True,
            priority=32,
            impact_level="★★",
            params_json={},
            applicable_processes=["저압절연"],
            implementation_type="code_logic",
        ),
        ConstraintConfig(
            constraint_id="5-4",
            constraint_name="나선/연동선 별도",
            category="품명/규격",
            is_enabled=True,
            priority=33,
            impact_level="★★",
            params_json={},
            applicable_processes=["연선"],
            implementation_type="table_param",
        ),
        ConstraintConfig(
            constraint_id="5-5",
            constraint_name="TFR-GV 절연 생략",
            category="품명/규격",
            is_enabled=True,
            priority=34,
            impact_level="★★★",
            params_json={},
            applicable_processes=["저압절연"],
            implementation_type="table_param",
            notes="9-3과 통합",
        ),
        ConstraintConfig(
            constraint_id="6-1",
            constraint_name="안전교육(매월 마지막2주 월요일)",
            category="캘린더",
            is_enabled=True,
            priority=40,
            impact_level="★★",
            params_json={"deduction_hours": 2},
            applicable_processes=["전체"],
            implementation_type="table_param",
        ),
        ConstraintConfig(
            constraint_id="6-2",
            constraint_name="금요일 야간 단축",
            category="캘린더",
            is_enabled=True,
            priority=41,
            impact_level="★",
            params_json={"friday_hours": 14},
            applicable_processes=["전체"],
            implementation_type="table_param",
        ),
        ConstraintConfig(
            constraint_id="6-3",
            constraint_name="부재자 계획 반영",
            category="캘린더",
            is_enabled=True,
            priority=42,
            impact_level="★★",
            params_json={},
            applicable_processes=["전체"],
            implementation_type="hybrid",
        ),
        ConstraintConfig(
            constraint_id="6-4",
            constraint_name="공휴일/휴무",
            category="캘린더",
            is_enabled=True,
            priority=43,
            impact_level="★★",
            params_json={},
            applicable_processes=["전체"],
            implementation_type="table_param",
        ),
        ConstraintConfig(
            constraint_id="7-1",
            constraint_name="불량 재작업 버퍼",
            category="불량/설비",
            is_enabled=True,
            priority=50,
            impact_level="★★",
            params_json={"defect_buffer_pct": 0.05},
            applicable_processes=["전체"],
            implementation_type="hybrid",
        ),
        ConstraintConfig(
            constraint_id="7-2",
            constraint_name="설비고장 대체",
            category="불량/설비",
            is_enabled=False,
            priority=51,
            impact_level="★★",
            params_json={},
            applicable_processes=["전체"],
            implementation_type="code_logic",
            notes="현장 확인 필요",
        ),
        ConstraintConfig(
            constraint_id="8-1",
            constraint_name="테이프/컴파운드 재고",
            category="자재수급",
            is_enabled=False,
            priority=60,
            impact_level="★★",
            params_json={},
            applicable_processes=["전체"],
            implementation_type="hybrid",
            notes="현장 확인 필요",
        ),
        ConstraintConfig(
            constraint_id="8-2",
            constraint_name="조달 리드타임",
            category="자재수급",
            is_enabled=False,
            priority=61,
            impact_level="★★",
            params_json={"lead_time_days": 7},
            applicable_processes=["전체"],
            implementation_type="table_param",
            notes="현장 확인 필요",
        ),
        ConstraintConfig(
            constraint_id="8-3",
            constraint_name="CU/AL 원자재",
            category="자재수급",
            is_enabled=False,
            priority=62,
            impact_level="★★",
            params_json={},
            applicable_processes=["전체"],
            implementation_type="hybrid",
            notes="현장 확인 필요",
        ),
        ConstraintConfig(
            constraint_id="9-1",
            constraint_name="다심 연합 트리거",
            category="후속공정",
            is_enabled=True,
            priority=70,
            impact_level="★★★",
            params_json={},
            applicable_processes=["연합"],
            implementation_type="code_logic",
        ),
        ConstraintConfig(
            constraint_id="9-2",
            constraint_name="GC 라우팅 제외(B100 X)",
            category="후속공정",
            is_enabled=True,
            priority=71,
            impact_level="★★★",
            params_json={},
            applicable_processes=["저압절연"],
            implementation_type="table_param",
        ),
        ConstraintConfig(
            constraint_id="9-3",
            constraint_name="(5-5와 통합) TFR-GV 바이패스",
            category="후속공정",
            is_enabled=True,
            priority=72,
            impact_level="★★★",
            params_json={},
            applicable_processes=["저압절연"],
            implementation_type="table_param",
            notes="5-5와 동일",
        ),
        ConstraintConfig(
            constraint_id="10-1",
            constraint_name="(5-1과 통합) SQ 기준 설비 배정",
            category="기타",
            is_enabled=True,
            priority=80,
            impact_level="★★",
            params_json={},
            applicable_processes=["전체"],
            implementation_type="table_param",
            notes="5-1와 동일",
        ),
        ConstraintConfig(
            constraint_id="10-2",
            constraint_name="CU/AL 재질 설비 분리",
            category="기타",
            is_enabled=True,
            priority=81,
            impact_level="★★★",
            params_json={},
            applicable_processes=["연선", "저압시스"],
            implementation_type="table_param",
        ),
        ConstraintConfig(
            constraint_id="10-3",
            constraint_name="시스 재질 라우팅",
            category="기타",
            is_enabled=True,
            priority=82,
            impact_level="★★",
            params_json={},
            applicable_processes=["저압시스"],
            implementation_type="table_param",
        ),
        ConstraintConfig(
            constraint_id="10-4",
            constraint_name="전압별 드럼 분류",
            category="기타",
            is_enabled=True,
            priority=83,
            impact_level="★",
            params_json={},
            applicable_processes=["전체"],
            implementation_type="table_param",
        ),
        ConstraintConfig(
            constraint_id="10-5",
            constraint_name="4심 계산법",
            category="기타",
            is_enabled=True,
            priority=84,
            impact_level="★★",
            params_json={},
            applicable_processes=["저압시스"],
            implementation_type="table_param",
        ),
    ]


# ---------------------------------------------------------------------------
# Main seed function
# ---------------------------------------------------------------------------
def seed():
    db = SessionLocal()
    try:
        # 멱등성 체크: customer_master에 데이터가 이미 있으면 스킵
        if db.query(CustomerMaster).count() > 0:
            print("Database already seeded. Skipping.")
            return

        # 1. customer_master
        customers = _customers()
        db.add_all(customers)
        print(f"  [1/8] customer_master: {len(customers)} rows")

        # 2. process_routing
        routings = _routings()
        db.add_all(routings)
        print(f"  [2/8] process_routing: {len(routings)} rows")

        # 3. equipment_master
        equipment = _equipment()
        db.add_all(equipment)
        print(f"  [3/8] equipment_master: {len(equipment)} rows")

        # 4. operation_calendar
        calendars = _calendars()
        db.add_all(calendars)
        print(f"  [4/8] operation_calendar: {len(calendars)} rows")

        # 5. drum_lot_master
        drum_lots = _drum_lots()
        db.add_all(drum_lots)
        print(f"  [5/8] drum_lot_master: {len(drum_lots)} rows")

        # 6. decision_criteria
        criteria = _decision_criteria()
        db.add_all(criteria)
        print(f"  [6/8] decision_criteria: {len(criteria)} rows")

        # 7. speed_master
        speeds = _speed_master()
        db.add_all(speeds)
        print(f"  [7/8] speed_master: {len(speeds)} rows")

        # 8. constraint_config
        constraints = _constraints()
        db.add_all(constraints)
        print(f"  [8/8] constraint_config: {len(constraints)} rows")

        db.commit()
        total = (
            len(customers)
            + len(routings)
            + len(equipment)
            + len(calendars)
            + len(drum_lots)
            + len(criteria)
            + len(speeds)
            + len(constraints)
        )
        print(f"\nSeed complete! Total {total} rows inserted across 8 tables.")

    except Exception as e:
        db.rollback()
        print(f"Seed failed: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed()
