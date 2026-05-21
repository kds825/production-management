"""schedules/list.py — 스케줄 GET 조회 엔드포인트.

핸들러:
- GET /tasks                    → list_tasks
- GET /versions                 → list_versions
- GET /versions/{version_id}    → get_version

`__init__.py` 가 본 모듈 router 를 prefix="/schedules" 패키지 router 에
include 한다.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.infrastructure.database import get_db
from app.infrastructure.models.production_batch import (
    ProductionBatch as ProductionBatchModel,
)
from app.infrastructure.models.schedule_task import (
    ScheduleTask as ScheduleTaskModel,
)
from app.presentation.routes.schedules._shared import (
    VersionDetailResponse,
    VersionSummaryResponse,
    _db_task_to_response,
    _versions,
)
from app.presentation.schemas import ScheduleTaskResponse


router = APIRouter()


@router.get("/tasks", response_model=list[ScheduleTaskResponse])
def list_tasks(
    date_from: str | None = Query(None, description="시작일 YYYY-MM-DD"),
    date_to: str | None = Query(None, description="종료일 YYYY-MM-DD"),
    process_type: str | None = Query(
        None, description="공정 필터 (연선,저압절연,저압시스 등)"
    ),
    equipment_id: str | None = Query(None, description="설비 필터"),
    voltage: str | None = Query(None, description="전압 필터 (저압/고압)"),
    run_label: str | None = Query(
        None,
        description=(
            "run_label 필터. 미지정 시 최신 run_label (schedule_task.created_at"
            " 최대) 자동 선택 — 여러 버전의 task 가 이중 렌더되는 것을 방지."
        ),
    ),
    db: Session = Depends(get_db),
) -> list[ScheduleTaskResponse]:
    """스케줄 작업 목록 조회 (시작 시간 오름차순).

    Stage 2 auto-scheduling 결과를 PostgreSQL에서 읽어 반환한다.
    date_from/date_to/process_type/equipment_id/voltage/run_label 쿼리
    파라미터로 Gantt 뷰에 필요한 구간만 필터링하여 전송량을 줄인다.
    DB에 schedule_task 레코드가 없을 경우 인메모리 store로 폴백하여
    개발 초기 샘플 데이터도 계속 볼 수 있다.

    run_label 동작:
    - 명시적 값: 해당 run 의 task 만 반환
    - 미지정: 가장 최근에 생성된 run 을 자동 선택 (이중 표시 방지)
    """
    # run_label 자동 해결: 미지정 시 최신 run (MAX(created_at) 기준) 선택.
    # `test-%` 접두어는 conftest 의 savepoint rollback 이 실패했을 때 leak 되는
    # 테스트 데이터 (2026-05-21 조사: B100EXT 4/25 placeholder 가 1행만 떠서
    # ERP 업로드 직후 stage2 전에 dummy 블록으로 그려짐). 명시 지정이 아닌
    # 자동 선택에서는 항상 제외해 사용자 화면을 오염시키지 않는다.
    effective_run_label = run_label
    if not effective_run_label:
        latest_row = (
            db.query(ScheduleTaskModel.run_label)
            .filter(
                ScheduleTaskModel.run_label.isnot(None),
                ~ScheduleTaskModel.run_label.like("test-%"),
            )
            .order_by(ScheduleTaskModel.created_at.desc())
            .first()
        )
        if latest_row:
            effective_run_label = latest_row[0]

    q = db.query(ScheduleTaskModel, ProductionBatchModel).join(
        ProductionBatchModel,
        ScheduleTaskModel.batch_id == ProductionBatchModel.batch_id,
    )

    # run_label 스코프 (이중 렌더 방지)
    if effective_run_label:
        q = q.filter(ScheduleTaskModel.run_label == effective_run_label)

    # WIP 완료 배치는 간트에 미표시 — 재고로 대체된 공정이므로 스케줄 불필요
    q = q.filter(ProductionBatchModel.status != "wip_complete")

    # 날짜 범위 필터 — 태스크 시간대가 윈도우와 겹치는 것 포함
    # (start < window_end AND end > window_start 조건으로 부분 겹침도 포함)
    if date_from:
        dt_from = datetime.fromisoformat(date_from)
        q = q.filter(ScheduleTaskModel.end_datetime > dt_from)
    if date_to:
        # "YYYY-MM-DD" 형식이면 해당 날 끝까지 포함 (23:59:59)
        dt_to_str = date_to if "T" in date_to else f"{date_to}T23:59:59"
        dt_to = datetime.fromisoformat(dt_to_str)
        q = q.filter(ScheduleTaskModel.start_datetime < dt_to)

    # 공정명 필터
    if process_type:
        q = q.filter(ProductionBatchModel.process_name == process_type)

    # 설비 코드 필터
    if equipment_id:
        q = q.filter(ScheduleTaskModel.equipment_code == equipment_id)

    # 전압 필터 — 저압: 0.6kV 계열, 고압: 22.9kV / 35kV 계열
    if voltage == "저압":
        q = q.filter(ProductionBatchModel.voltage.contains("0.6"))
    elif voltage == "고압":
        q = q.filter(
            ProductionBatchModel.voltage.contains("22.9")
            | ProductionBatchModel.voltage.contains("35")
        )

    db_tasks = q.order_by(ScheduleTaskModel.start_datetime).all()

    # batch_group별 volume 계산:
    # 헤더(batch_seq=-1)가 있으면 헤더의 total_length_m = 실제 생산지시(틀단위) 수량
    # 없으면 개별 수주(batch_seq>=0) 합산
    header_rows = (
        db.query(
            ProductionBatchModel.batch_group,
            ProductionBatchModel.total_length_m,
            ProductionBatchModel.drum_count,
        )
        .filter(
            ProductionBatchModel.batch_group.isnot(None),
            ProductionBatchModel.batch_seq == -1,
        )
        .all()
    )
    header_volumes = {bg: float(vol or 0) for bg, vol, _ in header_rows}
    header_drum_counts: dict[str, int] = {bg: int(dc or 1) for bg, _, dc in header_rows}

    order_stats_rows = (
        db.query(
            ProductionBatchModel.batch_group,
            func.sum(ProductionBatchModel.total_length_m),
            func.count(ProductionBatchModel.batch_id),
        )
        .filter(
            ProductionBatchModel.batch_group.isnot(None),
            ProductionBatchModel.batch_seq >= 0,
        )
        .group_by(ProductionBatchModel.batch_group)
        .all()
    )
    # 헤더가 있으면 헤더 값, 없으면 수주 합산
    group_volumes = {
        bg: header_volumes.get(bg, float(vol or 0)) for bg, vol, _ in order_stats_rows
    }
    # 헤더만 있고 order가 없는 그룹도 포함
    for bg, vol in header_volumes.items():
        if bg not in group_volumes:
            group_volumes[bg] = vol
    group_counts = {bg: int(cnt or 1) for bg, _, cnt in order_stats_rows}

    # 색상교체 시간 계산을 위해 같은 설비의 직전 배치 sheath_color 조회
    prev_colors: dict[str, str] = {}  # equipment_code → 직전 batch sheath_color
    color_change_map: dict[int, int] = {}  # task_id → color_change_min
    for task_row, batch_row in db_tasks:
        eq = task_row.equipment_code
        curr_color = (batch_row.sheath_color or "").strip()
        if batch_row.process_name in ("저압시스", "고압시스", "HFCO시스"):
            prev_color = prev_colors.get(eq, "")
            if prev_color and curr_color and prev_color != curr_color:
                color_change_map[task_row.task_id] = (
                    120  # default, could query SpeedMaster
                )
        prev_colors[eq] = curr_color

    # spec_list 계산 — 시스 batch_group 에 묶인 SQ 규격 목록 (오름차순, 중복 제거)
    # 왜 bulk 1-query: task 별 N+1 쿼리 방지. 시스 batch_group 만 대상이므로
    # 응답에 포함된 시스 태스크의 batch_group 집합만 스캔한다.
    sheath_groups: set[str] = {
        t.batch_group
        for t, _ in db_tasks
        if t.batch_group and (t.equipment_code or "").startswith("SH-")
    }
    group_spec_list: dict[str, list[str]] = {}
    if sheath_groups:
        sq_rows = (
            db.query(
                ProductionBatchModel.batch_group,
                ProductionBatchModel.sq_mm2,
            )
            .filter(
                ProductionBatchModel.batch_group.in_(sheath_groups),
                ProductionBatchModel.sq_mm2.isnot(None),
                # batch_seq >= 0: 헤더(-1) 제외 — 헤더는 대표 SQ 만 갖고 있어
                # 실제 묶인 수주 규격 목록을 왜곡한다.
                ProductionBatchModel.batch_seq >= 0,
            )
            .all()
        )
        tmp: dict[str, set[int]] = {}
        for bg, sq in sq_rows:
            if sq is None:
                continue
            tmp.setdefault(bg, set()).add(int(sq))
        group_spec_list = {
            bg: [f"{sq}SQ" for sq in sorted(sqs)] for bg, sqs in tmp.items()
        }

    def _spec_list_for(task: ScheduleTaskModel) -> list[str] | None:
        """시스 task 에만 spec_list 부여, 비시스는 None."""
        if not (task.equipment_code or "").startswith("SH-"):
            return None
        return group_spec_list.get(task.batch_group) or []

    # S6 #12: 매뉴얼 조정된 batch_id set — SolverDecision.manual_override_change_set_id
    # 가 link 한 ScheduleChangeSet 의 snapshot key (task_id str) → batch_id 매핑.
    # bulk 3-query (decision link / change_set snapshot / task→batch).
    # Local import: 모듈 최상위 import 는 formatter 가 unused 로 제거함.
    from app.infrastructure.models.schedule_change_set import (
        ScheduleChangeSet as _CS,
    )
    from app.infrastructure.models.solver_decision import SolverDecision as _SD

    manual_cs_ids = {
        cs_id
        for (cs_id,) in db.query(_SD.manual_override_change_set_id)
        .filter(_SD.manual_override_change_set_id.isnot(None))
        .distinct()
        .all()
        if cs_id is not None
    }
    manual_task_ids: set[int] = set()
    if manual_cs_ids:
        for cs_row in (
            db.query(_CS.snapshot_before, _CS.snapshot_after)
            .filter(_CS.change_set_id.in_(manual_cs_ids))
            .all()
        ):
            for snapshot in cs_row:
                if not snapshot:
                    continue
                for k in snapshot.keys():
                    if isinstance(k, str) and k.isdigit():
                        manual_task_ids.add(int(k))
    manual_batch_ids: set[int] = set()
    if manual_task_ids:
        rows = (
            db.query(ScheduleTaskModel.batch_id)
            .filter(ScheduleTaskModel.task_id.in_(manual_task_ids))
            .all()
        )
        manual_batch_ids = {r[0] for r in rows if r[0] is not None}

    return [
        _db_task_to_response(
            task,
            batch,
            group_volume_m=group_volumes.get(task.batch_group),
            group_order_count=group_counts.get(task.batch_group, 1),
            color_change_min=color_change_map.get(task.task_id, 0),
            lot_count=header_drum_counts.get(task.batch_group),
            spec_list=_spec_list_for(task),
            is_manually_adjusted=batch.batch_id in manual_batch_ids,
        )
        for task, batch in db_tasks
    ]


@router.get("/versions", response_model=list[VersionSummaryResponse])
def list_versions() -> list[VersionSummaryResponse]:
    """저장된 모든 버전 목록 조회 (최신 순)."""
    return [
        VersionSummaryResponse(
            id=v["id"],
            label=v["label"],
            created_at=v["created_at"],
            task_count=len(v["tasks"]),
        )
        for v in reversed(_versions)
    ]


@router.get("/versions/{version_id}", response_model=VersionDetailResponse)
def get_version(version_id: str) -> VersionDetailResponse:
    """특정 버전의 전체 작업 목록 조회."""
    version = next((v for v in _versions if v["id"] == version_id), None)
    if version is None:
        raise HTTPException(
            status_code=404,
            detail=f"버전 '{version_id}'를 찾을 수 없습니다.",
        )
    return VersionDetailResponse(
        id=version["id"],
        label=version["label"],
        created_at=version["created_at"],
        tasks=version["tasks"],
    )
