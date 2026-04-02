from sqlalchemy import (
    Column,
    String,
    Integer,
    Numeric,
    Text,
    Date,
    DateTime,
    ForeignKey,
)
from datetime import datetime
from app.infrastructure.database import Base


# Stage 1 <-> Stage 2 interface contract
class ProductionBatch(Base):
    __tablename__ = "production_batch"

    batch_id = Column(Integer, primary_key=True, autoincrement=True)
    run_label = Column(String(50), nullable=False, index=True)
    sales_order_id = Column(String(30))
    sales_order_line = Column(Integer, default=1)
    item_code = Column(String(20), ForeignKey("item_master.item_code"), nullable=True)
    routing_code = Column(
        String(20), ForeignKey("process_routing.routing_code"), nullable=True
    )
    process_name = Column(String(30), nullable=False)  # 연선, 저압절연, ...
    equipment_code = Column(
        String(20), ForeignKey("equipment_master.equipment_code"), nullable=True
    )
    batch_seq = Column(Integer, default=1)
    drum_length_m = Column(Numeric)
    drum_count = Column(Integer, default=1)
    total_length_m = Column(Numeric)
    extra_length_m = Column(Numeric, default=0)
    sq_mm2 = Column(Numeric)
    core_count = Column(Integer, default=1)
    core_colors = Column(String(100))
    sheath_color = Column(String(50))
    customer_name = Column(String(100))
    due_date = Column(Date)
    customer_priority = Column(Integer, default=99)
    wip_matched_id = Column(Integer, ForeignKey("wip_inventory.wip_id"), nullable=True)
    line_speed_mpm = Column(Numeric)
    setup_time_min = Column(Numeric, default=0)
    estimated_duration_min = Column(Numeric)
    status = Column(
        String(20), default="planned"
    )  # planned, scheduled, in_progress, completed
    remarks = Column(Text)
    product_group = Column(String(50))
    voltage = Column(String(20))
    conductor_material = Column(String(10))
    stranding_type = Column(String(20))
    created_at = Column(DateTime, default=datetime.utcnow)

    # 배치 그룹 — 같은 (공정, SQ) 조합의 행들을 하나의 간트 블록으로 묶는 식별자
    # 예: "연선_120SQ_G01" → 연선 120SQ 1번 그룹
    batch_group = Column(String(50), index=True)

    # 원본 규격 텍스트 (SalesOrder.spec_raw 복사) — 고압 AWG/KCMIL 표기 보존용
    spec_raw = Column(String(200), nullable=True)

    # SM 재고 출력 — 이 배치 생산 시 발생하는 반제품 재고량
    wip_output_expected_m = Column(Numeric, default=0)  # 예상 SM재고 발생량
    wip_output_actual_m = Column(Numeric, nullable=True)  # 실제 SM재고 발생량
