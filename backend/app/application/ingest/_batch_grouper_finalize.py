"""create_batches() 의 후처리 단계 — 정렬 / 그룹 / DB 기록 / dedupe.

Phase 2 refactor (Task 2.2): batch_grouper.create_batches() 의 line 795~918
'잔량 흑색 + 정렬 + 그룹 부여 + DB 기록' 블록과 deduplicate_group_headers
함수를 분리. 각 phase 는 자유 함수로 노출되어 create_batches() 에서 순차
호출된다 (플로우 보존).
"""

import math
from collections import defaultdict
from datetime import date

from sqlalchemy.orm import Session

from app.application.ingest._batch_grouper_loader import _GrouperInputs
from app.application.ingest.batch_helpers import _process_order
from app.domain.batch_sheath_keys import (
    _SHEATH_COLOR_RANK,
    _TFR8_PATTERN,
    _compose_sheath_group_key,
)
from app.infrastructure.models.production_batch import ProductionBatch


def apply_remnant_blackout(batches: list[ProductionBatch], ctx: _GrouperInputs) -> None:
    """잔량 흑색 소진 후처리 (3-4) — 원본 line 795-807.

    배치 생성이 모두 끝난 후에 길이 기준으로 일괄 처리한다.
    잔량(짧은 배치)의 외피 색상을 흑색으로 맞춰 흑색 원재료 재고를 우선 소진.
    """
    remnant_threshold_m = ctx.remnant_threshold_m
    for b in batches:
        batch_len = float(b.total_length_m) if b.total_length_m is not None else 0.0
        if 0 < batch_len < remnant_threshold_m:
            b.sheath_color = "흑"
            existing_remarks = b.remarks or ""
            b.remarks = (
                f"{existing_remarks} 잔량흑색소진".strip()
                if existing_remarks
                else "잔량흑색소진"
            )


def sort_batches(batches: list[ProductionBatch]) -> None:
    """공정 → SQ 내림차순 → 전압 → 연선방식 → 다심 우선 → 색상 그루핑 — 원본 line 809-832.

    SQ 내림차순을 최우선으로 — 원본 계획서와 동일한 400SQ→300SQ→240SQ 정렬.
    5-2: stranding_type 으로 연선방식 격리, 10-4: voltage 로 전압별 드럼 분류.
    5-3: due_date 3일 이내 차이 시 다심(core_count>1) 우선 처리.
    """
    today: date = date.today()

    def _sort_key(b: ProductionBatch) -> tuple:
        due = b.due_date or date.max
        days_until_due = (due - today).days if due != date.max else 9999
        due_bucket = math.floor(days_until_due / 3)
        multi_core_penalty = 0 if (b.core_count or 1) > 1 else 1
        return (
            _process_order(b.process_name),  # 공정 순서
            b.batch_seq or 0,  # batch_seq: 61연선 코어(0)가 메인(1)보다 먼저
            -(b.sq_mm2 or 0),  # SQ 내림차순 (최우선)
            b.voltage or "",  # 전압별 드럼 분류 (10-4)
            b.stranding_type or "",  # 연선방식 구분 (5-2)
            due_bucket,  # 납기 버킷 (빠른 납기 우선)
            multi_core_penalty,  # 다심 우선 완성 (5-3)
            b.sheath_color or "",  # 색상 전환 최소화
            b.core_colors or "",
        )

    batches.sort(key=_sort_key)


def apply_sheath_secondary_sort(batches: list[ProductionBatch]) -> None:
    """시스(저압/고압) 전용 2차 정렬 — 원본 line 834-852.

    납기 최우선, 같은 주차 내에서만 색상 묶기 (체인지오버 최소화).
    Python sort 가 stable 이므로 비시스 배치의 상대 순서는 _sort_key 결과 유지.
    """

    def _sheath_chain_key(b: ProductionBatch) -> tuple:
        if b.process_name not in ("저압시스", "고압시스"):
            # 비시스는 고정 키 → 원순서 유지 (stable sort)
            return (0, 0, 0, 0)
        color = (b.sheath_color or "").strip() or "기타"
        color_rank = _SHEATH_COLOR_RANK.get(color, 99)
        if b.due_date:
            yr, wk, _ = b.due_date.isocalendar()
            due_wk_int = yr * 100 + wk
            due_ord = b.due_date.toordinal()
        else:
            due_wk_int = 999999
            due_ord = 9999999
        return (1, due_wk_int, color_rank, due_ord)

    batches.sort(key=_sheath_chain_key)


def assign_batch_groups(batches: list[ProductionBatch]) -> None:
    """배치 그룹 부여 — 원본 line 854-896.

    같은 (process_name, sq_mm2) 를 하나의 batch_group 으로 묶는다.
    원본 계획서의 "120SQ--->1틀(연선5285)" 묶음 = 1 batch_group = 1 간트 블록.
    틀분할(lot_stranding) 이 있으면 lot_idx 별로 별도 그룹 → 틀당 1블록.
    CORE-/ST- 등 Phase 1 에서 이미 할당된 batch_group 은 보존.
    """
    group_counters: dict[str, int] = {}
    for b in batches:
        proc = b.process_name
        sq_key = int(b.sq_mm2 or 0)
        group_key = f"{proc}_{sq_key}SQ"

        # 저압절연: 고내화 제품군(TFR-8(…))은 일반 제품과 혼합 생산 불가 → 별도 그룹
        if (
            proc == "저압절연"
            and b.product_group
            and _TFR8_PATTERN.search(b.product_group)
        ):
            group_key = f"{proc}_{sq_key}SQ_고내화"

        # 시스: 색상 + 반주차(H1/H2) + SQ 기준으로 묶음 (`_compose_sheath_group_key`)
        if proc in ("저압시스", "고압시스"):
            group_key = _compose_sheath_group_key(
                proc=proc,
                color=b.sheath_color,
                due_date=b.due_date,
                sq=b.sq_mm2,
            )

        # CORE-/ST- 등 Phase 1에서 이미 할당된 batch_group은 보존
        if b.batch_group:
            if b.batch_group not in group_counters:
                group_counters[b.batch_group] = len(group_counters) + 1
            continue

        if group_key not in group_counters:
            group_counters[group_key] = len(group_counters) + 1
        b.batch_group = group_key


def persist_batches(
    db: Session, run_label: str, batches: list[ProductionBatch]
) -> dict[str, int]:
    """DB 기록 + 공정별 집계 — 원본 line 898-900 + 913-915.

    db.add_all + flush 후 by_process dict 반환. 커밋은 호출자에게 위임
    (Stage 1 트랜잭션 경계는 더 상위에서 관리).
    """
    db.add_all(batches)
    db.flush()  # batch_id 자동 채번 (autoincrement)을 트리거하되 커밋은 호출자에게 위임

    by_process: dict[str, int] = {}
    for b in batches:
        proc = b.process_name
        by_process[proc] = by_process.get(proc, 0) + 1
    return by_process


def deduplicate_group_headers(run_label: str, db: Session) -> dict:
    """배치 중복 정리 — "한 batch_group = 한 헤더, 수주-라인별 1 row" 불변식 복원.

    Why: create_batches / incremental Stage1 update / auto-split 복합 경로에서
    같은 (batch_group, sales_order_id, sales_order_line, batch_seq) 튜플이
    다중 insert 되는 현상 확인 (KBI PoC run 기준 전 공정 362건 중복). 대표
    증상:
      - batch_seq=-1 헤더 2개 공존 → CP-SAT 가 한 헤더만 스케줄링 → 다른
        수주 통째로 누락 (예: 300SQ 9500m 분량이 계획에 안 나타남)
      - UI 라벨 ("1틀 9500m") 이 bar 가 실제 처리량 (3틀 28500m) 와 불일치
        — label 은 첫 헤더에서, 처리시간은 두번째 헤더에서 오는 혼선

    본 함수는 근본 경로 수정 전 post-hoc 방어선으로 invariant 를 보장한다.
    근본 수정 (create_batches idempotent 화 / split 잔재 정리) 은 후속 과제.

    Dedupe 절차:
      1. batch_seq != -1 (CORE=0, 공정 sub-batch >= 1) 먼저 dedup — 같은
         (batch_group, so, line, seq, process_name) 튜플은 최소 batch_id
         하나만 유지, 나머지 delete.
      2. batch_seq = -1 (연선 aggregate header) 의 중복 처리 — 같은 batch_group
         에 헤더 2개 이상이면 canonical (가장 이른 납기 → 우선순위 → batch_id)
         하나만 남김. 남은 sub-batch (1번에서 정리된 상태) 기준으로
         total_length_m / drum_count / est_duration / due / priority 재집계.

    Returns: {
        "non_header_deleted": int,   # seq != -1 중복 삭제 row 수
        "headers_deleted": int,      # seq = -1 중복 삭제 row 수
        "groups_rebalanced": int,    # 재집계된 batch_group 수 (헤더 재집계 트리거된 그룹)
    }
    """
    stats: dict = {
        "non_header_deleted": 0,
        "headers_deleted": 0,
        "groups_rebalanced": 0,
    }

    # ── 1. Non-header 중복 제거 (batch_seq != -1) ──────────────────────────
    # 키: (batch_group, sales_order_id, sales_order_line, batch_seq, process_name).
    # process_name 까지 포함 이유: batch_group 문자열에 공정명이 내재돼 있지만
    # CORE 배치처럼 cross-process 가능성 방어. 동일 키 2회 이상 → 최소 batch_id
    # 만 canonical, 나머지 delete.
    non_headers = (
        db.query(ProductionBatch)
        .filter(
            ProductionBatch.run_label == run_label,
            ProductionBatch.batch_seq != -1,
        )
        .order_by(ProductionBatch.batch_id)
        .all()
    )
    seen: set[tuple] = set()
    to_delete_nh: list = []
    for b in non_headers:
        if b.batch_group is None:
            continue
        key = (
            b.batch_group,
            b.sales_order_id,
            b.sales_order_line,
            b.batch_seq,
            b.process_name,
        )
        if key in seen:
            to_delete_nh.append(b)
        else:
            seen.add(key)
    for b in to_delete_nh:
        db.delete(b)
    stats["non_header_deleted"] = len(to_delete_nh)
    if to_delete_nh:
        db.flush()

    # ── 2. Header (batch_seq=-1) 중복 제거 + 재집계 ────────────────────────
    headers = (
        db.query(ProductionBatch)
        .filter(
            ProductionBatch.run_label == run_label,
            ProductionBatch.batch_seq == -1,
        )
        .all()
    )
    by_group: dict[str, list] = defaultdict(list)
    for h in headers:
        if h.batch_group:
            by_group[h.batch_group].append(h)

    for bg, hs in by_group.items():
        # Canonical: 가장 이른 납기 → 가장 높은 우선순위(작은 숫자) → 작은 batch_id
        hs_sorted = sorted(
            hs,
            key=lambda h: (
                h.due_date or date.max,
                h.customer_priority or 99,
                h.batch_id or 0,
            ),
        )
        canon = hs_sorted[0]
        others = hs_sorted[1:]

        # 재집계 source: 1번에서 dedup 완료된 sub-batch.
        group_subs = (
            db.query(ProductionBatch)
            .filter(
                ProductionBatch.run_label == run_label,
                ProductionBatch.batch_group == bg,
                ProductionBatch.batch_seq >= 1,
            )
            .all()
        )
        if not group_subs:
            # 헤더만 있고 sub 없는 특수 케이스 — 재집계 skip, 중복 헤더만 삭제.
            for h in others:
                db.delete(h)
            stats["headers_deleted"] += len(others)
            if others:
                stats["groups_rebalanced"] += 1
            continue

        core_mul = int(canon.core_count or 1)
        raw_total = sum(float(s.total_length_m or 0) for s in group_subs)
        net_qty = raw_total * core_mul
        lot_size = float(canon.drum_length_m or 0)

        if lot_size > 0 and net_qty > 0:
            drum_count = max(math.ceil(net_qty / lot_size), 1)
            total_len_m = drum_count * lot_size
        elif net_qty > 0:
            drum_count = 1
            total_len_m = net_qty
        else:
            # net=0 인 그룹은 헤더 자체가 무의미 — 현 헤더 값 유지.
            drum_count = int(canon.drum_count or 0)
            total_len_m = float(canon.total_length_m or 0)

        earliest_due = min(
            (s.due_date for s in group_subs if s.due_date),
            default=canon.due_date,
        )
        best_priority = min(
            (s.customer_priority or 99 for s in group_subs),
            default=99,
        )
        line_speed = float(canon.line_speed_mpm) if canon.line_speed_mpm else 0.0
        est_dur = (
            total_len_m / line_speed if line_speed > 0 else canon.estimated_duration_min
        )
        surplus = max(0.0, total_len_m - raw_total * core_mul)

        # 재집계로 실제 변경이 발생했는지 여부 판단 (통계용).
        rebalanced = (
            len(others) > 0
            or abs(float(canon.total_length_m or 0) - total_len_m) > 0.5
            or int(canon.drum_count or 0) != drum_count
        )

        canon.total_length_m = total_len_m
        canon.drum_count = drum_count
        canon.due_date = earliest_due
        canon.customer_priority = best_priority
        canon.estimated_duration_min = est_dur
        canon.wip_output_expected_m = surplus
        if len(hs) > 1:
            canon.remarks = (
                (canon.remarks or "") + f" [dedup:헤더{len(hs)}→1 병합]"
            ).strip()

        for h in others:
            db.delete(h)

        stats["headers_deleted"] += len(others)
        if rebalanced:
            stats["groups_rebalanced"] += 1

    if stats["headers_deleted"] or stats["groups_rebalanced"]:
        db.flush()

    return stats
