from sqlalchemy import (
    Column,
    String,
    Integer,
    Numeric,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
)
from datetime import datetime
from app.infrastructure.database import Base


class SalesOrder(Base):
    __tablename__ = "sales_order"

    order_id = Column(String(30), primary_key=True)  # ERP 수주번호
    order_line = Column(Integer, primary_key=True, default=1)  # 동일 수주번호 내 라인
    order_status = Column(String(20), default="대기")  # 진행, 대기
    product_group = Column(String(50))
    voltage = Column(String(20))
    spec_raw = Column(String(100))  # 원본 규격 텍스트
    item_code = Column(String(20), ForeignKey("item_master.item_code"), nullable=True)
    customer_code = Column(
        String(20), ForeignKey("customer_master.customer_code"), nullable=True
    )
    customer_name = Column(String(100))
    due_date = Column(Date)
    due_type = Column(String(20))  # 도착기준, 출하기준
    customer_priority = Column(Integer, default=99)
    drum_length_m = Column(Numeric)  # 조장(M)
    drum_count = Column(Integer)  # 개수(ea)
    ordered_qty_m = Column(Numeric)  # 수주수량(M)
    self_plan_qty_m = Column(Numeric)  # 자체계획 수량
    outsource_qty_m = Column(Numeric)  # 외주 수량
    unit_price_krw = Column(Numeric)
    amount_krw = Column(Numeric)
    cu_weight_kg = Column(Numeric)
    al_weight_kg = Column(Numeric)
    core_count = Column(Integer, default=1)
    core_colors = Column(String(100))
    sheath_color = Column(String(50))
    neutral_wire = Column(String(50))
    use_wip = Column(Boolean, default=False)
    wip_type = Column(String(20))  # 연선, 절연, 연합, 완제품
    actual_length_m = Column(Numeric)
    is_outsourced = Column(Boolean, default=False)
    run_label = Column(String(50))  # 어떤 계획 실행에 속하는지
    created_at = Column(DateTime, default=datetime.utcnow)
