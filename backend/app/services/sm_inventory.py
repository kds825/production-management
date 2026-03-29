"""SM(반제품) 재고 관리 — 예상/실적 업데이트 + 차이 자동 보정"""

from sqlalchemy.orm import Session

from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.wip_inventory import WipInventory
from app.services.audit_logger import log_decision


def update_wip_actual(wip_id: int, actual_length_m: float, db: Session) -> dict:
    """예상 SM재고를 실적으로 업데이트.

    expected_length_m 기준으로 variance를 계산한다.
    expected_length_m이 없으면 기존 total_length_m을 폴백으로 사용하여
    초기 데이터 마이그레이션 전에도 동작하도록 한다.
    """
    wip = db.query(WipInventory).filter(WipInventory.wip_id == wip_id).first()
    if not wip:
        return {"error": f"WIP {wip_id} not found"}

    old_expected = float(wip.expected_length_m or wip.total_length_m or 0)
    wip.actual_length_m = actual_length_m
    wip.total_length_m = actual_length_m  # 활성 수량 업데이트
    wip.variance_m = actual_length_m - old_expected
    wip.status = "실적"

    result = {
        "wip_id": wip_id,
        "expected": old_expected,
        "actual": actual_length_m,
        "variance": float(wip.variance_m),
        "shortage": max(0, old_expected - actual_length_m),
    }

    # 부족이 발생하고 수주에 매칭된 WIP이면 추가 생산 필요 신호 기록
    if wip.variance_m < 0 and wip.matched_order_id:
        shortage = abs(float(wip.variance_m))
        result["additional_batch_needed"] = True
        result["shortage_m"] = shortage
        result["matched_order_id"] = wip.matched_order_id

        log_decision(
            db=db,
            run_label=wip.run_label or "manual",
            stage="stage1",
            action_type="wip_shortage_detected",
            constraints_applied=[
                {
                    "id": "2-1",
                    "name": "재공 활용",
                    "result": "shortage",
                    "detail": (
                        f"WIP#{wip_id}: 예상 {old_expected}m → "
                        f"실적 {actual_length_m}m, 부족 {shortage}m"
                    ),
                }
            ],
            reason=(
                f"SM재고 부족 감지: {shortage}m 추가 생산 필요 "
                f"(수주 {wip.matched_order_id})"
            ),
        )

    db.flush()
    return result


def create_shortage_batches(run_label: str, db: Session) -> dict:
    """부족분에 대한 추가 생산 배치 자동 생성.

    variance_m < 0 인 실적 WIP을 스캔하여 부족량만큼 신규 배치를 생성한다.
    1m 미만의 오차는 절삭 손실 범위로 보고 무시한다.
    """
    shortages = (
        db.query(WipInventory)
        .filter(
            WipInventory.run_label == run_label,
            WipInventory.variance_m < 0,
            WipInventory.status == "실적",
        )
        .all()
    )

    created = 0
    for wip in shortages:
        shortage = abs(float(wip.variance_m))
        if shortage < 1:  # 1m 미만 오차는 무시
            continue

        # 원래 이 WIP을 사용하려던 배치 정보를 참조해 배치 속성을 최대한 복원
        original_batch = None
        if wip.source_batch_id:
            original_batch = (
                db.query(ProductionBatch)
                .filter(ProductionBatch.batch_id == wip.source_batch_id)
                .first()
            )

        batch = ProductionBatch(
            run_label=run_label,
            sales_order_id=wip.matched_order_id,
            process_name=(
                original_batch.process_name
                if original_batch
                else wip.process_stage or "연선"
            ),
            total_length_m=shortage,
            sq_mm2=wip.cross_section,
            core_count=1,
            core_colors=wip.core_colors,
            customer_name="SM재고 부족분",
            status="planned",
            remarks=f"SM부족 보정: WIP#{wip.wip_id} 부족 {shortage}m",
            conductor_material=wip.material,
            voltage=wip.voltage_class,
        )
        db.add(batch)
        created += 1

    db.flush()
    return {"shortage_batches_created": created}


def get_wip_summary(run_label: str, db: Session) -> dict:
    """SM재고 요약 — 예상/실적/차이/매칭 현황"""
    wips = db.query(WipInventory).filter(WipInventory.run_label == run_label).all()

    return {
        "total": len(wips),
        "expected": sum(1 for w in wips if w.status == "예상"),
        "confirmed": sum(1 for w in wips if w.status == "실적"),
        "matched": sum(1 for w in wips if w.matched_order_id),
        "shortages": sum(1 for w in wips if w.variance_m and float(w.variance_m) < 0),
        "total_shortage_m": sum(
            abs(float(w.variance_m))
            for w in wips
            if w.variance_m and float(w.variance_m) < 0
        ),
        "items": [
            {
                "wip_id": w.wip_id,
                "process": w.process_stage,
                "spec": w.spec,
                "sq": float(w.cross_section) if w.cross_section else None,
                "expected_m": float(w.expected_length_m)
                if w.expected_length_m
                else None,
                "actual_m": float(w.actual_length_m) if w.actual_length_m else None,
                "total_m": float(w.total_length_m) if w.total_length_m else None,
                "variance_m": float(w.variance_m) if w.variance_m else None,
                "status": w.status,
                "matched_order": w.matched_order_id,
                "colors": w.core_colors,
            }
            for w in wips
        ],
    }
