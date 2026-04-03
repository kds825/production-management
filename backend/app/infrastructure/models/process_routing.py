from sqlalchemy import Column, String

from app.infrastructure.database import Base


class ProcessRouting(Base):
    __tablename__ = "process_routing"

    routing_code = Column(String(20), primary_key=True)  # RT-001
    routing_name = Column(String(100), nullable=False)
    process_1 = Column(String(30))  # 신선
    process_2 = Column(String(30))  # 연선
    process_3 = Column(String(30))  # T/P / 저압절연 / 고압절연
    process_4 = Column(String(30))  # 저압절연(T/P 후) / 연합 / 저압시스 / 고압시스
    process_5 = Column(String(30))  # 연합 / 저압시스 / null
    process_6 = Column(String(30))  # 저압시스(다심 고내화) / null
