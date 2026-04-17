"""자동 스케줄링 엔진 — 납기역산 + 그리디 배치

── Task #7 조사 결과 ─────────────────────────────────────────────────
1. batch_seq 정렬:
   - 61연선의 7연선 코어 배치(batch_seq=0)가 본 배치(batch_seq=1)보다 먼저
     스케줄링되도록 batches query에서 batch_seq.asc()를 포함한다 (line 78).
   - 이는 predecessor_map을 통해 코어→본 배치 선행관계를 올바르게 구성하기 위함이다.

2. 공정 간 선행관계:
   - _PREDECESSOR_PROCESS dict가 연선→절연→시스 파이프라인을 강제한다.
   - process_end_by_sq로 같은 SQ의 앞 공정 종료 시각을 추적하여 후공정 시작을 지연시킨다.
   - A100/A120 시스 배치는 저압절연 첫 번째 드럼 출력 후 시작 (파이프라인 겹침).

3. 제한사항:
   - 프론트엔드 간트에서의 수동 블록 이동 시에는 이 파이프라인 제약이 재적용되지 않는다.
   - scheduleStore.ts의 moveTask는 같은 order_id의 후공정만 연동하며,
     cross-equipment cascade는 미구현 상태이다.
"""

from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.schedule_task import ScheduleTask
from app.infrastructure.models.equipment_master import EquipmentMaster
from app.infrastructure.models.speed_master import SpeedMaster
from app.infrastructure.models.constraint_config import ConstraintConfig
from app.infrastructure.models.drum_lot_master import DrumLotMaster
from app.domain.constants import PROCESS_ORDER
from app.services.calendar_engine import (
    calculate_end_datetime,
    calculate_start_datetime,
)
from app.services.audit_logger import log_decision
from app.exceptions import SchedulerOverlapError

# WIP 공정 스킵 매핑: process_stage → 간트 미배치 공정 목록
# batch_grouping._WIP_COVERED_PROCESSES와 동일한 기준 — Phase 2에서 대부분 걸러지지만
# 증분 업데이트 등으로 잔존 배치가 있을 경우의 안전망으로 유지한다.
_WIP_SKIP_PROCESSES: dict[str, set[str]] = {
    "연선재고": {"신선", "연선"},
    "절연재고": {"신선", "연선", "저압절연", "고압절연"},
    "연합재고": {"신선", "연선", "저압절연", "고압절연", "연합", "T/P"},
    "완제품": {
        "신선",
        "연선",
        "저압절연",
        "고압절연",
        "연합",
        "T/P",
        "저압시스",
        "고압시스",
    },
}

# 용접 시간 기본값 (4-4): constraint_config params_json에서 읽을 때 없으면 사용
_DEFAULT_WELDING_MIN = 30

# 시스 재질 → 설비 라우팅 규칙 (10-3)
# 값은 equipment_code prefix 또는 특수 라우팅 키
_SHEATH_ROUTING = {
    "HFPO": "HFPO",  # HFPO 전용 라우팅 (설비 선택 시 HFPO 계열만)
    "PVC": "PVC",  # 표준 PVC 라우팅
    "LLDPE": "A150",  # LLDPE → A150 설비 고정
}

# 공정 간 선행/후행 관계 — 공정 프로세스도 기준
# schedules.py cascade_preview 와 공유하는 단일 진실 공급원(single source of truth)
#
# 저압(0.6/1kV):
#   1Core:    연선 → 저압절연(B100) → 시스(A100/A120)
#   2~4Core:  연선 → 저압절연(B100) → 연합(4BO) → 시스(A100/A120)
# 고압(6/10kV, 22.9kV):
#   1Core:    연선 → 고압절연(CV) → T/P → 시스
#   2~4Core:  연선 → 고압절연(CV) → T/P → 연합(4BO) → 시스
PREDECESSOR_PROCESS: dict[str, str] = {
    "저압절연": "연선",
    "고압절연": "연선",
    "연합": "저압절연",  # 연합은 절연 완료 후 (다심 케이블 연합 공정)
    "T/P": "저압절연",  # T/P(동테이프)는 절연 완료 후
    "저압시스": "저압절연",  # 1Core는 절연→시스 직행, 다심은 연합 경유하지만 절연 기준
    "고압시스": "고압절연",
}


def _is_core_group(group_key: str) -> bool:
    """CORE 또는 AL-CORE 그룹 키인지 판별 (CU/AL 공통)."""
    return group_key.startswith("CORE-") or group_key.startswith("AL-CORE-")


def _st_sq(group_key: str) -> int:
    """ST-{sq}-... 그룹 키에서 SQ 정수를 추출한다. 실패 시 0."""
    try:
        return int(group_key.split("-")[1])
    except (IndexError, ValueError):
        return 0


def _group_earliest_due(batches: list) -> date:
    """배치 목록에서 가장 이른 납기일을 반환한다. 납기 없으면 date.max."""
    dates = [b.due_date for b in batches if b.due_date is not None]
    return min(dates) if dates else date.max


def _is_sheath_group(group_key: str, batches: list) -> bool:
    """시스 공정 그룹 판별 — batch_group prefix + 대표 배치 공정명 폴백.

    create_batches 가 할당한 A120_*/A100_* 외에도, 수동 시드로 batch_group
    이 비어 있는 시스 배치도 체인 정렬 대상에 포함시킨다.
    """
    if group_key.startswith("A120_") or group_key.startswith("A100_"):
        return True
    if batches and batches[0].process_name in ("저압시스", "고압시스"):
        return True
    return False


def _sheath_group_color_rank(batches: list) -> int:
    """대표 배치의 sheath_color 로부터 색상 순위를 반환 (A'' 접근안).

    순위는 batch_grouping._SHEATH_COLOR_RANK 를 참조 (단일 소스 유지).
    순환 import 회피를 위해 함수 내부에서 지연 로드한다.
    """
    # 지연 import: batch_grouping 모듈의 비공개 상수를 참조하되 top-level
    # 순환 의존성과 린터의 "unused import 제거" 부작용을 동시에 방지한다.
    from app.services.batch_grouping import _SHEATH_COLOR_RANK

    if not batches:
        return 99
    color = (batches[0].sheath_color or "").strip() or "기타"
    return _SHEATH_COLOR_RANK.get(color, 99)


def _sheath_group_due_week_int(batches: list) -> int:
    """대표 배치의 납기 반-주차(H1/H2) 버킷.

    batch_grouping 의 H1(월~수)/H2(목~일) 분할과 정합시키기 위해 주차 × 2
    해상도로 인코딩한다. 같은 색상 안에서 H1 이 H2 보다 먼저 스케줄링되고,
    다른 색상 간에는 earliest_due 가 3차 tiebreaker 로 동작한다.
    """
    due = _group_earliest_due(batches)
    if due == date.max:
        return 9999999
    yr, wk, wday = due.isocalendar()
    half = 0 if wday <= 3 else 1  # H1 → 0, H2 → 1
    return (yr * 100 + wk) * 2 + half


def _extract_core_main_sq(group_key: str) -> int | None:
    """CORE/AL-CORE 그룹 키에서 main SQ를 추출한다.

    "CORE-633-35kV" → 633, "AL-CORE-633-35kV" → 633
    """
    try:
        parts = group_key.split("-")
        if group_key.startswith("AL-CORE-"):
            return int(parts[2])  # AL-CORE-{sq}-...
        if group_key.startswith("CORE-"):
            return int(parts[1])  # CORE-{sq}-...
    except (IndexError, ValueError):
        pass
    return None


def auto_schedule(
    run_label: str, db: Session, *, use_cpsat: bool = False, **kwargs
) -> dict:
    """겹침 재시도 2회 포함 스케줄 생성 래퍼 (greedy / CP-SAT 공용).

    동작:
      1. 전략 선택
         - ``use_cpsat=False`` (기본): ``_run_optimization_once`` 만 실행 (greedy)
         - ``use_cpsat=True``        : ``cp_sat_schedule`` 먼저 시도, infeasible
           /타임아웃 시 같은 retry 사이클 안에서 greedy 폴백
      2. ``constraint_checker.validate_all`` 로 겹침 검증
      3. 겹침 있으면 최대 2회 재시도
         - CP-SAT 경로는 시도 번호를 ``random_seed`` 로 전달해 결정론적 동일 해가
           반복되지 않도록 변동 (Fix P0-4B)
         - 재시도 전 ``_purge_run_tasks`` 로 기존 태스크/감사 로그 정리
      4. 2회 재시도 후에도 겹침이면 ``SchedulerOverlapError`` 발생 (DB rollback)

    왜 단일 public entry 로 통합했는가:
      기존에는 plan_pipeline 이 ``cp_sat_schedule`` 을 직접 호출하여
      retry+validate 루프를 완전히 우회했음 (Review B CRITICAL). 같은 실패 모드를
      두 경로가 공유하도록 여기서 묶어 안전망을 단일 지점에 집약한다.

    왜 지역 import인가: constraint_checker / cp_sat_optimizer 는 monkeypatch
    시나리오에서 실시간 lookup 이 필요하므로 함수 내부에서 import 하여
    패치된 바인딩을 그대로 사용한다.
    """
    from app.services import constraint_checker

    MAX_RETRIES = 2
    result: dict = {}
    violations: list[dict] = []

    for attempt in range(MAX_RETRIES + 1):
        if use_cpsat:
            # CP-SAT 우선 시도. random_seed 를 시도 번호로 변동 → 동일 해 반복 방지.
            from app.services.cp_sat_optimizer import cp_sat_schedule

            result = cp_sat_schedule(run_label, db, random_seed=attempt, **kwargs)
            # INFEASIBLE / UNKNOWN / timeout → 같은 시도 사이클 내 greedy 폴백.
            # (plan_pipeline 에서 별도 폴백을 수행했으나, retry 래퍼 안으로 끌어와
            # 폴백 경로도 동일한 validate+retry 안전망을 공유하도록 한다.)
            if result.get("solver_status") not in ("OPTIMAL", "FEASIBLE"):
                result.setdefault("warnings", []).append(
                    "CP-SAT 솔버 미해결 — 그리디 폴백으로 전환합니다"
                )
                _purge_run_tasks(db, run_label)
                result = _run_optimization_once(run_label, db, **kwargs)
        else:
            result = _run_optimization_once(run_label, db, **kwargs)

        violations = constraint_checker.validate_all(run_label, db)

        if not constraint_checker.has_overlap(violations):
            result["overlap_alert"] = False
            return result

        overlap_hits = [v for v in violations if v.get("constraint_id") == "overlap"]

        # 재시도 전 audit 기록 + 현재 run 의 기존 태스크 정리
        # audit 실패는 스케줄링 실패로 연결하지 않는다 (best-effort 로깅).
        try:
            log_decision(
                db,
                run_label=run_label,
                stage="stage2",
                action_type="overlap_detected_retry",
                reason=(
                    f"겹침 감지 — 재시도 {attempt + 1}/{MAX_RETRIES + 1}회차, "
                    f"위반 {len(overlap_hits)}건"
                ),
                constraints_applied=overlap_hits,
            )
        except Exception:
            pass
        _purge_run_tasks(db, run_label)

    # 모든 재시도 후에도 겹침 지속 → 고위 경보 + DB 롤백 + 예외
    try:
        log_decision(
            db,
            run_label=run_label,
            stage="stage2",
            action_type="overlap_persist_alert",
            reason=(f"겹침 재시도 {MAX_RETRIES + 1}회 모두 실패 — 스케줄 저장 거부"),
            constraints_applied=[
                v for v in violations if v.get("constraint_id") == "overlap"
            ],
        )
    except Exception:
        pass

    db.rollback()
    raise SchedulerOverlapError(
        "스케줄 겹침이 재시도 후에도 지속됩니다.",
        run_label=run_label,
        violations=[v for v in violations if v.get("constraint_id") == "overlap"],
        attempts=MAX_RETRIES + 1,
    )


def _purge_run_tasks(db: Session, run_label: str) -> None:
    """재시도 전 해당 run 의 감사로그 + ScheduleTask 삭제 + 배치 상태 리셋.

    왜 AuditLog 를 먼저 지우는가:
      ``audit_log.task_id`` 는 ``schedule_task.task_id`` 에 FK 참조(ON DELETE
      CASCADE 없음). 첫 시도에서 ``schedule_placed`` 액션으로 남긴 감사 로그가
      ScheduleTask 를 참조하므로, ScheduleTask 먼저 삭제 시 Postgres 에서
      FK violation 발생 → AuditLog 삭제를 가장 먼저 수행.

    왜 배치 상태 리셋이 필요한가:
      ``_run_optimization_once`` 는 placement 시점에 ``ProductionBatch.status`` 를
      ``"scheduled"`` 로 변경하고, 다음 호출에서는 ``status == "planned"`` 인 배치만
      다시 로드한다. 상태 리셋을 하지 않으면 재시도 시 0건 배치만 발견되어
      빈 스케줄이 반환되고 겹침 검증도 통과(0 태스크 → 겹침 없음)해
      ``overlap_alert=False`` 로 조용히 성공 처리되는 심각한 integrity 버그 발생.

    동작:
      1. 해당 run 의 AuditLog 삭제 (FK 참조 제거)
      2. 해당 run 의 ScheduleTask 삭제
      3. ``status == "scheduled"`` 인 ProductionBatch 를 ``"planned"`` 로 복원하고
         ``equipment_code`` 도 해제 (재배정 허용)
    """
    from app.infrastructure.models.audit_log import AuditLog
    from app.infrastructure.models.production_batch import ProductionBatch

    # 0. 세션에 아직 flush 되지 않은 INSERT (schedule_placed audit, overlap_detected_retry 등)
    #    를 먼저 DB 에 밀어넣는다. autoflush=False 세션이므로, 이 단계 없이 bulk DELETE 를
    #    실행한 뒤 db.flush() 시점에 pending INSERT 가 뒤늦게 수행되면 "삭제된 task_id 를
    #    참조하는 audit row 를 insert" 하려다 FK violation 이 발생한다.
    db.flush()

    # 1. AuditLog 먼저 — FK 무결성 (audit_log.task_id → schedule_task.task_id)
    db.query(AuditLog).filter(AuditLog.run_label == run_label).delete(
        synchronize_session=False
    )
    # 2. ScheduleTask
    db.query(ScheduleTask).filter(ScheduleTask.run_label == run_label).delete(
        synchronize_session=False
    )
    # 3. 배치 상태 리셋
    db.query(ProductionBatch).filter(
        ProductionBatch.run_label == run_label,
        ProductionBatch.status == "scheduled",
    ).update(
        {"status": "planned", "equipment_code": None},
        synchronize_session=False,
    )
    db.flush()


def _run_optimization_once(
    run_label: str, db: Session, *, base_date: datetime | None = None
) -> dict:
    """
    run_label의 production_batch를 간트 차트에 자동 배치.

    Args:
        base_date: 스케줄 시작 기준일시. None이면 KST 당일 08:00.
    Returns: {"total_tasks": int, "violations": list, "warnings": list}
    """
    result = {"total_tasks": 0, "violations": [], "warnings": []}

    # Load all batches for this run, excluding outsourced and already-scheduled
    # 공정 순서를 포함하여 정렬 — 같은 수주의 연선이 절연보다 먼저 스케줄링되어야
    # predecessor_map이 올바르게 동작함
    # PROCESS_ORDER: 공정 순서 상수 (domain.constants에서 공유)
    batches = (
        db.query(ProductionBatch)
        .filter(
            ProductionBatch.run_label == run_label,
            ProductionBatch.status == "planned",
        )
        .order_by(
            ProductionBatch.due_date.asc(),
            ProductionBatch.customer_priority.asc(),
            ProductionBatch.batch_seq.asc(),
        )
        .all()
    )
    # Python 레벨 재정렬: batch_seq는 라우팅 내 공정 순서이지만,
    # batch_group 스케줄링: 공정 순서 최우선 (연선→절연→시스 파이프라인)
    # 같은 공정 내에서 납기→우선순위→SQ 순으로 정렬 (실제 공장 스케줄링 기준)
    batches.sort(
        key=lambda b: (
            PROCESS_ORDER.get(b.process_name, 50),
            b.batch_seq or 0,  # 61연선 코어(seq=0)가 메인(seq=1)보다 먼저
            b.due_date or date.max,
            b.customer_priority or 99,
            -(float(b.sq_mm2 or 0)),
        )
    )

    if not batches:
        result["warnings"].append("배치 없음 — Stage 1을 먼저 실행하세요")
        return result

    # ── WIP 공정 스킵: 재고로 대체 가능한 공정은 간트에 미배치 ───────────────
    from app.infrastructure.models.wip_inventory import WipInventory

    wip_ids = {b.wip_matched_id for b in batches if b.wip_matched_id is not None}
    wip_stage_map: dict[int, str] = {}
    if wip_ids:
        wips = db.query(WipInventory).filter(WipInventory.wip_id.in_(wip_ids)).all()
        wip_stage_map = {w.wip_id: w.process_stage or "" for w in wips}

    schedulable: list[ProductionBatch] = []
    wip_skipped = 0
    for batch in batches:
        if batch.wip_matched_id and batch.wip_matched_id in wip_stage_map:
            wip_stage = wip_stage_map[batch.wip_matched_id]
            skip_set = _WIP_SKIP_PROCESSES.get(wip_stage, set())
            if batch.process_name in skip_set:
                batch.status = "wip_complete"
                wip_skipped += 1
                continue
        schedulable.append(batch)

    batches = schedulable
    if wip_skipped:
        result["wip_skipped"] = wip_skipped

    # ── 기준일시 설정 — 계획 생성일(run_label) 08:00 ─────────────────────
    # run_label 형식: "YYYYMMDD_HHMMSS" — 앞 8자리를 날짜로 파싱한다.
    # 파싱 실패 시 KST 당일 08:00으로 폴백.
    if base_date is None:
        try:
            date_part = run_label.split("_")[0]  # "20260406"
            base_date = datetime(
                int(date_part[:4]),
                int(date_part[4:6]),
                int(date_part[6:8]),
                8,
                0,
                0,
            )
        except Exception:
            from zoneinfo import ZoneInfo

            kst_now = datetime.now(ZoneInfo("Asia/Seoul"))
            base_date = kst_now.replace(
                hour=8, minute=0, second=0, microsecond=0
            ).replace(tzinfo=None)

    # Load equipment into memory
    equipment_list = db.query(EquipmentMaster).all()
    equipment_by_process = {}
    for eq in equipment_list:
        equipment_by_process.setdefault(eq.process_name, []).append(eq)

    # Load speed master into memory: (equipment_code, sq_mm2) → SpeedMaster row
    speed_records = db.query(SpeedMaster).all()
    speed_map: dict[tuple, SpeedMaster] = {}
    for sr in speed_records:
        speed_map[(sr.equipment_code, float(sr.cross_section or 0))] = sr

    # 용접 시간 (4-4): constraint_config에서 welding_min 읽기
    welding_cfg = (
        db.query(ConstraintConfig)
        .filter(ConstraintConfig.constraint_id == "4-4")
        .first()
    )
    welding_min = _DEFAULT_WELDING_MIN
    if welding_cfg and welding_cfg.params_json:
        welding_min = float(
            welding_cfg.params_json.get("welding_min", _DEFAULT_WELDING_MIN)
        )

    # Load existing tasks (to check overlaps)
    existing_tasks = (
        db.query(ScheduleTask)
        .filter(
            ScheduleTask.run_label == run_label,
        )
        .all()
    )

    # Build equipment timeline: equipment_code → list of (start, end) occupied slots
    timeline = {}
    for t in existing_tasks:
        timeline.setdefault(t.equipment_code, []).append(
            (t.start_datetime, t.end_datetime)
        )

    # Track predecessor tasks by (sales_order_id, sales_order_line)
    predecessor_map = {}  # (order_id, order_line) → last task_id for this order

    # 용접 시간 추적 (4-4): equipment_code → last placed batch (sq_mm2, sales_order_id)
    last_batch_on_equip: dict[str, ProductionBatch] = {}

    # ── 규칙 2: 19연선 이상(70SQ+)은 같은 SQ→같은 설비 고정 ────────────────
    # 이미 배정된 SQ→설비 매핑을 추적하여 동일 SQ는 같은 설비에 배치
    sq_to_equip: dict[tuple[str, int], str] = {}  # (process_name, sq) → equipment_code

    tasks_created = []

    # 공정 간 선행관계 추적 — SQ 단위로 앞 공정의 종료 시각 기록
    # 연선_120SQ 종료 → 저압절연_120SQ 시작 가능
    # 저압절연_120SQ 종료 → A100_120SQ / A120_120SQ 시작 가능
    process_end_by_sq: dict[tuple[str, int], datetime] = {}
    # key: (공정명, SQ) → value: 해당 공정+SQ 그룹의 종료 시각

    # 파이프라인 겹침용: 앞 공정에서 첫 번째 드럼이 출력되는 시각
    # 연선에서 1틀이 나오면 절연 시작 가능, 절연 1틀 나오면 시스 시작 가능
    # = task.start_datetime + setup_min + (group_run_duration / drum_count)
    process_first_output_by_sq: dict[tuple[str, int], datetime] = {}

    # 저압절연 전체 중 가장 이른 첫 번째 드럼 출력 시각 — A100/A120 시스 그룹 시작 기준
    first_insul_output: datetime | None = None

    # 61연선 코어(T6B0/AL6BO) 첫 드럼 출력 시각 — pipeline overlap 기준
    # "CORE-300-..." 첫 드럼 완료 후 "ST-300-..." 시작 가능
    core_first_drum_by_main_sq: dict[int, datetime] = {}

    # ── batch_group 단위로 그루핑 ────────────────────────────────────────────
    from collections import OrderedDict

    batch_groups: OrderedDict[str, list[ProductionBatch]] = OrderedDict()
    for batch in batches:
        key = batch.batch_group or f"_single_{batch.batch_id}"
        batch_groups.setdefault(key, []).append(batch)

    # ── drum_lot_master에서 SQ별 소선경(wire_diameter) 로드 ──────────────────
    # ST- 연선 그룹 정렬 시 동일 소선경 그룹이 연속 배치되도록 하기 위해 사용
    sq_to_wire_d: dict[int, float] = {
        int(d.cross_section): float(d.wire_diameter)
        for d in db.query(DrumLotMaster).all()
        if d.wire_diameter is not None
    }

    # 소선경 클러스터별 최초 납기: wire_diameter → min(due_date of all ST- groups in cluster)
    # 가장 급한 소선경 클러스터를 먼저 처리하기 위해 사용
    wire_d_earliest: dict[float, date] = {}
    for gk, gb in batch_groups.items():
        if gk.startswith("ST-"):
            wd = sq_to_wire_d.get(_st_sq(gk), 0.0)
            if wd > 0:
                ed = _group_earliest_due(gb)
                if wd not in wire_d_earliest or ed < wire_d_earliest[wd]:
                    wire_d_earliest[wd] = ed

    # ── 그룹 처리 순서 결정 ──────────────────────────────────────────────────
    # 우선순위:
    #   0 = CORE/AL-CORE: 선행 공정이므로 반드시 먼저 스케줄링
    #   1 = ST- 연선 그룹: 소선경(wire_diameter) 클러스터 단위로 연속 배치
    #       클러스터 내 정렬: 클러스터 최초납기 → 소선경 → 그룹 최초납기
    #   2 = 그 외 공정(절연·시스 등): 공정 순서(PROCESS_ORDER) 최우선 → EDD
    #       파이프라인 보장: 절연(2)이 시스(4)보다 항상 먼저 스케줄링되어야
    #       process_first_output_by_sq에 절연 데이터가 등록된 후 시스가 참조 가능.
    #
    # 시스 그룹 전용 체인 정렬 (A'' 접근안):
    #   - 1차: 색상 순위 (_SHEATH_COLOR_RANK: 흑→갈→회→청…)
    #   - 2차: 납기 ISO 주차 (같은 색상 내 빠른 주차 먼저)
    #   - 3차: 실제 납기일 (같은 주+색상 내 stable EDD)
    #   → 색상 체인지오버 최소화가 목표. 동일 EDD 의 다른 색상 두 그룹을
    #     하나의 chain 으로 묶기 위해 색상을 1차 키로 둔다.
    #   Tradeoff: 서로 다른 색상 간에서는 납기가 뒤로 밀릴 수 있다.
    #     예) 청(W15) 이 흑(W16) 뒤로 밀림. 하지만 시스 설비(SH-A120/100) 는
    #         그룹 수가 제한적(≤ 주당 ~4건)이라 실무적 영향은 미미하며,
    #         색상 교체로 인한 setup loss(수십 분) 를 절감한다.
    #     납기 위반은 위반 체커가 warning 으로 감지하므로 보고 가능.
    def _group_sort_key(kv):
        gk, gb = kv
        tier = 0 if _is_core_group(gk) else (1 if gk.startswith("ST-") else 2)
        proc_order = PROCESS_ORDER.get(gb[0].process_name, 50) if gb else 50
        earliest_due = _group_earliest_due(gb)
        cust_prio = gb[0].customer_priority or 99 if gb else 99

        # 시스 체인: 색상 → 주 버킷 → EDD 순으로 정렬키 구성
        if _is_sheath_group(gk, gb):
            return (
                tier,
                date.max,  # ST- 클러스터 납기 (비해당)
                0.0,  # ST- 소선경 (비해당)
                proc_order,
                _sheath_group_color_rank(gb),  # 1차: 색상 체인
                _sheath_group_due_week_int(gb),  # 2차: 주 버킷
                earliest_due,  # 3차: 실제 EDD
                cust_prio,
            )

        # 비시스 기존 정렬 (호환성 유지)
        return (
            tier,
            wire_d_earliest.get(sq_to_wire_d.get(_st_sq(gk), 0.0), date.max)
            if gk.startswith("ST-")
            else date.max,
            sq_to_wire_d.get(_st_sq(gk), 0.0) if gk.startswith("ST-") else 0.0,
            proc_order,
            earliest_due,
            # 시스 정렬키와 길이를 맞추기 위한 padding (비교 시 영향 없도록 동일 상수)
            0,
            date.max,
            cust_prio,
        )

    ordered_group_items = sorted(batch_groups.items(), key=_group_sort_key)

    for group_key, group_batches in ordered_group_items:
        rep = group_batches[0]  # 대표 배치 (설비 선정용)

        # 10-3: 시스 재질 라우팅
        candidate_equip = equipment_by_process.get(rep.process_name, [])
        if rep.process_name in ("고압시스", "저압시스"):
            candidate_equip = _filter_by_sheath_routing(rep, candidate_equip)

        # 저압시스 A100/A120 색상별 설비 강제 라우팅
        # A120 배치(흑/청) → SH-A120 전용, A100 배치(갈/회/녹황) → SH-A100 전용
        if group_key.startswith("A120_"):
            candidate_equip = [
                e for e in candidate_equip if e.equipment_code == "SH-A120"
            ]
        elif group_key.startswith("A100_"):
            candidate_equip = [
                e for e in candidate_equip if e.equipment_code == "SH-A100"
            ]

        eligible = _find_eligible_equipment(rep, candidate_equip)

        sq = int(rep.sq_mm2 or 0)
        sq_key = (rep.process_name, sq)
        is_stranding = rep.process_name == "연선"

        # ── 61연선 설비 선호도 좁히기 (T6BO / 54BO) ──────────────────────
        # 매칭 설비 없으면 eligible 전체 유지 → 스케줄링 skip 방지
        if is_stranding:
            eligible = _narrow_by_stranding(rep, eligible)

        # ── 규칙 2: 같은 SQ → 같은 설비 (연선 공정만, 70SQ+) ─────────────
        # CORE-/AL-CORE- 그룹 제외: 61연선에서 CORE와 ST는 서로 다른 설비를 타야 함
        if (
            is_stranding
            and sq >= 70
            and sq_key in sq_to_equip
            and not _is_core_group(group_key)
        ):
            preferred_eq = sq_to_equip[sq_key]
            pref_match = [e for e in eligible if e.equipment_code == preferred_eq]
            if pref_match:
                eligible = pref_match

        # ── T/P 공정 preferred 설비: TP-2 (not alphabetical TP-1) ────────────
        # Why: _find_speed.equipment_map["T/P"] = ["TP-2"] 이므로 duration 계산과
        # 실제 배정 설비를 일관되게 유지한다. PDF 1안도 T/P#2 만 사용.
        # 만약 TP-2 가 eligible 에서 제외 (color/range 필터 등) 되면 fallback.
        if rep.process_name == "T/P":
            tp2_match = [e for e in eligible if e.equipment_code == "TP-2"]
            if tp2_match:
                eligible = tp2_match

        # ── 규칙 3: 소선경 그루핑 ────────────────────────────────────────────
        # drum_lot_master.wire_diameter 기준 — 동일 소선경 SQ는 같은 설비 선호
        if is_stranding and sq_key not in sq_to_equip and not _is_core_group(group_key):
            wire_d = sq_to_wire_d.get(sq, 0.0)
            if wire_d > 0:
                same_wd_equips = set()
                for (proc, s), eq_code in sq_to_equip.items():
                    if proc == "연선" and sq_to_wire_d.get(s, -1.0) == wire_d:
                        same_wd_equips.add(eq_code)
                if same_wd_equips:
                    wd_match = [
                        e for e in eligible if e.equipment_code in same_wd_equips
                    ]
                    if wd_match:
                        eligible = wd_match

        if not eligible:
            candidate_codes = [e.equipment_code for e in candidate_equip]
            result["warnings"].append(
                f"배치그룹 {group_key}: 공정 '{rep.process_name}' SQ={rep.sq_mm2} "
                f"재질={rep.conductor_material} — 적합한 설비 없음 "
                f"(후보설비={candidate_codes})"
            )
            # 미스케줄된 공정을 max 시간으로 등록 → 후행 공정이 이 공정 없이 시작하는 것을 방지
            sq_int = int(rep.sq_mm2 or 0)
            process_end_by_sq[(rep.process_name, sq_int)] = datetime.max
            process_first_output_by_sq[(rep.process_name, sq_int)] = datetime.max
            continue

        # ── 멀티설비 분배: 드럼 수 >= 2 이고 적격 설비 >= 2 일 때
        #    드럼을 설비 수로 균등 분할하여 병렬 배치 ─────────────────────────
        #    대상: 연선(CORE 제외) + 고압절연(CV#1/CV#2 분배)
        header_batch_chk = next((b for b in group_batches if b.batch_seq == -1), None)
        if header_batch_chk:
            total_drums = int(header_batch_chk.drum_count or 0)
        else:
            # 고압절연 등 헤더 없는 공정: 그룹 내 배치 drum_count 합산
            total_drums = sum(int(b.drum_count or 0) for b in group_batches)
        is_high_insul = rep.process_name == "고압절연"
        is_high_sheath = rep.process_name == "고압시스"
        multi_eligible = (
            (
                is_stranding
                and not _is_core_group(group_key)
                and sq_key not in sq_to_equip
            )
            or is_high_insul
            or is_high_sheath
        )
        if multi_eligible and total_drums >= 2 and len(eligible) >= 2:
            split_ok = _schedule_multi_equipment(
                group_key=group_key,
                group_batches=group_batches,
                eligible=eligible,
                total_drums=total_drums,
                header_batch=header_batch_chk,
                base_date=base_date,
                run_label=run_label,
                db=db,
                speed_map=speed_map,
                timeline=timeline,
                last_batch_on_equip=last_batch_on_equip,
                sq_to_equip=sq_to_equip,
                predecessor_map=predecessor_map,
                process_end_by_sq=process_end_by_sq,
                process_first_output_by_sq=process_first_output_by_sq,
                core_first_drum_by_main_sq=core_first_drum_by_main_sq,
                tasks_created=tasks_created,
                result=result,
                welding_min=welding_min,
                sq_to_wire_d=sq_to_wire_d,
            )
            if split_ok:
                continue

        # ── 그룹 전체 duration 계산 ──────────────────────────────────────────
        # batch_seq=-1 헤더 배치가 있으면 그 estimated_duration_min을 직접 사용.
        # (연선 그룹: 실제 작업량 work_qty_g / 선속 — 수주 건수와 무관)
        # 헤더 없으면 기존 방식으로 각 배치 duration 합산.
        rep_speed = float(rep.line_speed_mpm or 0)
        if rep_speed <= 0:
            # SpeedMaster에서 해당 설비+SQ 조합의 line_speed 조회
            for eq in eligible:
                sm = speed_map.get((eq.equipment_code, float(rep.sq_mm2 or 0)))
                if sm and sm.line_speed_mpm and float(sm.line_speed_mpm) > 0:
                    rep_speed = float(sm.line_speed_mpm)
                    break
        line_speed = rep_speed if rep_speed > 0 else 10
        header_batch = next((b for b in group_batches if b.batch_seq == -1), None)
        if header_batch is not None:
            hd = float(header_batch.estimated_duration_min or 0)
            if hd <= 0:
                total = float(header_batch.total_length_m or 0)
                ls = float(header_batch.line_speed_mpm or 0) or line_speed
                hd = total / ls if ls > 0 else 60
            group_duration = hd
        else:
            group_duration = 0.0
            for b in group_batches:
                d = float(b.estimated_duration_min or 0)
                if d <= 0:
                    total = float(b.total_length_m or 0) + float(b.extra_length_m or 0)
                    ls = float(b.line_speed_mpm or 0) or line_speed
                    d = total / ls if ls > 0 else 60
                group_duration += d

        setup_min = float(rep.setup_time_min or 0)
        drum_winding_min = _get_drum_winding_min(
            eligible[0].equipment_code, rep.sq_mm2, speed_map
        )
        total_duration = group_duration + setup_min + drum_winding_min

        # ── 설비 선택 (최적 슬롯 탐색) ───────────────────────────────────────
        best_eq = None
        best_start = None
        best_total_duration = total_duration

        for eq in eligible:
            eq_code = eq.equipment_code
            slots = timeline.get(eq_code, [])

            eq_total_duration = total_duration

            # 4-1: 연선 셋업 3-tier (동일SQ=0 / 동일소선경=선재교체 / 다른소선경=규격교체)
            prev_batch = last_batch_on_equip.get(eq_code)
            if prev_batch is not None and rep.process_name == "연선":
                compound_min = float(
                    speed_map.get((eq_code, float(rep.sq_mm2 or 0)), None)
                    and speed_map[(eq_code, float(rep.sq_mm2 or 0))].setup_compound_min
                    or 0
                )
                actual_setup = _get_stranding_setup_min(
                    float(prev_batch.sq_mm2) if prev_batch.sq_mm2 else None,
                    float(rep.sq_mm2) if rep.sq_mm2 else None,
                    sq_to_wire_d,
                    spec_min=setup_min,
                    compound_min=compound_min,
                )
            else:
                actual_setup = setup_min
                if prev_batch is not None:
                    same_sq = (
                        prev_batch.sq_mm2 is not None
                        and rep.sq_mm2 is not None
                        and float(prev_batch.sq_mm2) == float(rep.sq_mm2)
                    )
                    if same_sq:
                        actual_setup = 0.0
            eq_total_duration = eq_total_duration - setup_min + actual_setup

            # 4-2: 색상교체 시간 — 그룹 간 변경 시 (SpeedMaster 조회)
            color_change_min = 0.0
            if prev_batch is not None and rep.process_name in (
                "저압시스",
                "고압시스",
                "HFCO시스",
            ):
                prev_color = (prev_batch.sheath_color or "").strip()
                curr_color = (rep.sheath_color or "").strip()
                if prev_color and curr_color and prev_color != curr_color:
                    # SpeedMaster에서 해당 설비의 색상교체 시간 조회
                    sm_color = (
                        db.query(SpeedMaster.setup_color_min)
                        .filter(SpeedMaster.equipment_code == eq.equipment_code)
                        .first()
                    )
                    color_change_min = (
                        float(sm_color[0] or 120.0) if sm_color else 120.0
                    )
            eq_total_duration += color_change_min

            # ── 선행공정(predecessor) — 공정 순서에 따라 앞 공정 종료 후 시작
            earliest = base_date
            sq_int = int(rep.sq_mm2 or 0)

            # 파이프라인 겹침: 앞 공정에서 첫 번째 드럼이 나오면 후공정 시작 가능
            # 연선 1틀 완료 → 절연 시작 / 절연 1틀 완료 → 시스 시작
            pred_proc = PREDECESSOR_PROCESS.get(rep.process_name)
            if pred_proc:
                all_sqs = {int(b.sq_mm2 or 0) for b in group_batches}
                if len(all_sqs) > 1:
                    # 색상 기준 혼합 SQ 그룹(시스): 어느 SQ든 첫 드럼이 나오면 시작 가능
                    # → 그룹 내 SQ 중 가장 이른 첫 출력 시각을 선행 제약으로 사용
                    valid_firsts = [
                        t
                        for sq_i in all_sqs
                        if (t := process_first_output_by_sq.get((pred_proc, sq_i)))
                        and t < datetime.max
                    ]
                    if valid_firsts:
                        pred_min = min(valid_firsts)
                        if pred_min > earliest:
                            earliest = pred_min
                else:
                    # 단일 SQ 그룹: 해당 SQ의 선행 제약만 확인
                    sq_i = next(iter(all_sqs))
                    pred_first = process_first_output_by_sq.get((pred_proc, sq_i))
                    if pred_first and pred_first > earliest:
                        earliest = pred_first
                if rep.process_name == "고압시스":
                    earliest += timedelta(hours=20)

            # 시스 배치(A100/A120): 저압절연 첫 번째 드럼 출력 후 시작
            if group_key.startswith("A100_") or group_key.startswith("A120_"):
                if first_insul_output and first_insul_output > earliest:
                    earliest = first_insul_output

            # 61연선 ST- 그룹: 동일 SQ의 CORE/AL-CORE 첫 드럼 출력 후 시작 (overlap)
            # 예: "ST-633-..." 그룹 → core_first_drum_by_main_sq[633] 이후 시작
            if group_key.startswith("ST-") and rep.process_name == "연선":
                try:
                    main_sq = int(group_key.split("-")[1])
                except (IndexError, ValueError):
                    main_sq = sq_int
                core_first = core_first_drum_by_main_sq.get(main_sq)
                if core_first and core_first > earliest:
                    earliest = core_first

            # 개별 수주 레벨 predecessor — 절연/시스/연합/T/P는 first-drum overlap만 사용
            # ST-* 연선 그룹: CORE first-drum overlap 사용 → 개별 predecessor 스킵
            # 개별 predecessor end_datetime을 쓰면 전체 완료를 기다리게 되어 overlap 무효화
            _is_st_group = group_key.startswith("ST-") and rep.process_name == "연선"
            _skip_individual = (
                rep.process_name
                in (
                    "저압절연",
                    "고압절연",
                    "저압시스",
                    "고압시스",
                    "연합",
                    "T/P",
                )
                or _is_st_group
            )
            if not _skip_individual:
                for b in group_batches:
                    pred_key = (b.sales_order_id, b.sales_order_line)
                    pred_tid = predecessor_map.get(pred_key)
                    if pred_tid:
                        pred_task = next(
                            (t for t in tasks_created if t.task_id == pred_tid),
                            None,
                        )
                        if pred_task and pred_task.end_datetime > earliest:
                            earliest = pred_task.end_datetime

            slot_start = _find_available_slot(
                earliest, eq_total_duration, slots, db, eq.equipment_code
            )

            if best_start is None or slot_start < best_start:
                best_eq = eq
                best_start = slot_start
                best_total_duration = eq_total_duration

        if best_eq is None or best_start is None:
            result["warnings"].append(f"배치그룹 {group_key}: 가용 슬롯 없음")
            continue

        end_dt = calculate_end_datetime(
            best_start, best_total_duration, db, best_eq.equipment_code
        )

        # ── 파이프라인 유휴 최소 역산 공식 ────────────────────────────────────
        # T_succ_start = max(T_pred_first_drum, T_pred_end - D_succ)
        # 불변식: T_succ_end >= T_pred_end (후공정 끝 ≥ 선행공정 끝)
        # 효과: 절연 선속이 연선보다 빠르면 시작을 늦춰 끝을 정렬. 블록 width 불변.
        best_start, end_dt = align_start_to_predecessor_end(
            process_name=rep.process_name,
            pred_proc=pred_proc,
            group_sqs={int(b.sq_mm2 or 0) for b in group_batches},
            process_end_by_sq=process_end_by_sq,
            current_start=best_start,
            current_end=end_dt,
            duration_min=best_total_duration,
            slots=timeline.get(best_eq.equipment_code, []),
            db=db,
            equipment_code=best_eq.equipment_code,
        )

        # ── 시간 올림 — 간트 블록은 정각 단위로 표시 ────────────────────────
        if end_dt.minute > 0 or end_dt.second > 0 or end_dt.microsecond > 0:
            end_dt = end_dt.replace(minute=0, second=0, microsecond=0) + timedelta(
                hours=1
            )

        # ── 그룹당 1 schedule_task 생성 ──────────────────────────────────────
        task = ScheduleTask(
            batch_id=rep.batch_id,  # 대표 배치 ID
            equipment_code=best_eq.equipment_code,
            start_datetime=best_start,
            end_datetime=end_dt,
            setup_time_min=setup_min,
            status="scheduled",
            run_label=run_label,
            batch_group=group_key,
        )
        db.add(task)
        db.flush()

        timeline.setdefault(best_eq.equipment_code, []).append((best_start, end_dt))

        # 공정+SQ별 종료 시각 갱신 (후공정 선행관계 추적)
        sq_int = int(rep.sq_mm2 or 0)
        proc_sq_key = (rep.process_name, sq_int)
        if (
            proc_sq_key not in process_end_by_sq
            or end_dt > process_end_by_sq[proc_sq_key]
        ):
            process_end_by_sq[proc_sq_key] = end_dt

        # ── 파이프라인 겹침: 첫 번째 드럼 출력 시각 계산 ────────────────────
        # 연선 ST-: 헤더 배치(seq=-1)의 drum_count = 실제 틀 수
        # CORE-/AL-CORE-: drum_count 합산 — 드럼 하나씩 완료될 때마다 ST 시작 가능
        #   (AL6BO에서 한 드럼 완료 → 54BO 즉시 시작하는 파이프라인)
        # 절연/시스 등: 헤더 없으므로 그룹 내 배치 수 = 순차 처리 단위 수
        if header_batch is not None:
            lot_count = max(int(header_batch.drum_count or 1), 1)
        else:
            # CORE 그룹 포함, 절연/시스 등 헤더 없는 그룹 모두 drum_count 합산
            lot_count = max(sum(int(b.drum_count or 1) for b in group_batches), 1)
        first_drum_min = setup_min + (group_duration / lot_count)
        first_output_dt = calculate_end_datetime(
            best_start, first_drum_min, db, best_eq.equipment_code
        )
        # CORE-/AL-CORE- 그룹 제외: 절연은 ST(54BO) 첫 드럼 기준으로 시작해야 함
        # (CORE 첫 드럼은 너무 이르므로 후행 공정 선행 제약으로 부적합)
        if not _is_core_group(group_key) and (
            proc_sq_key not in process_first_output_by_sq
            or first_output_dt < process_first_output_by_sq[proc_sq_key]
        ):
            process_first_output_by_sq[proc_sq_key] = first_output_dt

        # 61연선 CORE-/AL-CORE- 그룹 첫 드럼 출력 시각 기록 — pipeline overlap
        # CU: "CORE-{main_sq}-...", AL: "AL-CORE-{main_sq}-..." 패턴
        if _is_core_group(group_key):
            main_sq = _extract_core_main_sq(group_key)
            if main_sq is not None:
                if (
                    main_sq not in core_first_drum_by_main_sq
                    or first_output_dt < core_first_drum_by_main_sq[main_sq]
                ):
                    core_first_drum_by_main_sq[main_sq] = first_output_dt

        # 저압절연 첫 번째 드럼 출력 시각 — A100/A120 시스 그룹 시작 기준
        if rep.process_name == "저압절연":
            if first_insul_output is None or first_output_dt < first_insul_output:
                first_insul_output = first_output_dt

        # 그룹 내 모든 배치의 predecessor + status 갱신
        for b in group_batches:
            pred_key = (b.sales_order_id, b.sales_order_line)
            predecessor_map[pred_key] = task.task_id
            b.equipment_code = best_eq.equipment_code
            b.status = "scheduled"

        # 규칙 2: SQ→설비 매핑 기록 (CORE/AL-CORE 그룹 제외 — 코어는 ST설비 고정 대상 아님)
        if rep.process_name == "연선" and not _is_core_group(group_key):
            sq_to_equip[sq_key] = best_eq.equipment_code

        # 용접 시간 추적 (4-4): 설비별 마지막 배치 갱신 (그룹의 마지막 배치)
        last_batch_on_equip[best_eq.equipment_code] = group_batches[-1]

        tasks_created.append(task)

        # Check delivery date violation — 그룹 내 가장 빠른 납기 기준
        # 납기는 사용자 요구 상 하드 제약 → severity=error 로 상향
        # (validate_all 이 이를 보고 재시도/알림을 유발하도록)
        earliest_due = min(
            (b.due_date for b in group_batches if b.due_date), default=None
        )
        if earliest_due and end_dt.date() > earliest_due:
            violation = {
                "batch_id": rep.batch_id,
                "task_id": task.task_id,
                "type": "delivery",
                "severity": "error",
                "detail": f"납기 {earliest_due} 초과 → 완료 예정 {end_dt.date()}",
            }
            result["violations"].append(violation)
            result.setdefault("warnings", []).append(
                f"납기 위반 예상: 배치그룹 {group_key} end={end_dt.date()} > due={earliest_due}"
            )

        # Audit log
        log_decision(
            db=db,
            run_label=run_label,
            stage="stage2",
            batch_id=rep.batch_id,
            task_id=task.task_id,
            action_type="schedule_placed",
            constraints_applied=[
                {
                    "id": "1-1",
                    "name": "거래처 우선순위",
                    "result": "pass",
                    "detail": f"priority={rep.customer_priority}",
                },
                {
                    "id": "4-3",
                    "name": "드럼 권취 시간",
                    "result": "pass",
                    "detail": f"drum_winding={drum_winding_min:.0f}분 추가",
                },
                {
                    "id": "4-4",
                    "name": "용접 시간",
                    "result": "pass",
                    "detail": f"welding={welding_min:.0f}분 (스플라이스 로트 시 적용)",
                },
                {
                    "id": "4-5",
                    "name": "테이핑 속도 제한",
                    "result": "pass",
                    "detail": f"process={rep.process_name}, speed={line_speed:.1f}mpm",
                },
                {
                    "id": "5-1",
                    "name": "SQ 기준 설비 배정",
                    "result": "pass",
                    "detail": f"{best_eq.equipment_name} (range {best_eq.range_min}~{best_eq.range_max})",
                },
                {
                    "id": "10-2",
                    "name": "CU/AL 재질 분리",
                    "result": "pass",
                    "detail": f"material={rep.conductor_material}, equip_limit={best_eq.material_limit}",
                },
                {
                    "id": "10-3",
                    "name": "시스 재질 라우팅",
                    "result": "pass",
                    "detail": f"sheath_type={_get_sheath_type(rep)}, equip={best_eq.equipment_code}",
                },
            ],
            reason=(
                f"설비 {best_eq.equipment_name}에 배치: "
                f"SQ={batch.sq_mm2}, 납기={batch.due_date}, 소요={best_total_duration:.0f}분"
            ),
        )

        result["total_tasks"] += 1

    return result


# ── 멀티설비 분배 ────────────────────────────────────────────────────────────
def _schedule_multi_equipment(
    *,
    group_key: str,
    group_batches: list,
    eligible: list,
    total_drums: int,
    header_batch,
    base_date: datetime,
    run_label: str,
    db: "Session",
    speed_map: dict,
    timeline: dict,
    last_batch_on_equip: dict,
    sq_to_equip: dict,
    predecessor_map: dict,
    process_end_by_sq: dict,
    process_first_output_by_sq: dict,
    core_first_drum_by_main_sq: dict,
    tasks_created: list,
    result: dict,
    welding_min: float,
    sq_to_wire_d: dict | None = None,
) -> bool:
    """연선/고압절연 그룹의 드럼을 eligible 설비에 균등 분배하여 병렬 스케줄링.

    드럼 수를 설비 수로 나눠 각 설비에 proportional duration의 task를 생성한다.
    process_end_by_sq / process_first_output_by_sq는 가장 이른 완료 기준으로 갱신.

    - 연선: header_batch(seq=-1)의 duration 사용
    - 고압절연: header 없음 → 그룹 내 배치 duration 합산

    Returns:
        True면 분배 성공 (호출측에서 continue), False면 단일설비 경로로 폴백.
    """

    rep = group_batches[0]
    sq = int(rep.sq_mm2 or 0)
    sq_key = (rep.process_name, sq)

    # 그룹 전체 duration 계산
    # line_speed fallback: rep에 없으면 speed_map에서 설비별 기본값 조회
    rep_speed = float(rep.line_speed_mpm or 0)
    if rep_speed <= 0:
        # SpeedMaster에서 해당 설비+SQ 조합의 line_speed 조회
        for eq in eligible:
            sm = speed_map.get((eq.equipment_code, float(rep.sq_mm2 or 0)))
            if sm and sm.line_speed_mpm and float(sm.line_speed_mpm) > 0:
                rep_speed = float(sm.line_speed_mpm)
                break
    line_speed = rep_speed if rep_speed > 0 else 10  # 최종 fallback 10 mpm

    if header_batch is not None:
        # 연선: header batch(seq=-1)의 estimated_duration_min 직접 사용
        hd = float(header_batch.estimated_duration_min or 0)
        if hd <= 0:
            total_len = float(header_batch.total_length_m or 0)
            ls = float(header_batch.line_speed_mpm or 0) or line_speed
            hd = total_len / ls if ls > 0 else 60
        group_duration = hd
    else:
        # 고압절연 등 헤더 없는 공정: 각 배치 duration 합산
        group_duration = 0.0
        for b in group_batches:
            d = float(b.estimated_duration_min or 0)
            if d <= 0:
                total_len = float(b.total_length_m or 0) + float(b.extra_length_m or 0)
                ls = float(b.line_speed_mpm or 0) or line_speed
                d = total_len / ls if ls > 0 else 60
            group_duration += d
        if group_duration <= 0:
            return False  # duration 계산 불가 시 단일설비 폴백

    setup_min = float(rep.setup_time_min or 0)

    # 드럼을 설비 수로 균등 분할
    num_eq = len(eligible)
    drums_per_eq = []
    base_drums = total_drums // num_eq
    remainder = total_drums % num_eq
    for i in range(num_eq):
        drums_per_eq.append(base_drums + (1 if i < remainder else 0))

    # 선행공정 earliest 계산 — 단일설비 경로와 동일하되,
    # 혼합 SQ 그룹(고압시스_흑_적 등)은 모든 SQ의 predecessor를 확인
    earliest = base_date
    sq_int = sq

    pred_proc = PREDECESSOR_PROCESS.get(rep.process_name)
    if pred_proc:
        # 혼합 SQ 그룹: 그룹 내 모든 SQ의 predecessor 중 가장 이른 first output
        all_sqs = {int(b.sq_mm2 or 0) for b in group_batches}
        if len(all_sqs) > 1:
            valid_firsts = [
                t
                for sq_i in all_sqs
                if (t := process_first_output_by_sq.get((pred_proc, sq_i)))
                and t < datetime.max
            ]
            if valid_firsts:
                pred_min = min(valid_firsts)
                if pred_min > earliest:
                    earliest = pred_min
        else:
            pred_first = process_first_output_by_sq.get((pred_proc, sq_int))
            if pred_first and pred_first > earliest:
                earliest = pred_first

        # 고압시스: 절연 경화 대기 시간 20h (단일설비 경로와 동일)
        if rep.process_name == "고압시스":
            earliest += timedelta(hours=20)

    # 61연선 ST- 그룹: CORE 첫 드럼 출력 후 시작 (pipeline overlap)
    if group_key.startswith("ST-") and rep.process_name == "연선":
        try:
            main_sq = int(group_key.split("-")[1])
        except (IndexError, ValueError):
            main_sq = sq_int
        core_first = core_first_drum_by_main_sq.get(main_sq)
        if core_first and core_first > earliest:
            earliest = core_first

    # 개별 수주 predecessor 확인
    # 절연/시스/연합/T/P는 first-drum overlap만 사용 (단일설비 경로와 동일)
    # ST-* 연선 그룹: CORE first-drum overlap 사용 → 개별 predecessor 스킵
    is_st_group = group_key.startswith("ST-") and rep.process_name == "연선"
    skip_individual_pred = (
        rep.process_name
        in (
            "저압절연",
            "고압절연",
            "저압시스",
            "고압시스",
            "연합",
            "T/P",
        )
        or is_st_group
    )
    if not skip_individual_pred:
        for b in group_batches:
            pred_key = (b.sales_order_id, b.sales_order_line)
            pred_tid = predecessor_map.get(pred_key)
            if pred_tid:
                pred_task = next(
                    (t for t in tasks_created if t.task_id == pred_tid), None
                )
                if pred_task and pred_task.end_datetime > earliest:
                    earliest = pred_task.end_datetime

    # ── earliest 확정 후: 설비 우선순위 정렬 + 수주 납기 우선 배정 ───────────
    # 각 설비의 예상 최초 가용 시각 계산 (earliest 반영)
    one_drum_dur = (group_duration / max(total_drums, 1)) + setup_min
    machine_est_starts = []
    for eq in eligible:
        slots = timeline.get(eq.equipment_code, [])
        est_start = _find_available_slot(
            earliest, one_drum_dur, slots, db, eq.equipment_code
        )
        machine_est_starts.append((est_start, eq))
    # 가장 빨리 시작 가능한 설비 순으로 정렬
    machine_est_starts.sort(key=lambda x: x[0])
    sorted_eligible = [eq for _, eq in machine_est_starts]

    # 개별 수주(seq >= 1)를 납기 오름차순으로 정렬 → 급한 수주를 빠른 설비에 배정
    order_batches_sorted = sorted(
        [b for b in group_batches if (b.batch_seq or 0) >= 1],
        key=lambda b: (b.due_date or date.max, b.customer_priority or 99),
    )
    machine_order_assignments: list[list] = [[] for _ in range(num_eq)]
    order_cursor = 0
    for i in range(num_eq):
        drums_left = drums_per_eq[i]
        while drums_left > 0 and order_cursor < len(order_batches_sorted):
            b = order_batches_sorted[order_cursor]
            machine_order_assignments[i].append(b)
            drums_left -= int(b.drum_count or 1)
            order_cursor += 1
    # 미처리 수주는 마지막 설비에 추가
    if order_cursor < len(order_batches_sorted):
        machine_order_assignments[-1].extend(order_batches_sorted[order_cursor:])

    # 설비별 서브배치의 납기: 해당 설비에 배정된 수주 중 가장 이른 납기
    sub_due_dates: list[date | None] = [
        min((b.due_date for b in orders if b.due_date), default=None)
        for orders in machine_order_assignments
    ]

    # 각 설비에 분배 task 생성 (납기 우선 배정된 sorted_eligible 순서)
    split_tasks = []
    split_end_dts = []
    split_first_outputs = []
    split_sub_dues: list[date | None] = []

    for i, eq in enumerate(sorted_eligible):
        eq_drums = drums_per_eq[i]
        if eq_drums <= 0:
            continue

        eq_code = eq.equipment_code
        # proportional duration (배정된 드럼 수 기반)
        eq_duration = group_duration * (eq_drums / total_drums)

        drum_winding_min = _get_drum_winding_min(eq_code, rep.sq_mm2, speed_map)

        # 4-1: 연선 셋업 3-tier (동일SQ=0 / 동일소선경=선재교체 / 다른소선경=규격교체)
        prev_batch = last_batch_on_equip.get(eq_code)
        if prev_batch is not None and rep.process_name == "연선" and sq_to_wire_d:
            compound_min = float(
                speed_map.get((eq_code, float(rep.sq_mm2 or 0)), None)
                and speed_map[(eq_code, float(rep.sq_mm2 or 0))].setup_compound_min
                or 0
            )
            actual_setup = _get_stranding_setup_min(
                float(prev_batch.sq_mm2) if prev_batch.sq_mm2 else None,
                float(rep.sq_mm2) if rep.sq_mm2 else None,
                sq_to_wire_d,
                spec_min=setup_min,
                compound_min=compound_min,
            )
        else:
            actual_setup = setup_min
            if prev_batch is not None:
                same_sq = (
                    prev_batch.sq_mm2 is not None
                    and rep.sq_mm2 is not None
                    and float(prev_batch.sq_mm2) == float(rep.sq_mm2)
                )
                if same_sq:
                    actual_setup = 0.0

        eq_total_duration = eq_duration + actual_setup + drum_winding_min

        slots = timeline.get(eq_code, [])
        slot_start = _find_available_slot(
            earliest, eq_total_duration, slots, db, eq_code
        )
        end_dt = calculate_end_datetime(slot_start, eq_total_duration, db, eq_code)

        # ── 파이프라인 유휴 최소 역산 — 서브태스크별 독립 적용 ────────────────
        # duration(= eq_total_duration)은 드럼 수 비례이므로 서브태스크마다 다름.
        # 각 서브태스크가 선행공정 종료 이상에서 끝나도록 개별 정렬.
        slot_start, end_dt = align_start_to_predecessor_end(
            process_name=rep.process_name,
            pred_proc=pred_proc,
            group_sqs={int(b.sq_mm2 or 0) for b in group_batches},
            process_end_by_sq=process_end_by_sq,
            current_start=slot_start,
            current_end=end_dt,
            duration_min=eq_total_duration,
            slots=slots,
            db=db,
            equipment_code=eq_code,
        )

        # 시간 올림 — 간트 블록은 정각 단위
        if end_dt.minute > 0 or end_dt.second > 0 or end_dt.microsecond > 0:
            end_dt = end_dt.replace(minute=0, second=0, microsecond=0) + timedelta(
                hours=1
            )

        task = ScheduleTask(
            batch_id=rep.batch_id,
            equipment_code=eq_code,
            start_datetime=slot_start,
            end_datetime=end_dt,
            setup_time_min=actual_setup,
            status="scheduled",
            run_label=run_label,
            batch_group=group_key,
        )
        db.add(task)
        db.flush()

        timeline.setdefault(eq_code, []).append((slot_start, end_dt))
        last_batch_on_equip[eq_code] = group_batches[-1]
        split_tasks.append(task)
        split_end_dts.append(end_dt)
        split_sub_dues.append(sub_due_dates[i] if i < len(sub_due_dates) else None)

        # 첫 번째 드럼 출력 시각
        first_drum_min = actual_setup + (eq_duration / eq_drums)
        first_output_dt = calculate_end_datetime(
            slot_start, first_drum_min, db, eq_code
        )
        split_first_outputs.append(first_output_dt)

        tasks_created.append(task)
        result["total_tasks"] += 1

    if not split_tasks:
        return False

    # 공정+SQ별 종료/첫출력 시각 — 가장 늦은 종료, 가장 이른 첫출력
    proc_sq_key = (rep.process_name, sq_int)
    latest_end = max(split_end_dts)
    earliest_first = min(split_first_outputs)

    if (
        proc_sq_key not in process_end_by_sq
        or latest_end > process_end_by_sq[proc_sq_key]
    ):
        process_end_by_sq[proc_sq_key] = latest_end

    if (
        proc_sq_key not in process_first_output_by_sq
        or earliest_first < process_first_output_by_sq[proc_sq_key]
    ):
        process_first_output_by_sq[proc_sq_key] = earliest_first

    # 그룹 내 배치 status + equipment 갱신 (첫 번째 설비를 대표로)
    for b in group_batches:
        pred_key = (b.sales_order_id, b.sales_order_line)
        predecessor_map[pred_key] = split_tasks[0].task_id
        b.equipment_code = split_tasks[0].equipment_code
        b.status = "scheduled"

    # 규칙 2 매핑은 기록하지 않음 — 분배된 그룹은 여러 설비를 사용하므로

    # ── 납기 위반 체크: 서브배치별 독립 검사 ─────────────────────────────────
    # 각 설비 서브배치는 자신에게 배정된 수주의 가장 이른 납기를 기준으로 위반 여부 판정
    for j, (task_j, end_j, due_j) in enumerate(
        zip(split_tasks, split_end_dts, split_sub_dues)
    ):
        if due_j and end_j.date() > due_j:
            late_days = (end_j.date() - due_j).days
            result["violations"].append(
                {
                    "batch_id": rep.batch_id,
                    "task_id": task_j.task_id,
                    "type": "delivery",
                    "severity": "warning",
                    "detail": (
                        f"[분할배치 {j + 1}/{len(split_tasks)}] 납기 {due_j} 초과 "
                        f"→ 완료 {end_j.date()} (+{late_days}일)"
                    ),
                }
            )

    return True


def _find_eligible_equipment(
    batch: ProductionBatch, equipment: list[EquipmentMaster]
) -> list[EquipmentMaster]:
    """배치에 적합한 설비 필터링 (재질, SQ범위, 색상그룹)"""
    eligible = []
    for eq in equipment:
        # ── Material filter ────────────────────────────────────────────────
        if eq.material_limit and eq.material_limit != "ALL":
            if (
                batch.conductor_material
                and batch.conductor_material != eq.material_limit
            ):
                continue

        # ── Range filter ───────────────────────────────────────────────────
        if eq.range_unit == "mm":
            pass
        elif eq.range_unit == "Ø":
            # 연합 설비: SQ 기준으로 직접 분류 (小4BO: ~25SQ, 4BO: 35SQ~)
            sq = float(batch.sq_mm2) if batch.sq_mm2 else None
            if sq and eq.range_min and sq < float(eq.range_min):
                continue
            if sq and eq.range_max and sq > float(eq.range_max):
                continue
        else:
            sq = float(batch.sq_mm2) if batch.sq_mm2 else None
            if sq and eq.range_min and sq < float(eq.range_min):
                continue
            if sq and eq.range_max and sq > float(eq.range_max):
                continue

        # ── Color group filter (저압시스) ──────────────────────────────────
        if eq.color_group:
            color = (batch.sheath_color or "").strip()
            if eq.color_group == "흑/청":
                if color not in (
                    "흑",
                    "청",
                    "흑색",
                    "청색",
                    "BLACK",
                    "BLUE",
                    "BK",
                    "BL",
                    "",
                ):
                    continue

        eligible.append(eq)

    return eligible


def _narrow_by_stranding(
    batch: ProductionBatch, eligible: list[EquipmentMaster]
) -> list[EquipmentMaster]:
    """61연선 케이스 설비 선호도 좁히기 (fallback 있음).

    stranding_method 컬럼 또는 equipment_code 명칭으로 선호 설비를 추립니다.
    매칭 설비가 없으면 eligible 전체를 그대로 반환하여 스케줄링 skip을 방지합니다.
    """
    batch_st = (batch.stranding_type or "").strip()
    sq_val = float(batch.sq_mm2 or 0)

    if batch_st == "7연선코어":
        mat = (batch.conductor_material or "").upper()
        if mat == "AL":
            # AL 7연선코어 → AL6BO 선호 (material_limit="AL", stranding_method="7연선")
            preferred = [
                e
                for e in eligible
                if "AL6B" in (e.equipment_code or "").upper()
                or (
                    (e.stranding_method or "").strip() == "7연선"
                    and (e.material_limit or "").upper() == "AL"
                )
            ]
            return preferred if preferred else eligible
        # CU 7연선코어 → T6BO 선호
        preferred = [
            e
            for e in eligible
            if (e.stranding_method or "").strip() == "7연선"
            or "T6B" in (e.equipment_code or "").upper()
        ]
        return preferred if preferred else eligible

    if sq_val >= 300:
        # 54BO 선호: stranding_method="61연선" 또는 코드에 "54BO" 포함
        preferred = [
            e
            for e in eligible
            if (e.stranding_method or "").strip() == "61연선"
            or "54BO" in (e.equipment_code or "").upper()
        ]
        return preferred if preferred else eligible

    # 일반 연선: 7연선/61연선 전용 설비는 제외 (stranding_method 기준, 없으면 무시)
    excluded = [
        e for e in eligible if (e.stranding_method or "").strip() in ("7연선", "61연선")
    ]
    if len(excluded) < len(eligible):
        return [e for e in eligible if e not in excluded]
    return eligible


def align_start_to_predecessor_end(
    *,
    process_name: str,
    pred_proc: str | None,
    group_sqs: set[int],
    process_end_by_sq: dict[tuple[str, int], datetime],
    current_start: datetime,
    current_end: datetime,
    duration_min: float,
    slots: list,
    db: "Session",
    equipment_code: str,
) -> tuple[datetime, datetime]:
    """후공정 종료가 선행공정 종료 이상이 되도록 시작을 지연한다.

    불변식:
      - aligned_end >= pred_end_latest (후공정 끝 ≥ 선행공정 끝)
      - aligned_end - aligned_start == duration_min (블록 폭 유지, 캘린더 보정 오차 허용)

    시스 공정(저압시스/고압시스)은 pred_proc 외에 "연합" 종료도 함께 고려.
    pred_proc 가 None 이거나 process_end_by_sq 에 기록이 없으면 입력 그대로 반환.

    반환: (aligned_start, aligned_end)
    """
    pipeline_procs: list[str] = []
    if pred_proc:
        pipeline_procs.append(pred_proc)
    if process_name in ("저압시스", "고압시스"):
        pipeline_procs.append("연합")

    if not pipeline_procs:
        return current_start, current_end

    pred_end_latest: datetime | None = None
    for pp in pipeline_procs:
        for sq_i in group_sqs:
            pe = process_end_by_sq.get((pp, sq_i))
            if pe and pe < datetime.max:
                if pred_end_latest is None or pe > pred_end_latest:
                    pred_end_latest = pe

    if pred_end_latest is None:
        return current_start, current_end

    aligned_start = current_start
    aligned_end = current_end

    reverse_start = calculate_start_datetime(
        pred_end_latest, duration_min, db, equipment_code
    )
    if reverse_start > current_start:
        aligned_start = _find_available_slot(
            reverse_start, duration_min, slots, db, equipment_code
        )
        aligned_end = calculate_end_datetime(
            aligned_start, duration_min, db, equipment_code
        )

    # 캘린더 보정 오차 대비: end < pred_end_latest 면 bump
    if aligned_end < pred_end_latest:
        aligned_end = pred_end_latest

    return aligned_start, aligned_end


def _find_available_slot(
    earliest: datetime,
    duration_min: float,
    occupied_slots: list,
    db=None,
    equipment_code: str | None = None,
) -> datetime:
    """설비에서 가용한 첫 번째 슬롯 찾기.

    근무시간(08~22시) 기반 캘린더를 사용하여 실제 종료 시각을 계산한다.
    단순 timedelta 덧셈은 야간/주말을 무시하여 슬롯 겹침을 유발할 수 있다.
    """
    candidate = earliest
    sorted_slots = sorted(occupied_slots, key=lambda s: s[0])

    for slot_start, slot_end in sorted_slots:
        # 캘린더 기반 종료 시각으로 슬롯 겹침 판단
        if db is not None:
            candidate_end = calculate_end_datetime(
                candidate, duration_min, db, equipment_code
            )
        else:
            candidate_end = candidate + timedelta(minutes=duration_min)
        # 올림 일관성: auto_schedule line 922-925 는 end_dt 를 다음 정각으로 올림
        # 하여 timeline 에 등록한다. 여기서도 같은 규칙을 적용해야 slot_start 비교가
        # 실제 점유 시간과 일치 (Phase C Task 3 root cause — SH-A100 task 28942×28966
        # 19분 overlap 원인).
        if (
            candidate_end.minute > 0
            or candidate_end.second > 0
            or candidate_end.microsecond > 0
        ):
            candidate_end = candidate_end.replace(
                minute=0, second=0, microsecond=0
            ) + timedelta(hours=1)
        if candidate_end <= slot_start:
            # Fits before this slot
            return candidate
        if candidate < slot_end:
            candidate = slot_end  # Push after this slot

    return candidate


def _get_drum_winding_min(
    equipment_code: str,
    sq_mm2,
    speed_map: dict,
) -> float:
    """
    4-3: 드럼 권취 시간 반환.
    SpeedMaster.setup_start_min을 (equipment_code, sq_mm2) 키로 조회.
    매칭되는 레코드가 없으면 0 반환.
    """
    if sq_mm2 is None:
        return 0.0
    sq_key = float(sq_mm2)
    record = speed_map.get((equipment_code, sq_key))
    if record is None:
        # SQ 정확 매칭 실패 시 가장 가까운 SQ 레코드 탐색
        candidates = [
            (abs(k[1] - sq_key), v)
            for k, v in speed_map.items()
            if k[0] == equipment_code
        ]
        if candidates:
            record = min(candidates, key=lambda x: x[0])[1]
    if record is None:
        return 0.0
    return float(record.setup_start_min or 0)


def _get_stranding_setup_min(
    prev_sq: float | None,
    curr_sq: float | None,
    sq_to_wire_d: dict,
    spec_min: float,
    compound_min: float,
) -> float:
    """연선 공정 셋업 시간 결정 (3-tier).

    동일 SQ          → 0분 (교체 없음)
    다른 SQ, 동일 소선경 → compound_min (선재교체만, 기본 120분)
    다른 SQ, 다른 소선경 → spec_min     (규격교체만, 기본 240분)
    """
    if prev_sq is None or curr_sq is None:
        return spec_min
    if prev_sq == curr_sq:
        return 0.0
    prev_wd = sq_to_wire_d.get(int(prev_sq), None)
    curr_wd = sq_to_wire_d.get(int(curr_sq), None)
    if prev_wd and curr_wd and prev_wd == curr_wd:
        return compound_min  # 동일 소선경: 선재교체만
    return spec_min  # 다른 소선경: 규격교체만


def _get_tp_line_speed(
    equipment_code: str,
    sq_mm2,
    speed_map: dict,
) -> float | None:
    """
    4-5: T/P 설비 전용 라인 속도 반환.
    SpeedMaster에서 해당 설비 + SQ 조합의 line_speed_mpm을 읽는다.
    매칭 레코드 없으면 None 반환 (호출자가 기본값 사용).
    """
    if sq_mm2 is None:
        return None
    sq_key = float(sq_mm2)
    record = speed_map.get((equipment_code, sq_key))
    if record is None:
        # 근사 SQ 탐색
        candidates = [
            (abs(k[1] - sq_key), v)
            for k, v in speed_map.items()
            if k[0] == equipment_code
        ]
        if candidates:
            record = min(candidates, key=lambda x: x[0])[1]
    if record is None:
        return None
    speed = record.line_speed_mpm
    return float(speed) if speed else None


def _get_sheath_type(batch: ProductionBatch) -> str:
    """
    10-3: 배치에서 시스 재질 추출.
    ProductionBatch에 별도 sheath_type 컬럼이 없으므로 remarks 필드를
    우선 확인하고, 없으면 product_group에서 추론한다.
    인식 가능한 값: HFPO, LLDPE, PVC (기본값)
    """
    # remarks 또는 product_group에 재질 명시된 경우
    for field_val in (batch.remarks or "", batch.product_group or ""):
        upper = field_val.upper()
        if "HFPO" in upper:
            return "HFPO"
        if "LLDPE" in upper:
            return "LLDPE"
        if "PVC" in upper:
            return "PVC"
    return "PVC"  # 기본값


def _filter_by_sheath_routing(
    batch: ProductionBatch,
    equipment: list[EquipmentMaster],
) -> list[EquipmentMaster]:
    """
    10-3: 시스 재질에 따른 설비 라우팅 필터.
    - HFPO → equipment_code 또는 equipment_name에 'HFPO' 포함 설비만
    - LLDPE → equipment_code == 'A150' 설비만
    - PVC   → 표준 라우팅 (필터 없음)
    """
    sheath_type = _get_sheath_type(batch)
    routing_key = _SHEATH_ROUTING.get(sheath_type, "PVC")

    if routing_key == "PVC":
        # 표준 라우팅 — 제약 없음
        return equipment

    if routing_key == "HFPO":
        # HFPO 전용 설비 필터
        filtered = [
            eq
            for eq in equipment
            if "HFPO" in (eq.equipment_code or "").upper()
            or "HFPO" in (eq.equipment_name or "").upper()
        ]
        # HFPO 전용 설비가 없으면 전체 허용 (fallback — 경고는 checker에서 처리)
        return filtered if filtered else equipment

    if routing_key == "A150":
        # LLDPE → A150 고정
        filtered = [eq for eq in equipment if eq.equipment_code == "A150"]
        return filtered if filtered else equipment

    return equipment


def reschedule(
    run_label: str, db: Session, *, base_date: datetime | None = None
) -> dict:
    """
    1-3: 긴급 변경 대응 — 기존 스케줄을 초기화하고 재스케줄링 수행.

    frozen 배치(in_progress/completed)의 schedule_tasks는 보존하고,
    planned/scheduled 배치의 schedule_tasks만 삭제 후 auto_schedule을 재실행한다.
    Returns: auto_schedule과 동일한 결과 dict + "cleared_tasks" 수
    """
    # run_stage1_update()와 동일하게 status != "planned"인 배치를 보호
    # wip_complete: WIP 소진 완료 배치 — 재스케줄 시에도 반드시 보존
    # in_progress/completed: 사용자 수동 설정 — 반드시 보존
    # scheduled: 기존 스케줄 결과 — 재스케줄 대상이므로 planned으로 초기화
    _ALWAYS_FROZEN = {"in_progress", "completed", "wip_complete"}

    # frozen 배치의 batch_id 수집 — 해당 schedule_tasks는 삭제하지 않음
    all_batches = (
        db.query(ProductionBatch).filter(ProductionBatch.run_label == run_label).all()
    )
    frozen_batch_ids = {b.batch_id for b in all_batches if b.status in _ALWAYS_FROZEN}

    # frozen 배치에 속하지 않는 schedule_tasks만 삭제
    existing_tasks = (
        db.query(ScheduleTask).filter(ScheduleTask.run_label == run_label).all()
    )
    cleared_count = 0
    for task in existing_tasks:
        if task.batch_id not in frozen_batch_ids:
            db.delete(task)
            cleared_count += 1

    # 배치 상태 초기화 — frozen 배치는 건드리지 않음
    # scheduled → planned (재스케줄 대상), wip_complete/in_progress/completed 유지
    for batch in all_batches:
        if batch.status == "scheduled":
            batch.status = "planned"
            batch.equipment_code = None

    db.flush()  # 삭제 반영 후 재스케줄

    # 재스케줄링 실행 — auto_schedule은 status='planned' 배치만 처리하므로 frozen 배치 안전
    result = auto_schedule(run_label, db, base_date=base_date)
    result["cleared_tasks"] = cleared_count
    return result
