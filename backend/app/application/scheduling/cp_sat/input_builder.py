"""`cp_sat_schedule` DB-로드 블록의 pure-function 추출 (Task 1.1).

이 모듈은 아직 `cp_sat_schedule` 내부 동작을 바꾸지 않는다 — Week 1 파러티
하니스가 값 객체 `SolverInput` 을 통해 "같은 DB → 같은 입력" 을 재현할 수
있도록 하는 scaffold 일 뿐이다. Rev 3 플랜 §Task 1.1 의 "behaviour-neutral
extraction" 원칙에 따라:

1. 쿼리 순서·필터·정렬·WIP-skip 로직은 `cp_sat_schedule` 1067-1167 라인과
   **1:1 동일** (단 한 글자라도 달라지면 파러티가 깨진다).
2. 부가 필드(예: 가상의 "sales_orders") 는 **추가하지 않는다**. 오직 현재
   솔버가 실제로 `db` 에서 읽는 값만 담는다 — zero speculation.
3. `result["warnings"]`/`result["wip_skipped"]` 같은 **side-effect 는 이
   함수에서 수행하지 않는다**. 카운터(wip_skipped int) 만 반환하고, 호출
   측(cp_sat_schedule) 이 기존대로 result dict 에 기록한다.

필드는 `cp_sat_schedule` 이 변수로 쓰는 이름과 동일하게 맞춰 override
경로가 "값 객체 → 로컬 변수" 리바인드 한 줄로 끝나도록 했다.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.domain.constants import PROCESS_ORDER
from app.infrastructure.models.drum_lot_master import DrumLotMaster
from app.infrastructure.models.equipment_master import EquipmentMaster
from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.speed_master import SpeedMaster
from app.application._shared.constraint_params import ConstraintParams
from app.domain.constants import _DEFAULT_WELDING_MIN, _WIP_SKIP_PROCESSES


class SolverInput(BaseModel):
    """`cp_sat_schedule` 이 DB 에서 로드하는 값들의 immutable 스냅샷.

    용도:
      - 파러티 하니스(Task 1.4-1.5): pre/post 리팩터가 동일 DB 상태에서
        동일 입력을 받는지 bit-exact 비교.
      - 향후(Week 3 이후) 솔버 리플레이/스냅샷 테스트 기반.

    주의:
      - ORM 인스턴스(`ProductionBatch`, `EquipmentMaster`, `SpeedMaster`,
        `ConstraintParams`) 를 그대로 담는다 — 직렬화는 파러티 하니스에서
        별도 해셔로 처리. 그래서 `arbitrary_types_allowed=True`.
      - `frozen=True` 로 값 객체화 — 누가 실수로 필드를 바꿔도 override 된
        솔버 경로가 오염되지 않는다.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    # ── §1. 배치 (WIP-skip 후, 공정/seq/due/priority/sq 정렬 완료) ───────
    # `cp_sat_schedule` L1068-1113 과 동일. batch.status 는 WIP-skip 시
    # `"wip_complete"` 로 mutate 됨 — override 경로에서도 동일한 side-effect
    # 를 남겨야 하므로 caller 쪽에서 batch 객체를 세션에 연결 후 사용해야 함
    # (파러티 하니스는 동일 세션을 공유하여 이 전제를 만족).
    batches: list[ProductionBatch]
    """그룹핑·공정순 정렬이 끝난 스케줄 대상 배치 리스트.

    `cp_sat_schedule` §1 의 최종 `batches` 와 bit-exact 동일해야 한다
    (WIP-skip 이후, 공정/seq/due/priority/sq 2단계 정렬 반영)."""

    wip_skipped: int
    """WIP 재고로 스킵된 배치 수 — caller 가 `result["wip_skipped"]` 에 기록.

    0 이면 기존 코드에서 `result` 에 키를 넣지 않으므로, `> 0` 일 때만
    `result["wip_skipped"] = wip_skipped` 를 호출해야 side-effect 동일."""

    # ── §2. 기준일시 (run_label prefix 에서 파생 또는 KST now 폴백) ─────
    base_date: datetime
    """CP-SAT 시간축의 origin (근무 분 0 지점).

    `cp_sat_schedule` §2 L1117-1128 와 동일. 호출측이 `base_date` 를
    명시 전달하면 그 값 그대로, 아니면 `run_label` 앞 8자리(YYYYMMDD) 에서
    `datetime(..., 8, 0, 0)` naive 로 파생. 실패 시 KST now 의 08:00 으로
    폴백(tzinfo 제거)."""

    # ── §3. 설비 마스터 (공정별 그룹화) ──────────────────────────────────
    equipment_list: list[EquipmentMaster]
    """전체 EquipmentMaster 레코드 — §6-a `all_eq_codes` 집합 생성에 사용.

    `cp_sat_schedule` L1131 과 동일 (`db.query(EquipmentMaster).all()`)."""

    equipment_by_process: dict[str, list[EquipmentMaster]]
    """공정명 → 해당 공정에 배치 가능한 설비 리스트.

    §5 그룹 메타 계산에서 `candidate = equipment_by_process.get(process, [])`
    로 조회 → sheath routing / SQ 필터 적용 후 `_find_eligible_equipment`."""

    # ── §4. SpeedMaster 기반 lookup 맵 (N+1 방지용 프리페치) ────────────
    speed_map: dict[tuple[str, float], SpeedMaster]
    """(equipment_code, cross_section) → SpeedMaster 행 lookup.

    `_compute_group_duration*` / `_get_drum_winding_min` 이 사용. 키의 float
    변환은 `cp_sat_schedule` L1142 와 동일 (`float(sr.cross_section or 0)`)."""

    color_setup_map: dict[str, float | None]
    """설비코드 → 색상 교체 setup 분.

    §4-2 색상교체 판정 시 ConstraintParams 폴백 전에 먼저 조회. 한 설비에
    복수 SQ row 가 있으면 첫 non-null 을 채택 (L1147-1152 로직 동일)."""

    # ── §5. ConstraintConfig 프리페치 ────────────────────────────────────
    constraint_params: ConstraintParams
    """4-2 색상교체/4-4 용접/기타 ConstraintConfig 파라미터 캐시.

    `cp_sat_schedule` L1155 의 `ConstraintParams.load(db)` 결과와 동치."""

    welding_min: float
    """4-4 용접 소요 분 (ConstraintParams 4-4.welding_min, default
    `_DEFAULT_WELDING_MIN`).

    §5 그룹 duration 계산 시 연선→(절연 skip)→시스 chain 의 welding 보정에
    사용 (현 코드에서는 `_DEFAULT_WELDING_MIN` fallback 경로)."""

    # ── §6. SQ → 소선경 매핑 (연선 셋업 3-tier 계산용) ──────────────────
    sq_to_wire_d: dict[int, float]
    """SQ(mm²) → wire_diameter (mm) 매핑. DrumLotMaster 에서 non-null 만 채택.

    `_get_stranding_setup_min` 3-tier (굵은/중간/가는) 분기에 사용. 키는
    `int(cross_section)` — `cp_sat_schedule` L1164 와 동일."""


def build_solver_input(
    db: Session,
    run_label: str,
    *,
    base_date: datetime | None = None,
) -> SolverInput:
    """`cp_sat_schedule` DB-로드 블록(L1067-1167) 의 pure 함수 미러.

    **Behavior 동일성 보장 규칙**:
      - 쿼리 순서·필터·정렬·WIP-skip 조건·base_date 폴백 경로는 원본과
        한 글자도 달라지면 안 된다. 수정 시 원본도 같이 수정해야 파러티
        하니스(Task 1.4) 가 green 유지.
      - "배치 없음" / "스케줄링 가능한 배치 그룹 없음" 과 같은 early-return
        경고는 이 함수에서 발생시키지 않는다 — 호출측(cp_sat_schedule) 이
        기존 흐름대로 검사하도록 `batches=[]` 를 그대로 전달할 수도 있다.

    Args:
      db: 열려있는 SQLAlchemy Session. `batches` 필드는 이 세션에 attach 된
        ORM 인스턴스를 담으므로, override 경로에서 `cp_sat_schedule` 이
        같은 세션으로 `log_decision`/`ScheduleTask` write 를 할 수 있어야 함.
      run_label: 스케줄 실행 라벨. `base_date` 기본값을 앞 8자리에서 파생
        (`YYYYMMDD → datetime(..., 8, 0, 0)`).
      base_date: 명시적 기준일시 (kw-only). None 이면 run_label 에서 파생.

    Returns:
      immutable `SolverInput`.
    """
    # ── §1. 배치 로드 (cp_sat_schedule L1068-1088 미러) ─────────────────
    batches = (
        db.query(ProductionBatch)
        .filter(
            ProductionBatch.run_label == run_label, ProductionBatch.status == "planned"
        )
        .order_by(
            ProductionBatch.due_date.asc(),
            ProductionBatch.customer_priority.asc(),
            ProductionBatch.batch_seq.asc(),
        )
        .all()
    )
    batches.sort(
        key=lambda b: (
            PROCESS_ORDER.get(b.process_name, 50),
            b.batch_seq or 0,
            b.due_date or date.max,
            b.customer_priority or 99,
            -(float(b.sq_mm2 or 0)),
        )
    )

    # WIP 스킵 (L1094-1115 미러). 주의: `batch.status` 를 in-place 로
    # mutate 한다 — 원본과 동일한 side-effect 이므로 유지. `wip_skipped`
    # 는 caller 가 `result["wip_skipped"]` 에 기록.
    wip_ids = {b.wip_matched_id for b in batches if b.wip_matched_id}
    wip_stage_map: dict[int, str] = {}
    if wip_ids:
        from app.infrastructure.models.wip_inventory import WipInventory

        wips = db.query(WipInventory).filter(WipInventory.wip_id.in_(wip_ids)).all()
        wip_stage_map = {w.wip_id: w.process_stage or "" for w in wips}

    schedulable: list[ProductionBatch] = []
    wip_skipped = 0
    for b in batches:
        if b.wip_matched_id and b.wip_matched_id in wip_stage_map:
            skip_set = _WIP_SKIP_PROCESSES.get(wip_stage_map[b.wip_matched_id], set())
            if b.process_name in skip_set:
                b.status = "wip_complete"
                wip_skipped += 1
                continue
        schedulable.append(b)
    batches = schedulable

    # ── §2. 기준일시 (L1117-1128 미러) ──────────────────────────────────
    if base_date is None:
        try:
            dp = run_label.split("_")[0]
            base_date = datetime(int(dp[:4]), int(dp[4:6]), int(dp[6:8]), 8, 0, 0)
        except Exception:
            from zoneinfo import ZoneInfo

            kst = datetime.now(ZoneInfo("Asia/Seoul"))
            base_date = kst.replace(hour=8, minute=0, second=0, microsecond=0).replace(
                tzinfo=None
            )

    # ── §3. 설비 마스터 (L1131-1134 미러) ───────────────────────────────
    equipment_list = db.query(EquipmentMaster).all()
    equipment_by_process: dict[str, list[EquipmentMaster]] = {}
    for eq in equipment_list:
        equipment_by_process.setdefault(eq.process_name, []).append(eq)

    # ── §4. SpeedMaster lookup 맵 (L1140-1152 미러) ─────────────────────
    _speed_rows = db.query(SpeedMaster).all()
    speed_map: dict[tuple[str, float], SpeedMaster] = {
        (sr.equipment_code, float(sr.cross_section or 0)): sr for sr in _speed_rows
    }
    color_setup_map: dict[str, float | None] = {}
    for sr in _speed_rows:
        code = sr.equipment_code
        if code not in color_setup_map:
            color_setup_map[code] = sr.setup_color_min
        elif color_setup_map[code] is None and sr.setup_color_min is not None:
            color_setup_map[code] = sr.setup_color_min

    # ── §5. ConstraintConfig + 용접 분 (L1155-1160 미러) ────────────────
    constraint_params = ConstraintParams.load(db)
    welding_min = constraint_params.get(
        "4-4", "welding_min", default=_DEFAULT_WELDING_MIN
    )

    # ── §6. SQ → 소선경 (L1163-1167 미러) ───────────────────────────────
    sq_to_wire_d: dict[int, float] = {
        int(d.cross_section): float(d.wire_diameter)
        for d in db.query(DrumLotMaster).all()
        if d.wire_diameter is not None
    }

    return SolverInput(
        batches=batches,
        wip_skipped=wip_skipped,
        base_date=base_date,
        equipment_list=equipment_list,
        equipment_by_process=equipment_by_process,
        speed_map=speed_map,
        color_setup_map=color_setup_map,
        constraint_params=constraint_params,
        welding_min=welding_min,
        sq_to_wire_d=sq_to_wire_d,
    )
