from sqlalchemy import Column, String, Integer, Numeric, Text, Date

from app.infrastructure.database import Base


class OperationCalendar(Base):
    __tablename__ = "operation_calendar"

    calendar_id = Column(Integer, primary_key=True, autoincrement=True)
    rule_code = Column(
        String(20), nullable=False, index=True
    )  # CAL-STD, CAL-FRI, CAL-MON-EDU, CAL-HOL
    rule_name = Column(String(100), nullable=False)
    day_of_week = Column(String(50))  # Mon~Thu, Friday, specific dates
    working_hours = Column(Numeric)  # available hours
    start_time = Column(String(10))  # 08:00, 06:00
    end_time = Column(String(10))  # 08:00(next), 24:00
    deduction_hours = Column(Numeric, default=0)  # 차감시간 (안전교육 2hr)
    specific_date = Column(Date, nullable=True)  # 특정 날짜 (공휴일 등)
    notes = Column(Text)
