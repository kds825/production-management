"""마스터 데이터 제네릭 CRUD API — 화이트리스트 기반"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import inspect

from app.infrastructure.database import get_db
from app.infrastructure.models import (
    CustomerMaster,
    EquipmentMaster,
    ItemMaster,
    ProcessRouting,
    SpeedMaster,
    DrumLotMaster,
    OperationCalendar,
    DecisionCriteria,
    ConstraintConfig,
)

router = APIRouter(prefix="/master", tags=["마스터데이터"])

# 화이트리스트: table_name → ORM 모델
TABLE_WHITELIST = {
    "customer_master": CustomerMaster,
    "equipment_master": EquipmentMaster,
    "item_master": ItemMaster,
    "process_routing": ProcessRouting,
    "speed_master": SpeedMaster,
    "drum_lot_master": DrumLotMaster,
    "operation_calendar": OperationCalendar,
    "decision_criteria": DecisionCriteria,
    "constraint_config": ConstraintConfig,
}


def _get_model(table_name: str):
    model = TABLE_WHITELIST.get(table_name)
    if not model:
        raise HTTPException(
            status_code=404,
            detail=f"테이블 '{table_name}' 없음. 허용: {list(TABLE_WHITELIST.keys())}",
        )
    return model


def _row_to_dict(row) -> dict:
    return {c.key: getattr(row, c.key) for c in inspect(row).mapper.column_attrs}


@router.get("/{table_name}")
def list_records(table_name: str, db: Session = Depends(get_db)):
    model = _get_model(table_name)
    rows = db.query(model).all()
    return {
        "table": table_name,
        "items": [_row_to_dict(r) for r in rows],
        "total": len(rows),
    }


@router.get("/{table_name}/{record_id}")
def get_record(table_name: str, record_id: str, db: Session = Depends(get_db)):
    model = _get_model(table_name)
    pk_col = inspect(model).mapper.primary_key[0]
    row = db.query(model).filter(pk_col == record_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="레코드 없음")
    return _row_to_dict(row)


@router.post("/{table_name}")
def create_record(table_name: str, body: dict, db: Session = Depends(get_db)):
    model = _get_model(table_name)
    row = model()
    for key, value in body.items():
        if hasattr(row, key):
            setattr(row, key, value)
    db.add(row)
    db.commit()
    db.refresh(row)
    return _row_to_dict(row)


@router.put("/{table_name}/{record_id}")
def update_record(
    table_name: str, record_id: str, body: dict, db: Session = Depends(get_db)
):
    model = _get_model(table_name)
    pk_col = inspect(model).mapper.primary_key[0]
    row = db.query(model).filter(pk_col == record_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="레코드 없음")
    for key, value in body.items():
        if hasattr(row, key):
            setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return _row_to_dict(row)
