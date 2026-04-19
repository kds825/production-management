"""시스 배치 응답에 spec_list 필드 추가 테스트.

왜 필요: 프론트 간트 블록이 같은 batch_group 으로 묶인 다중 SQ 수주를
한 블록 안에 표시해야 한다. 백엔드 응답에 spec_list(['50SQ','100SQ',...])
를 내려 블록 라벨을 구성할 수 있게 한다.

테스트 전략:
- 공용 ``db`` 픽스처(트랜잭션 롤백)를 사용하므로 commit 없이 검증.
- TestClient 대신 list_tasks() 라우트 함수에 ``db`` 를 직접 주입하여
  세션 가시성 문제를 회피한다.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.schedule_task import ScheduleTask
from app.presentation.routes.schedules import list_tasks


def _seed_sheath_group(
    db: Session,
    *,
    run_label: str,
    batch_group: str,
    equipment_code: str,
    sq_values: list[int],
    start: datetime,
) -> list[int]:
    """같은 batch_group 아래 여러 SQ 배치 + 동일 시스 task 를 생성한다.

    현실 모델: 시스 공정은 '굵은→얇은' 조합 생산이므로 batch_group 은 하나,
    그 안에 SQ 가 다른 행이 여러 개 들어간다.
    """
    batch_ids: list[int] = []
    for sq in sq_values:
        b = ProductionBatch(
            run_label=run_label,
            batch_seq=0,
            process_name="저압시스",
            sheath_color="흑",
            sq_mm2=sq,
            due_date=date(2026, 4, 13),
            drum_count=1,
            drum_length_m=500,
            total_length_m=500,
            conductor_material="CU",
            sales_order_id=f"SO-SL-{sq}",
            sales_order_line=1,
            batch_group=batch_group,
            status="scheduled",
        )
        db.add(b)
        db.flush()
        batch_ids.append(b.batch_id)

    # batch_group 당 하나의 간트 블록 = 하나의 ScheduleTask 로 본다 (대표 배치는 첫 번째).
    task = ScheduleTask(
        batch_id=batch_ids[0],
        equipment_code=equipment_code,
        start_datetime=start,
        end_datetime=start + timedelta(hours=2),
        status="scheduled",
        run_label=run_label,
        batch_group=batch_group,
    )
    db.add(task)
    db.flush()
    return batch_ids


def _seed_non_sheath(
    db: Session,
    *,
    run_label: str,
    batch_group: str,
    equipment_code: str,
    start: datetime,
) -> int:
    b = ProductionBatch(
        run_label=run_label,
        batch_seq=0,
        process_name="저압절연",
        sq_mm2=50,
        due_date=date(2026, 4, 13),
        drum_count=1,
        drum_length_m=500,
        total_length_m=500,
        conductor_material="CU",
        sales_order_id="SO-NS-1",
        sales_order_line=1,
        batch_group=batch_group,
        status="scheduled",
    )
    db.add(b)
    db.flush()
    task = ScheduleTask(
        batch_id=b.batch_id,
        equipment_code=equipment_code,
        start_datetime=start,
        end_datetime=start + timedelta(hours=1),
        status="scheduled",
        run_label=run_label,
        batch_group=batch_group,
    )
    db.add(task)
    db.flush()
    return b.batch_id


def test_sheath_task_response_has_spec_list(db: Session):
    """같은 batch_group 에 SQ 50/100 배치 2개 → spec_list == ['50SQ','100SQ'].

    실제 Supabase 데이터와 충돌을 피하기 위해 batch_group·run_label 에
    테스트 전용 프리픽스(TEST-*)를 사용한다. 해당 프리픽스 스코프 안의
    시스 응답만 검증한다.
    """
    run_label = "TEST-spec-list-sheath"
    test_group = "TEST-SHEATH-GRP-50-100"
    _seed_sheath_group(
        db,
        run_label=run_label,
        batch_group=test_group,
        equipment_code="SH-A120",
        sq_values=[50, 100],
        start=datetime(2026, 4, 13, 8, 0),
    )

    responses = list_tasks(
        date_from=None,
        date_to=None,
        process_type="저압시스",
        equipment_id="SH-A120",
        voltage=None,
        db=db,
    )
    # 테스트가 생성한 batch_group 의 시스 응답만 검사 (DB 기존 데이터 격리)
    sheath_responses = [r for r in responses if r.batch_group == test_group]
    assert sheath_responses, (
        f"시드한 batch_group '{test_group}' 에 해당하는 시스 task 응답 없음"
    )
    target = sheath_responses[0]
    spec_list = getattr(target, "spec_list", None)
    assert spec_list is not None, "spec_list 미존재 — 시스 응답에 필드 추가 필요"
    assert spec_list == ["50SQ", "100SQ"], f"spec_list 내용/순서 어긋남: {spec_list}"


def test_non_sheath_task_spec_list_is_none(db: Session):
    """비시스(저압절연) task 응답은 spec_list == None."""
    run_label = "test-spec-list-nonsheath"
    _seed_non_sheath(
        db,
        run_label=run_label,
        batch_group="INS-50_2026W15",
        equipment_code="EX-B100",
        start=datetime(2026, 4, 13, 8, 0),
    )

    responses = list_tasks(
        date_from=None,
        date_to=None,
        process_type="저압절연",
        equipment_id="EX-B100",
        voltage=None,
        db=db,
    )
    assert responses, "비시스 task 응답 없음"
    for r in responses:
        assert getattr(r, "spec_list", "sentinel") in (None, []), (
            f"비시스 응답에 spec_list 가 채워짐: {r.spec_list}"
        )
