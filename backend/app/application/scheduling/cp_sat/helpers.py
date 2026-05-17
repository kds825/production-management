"""orchestrator.cp_sat_schedule 전용 helpers — 가중치 상수, 워커/우선순위/duration
레졸버, group meta builder.

Phase 3 step 2 (target.md §4): orchestrator.cp_sat_schedule 의 §1~§5 추출 일부.
- §1-§3 (DB-load) 는 input_builder.build_solver_input() 으로 이미 mirror 추출됨.
  본 step 에서는 inline DB-load 와 override-rebind 두 path 를 그대로 보존
  (early-return "배치 없음" warning 이 pre-WIP / post-WIP 두 path 에서 다르게
  동작하는 문제 — parity-safe 하게 유지).
- §4-§5 (그루핑 + group_meta) 를 본 모듈의 _build_group_meta 로 이동.

D7-C: 외부 importer (greedy/auto_schedule, scripts/, tests/) 는 cp_sat_schedule
심볼만 참조한다. 가중치 상수 / helper 함수는 cp_sat_optimizer 가 re-export 해
주던 path 가 그대로 남아있어 본 모듈 신설로 외부 영향 0.
"""

from __future__ import annotations

import math
import os
from collections import OrderedDict
from typing import TYPE_CHECKING, Any

from app.application._shared.calendar_ops import _due_work_min
from app.application._shared.group_ops import (
    _compute_group_duration_map,
    _get_drum_winding_min,
    _is_multi_equip_group,
)
from app.application._shared.slot_filters import (
    _filter_by_sheath_routing,
    _find_eligible_equipment,
    _narrow_by_stranding,
)
from app.application.scheduling.cp_sat.snapshot import SnapshotWeights
from app.domain.constants import (
    _CHAIN_WEIGHT,
    _DUE_HARD_WEIGHT,
    _TRANSITION_WEIGHT,
    _WORK_MIN_PER_DAY,
)

# ORM 모델은 type-hint 전용 — 함수 본문에서는 duck-type 으로만 사용 (b.line_speed_mpm
# 등 attribute access). runtime import 는 solver_boundary §7 (no DB I/O in solver
# modules) 위반이므로 TYPE_CHECKING guard 로 감싼다. `from __future__ import
# annotations` 가 있어 annotation 은 문자열로 lazy 평가.
if TYPE_CHECKING:
    from app.infrastructure.models.equipment_master import EquipmentMaster
    from app.infrastructure.models.production_batch import ProductionBatch

# ── 가중치 / 호라이즌 / 솔버 상수 (orchestrator 와 1:1 호환) ────────────────────

# Why 상수를 helpers 로: orchestrator §6 ModelWeights 구성, §7 솔버 파라미터,
# §5 group_meta 의 weight/severity 산식이 모두 동일 상수를 참조한다. Phase 3
# 분해의 첫 step 으로 helpers 모듈에 단일 source 로 두고 orchestrator 가 import.

_TARDINESS_WEIGHT = {
    "critical": _DUE_HARD_WEIGHT * 100,
    "urgent": _DUE_HARD_WEIGHT * 10,
    "normal": _DUE_HARD_WEIGHT,
}
_IDLE_WEIGHT = 1
_SLACK_WEIGHT_BASE = 100_000
_PAST_SEVERITY_K = 5
_EDD_PAIR_WEIGHT = 10_000
_EDD_MIXED_PASTDUE_WEIGHT = 1_000_000_000

# 연선연합(default) 카테고리 기준 1 근무일 최대 working-min (P9-E 신규).
# Mon-Thu 22h 가동 중 휴식 2h 제외 = 20h 실가동 + 8h idle (창 내부) 합쳐 24h 창.
# horizon 은 가장 큰 가용 카테고리 기준으로 잡아야 INFEASIBLE 를 피함 → 24h*60.
_WORK_MIN_PER_DAY_DEFAULT = 24 * 60  # 1440분 (창 full)

# CP-SAT 최대 계획 기간(근무 분) — 90 근무일. P9-E: calendar_engine 기반 축으로
# 확장되어 기존 840*90=75600 보다 큰 값 필요 (공휴일/금요일 때문에 실가용 분은
# 날마다 달라짐). 여유있게 90*1440 = 129600 으로 horizon 의 절대 상한.
#
# Phase 6 step 4 (2026-05): horizon 은 compute_horizon() 으로 동적 산정.
# _MAX_HORIZON_MIN 은 (a) no-due batch 의 due_wmin sentinel 값 (helpers.py:272
# 의 `else _MAX_HORIZON_MIN` 경로), (b) compute_horizon 결과의 절대 상한
# 으로만 사용. CP-SAT IntVar 의 upper bound 는 compute_horizon() 반환값.
_MAX_HORIZON_MIN = 90 * _WORK_MIN_PER_DAY_DEFAULT

# no-due batch 의 due_wmin sentinel — `_MAX_HORIZON_MIN` 과 같은 값이지만
# 의미가 분리됨 (sentinel 비교용). compute_horizon 에서 sentinel 그룹은
# horizon 산정에서 제외.
_NO_DUE_SENTINEL = _MAX_HORIZON_MIN

# CP-SAT 솔버 시간 제한(초)
_SOLVER_TIME_LIMIT_SEC = 30


def compute_horizon(
    group_meta: dict[str, Any],
    frozen_tasks_snapshot: dict[str, dict[str, Any]] | None,
    *,
    no_due_sentinel: int = _NO_DUE_SENTINEL,
    buffer_min: int = 14 * _WORK_MIN_PER_DAY_DEFAULT,
    min_horizon_min: int = 14 * _WORK_MIN_PER_DAY_DEFAULT,
    absolute_max: int = _MAX_HORIZON_MIN,
) -> int:
    """동적 horizon 산정 (Phase 6 step 4).

    기존 90일 (129,600분) 고정 horizon 은 CP-SAT IntVar 의 upper bound 를
    실제 납기보다 훨씬 크게 잡아 변수 도메인 폭주 → solve time 증가의 한
    원인. 실 데이터의 max(납기) + 여유분 만큼만 horizon 을 잡으면 모델
    크기가 줄어든다.

    공식:
        horizon = clamp(
            max(min_horizon_min,
                max(due_wmin for g in group_meta if due_wmin < sentinel) + buffer,
                max(frozen_end_wmin) + buffer),
            upper = absolute_max
        )

    Edge cases:
    - 모든 batch 가 no-due → due_wmin 항 제외, frozen / min 만 사용.
    - frozen 없음 → frozen_end 항 제외.
    - frozen 이 sentinel 이후를 가리킴 → frozen 항이 dominant, INFEASIBLE 방지.
    - 결과가 absolute_max(90d) 초과 → clamp (예전 동작과 동일).
    """
    candidates: list[int] = [min_horizon_min]

    due_excl_nodue = [
        int(meta.get("due_wmin", no_due_sentinel))
        for meta in group_meta.values()
        if int(meta.get("due_wmin", no_due_sentinel)) < no_due_sentinel
    ]
    if due_excl_nodue:
        # past-due (due_wmin<0) 인 경우 그대로 더하면 horizon 이 음수가 될 수
        # 있다. ``max(0, ...)`` 로 clamp 해 past-due 그룹은 "0 시각부터 buffer
        # 만큼" 의 의미로 처리 (납기는 hard 가 아니라 lex objective 가 흡수).
        candidates.append(max(0, max(due_excl_nodue)) + buffer_min)

    if frozen_tasks_snapshot:
        # frozen task 의 "end_wmin" 이 없으면 "start_wmin" 만 보존 (legacy
        # snapshot 일 수 있음). end_wmin 우선, 없으면 start_wmin 으로 보수적.
        frozen_ends = [
            int(t.get("end_wmin", t.get("start_wmin", 0)))
            for t in frozen_tasks_snapshot.values()
        ]
        if frozen_ends:
            candidates.append(max(0, max(frozen_ends)) + buffer_min)

    return min(max(candidates), absolute_max)


# ── 함수 ────────────────────────────────────────────────────────────────────


def _resolve_num_workers() -> int:
    """CP-SAT 포트폴리오 워커 수 해상도. ``CPSAT_WORKERS`` env 로 오버라이드.

    운영 기본값 8 — OR-Tools CP-SAT 은 워커별로 서로 다른 탐색 전략(LP/core/feasibility
    pump 등)을 독립 스레드로 돌리고 먼저 해를 찾는 쪽이 이긴다. 4→8 로 bump 시
    sublinear (1.5~2×) speedup 기대. 컨테이너/CI 환경 호스트 CPU 초과 방지를 위해
    환경변수 기반 오버라이드. 테스트 결정론을 위해 conftest 에서 ``CPSAT_WORKERS=1``
    을 강제한다 (멀티워커는 타이밍 의존 비결정성 위험).
    """
    raw = os.environ.get("CPSAT_WORKERS", "8")
    try:
        n = int(raw)
    except ValueError:
        n = 8
    return max(1, n)


def _build_snapshot_weights() -> SnapshotWeights:
    """Bundle the orchestrator scaling weights for the snapshot writer.

    Reading these once at call-site keeps ``cp_sat/snapshot.py`` free of
    upstream module imports while preserving the "what weights ran"
    record that diagnostic tooling relies on.
    """
    return SnapshotWeights(
        tardiness_normal=_TARDINESS_WEIGHT["normal"],
        past_severity_k=_PAST_SEVERITY_K,
        slack_base=_SLACK_WEIGHT_BASE,
        edd_pair=_EDD_PAIR_WEIGHT,
        edd_mixed_pastdue=_EDD_MIXED_PASTDUE_WEIGHT,
        transition=_TRANSITION_WEIGHT,
        chain=_CHAIN_WEIGHT,
        idle=_IDLE_WEIGHT,
    )


def _priority_label(customer_priority: int | None) -> str:
    cp = customer_priority or 99
    if cp <= 3:
        return "critical"
    if cp <= 7:
        return "urgent"
    return "normal"


def _compute_group_duration(
    group_batches: list[ProductionBatch],
    eligible: list[EquipmentMaster],
    speed_map: dict,
) -> float:
    """배치 그룹의 순수 작업 duration(분, 설업 제외)."""
    rep = group_batches[0]
    rep_speed = float(rep.line_speed_mpm or 0)
    if rep_speed <= 0:
        for eq in eligible:
            sm = speed_map.get((eq.equipment_code, float(rep.sq_mm2 or 0)))
            if sm and sm.line_speed_mpm and float(sm.line_speed_mpm) > 0:
                rep_speed = float(sm.line_speed_mpm)
                break
    line_speed = rep_speed if rep_speed > 0 else 10

    header = next((b for b in group_batches if b.batch_seq == -1), None)
    if header is not None:
        hd = float(header.estimated_duration_min or 0)
        if hd <= 0:
            ls = float(header.line_speed_mpm or 0) or line_speed
            hd = float(header.total_length_m or 0) / ls if ls > 0 else 60
        return hd

    total = 0.0
    for b in group_batches:
        d = float(b.estimated_duration_min or 0)
        if d <= 0:
            ls = float(b.line_speed_mpm or 0) or line_speed
            d = (
                (float(b.total_length_m or 0) + float(b.extra_length_m or 0)) / ls
                if ls > 0
                else 60
            )
        total += d
    return total


def _build_group_meta(
    batches: list[ProductionBatch],
    equipment_by_process: dict[str, list[EquipmentMaster]],
    speed_map: dict,
    *,
    base_date,
    warnings_out: list,
) -> tuple[OrderedDict, dict]:
    """orchestrator §4 (그루핑) + §5 (group_meta) 추출.

    Behavior 동일성: cp_sat_schedule line 595-734 와 1:1 대응. 한 글자 차이도
    parity (27/27 main + 11/11 quick) 가 catch.

    individual args 시그니처 (SolverInput 강결합 회피): DB-load path 와
    override-rebind path 양쪽이 같은 호출로 사용 가능. caller 는 batches /
    equipment_by_process / speed_map 을 이미 unpack 한 상태이므로 그대로 전달.

    warnings_out 은 caller 의 ``result["warnings"]`` 와 같은 list 객체 — 이 함수
    가 in-place append 하면 caller 가 그대로 본다. (return tuple 에 담아 caller
    가 extend 해도 되지만 §5 inline 시점의 side-effect 와 1:1 보존 위해 직접
    append).
    """
    # ── §4. 그루핑 ─────────────────────────────────────────────────────────
    batch_groups: OrderedDict[str, list[ProductionBatch]] = OrderedDict()
    for b in batches:
        key = b.batch_group or f"_single_{b.batch_id}"
        batch_groups.setdefault(key, []).append(b)

    # ── §5. 그룹별 메타 계산 ───────────────────────────────────────────────
    group_meta: dict[str, dict] = {}
    for gk, gb in batch_groups.items():
        rep = gb[0]
        candidate = equipment_by_process.get(rep.process_name, [])

        if rep.process_name in ("고압시스", "저압시스"):
            candidate = _filter_by_sheath_routing(rep, candidate)
        if gk.startswith("A120_"):
            candidate = [e for e in candidate if e.equipment_code == "SH-A120"]
        elif gk.startswith("A100_"):
            candidate = [e for e in candidate if e.equipment_code == "SH-A100"]

        eligible = _find_eligible_equipment(rep, candidate)
        if rep.process_name == "연선":
            eligible = _narrow_by_stranding(rep, eligible)

        if not eligible:
            warnings_out.append(
                f"배치그룹 {gk}: 공정 '{rep.process_name}' SQ={rep.sq_mm2} — 적합한 설비 없음"
            )
            continue

        work_dur = _compute_group_duration(gb, eligible, speed_map)
        setup_min = float(rep.setup_time_min or 0)
        drum_wind = _get_drum_winding_min(
            eligible[0].equipment_code, rep.sq_mm2, speed_map
        )
        # CP-SAT 내부 duration: 실제 근무 분 그대로 사용 (최소 1분).
        # 종전 840분 단위 올림은 모든 작업이 같은 크기로 보여 EDD 정렬 불가.
        cpsat_dur_raw = max(1, int(math.ceil(work_dur + setup_min + drum_wind)))

        # Round 2 HIGH #7: 멀티설비 분배 대상은 실제 배치 단계에서
        # _schedule_multi_equipment 로 N 설비 병렬 실행 → wall-clock duration 은
        # 대략 raw / N_split. CP-SAT 모델은 단일 interval 가정이라 raw 를 그대로
        # 쓰면 "분할 가능 그룹이 실제보다 오래 걸린다" 고 오인해 후속 배치를 뒤로
        # 밀게 됨. 여기서 근사치 N_split 로 나눠 solver 가 실제 wall-clock 을
        # 반영하게 함. 완전한 분할 모델링(N intervals per group)은 별도 phase.
        _is_multi_pre, _total_drums_pre = _is_multi_equip_group(gk, gb, eligible, {})
        if _is_multi_pre and _total_drums_pre >= 2 and len(eligible) >= 2:
            _n_split = max(1, min(_total_drums_pre, len(eligible)))
            _work_per_split = work_dur / _n_split
            cpsat_dur = max(1, int(math.ceil(_work_per_split + setup_min + drum_wind)))
        else:
            cpsat_dur = cpsat_dur_raw

        # Round 2 HIGH #5: 설비별 duration map (solver 가 "빠른 설비 선호" 가능).
        _dur_map = _compute_group_duration_map(gb, eligible, speed_map)
        _multi_split_divisor = (
            max(1, min(_total_drums_pre, len(eligible)))
            if (_is_multi_pre and _total_drums_pre >= 2 and len(eligible) >= 2)
            else 1
        )
        cpsat_dur_by_eq: dict[str, int] = {}
        if _dur_map:
            for _eq in eligible:
                _d = _dur_map.get(_eq.equipment_code, work_dur) / _multi_split_divisor
                cpsat_dur_by_eq[_eq.equipment_code] = max(
                    1, int(math.ceil(_d + setup_min + drum_wind))
                )
        else:
            for _eq in eligible:
                cpsat_dur_by_eq[_eq.equipment_code] = cpsat_dur
            warnings_out.append(
                f"그룹 {gk}: SpeedMaster 에 (설비, SQ={rep.sq_mm2}) 데이터 없음 — "
                f"설비별 duration 차등 없이 단일값 {cpsat_dur}min 사용"
            )

        # spread 판단 (10% 이상 차이나면 per-eq interval 사용)
        _dur_values = list(cpsat_dur_by_eq.values())
        _dur_spread_ratio = 0.0
        if _dur_values and max(_dur_values) > 0:
            _dur_spread_ratio = (max(_dur_values) - min(_dur_values)) / max(_dur_values)
        _per_eq_dur_enabled = _dur_spread_ratio >= 0.10

        earliest_due = min((b.due_date for b in gb if b.due_date), default=None)
        due_wmin = (
            _due_work_min(earliest_due, base_date) if earliest_due else _MAX_HORIZON_MIN
        )

        # Past-due 심각도 배율 — 자세한 설계는 _PAST_SEVERITY_K docstring 참조
        # (orchestrator 와 동일 산식). divisor 는 _WORK_MIN_PER_DAY (840).
        _past_days = max(0, (-due_wmin) / _WORK_MIN_PER_DAY) if due_wmin < 0 else 0.0
        _severity_mul = 1.0 + _past_days / _PAST_SEVERITY_K
        _weight = int(
            _TARDINESS_WEIGHT[_priority_label(rep.customer_priority)] * _severity_mul
        )

        group_meta[gk] = {
            "rep": rep,
            "batches": gb,
            "eligible": eligible,
            "work_dur": work_dur,
            "setup_min": setup_min,
            "drum_wind": drum_wind,
            "cpsat_dur": cpsat_dur,
            "cpsat_dur_by_eq": cpsat_dur_by_eq,
            "per_eq_dur_enabled": _per_eq_dur_enabled,
            "due_wmin": due_wmin,
            "weight": _weight,
            "earliest_due": earliest_due,
            # 인접 쌍 chain_terms 계산용 ordinal — None 안전
            "due_date_ord": earliest_due.toordinal() if earliest_due else None,
            "sq": int(rep.sq_mm2 or 0),
        }

    return batch_groups, group_meta
