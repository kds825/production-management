from sqlalchemy import Column, String, Integer, Numeric, Boolean, ForeignKey

from app.infrastructure.database import Base


class ItemMaster(Base):
    __tablename__ = "item_master"

    item_code = Column(String(20), primary_key=True)
    item_name = Column(String(100))
    spec_abbr = Column(String(100))  # 규격약칭
    product_group = Column(String(50))  # TFR-CV-WB, URD 100% etc
    voltage = Column(String(20))  # 0.6/1, 6/10, 22.9, 35
    conductor_material = Column(String(10))  # CU, AL
    wire_diameter = Column(Numeric)  # 소선경 mm
    wire_count = Column(Integer)  # 소선수
    cross_section = Column(Numeric)  # SQ mm²
    core_count = Column(Integer, default=1)
    stranding_type = Column(String(20))  # 압축, 반압축, 원형, 수밀
    insulation_type = Column(String(50))  # XLPE, PE, PVC, TR-XLPE
    sheath_type = Column(String(50))  # PVC, 난연PVC, HFPO, LLDPE
    core_colors = Column(String(100))  # 갈/흑/녹황
    routing_code = Column(String(20), ForeignKey("process_routing.routing_code"))
    drum_max_length = Column(Numeric)  # 드럼최대조장(m)
    lot_length = Column(Numeric)  # 틀단위(m)
    extra_allowance = Column(Numeric)  # 여척기준(m)
    daily_production = Column(Numeric)  # 일생산량(m)
    unit_weight = Column(Numeric)  # 단위중량 kg/m
    require_sample = Column(Boolean, default=False)
    is_outsourced = Column(Boolean, default=False)
