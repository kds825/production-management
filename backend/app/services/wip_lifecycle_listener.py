"""ProductionBatch after_insert listener — WIP 예상재공 자동 생성.

설계서: docs/specs/2026-04-18-wip-lifecycle-design.md §7.

Eng review 블로커 #1: 헤더 배치(batch_seq=-1 AND process_name="연선")에만 fire.
Eng review Medium #4: ON CONFLICT DO NOTHING 으로 IntegrityError 전파 차단.

Bulk insert 금지: Session.bulk_save_objects() / bulk_insert_mappings() 는 ORM event
를 bypass. Task 7 의 bulk API 가드와 짝으로 동작.
"""

from __future__ import annotations

from sqlalchemy import event, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.wip_inventory import WipInventory

# 헤더 게이트 — Stage 1 드럼 생산만 잉여 인정
_WIP_GATE_PROCESSES = frozenset({"연선"})


def _derive_process_stage(process_name: str | None) -> str:
    """ProductionBatch.process_name → WipInventory.process_stage 매핑."""
    if not process_name:
        return ""
    if "연선" in process_name:
        return "연선재고"
    if "절연" in process_name:
        return "절연재고"
    if "연합" in process_name or "T/P" in process_name:
        return "연합재고"
    return process_name


def _auto_create_expected_wip(mapper, connection, batch):
    """ProductionBatch INSERT 시 예상 WIP auto-create.

    게이트 (Eng review 블로커 #1):
      - batch_seq == -1 (그룹 헤더만)
      - process_name in {"연선"}
      - wip_output_expected_m > 0

    INSERT 정책:
      - connection.execute + PostgreSQL ON CONFLICT (source_batch_id) DO NOTHING
      - ORM session 우회 → WipInventory 쪽 event 는 발화하지 않음 (의도)
    """
    if batch.batch_seq != -1:
        return
    if batch.process_name not in _WIP_GATE_PROCESSES:
        return
    expected = float(batch.wip_output_expected_m or 0)
    if expected <= 0:
        return

    stmt = (
        pg_insert(WipInventory.__table__)
        .values(
            process_stage=_derive_process_stage(batch.process_name),
            cross_section=batch.sq_mm2,
            voltage_class=batch.voltage,
            material=batch.conductor_material,
            core_colors=getattr(batch, "core_colors", None),
            length_m=expected,
            count=1,
            total_length_m=expected,
            expected_length_m=expected,
            status="예상",
            source_batch_id=batch.batch_id,
            run_label=batch.run_label,
        )
        .on_conflict_do_nothing(
            index_elements=["source_batch_id"],
            # G1 UNIQUE 가 partial index (source_batch_id IS NOT NULL) 이므로
            # ON CONFLICT 에도 동일 WHERE 절 명시 필요.
            index_where=text("source_batch_id IS NOT NULL"),
        )
    )
    connection.execute(stmt)


_registered = False


def register_wip_listener() -> None:
    """SQLAlchemy after_insert listener 등록. 앱 시작 시 1회 호출.

    재호출 시 no-op. Task 7 에서 infrastructure/database.py 상단에서 호출.
    """
    global _registered
    if _registered:
        return
    event.listen(ProductionBatch, "after_insert", _auto_create_expected_wip)
    _registered = True
