"""공정 선후관계 hard constraint — 연선→절연→시스 + CORE→ST.

ConstraintConfig 매핑: implicit (도메인 invariant — process_master 의 routing).
원래 ``model_builder.build_model`` §6-d / §6-e 블록.

핵심:
  - PREDECESSOR_PROCESS dict 가 정의한 공정 사슬에서 동일 SQ 그룹 쌍을 묶어
    "선행 그룹 첫 드럼 완료 후 후행 그룹 시작" hard constraint 부여.
  - 첫 드럼 시간은 lot_count 로 분할한 cpsat_dur — header 배치 기준, CORE 그룹은
    drum_count 합산, 그 외는 batch 수로 fallback.
  - 추가 hard: 후공정 end ≥ 선행공정 end (파이프라인 유휴 최소 역산).
  - CORE → ST 선행 (AL6BO 첫 드럼 → 54BO 시작).

Why pure helper module:
  ``build_model`` 의 824 LOC 내 인라인이었던 §6-d / §6-e 를 80 LOC 모듈로 분리.
  proc_groups_by_sq 는 §6-f idle_terms 에서도 재사용하므로 별도 helper 로
  추출해 양쪽이 같은 dict 를 본다.
"""

from __future__ import annotations

import math
from typing import Any

from ortools.sat.python import cp_model

from app.domain.constants import PREDECESSOR_PROCESS
from app.application._shared.group_ops import (
    _extract_core_main_sq,
    _is_core_group,
    _st_sq,
)


def compute_proc_groups_by_sq(
    group_meta: dict[str, Any],
) -> dict[tuple[str, int], list[str]]:
    """``(process_name, sq_mm2) -> [group_key, ...]`` 딕셔너리 생성.

    §6-d 의 predecessor 와 §6-f 의 idle_terms 가 양쪽에서 사용하므로 한 번 만들어
    둔다. 중복 계산 방지 + 키 순서 결정론.
    """
    proc_groups_by_sq: dict[tuple[str, int], list[str]] = {}
    for gk in group_meta.keys():
        meta = group_meta[gk]
        proc_groups_by_sq.setdefault((meta["rep"].process_name, meta["sq"]), []).append(
            gk
        )
    return proc_groups_by_sq


def add_predecessor_precedence(
    *,
    model: cp_model.CpModel,
    group_meta: dict[str, Any],
    start_vars: dict[str, cp_model.IntVar],
    end_vars: dict[str, cp_model.IntVar],
    proc_groups_by_sq: dict[tuple[str, int], list[str]],
) -> None:
    """§6-d. 공정 선후관계 hard constraint.

    전 공정 첫 드럼이 완료된 시점부터 후 공정 시작 가능. 같은 SQ 그룹쌍에 적용.
    추가로 후공정 end ≥ 선행공정 end (파이프라인 유휴 0 역산).
    """
    for gk in group_meta.keys():
        meta = group_meta[gk]
        pred_proc = PREDECESSOR_PROCESS.get(meta["rep"].process_name)
        if not pred_proc:
            continue
        for pred_gk in proc_groups_by_sq.get((pred_proc, meta["sq"]), []):
            pred_meta = group_meta[pred_gk]
            pred_header = next(
                (b for b in pred_meta["batches"] if b.batch_seq == -1), None
            )
            if pred_header:
                lot_count = max(int(pred_header.drum_count or 1), 1)
            elif _is_core_group(pred_gk):
                lot_count = max(
                    sum(int(b.drum_count or 1) for b in pred_meta["batches"]), 1
                )
            else:
                lot_count = max(len(pred_meta["batches"]), 1)
            first_drum = max(1, math.ceil(pred_meta["cpsat_dur"] / lot_count))
            model.add(start_vars[gk] >= start_vars[pred_gk] + first_drum)
            # 파이프라인 유휴 최소 역산: 후공정 끝 ≥ 선행공정 끝
            model.add(end_vars[gk] >= end_vars[pred_gk])


def add_core_st_precedence(
    *,
    model: cp_model.CpModel,
    group_meta: dict[str, Any],
    start_vars: dict[str, cp_model.IntVar],
) -> None:
    """§6-e. CORE → ST 선행 (AL6BO 첫 드럼 → 54BO 시작)."""
    for core_gk in [gk for gk in group_meta.keys() if _is_core_group(gk)]:
        main_sq = _extract_core_main_sq(core_gk)
        if main_sq is None:
            continue
        core_meta = group_meta[core_gk]
        lot_c = max(sum(int(b.drum_count or 1) for b in core_meta["batches"]), 1)
        first_drum = max(1, math.ceil(core_meta["cpsat_dur"] / lot_c))
        for st_gk in [
            gk
            for gk in group_meta.keys()
            if gk.startswith("ST-") and _st_sq(gk) == main_sq
        ]:
            model.add(start_vars[st_gk] >= start_vars[core_gk] + first_drum)
