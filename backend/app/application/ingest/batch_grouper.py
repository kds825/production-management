"""batch_grouping 패키지 — Stage 1 본체 (`create_batches`, `deduplicate_group_headers`).

설계 의도:
- 수주 1건은 라우팅에 정의된 공정 수만큼 ProductionBatch 행으로 펼쳐진다.
- 모든 마스터 데이터를 함수 진입 시점에 메모리로 로드해 N+1 쿼리를 원천 차단한다.
- 정렬은 DB flush 전에 Python 레벨에서 수행해 INSERT 순서가 곧 작업 순서가 된다.

적용 제약 조건:
  2-2  외주 자동분류: SQ<=10 또는 product_group에 '고내화' 포함 시 외주 처리
  2-3  틀단위 분할: total_length_m > lot_stranding 이면 배치를 복수로 분할
  3-4  잔량 흑색 소진: total_length_m < 200m 배치는 sheath_color='흑' 강제
  5-2  연선방식 구분: 정렬 키에 stranding_type 추가 (압축/원형/수밀 혼합 방지)
  5-3  다심 우선 완성: 납기 3일 이내 차이 시 core_count>1 우선
  7-1  불량 재작업 버퍼: total_length_m에 defect_buffer_pct(기본 5%) 가산
  10-4 전압별 드럼 분류: 정렬 키에 voltage 추가
  10-5 4심 계산법: core_count==4 시 product_type 키를 '4C'로 사용
"""

from datetime import date

from sqlalchemy.orm import Session

from app.infrastructure.models.production_batch import ProductionBatch
from app.application.ingest._batch_grouper_loader import (
    load_grouper_inputs,
)
from app.application.ingest._batch_grouper_finalize import (
    apply_remnant_blackout,
    apply_sheath_secondary_sort,
    assign_batch_groups,
    deduplicate_group_headers,
    persist_batches,
    sort_batches,
)
from app.application.ingest._batch_grouper_per_order import (
    create_per_order_batches,
)
from app.application.ingest._batch_grouper_strand import (
    create_strand_batches,
)
from app.application._shared.constraint_params import ConstraintParams


def _is_outsource_rule(
    sq: float,
    product_group: str | None,
    customer_name: str | None,
) -> bool:
    """외주 자동분류 룰 (constraint 2-2). 호출측에서 sq/제품군/고객명 추출 후 위임.

    조건 (Phase 2 동일):
      (1) SQ ≤ 10  : 소단면적 특수 공정은 사내 설비로 생산 불가
      (2) TFR-8(... + SQ == 16  : 고온 사양 특수 외주 전용
      (3) 아이마켓코리아 + TFR-GV 품목  : 고객 지정 외주
    """
    pg_orig = product_group or ""
    pg_upper = pg_orig.upper()
    customer = customer_name or ""
    return (
        sq <= 10
        or ("TFR-8(" in pg_orig and sq == 16)
        or (customer == "아이마켓코리아" and "TFR-GV" in pg_upper)
    )


def is_outsource_batch(batch: ProductionBatch) -> bool:
    """ProductionBatch 가 외주 룰 (2-2) 에 해당하는지 판정.

    decision_card phrasing._resolve_key 가 "outsource" 키로 dispatch 할 때 사용.
    동일 룰을 SalesOrder 단계 (`create_batches`/`build_strand_batches`) 와
    공유하기 위해 `_is_outsource_rule` 에 위임.

    Note: is_enabled 토글은 이미 분류된 batch 를 재해석하는 게 아니므로 무시.
    분류 시점의 토글이 SalesOrder 단계 wrapper (`_is_outsource_rule_with_toggle`)
    에서 적용된다.
    """
    return _is_outsource_rule(
        sq=float(batch.sq_mm2 or 0),
        product_group=batch.product_group,
        customer_name=batch.customer_name,
    )


# ─── Track A (2026-04-28): 하드코딩 룰 → DB-toggle wrapper helper ─────────
# 2-2 외주 / 2-4 61연선 / 5-5 TFR-GV. ConstraintParams.is_rule_enabled 게이트.
# 룰 본문은 변경 X — wrapper 가 게이트 통과 시에만 본문 위임 (DRY).


def _is_outsource_rule_with_toggle(
    sq: float,
    product_group: str | None,
    customer_name: str | None,
    *,
    params: ConstraintParams,
) -> bool:
    """`_is_outsource_rule` + 2-2 is_enabled 게이트.

    is_enabled=False → 항상 False (외주 분류 미적용 → 사내 routing 폴백).
    행 미존재 (legacy DB) → True 폴백 → 기존 동작 유지.
    """
    if not params.is_rule_enabled("2-2", default=True):
        return False
    return _is_outsource_rule(sq, product_group, customer_name)


def _should_skip_stranding(
    product_group: str | None,
    sq: float,
    *,
    params: ConstraintParams,
) -> bool:
    """5-5 TFR-GV 절연 생략 룰 + is_enabled 게이트.

    Default rule: TFR-GV 제품군 + sq <= 25 → stranding 공정 생략 (단선 접지선).
    is_enabled=False → 항상 False (모든 TFR-GV sq<=25 가 normal stranding 수행).
    """
    if not params.is_rule_enabled("5-5", default=True):
        return False
    return "TFR-GV" in (product_group or "").upper() and sq <= 25


def _is_61strand_rule(
    sq: float,
    conductor_material: str | None,
    *,
    params: ConstraintParams,
) -> bool:
    """2-4 61연선 분리 룰 + is_enabled 게이트.

    Default rule: sq >= 300 + conductor_material == "CU" → 61연선 2단계 (T6B0 → 54BO).
    is_enabled=False → 항상 False (sq>=300 CU 도 normal 단일 stranding).
    """
    if not params.is_rule_enabled("2-4", default=True):
        return False
    return sq >= 300 and conductor_material == "CU"


def create_batches(
    run_label: str,
    db: Session,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    frozen_order_keys: set[tuple] | None = None,
) -> dict:
    """
    run_label에 해당하는 sales_order를 읽어서 production_batch를 생성한다.

    ERP 외주계획 플래그(is_outsourced)와 무관하게 모든 수주를 대상으로 배치를 생성한다.
    (외주 플래그는 특정 공정 외주를 의미하며, 연선/절연/시스 계획 대상에서 제외하지 않는다.)
    코드 내 하드코딩된 설비 제약 조건(SQ≤10 등)에 의한 외주 분류만 적용한다.
    라우팅이 없거나 SQ 파싱에 실패하면 warnings에 기록 후 계속 진행한다.

    Args:
        frozen_order_keys: 증분 업데이트 시 동결된 수주 키 set. (order_id, order_line)
            이 키에 해당하는 수주는 이미 배치가 존재하므로 배치 생성에서 제외한다.

    Returns:
        {
            "total_batches": int,
            "by_process": {process_name: count, ...},
            "warnings": [str, ...]
        }
    """
    result: dict = {
        "total_batches": 0,
        "by_process": {},
        "warnings": [],
        "outsource_count": 0,
    }

    # ── 입력 로드 (Task 2.1 추출) ──────────────────────────────────────────
    # ConstraintConfig 프리페치 + SalesOrder + WIP 매칭 + 마스터 일괄 로드 +
    # 파라미터 fetch + speed_lookup 빌드 → _GrouperInputs.
    ctx = load_grouper_inputs(
        run_label,
        db,
        date_from=date_from,
        date_to=date_to,
        frozen_order_keys=frozen_order_keys,
    )
    if ctx.excluded_count > 0:
        result["warnings"].append(
            f"동결 수주 {ctx.excluded_count}건 제외 (이미 배치 존재)"
        )

    # ── Phase 1: 연선 그룹 배치 생성 (Task 2.4 추출) ───────────────────────
    # 토글 wrapper (`_is_outsource_rule_with_toggle`, `_should_skip_stranding`)
    # 는 batch_grouper 모듈 내부에 머물러 있고, helper 가 callable 로 주입받는다
    # (순환 import 회피).
    batches: list[ProductionBatch] = create_strand_batches(
        ctx,
        is_outsource_rule_with_toggle=_is_outsource_rule_with_toggle,
        should_skip_stranding=_should_skip_stranding,
    )

    # ── Phase 2: 수주별 배치 생성 (절연·시스 등 연선 외 공정) ─ Task 2.3 추출 ─
    # 토글 wrapper 들 (`_is_outsource_rule_with_toggle`, `_should_skip_stranding`,
    # `_is_61strand_rule`) 는 batch_grouper 모듈 내부에 머물러 있고, helper
    # 가 callable 로 주입받는다 (순환 import 회피).
    batches.extend(
        create_per_order_batches(
            ctx,
            result,
            is_outsource_rule_with_toggle=_is_outsource_rule_with_toggle,
            should_skip_stranding=_should_skip_stranding,
            is_61strand_rule=_is_61strand_rule,
        )
    )

    # ── 후처리 (Task 2.2 추출) ─────────────────────────────────────────────
    # 잔량 흑색 소진 (3-4) → 정렬 → 시스 2차 정렬 → 그룹 부여 → DB 기록 →
    # 중복 정리 (deduplicate_group_headers) 순. 각 단계는 _batch_grouper_finalize
    # 의 자유 함수로 분리되어 있다.
    apply_remnant_blackout(batches, ctx)
    sort_batches(batches)
    apply_sheath_secondary_sort(batches)
    assign_batch_groups(batches)
    result["by_process"] = persist_batches(db, run_label, batches)
    result["total_batches"] = len(batches)

    # 중복 정리는 invariant 방어선 — 근본 경로 수정 전 post-hoc 정리.
    result["dedupe"] = deduplicate_group_headers(run_label, db)

    return result
