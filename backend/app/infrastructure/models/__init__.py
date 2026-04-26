"""SQLAlchemy ORM 모델 — import 시 Base.metadata에 자동 등록"""

from app.infrastructure.models.customer_master import CustomerMaster
from app.infrastructure.models.equipment_master import EquipmentMaster
from app.infrastructure.models.item_master import ItemMaster
from app.infrastructure.models.process_routing import ProcessRouting
from app.infrastructure.models.speed_master import SpeedMaster
from app.infrastructure.models.drum_lot_master import DrumLotMaster
from app.infrastructure.models.operation_calendar import OperationCalendar
from app.infrastructure.models.decision_criteria import DecisionCriteria
from app.infrastructure.models.constraint_config import ConstraintConfig
from app.infrastructure.models.sales_order import SalesOrder
from app.infrastructure.models.wip_inventory import WipInventory
from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.schedule_task import ScheduleTask
from app.infrastructure.models.audit_log import AuditLog
from app.infrastructure.models.schedule_change_set import ScheduleChangeSet
from app.infrastructure.models.wip_upload_log import WipUploadLog
from app.infrastructure.models.solver_run import SolverRun
from app.infrastructure.models.solver_decision import SolverDecision
from app.infrastructure.models.decision_feedback import DecisionFeedback
from app.infrastructure.models.decision_card_telemetry import DecisionCardTelemetry

__all__ = [
    "CustomerMaster",
    "EquipmentMaster",
    "ItemMaster",
    "ProcessRouting",
    "SpeedMaster",
    "DrumLotMaster",
    "OperationCalendar",
    "DecisionCriteria",
    "ConstraintConfig",
    "SalesOrder",
    "WipInventory",
    "ProductionBatch",
    "ScheduleTask",
    "AuditLog",
    "ScheduleChangeSet",
    "WipUploadLog",
    "SolverRun",
    "SolverDecision",
    "DecisionFeedback",
    "DecisionCardTelemetry",
]
