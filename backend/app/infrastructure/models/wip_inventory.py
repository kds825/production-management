from sqlalchemy import Column, String, Integer, Numeric, DateTime, ForeignKey
from datetime import datetime
from app.infrastructure.database import Base


class WipInventory(Base):
    __tablename__ = "wip_inventory"

    wip_id = Column(Integer, primary_key=True, autoincrement=True)
    process_stage = Column(String(30))  # 연선재고, 절연재고, 연합재고, 완제품
    voltage_class = Column(String(20))  # 저압, 고압
    material = Column(String(10))  # CU, AL
    product_name = Column(String(100))
    spec = Column(String(100))  # 200SQ, 50SQ, 1250kcmil
    core = Column(String(20))  # CORE 수 (연합 재공용, 예: 3, 4, 7)
    cross_section = Column(Numeric)
    length_m = Column(Numeric)
    count = Column(Integer, default=1)
    total_length_m = Column(Numeric)
    core_colors = Column(String(100))
    wire_diameter = Column(Numeric)
    wire_count = Column(Integer)
    status = Column(
        String(20), default="사용가능"
    )  # 사용가능, 사용완료, 스크랩, 예상, 실적
    run_label = Column(String(50))
    created_at = Column(DateTime, default=datetime.utcnow)

    # SM 재고 라이프사이클 — 예상→실적 전환 및 차이 추적
    expected_length_m = Column(Numeric)  # 최초 예상 수량
    actual_length_m = Column(Numeric)  # 실제 확정 수량
    # 차이값: actual - expected, 음수 = 부족
    variance_m = Column(Numeric)
    # 어떤 배치에서 발생한 SM재고인지 역추적
    source_batch_id = Column(
        Integer,
        ForeignKey("production_batch.batch_id"),
        nullable=True,
    )
    matched_order_id = Column(String(30), nullable=True)  # 어떤 수주에 매칭됨
