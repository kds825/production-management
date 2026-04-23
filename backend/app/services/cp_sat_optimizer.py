"""CP-SAT 기반 스케줄 최적화 엔진

── 설계 방침 ───────────────────────────────────────────────────────────────
CP-SAT 담당: 모든 배치 그룹의 처리 순서 결정 + 납기 초과 최소화
  - 목적함수: customer_priority 가중 납기 초과 근무일 합산 최소화
  - Hard 제약: 설비 충돌 없음, 공정 선후관계(연선→절연→시스), CORE 선행

실제 배치(캘린더): CP-SAT가 결정한 순서대로 그리디 캘린더 엔진 수행
  - 멀티설비 그룹 → _schedule_multi_equipment
  - 단일설비 그룹 → _find_available_slot + calculate_end_datetime
  - 실제 시작/종료는 항상 08~22시 근무 캘린더 기준

CP-SAT 시간 단위: 근무 분(working minute), 하루 = 840분(14h×60)
  - 캘린더와 직접 1:1 대응은 불가하지만 근무일 단위로 근사하여
    납기 제약의 방향성(어떤 그룹을 먼저 처리할지)을 올바르게 결정

폴백: CP-SAT 실패(INFEASIBLE / 타임아웃) 시 기존 그리디로 자동 전환
"""

from __future__ import annotations

import json
import math
import os
import time
from collections import OrderedDict
from datetime import date, datetime, timedelta
from typing import Any

from ortools.sat.python import cp_model
from sqlalchemy.orm import Session

from app.domain.constants import PROCESS_ORDER
from app.infrastructure.models.drum_lot_master import DrumLotMaster
from app.infrastructure.models.equipment_master import EquipmentMaster
from app.infrastructure.models.production_batch import ProductionBatch
from app.infrastructure.models.schedule_task import ScheduleTask
from app.infrastructure.models.speed_master import SpeedMaster
from app.services.audit_logger import log_decision
from app.services.calendar_engine import (
    calculate_end_datetime,
)
from app.services.constraint_params import ConstraintParams, resolve_color_change_min
from app.services.solver import SolverInput
from app.services.schedule_optimizer import (
    PREDECESSOR_PROCESS,
    _DEFAULT_WELDING_MIN,
    _WIP_SKIP_PROCESSES,
    _extract_core_main_sq,
    _filter_by_sheath_routing,
    _find_available_slot,
    _find_eligible_equipment,
    _get_drum_winding_min,
    _get_stranding_setup_min,
    _is_core_group,
    _is_sheath_group,
    _narrow_by_stranding,
    _schedule_multi_equipment,
    _st_sq,
    align_start_to_predecessor_end,
)

# 하루 근무 시간(분): 08:00~22:00 (CP-SAT 시간축 legacy 단위).
# 실제 가용 분은 calendar_engine 기반 _working_minutes_between 이 계산하므로
# 이 상수는 폴백(legacy path) 과 horizon 계산의 근사치로만 사용된다.
_WORK_MIN_PER_DAY = 14 * 60  # 840분

# 연선연합(default) 카테고리 기준 1 근무일 최대 working-min (P9-E 신규).
# Mon-Thu 22h 가동 중 휴식 2h 제외 = 20h 실가동 + 8h idle (창 내부) 합쳐 24h 창.
# horizon 은 가장 큰 가용 카테고리 기준으로 잡아야 INFEASIBLE 를 피함 → 24h*60.
_WORK_MIN_PER_DAY_DEFAULT = 24 * 60  # 1440분 (창 full)

# CP-SAT 최대 계획 기간(근무 분) — 90 근무일. P9-E: calendar_engine 기반 축으로
# 확장되어 기존 840*90=75600 보다 큰 값 필요 (공휴일/금요일 때문에 실가용 분은
# 날마다 달라짐). 여유있게 90*1440 = 129600 으로 horizon 확장.
_MAX_HORIZON_MIN = 90 * _WORK_MIN_PER_DAY_DEFAULT

# CP-SAT 솔버 시간 제한(초)
_SOLVER_TIME_LIMIT_SEC = 30


# CP-SAT 포트폴리오 워커 수. 환경변수 `CPSAT_WORKERS` 로 오버라이드 가능.
# 운영 기본값 8 — OR-Tools CP-SAT 은 워커별로 서로 다른 탐색 전략(LP/core/feasibility
# pump 등)을 독립 스레드로 돌리고 먼저 해를 찾는 쪽이 이긴다. 4→8 로 bump 시
# sublinear (1.5~2×) speedup 기대. 컨테이너/CI 환경 호스트 CPU 초과 방지를 위해
# 환경변수 기반 오버라이드. 테스트 결정론을 위해 conftest 에서 `CPSAT_WORKERS=1`
# 을 강제한다 (멀티워커는 타이밍 의존 비결정성 위험).
def _resolve_num_workers() -> int:
    raw = os.environ.get("CPSAT_WORKERS", "8")
    try:
        n = int(raw)
    except ValueError:
        n = 8
    return max(1, n)


# 납기 초과 가중치 — tardiness_hard=False 모드에서만 사용.
# tardiness_hard=True (기본) 에서는 model.add(e <= due_wmin) 로 직접 강제.
# _DUE_HARD_WEIGHT: 아이들(1)/체인(120)/선점 등 다른 목적함수 항들을 압도해
# 실질적 hard 로 동작시킨다 (soft 폴백 경로용).
_DUE_HARD_WEIGHT = 100000
_TARDINESS_WEIGHT = {
    "critical": _DUE_HARD_WEIGHT * 100,
    "urgent": _DUE_HARD_WEIGHT * 10,
    "normal": _DUE_HARD_WEIGHT,
}

# 색상 교체 cost 가중치 (분 단위). resolve_color_change_min 의 기본값(120min)과
# 일치시켜 chain_diff(boolean: 동색 0, 이색 1) 곱한 값이 실제 교체 시간과 동등
# scale 로 경쟁하게 함. 1 분 tardiness ≒ 1 분 idle ≒ 색상 1회 교체(120min).
# 기존 값(1)은 tardiness_weight(10만~1000만) 대비 사실상 무력했음 (P9-B 교정).
_CHAIN_WEIGHT = 120
_IDLE_WEIGHT = 1

# On-time 그룹 간 "납기 임박도" 에 가산점을 주는 slack 가중치 base.
# Why: tardiness_hard=True 에서 on-time 그룹은 `e ≤ due` hard constraint 만 걸리고
# soft 항은 0 → 슬랙 3일 vs 13일이 objective 에서 동등 취급됨. 결과: 같은 설비에
# 후보로 올라온 납기 여유 그룹이 납기 임박 그룹보다 앞에 놓이는 현상. EDD pair
# tie-breaker (weight=1/pair) 만으론 `_IDLE_WEIGHT=1/min`, `_CHAIN_WEIGHT=120`,
# `_TRANSITION_WEIGHT=180` 등 다른 항에 의해 압도될 수 있음.
#
# 해결: 각 on-time 그룹에 `weight × (end - base) / _WORK_MIN_PER_DAY` 항 추가.
# `weight = _SLACK_WEIGHT_BASE // slack_min` → 납기 임박할수록 큰 가중치.
#   - slack 1근무일(1440min) → w ≈ 69
#   - slack 10근무일(14400min) → w ≈ 7
#   → 약 10배 차등. idle_weight(1) 압도, past-due tardiness(1e5/min) 에는 열세.
# base 를 뺀 형태로 항 scale 을 (end - now) ≈ 0 ~ horizon 에 제한 (end 자체가
# 0~_MAX_HORIZON_MIN 라 절대값이 과대해지는 것 방지).
_SLACK_WEIGHT_BASE = 100_000

# Past-due 심각도 스케일 상수. past-due 그룹의 tardiness weight 는
# `_TARDINESS_WEIGHT[priority] × (1 + past_days / _PAST_SEVERITY_K)` 으로 증폭.
# Why: 기본 공식 `weight × (end - due)` 은 past-due 시 `weight × (end + |past|)`
# = `weight × end + const` 로 계산돼 |past| 상수항이 argmin 에 기여 못함. 즉
# "10일 지남 vs 3일 지남" 을 solver 가 동일 취급하고 WSPT (짧은 작업 먼저) 로
# 순서를 잡음 → 가장 긴 past-due 작업이 맨 뒤로 밀림 (KBI PoC 에서 150SQ 3틀
# past 3d 가 120SQ 3틀 past 10d 보다 뒤 배치되는 현상으로 관찰).
# 해결: weight 자체에 심각도 배율을 곱해 오래 밀린 그룹의 `w × end` 항 gradient
# 를 키움 → solver 가 실제로 앞으로 당기게 됨.
# K=5 기준 배율표:
#   past 0일  → ×1.0 (on-time 기준선)
#   past 3일  → ×1.6
#   past 5일  → ×2.0
#   past 10일 → ×3.0
#   past 20일 → ×5.0
# priority (normal=1e5, urgent=1e6, critical=1e7) 스케일 위에 곱해지므로
# 상대 순서만 영향, 다른 objective term 과의 상호작용은 기존과 유사.
_PAST_SEVERITY_K = 5

# 같은 공정·공유 설비 후보 쌍에서 EDD 위반(납기 빠른 게 늦게 시작)당 부과되는
# penalty. Why: 기존 weight=1 로는 WSPT(짧은 작업 먼저) 이익을 이길 수 없어
# 납기 임박한 긴 작업이 맨 뒤로 밀리는 현상 (KBI PoC 150SQ 33000m 납기 4/17
# 이 300SQ 9500m 납기 4/30 뒤에 배치). on-time 그룹 간엔 past-due severity 가
# 트리거되지 않고 slack weight 차이(수단위)도 WSPT 차이(수K~수만)를 압도
# 하지 못함. EDD pair 를 강화해 "납기 순서" 를 명시적 soft constraint 로 강제.
#
# 스케일 설계:
#   _IDLE_WEIGHT(1/min) × typical_duration(1000~3000min) ≈ 1000~3000
#   _TRANSITION_WEIGHT(180) × 1~2 transitions ≈ 180~360
#   → 일반 scheduling 결정에서 WSPT/idle/transition 이익은 수K 수준
#   → EDD 위반 1쌍 penalty 를 10_000 으로 두면 이들을 지배
#   → past-due tardiness(1e5/min × 수천min = 수억) 는 EDD 압도 → past-due
#     은 여전히 tardiness 로 강제 (EDD 는 on-time 간 정렬 전용)
_EDD_PAIR_WEIGHT = 10_000

# "past-due ↔ on-time" 혼합 쌍 전용 heavy penalty (Hybrid C 철학).
# Why: 과포화 설비에 past-due 긴 그룹 + on-time 짧은 그룹이 함께 올라간 상황
# (KBI PoC 54BO1 실 사례: past-due 150SQ dur 3096min vs on-time 300SQ_2차 dur
# 990min). 순수 가중 tardiness sum 관점에선 on-time 을 앞에 놓는 게 optimal 이
# 될 수 있으나 (150SQ 가 어차피 납기 불가능하므로 작은 on-time 을 앞에 끼워
# 넣는 게 총합 유리) 공장관리자 mental model 은 "past-due 가 무조건 먼저".
#
# 구현: past-due 가 on-time 뒤에 놓이면 `_EDD_MIXED_PASTDUE_WEIGHT` 만큼 penalty.
# tardiness weight (1e6~1e7/min × 수천 min = 1e9~1e10/그룹) 스케일에 맞춰
# 1e9 로 설정 — 단일 violation 이 그룹당 tardiness 한 항 수준의 기여로 생김.
# "past-due 앞당김으로 얻는 tardiness 감소 < EDD 위반 페널티" 관계를 만들어
# solver 가 항상 past-due 를 먼저 놓게 유도. hard constraint 가 아닌 soft 이유:
# past-due 가 물리적으로 먼저 끝날 수 없는 corner case (dur 이 상상 초월) 에
# feasibility 를 유지하기 위함.
#
# 스케일 근거: 실측 case 에서 Option A↔B 의 tardiness delta 가 1.7e9 수준.
# _EDD_MIXED_PASTDUE_WEIGHT = 1e9 은 단일 violation 으로도 이 delta 를 압도.
_EDD_MIXED_PASTDUE_WEIGHT = 1_000_000_000

# Round 2 HIGH #6: 연선 setup 3-tier (동일SQ 0 / 동일소선경 30 / 이소선경 210) 의
# 평균치. spec-level (다른 소선경) 전이만이 실제로 고비용이므로 avg(0, 30, 210) ≈ 80
# 대신 "다른 SQ 인접 시 피해야 할 비용" 의 대표값으로 180 min 사용 (spec 이 압도적).
# Solver 는 "같은 설비에서 인접 두 연선 그룹이 SQ 가 다르면 180 min penalty" 로
# 인식 → 같은 SQ 연속 처리를 선호. 이는 sequence-dependent setup 의 정확 모델링이
# 아닌 soft proxy 이지만, 현재 모델 구조 (group=single interval) 에서 실용적 절충안.
# 완전한 circuit-constraint 기반 모델링은 별도 phase.
_TRANSITION_WEIGHT = 180


# ── 진단: solver 스냅샷 덤프 ──────────────────────────────────────────────
# Why: EDD 역전·frozen 고정 등 scheduling 이상을 사후 추적하려면 solver 의 입력
# (group_meta, frozen 집합) 과 출력(start/end/equipment/tardiness 및 각 목적함수
# 항의 기여값) 이 한 파일에 묶여 있어야 재현 가능한 진단이 된다. 로그만으로는
# run_label 당 데이터를 재구성하기 어려움. 실패(쓰기 오류) 는 solver 결과를
# 막지 않도록 조용히 삼킨다.


def _snapshot_output_dir() -> str:
    """프로젝트 루트의 04_output/solver_snapshots 경로.

    파일 위치: backend/app/services/cp_sat_optimizer.py → 3단계 상위(.. .. ..)가 루트.
    (services → app → backend → 루트). 환경변수 `SOLVER_SNAPSHOT_DIR` 로
    테스트/배포별 경로 오버라이드 가능.
    """
    override = os.environ.get("SOLVER_SNAPSHOT_DIR")
    if override:
        return override
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(
        os.path.join(here, "..", "..", "..", "04_output", "solver_snapshots")
    )


def _write_solver_snapshot(
    *,
    run_label: str,
    base_date: datetime | None,
    group_meta: dict,
    frozen_group_keys: set[str] | None,
    solver: Any,
    solver_status_name: str,
    start_vars: dict,
    end_vars: dict,
    equip_vars: dict,
    tardiness_vars: dict,
    edd_pair_vars: list,
    slack_terms_meta: list,
    idle_terms: list | None = None,
    transition_terms: list | None = None,
    sheath_end_terms: list | None = None,
    edd_mixed_pastdue_vars: list | None = None,
) -> None:
    """Solver 입출력 + objective breakdown 을 `04_output/solver_snapshots/{run}.json` 에 저장."""
    try:
        out_dir = _snapshot_output_dir()
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, f"{run_label}.json")

        frozen_set = set(frozen_group_keys or [])

        groups_section: list[dict] = []
        tardiness_contrib_total = 0
        for gk, meta in group_meta.items():
            rep = meta["rep"]
            s_val = solver.value(start_vars[gk]) if gk in start_vars else None
            e_val = solver.value(end_vars[gk]) if gk in end_vars else None
            eq_chosen: str | None = None
            for ec, bool_var in equip_vars.get(gk, {}).items():
                if solver.value(bool_var) == 1:
                    eq_chosen = ec
                    break
            tard_val = (
                solver.value(tardiness_vars[gk]) if gk in tardiness_vars else None
            )
            tard_contrib = (
                int(meta["weight"]) * int(tard_val) if tard_val is not None else 0
            )
            tardiness_contrib_total += tard_contrib

            _due_wmin_v = int(meta["due_wmin"])
            if _due_wmin_v < 0 and _due_wmin_v != -_MAX_HORIZON_MIN * 2:
                # _due_work_min legacy 경로와 동일 축(_WORK_MIN_PER_DAY=840) 사용.
                _past_days = round(max(0, (-_due_wmin_v) / _WORK_MIN_PER_DAY), 2)
            else:
                _past_days = 0.0

            groups_section.append(
                {
                    "batch_group": gk,
                    "process_name": rep.process_name,
                    "sq_mm2": float(rep.sq_mm2 or 0),
                    "voltage": rep.voltage,
                    "due_date": (
                        meta["earliest_due"].isoformat()
                        if meta.get("earliest_due")
                        else None
                    ),
                    "due_wmin": _due_wmin_v,
                    "past_days": _past_days,
                    "weight": int(meta["weight"]),
                    "cpsat_dur": int(meta["cpsat_dur"]),
                    "eligible_equipment": [e.equipment_code for e in meta["eligible"]],
                    "is_frozen": gk in frozen_set,
                    "n_batches": len(meta["batches"]),
                    "order_ids": sorted(
                        {b.sales_order_id for b in meta["batches"] if b.sales_order_id}
                    ),
                    "solver_start_wmin": s_val,
                    "solver_end_wmin": e_val,
                    "solver_equipment": eq_chosen,
                    "solver_tardiness_wmin": tard_val,
                    "solver_tardiness_contribution": tard_contrib,
                }
            )

        edd_wrong_count = (
            sum(int(solver.value(v)) for v in edd_pair_vars) if edd_pair_vars else 0
        )
        edd_contribution = int(edd_wrong_count) * _EDD_PAIR_WEIGHT
        edd_mixed_wrong_count = (
            sum(int(solver.value(v)) for v in edd_mixed_pastdue_vars)
            if edd_mixed_pastdue_vars
            else 0
        )
        edd_mixed_contribution = int(edd_mixed_wrong_count) * _EDD_MIXED_PASTDUE_WEIGHT

        slack_contribution_total = 0
        for _gk_s, _w_s, _end_var_s in slack_terms_meta:
            slack_contribution_total += int(_w_s) * int(solver.value(_end_var_s))

        idle_sum = sum(int(solver.value(v)) for v in idle_terms) if idle_terms else 0
        idle_contribution = idle_sum * _IDLE_WEIGHT
        transition_sum = (
            sum(int(solver.value(v)) for v in transition_terms)
            if transition_terms
            else 0
        )
        transition_contribution = transition_sum * _TRANSITION_WEIGHT
        sheath_end_sum = (
            sum(int(solver.value(v)) for v in sheath_end_terms)
            if sheath_end_terms
            else 0
        )

        snapshot = {
            "run_label": run_label,
            "base_date": base_date.isoformat() if base_date else None,
            "solver_status": solver_status_name,
            "objective_value": (
                int(solver.objective_value)
                if solver_status_name in ("OPTIMAL", "FEASIBLE")
                else None
            ),
            "frozen_group_keys": sorted(frozen_set),
            "constants": {
                "TARDINESS_WEIGHT_normal": _TARDINESS_WEIGHT["normal"],
                "PAST_SEVERITY_K": _PAST_SEVERITY_K,
                "SLACK_WEIGHT_BASE": _SLACK_WEIGHT_BASE,
                "EDD_PAIR_WEIGHT": _EDD_PAIR_WEIGHT,
                "EDD_MIXED_PASTDUE_WEIGHT": _EDD_MIXED_PASTDUE_WEIGHT,
                "TRANSITION_WEIGHT": _TRANSITION_WEIGHT,
                "CHAIN_WEIGHT": _CHAIN_WEIGHT,
                "IDLE_WEIGHT": _IDLE_WEIGHT,
            },
            "breakdown": {
                "tardiness_contribution_total": tardiness_contrib_total,
                "edd_pair_wrong_count": int(edd_wrong_count),
                "edd_pair_contribution": edd_contribution,
                "edd_mixed_pastdue_wrong_count": int(edd_mixed_wrong_count),
                "edd_mixed_pastdue_contribution": edd_mixed_contribution,
                "slack_contribution_total": slack_contribution_total,
                "idle_sum_wmin": idle_sum,
                "idle_contribution": idle_contribution,
                "transition_sum_pairs": transition_sum,
                "transition_contribution": transition_contribution,
                "sheath_end_sum_wmin": sheath_end_sum,
            },
            "groups": groups_section,
        }

        with open(path, "w", encoding="utf-8") as fh:
            json.dump(snapshot, fh, ensure_ascii=False, indent=2, default=str)
    except Exception:  # noqa: BLE001 — 진단 쓰기 실패가 solver 결과를 막지 않도록
        pass


# ── 헬퍼 ──────────────────────────────────────────────────────────────────


def _priority_label(customer_priority: int | None) -> str:
    cp = customer_priority or 99
    if cp <= 3:
        return "critical"
    if cp <= 7:
        return "urgent"
    return "normal"


def _work_days_between(d1: date, d2: date) -> int:
    """d1(포함) ~ d2(미포함) 사이의 근무일 수(토·일 제외).

    Legacy fallback — calendar_engine 경로가 db/equipment_code 정보 없을 때 사용.
    공휴일은 반영하지 않는다.
    """
    days = 0
    cur = d1
    while cur < d2:
        if cur.weekday() < 5:
            days += 1
        cur += timedelta(days=1)
    return days


def _working_minutes_between(
    start: datetime,
    end: datetime,
    equipment_code: str | None = None,
    db: Session | None = None,
) -> int:
    """start → end 사이 실제 가용 근무분 (calendar_engine 기반, P9-E).

    calendar_engine.get_working_window / get_available_hours / _get_day_breaks 를
    결합해 공정 카테고리별 가동시간·요일별·휴식·공휴일(db 있을 때) 을 모두 반영.

    가정/한계:
      - tz-naive (KST) — 기존 코드 전체 규칙 준수.
      - equipment_code=None → 'default' 카테고리 (연선연합과 동일 22h Mon-Thu).
      - db=None → OperationCalendar(CAL-HOL) 공휴일 미반영, 주말만 제외.
      - 같은 날 start ~ end 는 day 의 working window 와 교집합 후 휴식 구간 제거.
      - 휴식 구간을 완전히 포함한 start/end 만 차감 (부분 겹침은 차감 분을 clamp).

    Edge case (알려진 근사):
      - start/end 가 working window 밖이면 day_start/day_end 로 clamp.
      - current date 가 금요일 오후이면 FRI_END_HOUR 로 창이 단축됨 — 자동 반영.

    Why: 기존 _work_days_between * _WORK_MIN_PER_DAY 는 하루=840분 고정 근사로
    "공휴일 있는 주에 작업이 하루 더 밀림" 같은 현실을 반영 못 했음. P9-E 로 교체.
    """
    # 지역 import — 최상단 import 가 formatter 에 의해 제거되는 환경 방어.
    from app.services.calendar_engine import (
        _get_category,
        _get_day_breaks,
        get_available_hours,
        get_working_window,
    )

    if end <= start:
        return 0

    cat = _get_category(equipment_code)
    total_min = 0
    current_date = start.date()
    end_date = end.date()

    while current_date <= end_date:
        # 1) 해당 날짜 사용 가능 시간 (공휴일이면 0 → skip)
        avail_hr = get_available_hours(current_date, equipment_code, db)
        if avail_hr <= 0:
            current_date = current_date + timedelta(days=1)
            continue

        # 2) 해당 날짜 작업 윈도우 (day_start, day_end) — 08:00 시작, cat/요일별 종료
        day_start, day_end = get_working_window(current_date, equipment_code)
        if day_end <= day_start:
            current_date = current_date + timedelta(days=1)
            continue

        # 3) 실제 구간 = [max(start, day_start), min(end, day_end)]
        if current_date == start.date():
            effective_start = max(start, day_start)
        else:
            effective_start = day_start
        if current_date == end_date:
            effective_end = min(end, day_end)
        else:
            effective_end = day_end

        if effective_end <= effective_start:
            current_date = current_date + timedelta(days=1)
            continue

        seg_min = int((effective_end - effective_start).total_seconds() / 60)

        # 4) 휴식 구간 제거 (월~목 연선연합 점심/저녁/간식). 부분 겹침도 정확히 차감.
        breaks = _get_day_breaks(current_date, cat)
        for b_start, b_end in breaks:
            # 겹침 없음 조기 탈출
            if b_start >= effective_end or b_end <= effective_start:
                continue
            overlap_start = max(b_start, effective_start)
            overlap_end = min(b_end, effective_end)
            seg_min -= max(0, int((overlap_end - overlap_start).total_seconds() / 60))

        total_min += max(0, seg_min)
        current_date = current_date + timedelta(days=1)

    return total_min


def _due_work_min(
    due: date,
    base: datetime,
    equipment_code: str | None = None,
    db: Session | None = None,
) -> int:
    """납기일까지 남은 근무 분(CP-SAT 내부 단위).

    P9-E: equipment_code / db 선택 파라미터로 calendar_engine 반영.
    - 제공되면 `_working_minutes_between(base, due 22:00, ...)` 으로 정확 계산.
    - 없으면 기존 legacy 축(_work_days_between * _WORK_MIN_PER_DAY) 유지 —
      `_datetime_to_wmin` 등 legacy wmin 축과 정합 유지용.

    due 의 "하루 끝" 을 22:00 (일반 오후 마감) 기준으로 해석. calendar_engine 의
    day_end 와 min() 을 통해 카테고리별 실제 마감(금요일 14:00 등) 로 자동 clamp.

    Round 2 (MED #13 Past-due 차등): past-due (due < base) 는 음수 반환.
      - 기존: past-due 시 `_working_minutes_between(base, due_end)` 가 end<=start
        이라 0, 혹은 `wd = _work_days_between(base, due)` 도 0 (둘 다 loss of
        signal). 결과: overdue 5일 == overdue 1일 == on-time 동일 처리.
      - 개선: due < base 일 때 -|past 근무 분| 반환. soft tardiness 공식
        `max(0, end - due_wmin)` 이 `end - (-x) = end + x` 로 자연스럽게 커져
        "3일 overdue 는 1일 overdue 의 3배 penalty" 실현. tardiness_hard=True
        경로는 호출부에서 음수 감지 후 제약 skip + warning.
    """
    from datetime import time as _time

    if equipment_code is not None or db is not None:
        due_end = datetime.combine(due, _time(22, 0))
        if due_end <= base:
            # Past due — 음수 반환 (얼마나 지났는지).
            past_min = _working_minutes_between(due_end, base, equipment_code, db)
            return -past_min
        return _working_minutes_between(base, due_end, equipment_code, db)

    # Legacy fallback — _datetime_to_wmin / horizon 과 동일 축 유지.
    if due < base.date():
        # Past due — 음수 근무일 × 하루 분.
        past_wd = _work_days_between(due, base.date())
        return -past_wd * _WORK_MIN_PER_DAY
    wd = _work_days_between(base.date(), due)
    return wd * _WORK_MIN_PER_DAY


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


def _compute_group_duration_map(
    group_batches: list[ProductionBatch],
    eligible: list[EquipmentMaster],
    speed_map: dict,
) -> dict[str, float]:
    """배치 그룹의 설비별 duration map (Round 2 HIGH #5).

    Returns:
        {equipment_code: work_duration_min} — 각 eligible 설비로 배치했을 때
        예상되는 순수 작업 시간(분, setup/drum_wind 제외).

    Why: 기존 `_compute_group_duration` 은 단일 스칼라 반환. 같은 배치를 설비
    A/B 에 할당해도 모델은 duration 이 동일하다고 가정 → solver 가 "빠른 설비
    우선" 을 인지 못 함. SpeedMaster 의 `(eq, sq)` 별 line_speed_mpm 을 활용해
    설비마다 실제 예상 duration 계산.

    Fallback 규칙:
      - 배치의 explicit estimated_duration_min (>0) 가 있으면 설비 무관 그 값
        사용 (이미 확정된 것이므로 설비 선택에 무영향).
      - SpeedMaster (eq, sq) 항목이 있으면 그 line_speed_mpm 사용.
      - 없으면 eligible 의 평균 speed 사용 (배치의 line_speed_mpm 도 고려).
      - 최종 fallback: _compute_group_duration 과 동일 평균치 (단일 스칼라).

    Note: hard-coded line_speed=10 fallback 은 신규 코드에서 제거. eligible
    설비 중 유효 speed 가 하나도 없으면 warning 용으로 모든 설비에 대해
    scalar fallback 값을 동일하게 반환한다 (solver 가 설비 구분 불가한 상태).
    """
    if not eligible:
        return {}

    rep = group_batches[0]
    sq = float(rep.sq_mm2 or 0)

    # 1) 각 설비의 line_speed 수집
    eq_speeds: dict[str, float] = {}
    for eq in eligible:
        sm = speed_map.get((eq.equipment_code, sq))
        if sm and sm.line_speed_mpm and float(sm.line_speed_mpm) > 0:
            eq_speeds[eq.equipment_code] = float(sm.line_speed_mpm)

    # 배치 자체 line_speed_mpm (배치 rep 에서 fallback)
    rep_line_speed = float(rep.line_speed_mpm or 0)

    # Fallback speed: 설비 개별 speed 못 찾은 경우 사용할 값
    fallback_speed = rep_line_speed if rep_line_speed > 0 else 0.0
    if fallback_speed <= 0 and eq_speeds:
        # eligible 중 일부만 speed 있고 나머지는 없을 때 — 평균으로 대체
        fallback_speed = sum(eq_speeds.values()) / len(eq_speeds)

    # 2) 설비별 duration 계산
    result: dict[str, float] = {}
    header = next((b for b in group_batches if b.batch_seq == -1), None)

    for eq in eligible:
        ls = eq_speeds.get(eq.equipment_code, fallback_speed)

        if header is not None:
            hd = float(header.estimated_duration_min or 0)
            if hd <= 0:
                _ls = float(header.line_speed_mpm or 0) or ls
                hd = float(header.total_length_m or 0) / _ls if _ls > 0 else 0
            result[eq.equipment_code] = hd
            continue

        total = 0.0
        for b in group_batches:
            d = float(b.estimated_duration_min or 0)
            if d <= 0:
                _ls = float(b.line_speed_mpm or 0) or ls
                d = (
                    (float(b.total_length_m or 0) + float(b.extra_length_m or 0)) / _ls
                    if _ls > 0
                    else 0
                )
            total += d
        result[eq.equipment_code] = total

    return result


def _is_multi_equip_group(
    gk: str,
    gb: list[ProductionBatch],
    eligible: list[EquipmentMaster],
    sq_to_equip: dict,
) -> tuple[bool, int]:
    """멀티설비 분배 대상 여부와 총 드럼 수 반환."""
    rep = gb[0]
    is_stranding = rep.process_name == "연선"
    sq_key = (rep.process_name, int(rep.sq_mm2 or 0))

    header = next((b for b in gb if b.batch_seq == -1), None)
    total_drums = (
        int(header.drum_count or 0)
        if header
        else sum(int(b.drum_count or 0) for b in gb)
    )

    multi_eligible = (
        (is_stranding and not _is_core_group(gk) and sq_key not in sq_to_equip)
        or rep.process_name == "고압절연"
        or rep.process_name == "고압시스"
    )
    return (multi_eligible and total_drums >= 2 and len(eligible) >= 2), total_drums


# ── 선점 스케줄링 헬퍼 ─────────────────────────────────────────────────────


def _drums_completable(
    task_start: datetime,
    preempt_at: datetime,
    setup_min: float,
    work_dur_min: float,
    total_drums: int,
    eq_code: str | None,
    db,
) -> int:
    """작업 시작부터 preempt_at 직전까지 완료 가능한 드럼 수 (이진탐색).

    setup이 끝나기 전에 preempt_at이 오면 0 반환.
    """
    if total_drums <= 0 or preempt_at <= task_start:
        return 0
    drum_min = work_dur_min / max(total_drums, 1)
    lo, hi = 0, total_drums
    while lo < hi:
        mid = (lo + hi + 1) // 2
        end_mid = calculate_end_datetime(
            task_start, setup_min + mid * drum_min, db, eq_code
        )
        if end_mid <= preempt_at:
            lo = mid
        else:
            hi = mid - 1
    return lo


def _delete_task_safely(db: Session, task: ScheduleTask) -> int:
    """ScheduleTask 삭제 전에 참조 FK 들을 안전하게 해제한다.

    왜: 동일 트랜잭션에서
      - `auto_assign` audit_log 가 task_id 로 이 행을 참조 (audit_log_task_id_fkey)
      - 다른 schedule_task 의 predecessor_task_id 가 이 행을 참조 (self-FK)
    둘 다 살아있으면 db.delete(task) 가 ForeignKeyViolation 으로 실패한다.
    audit 이력은 run_label/batch_id 로 추적 가능하므로 task_id 만 NULL 로 끊는다.
    predecessor 체인은 단방향 공정 순서이므로 NULL 허용 (선행 미상으로 표시).

    Returns:
        삭제된 task.task_id — 호출부에서 in-memory map(예: predecessor_map) 정리용.
    """
    from app.infrastructure.models.audit_log import AuditLog

    deleted_id = task.task_id
    db.query(AuditLog).filter(AuditLog.task_id == deleted_id).update(
        {"task_id": None}, synchronize_session=False
    )
    db.query(ScheduleTask).filter(
        ScheduleTask.predecessor_task_id == deleted_id
    ).update({"predecessor_task_id": None}, synchronize_session=False)
    db.delete(task)
    return deleted_id


def _try_preempt_for_urgent(
    earliest: datetime,
    chosen_eq_code: str,
    run_label: str,
    timeline: dict[str, list],
    db: Session,
    urgent_priority: int = 7,
    predecessor_map: dict[tuple, int] | None = None,
) -> list[ProductionBatch]:
    """긴급 배치를 위해 chosen_eq_code의 블로킹 태스크를 선점한다.

    earliest 시점을 가로막는 슬롯을 처리하는 두 가지 전략:

    A) 멀티드럼(drum_count >= 2): 드럼 경계에서 분할
       - 기존 ScheduleTask 의 end_datetime 을 earliest 이전으로 단축
       - 잔여 드럼 분량의 새 ProductionBatch (status='planned') 생성

    B) 단드럼(drum_count < 2) + 비긴급 블로킹 배치: 밀어내기(deferral)
       - 블로킹 ScheduleTask 삭제 + 해당 배치 status='planned' 리셋
       - 원 배치를 반환 → 긴급 배치 완료 후 재스케줄링

    두 전략 모두 timeline 인플레이스 갱신 후 remainder 배치 목록을 반환한다.

    Args:
        urgent_priority: 긴급 배치의 customer_priority (이 값 이하인 블로킹 배치는 밀지 않음)
    """
    slots = list(timeline.get(chosen_eq_code, []))
    if not slots:
        return []

    remainder_batches: list[ProductionBatch] = []

    for slot_start, slot_end in sorted(slots, key=lambda s: s[0]):
        if slot_end <= earliest:
            continue  # earliest 이전에 이미 끝난 슬롯 → 무시
        if slot_start >= earliest:
            break  # earliest 이후 시작 → 긴급 배치가 앞에 끼어들 여지가 있음

        # slot_start < earliest < slot_end → 진행 중인 블록이 earliest를 가로막는 경우
        task = (
            db.query(ScheduleTask)
            .filter(
                ScheduleTask.run_label == run_label,
                ScheduleTask.equipment_code == chosen_eq_code,
                ScheduleTask.start_datetime == slot_start,
                ScheduleTask.end_datetime == slot_end,
            )
            .first()
        )
        if task is None:
            break

        src_batch = (
            db.query(ProductionBatch)
            .filter(ProductionBatch.batch_id == task.batch_id)
            .first()
        )
        if src_batch is None:
            break

        total_drums = int(src_batch.drum_count or 1)
        setup_min = float(task.setup_time_min or 0)
        work_dur_min = float(src_batch.estimated_duration_min or 0)

        blocking_priority = int(src_batch.customer_priority or 99)

        if total_drums < 2:
            # ── 전략 B: 단드럼 밀어내기 ───────────────────────────────────────
            # 블로킹 배치도 긴급/중요 수준이면 양보 불가
            if blocking_priority <= urgent_priority:
                break  # 동급 이상 긴급 배치 — 밀 수 없음

            # 비긴급 단드럼 배치: ScheduleTask 삭제 후 재스케줄링 대상으로 반환
            deleted_id = _delete_task_safely(db, task)
            if predecessor_map is not None:
                # 삭제된 task_id 를 가리키던 predecessor 엔트리 제거 —
                # 이후 INSERT 가 없어진 task 를 참조해 FK 위반되는 것을 방지.
                for _k in [k for k, v in predecessor_map.items() if v == deleted_id]:
                    predecessor_map.pop(_k, None)
            db.flush()

            # timeline에서 슬롯 제거 (긴급 배치가 이 자리를 사용)
            tl = timeline[chosen_eq_code]
            tl.remove((slot_start, slot_end))

            # 배치 상태 planned로 되돌리고 재스케줄링 대상에 추가
            src_batch.status = "planned"
            src_batch.equipment_code = chosen_eq_code  # 같은 설비에서 재스케줄링
            db.flush()
            remainder_batches.append(src_batch)
            break

        # ── 전략 A: 멀티드럼 분할 ─────────────────────────────────────────
        k = _drums_completable(
            slot_start,
            earliest,
            setup_min,
            work_dur_min,
            total_drums,
            chosen_eq_code,
            db,
        )
        if k == 0:
            # 셋업조차 완료 불가 → 단드럼 밀어내기와 동일 처리 (비긴급인 경우)
            if blocking_priority <= urgent_priority:
                break  # 동급 이상 긴급 → 포기
            deleted_id = _delete_task_safely(db, task)
            if predecessor_map is not None:
                for _k in [k2 for k2, v in predecessor_map.items() if v == deleted_id]:
                    predecessor_map.pop(_k, None)
            db.flush()
            tl = timeline[chosen_eq_code]
            tl.remove((slot_start, slot_end))
            src_batch.status = "planned"
            src_batch.equipment_code = chosen_eq_code
            db.flush()
            remainder_batches.append(src_batch)
            break

        # k > 0: earliest 전에 k 드럼 완료 → 분할 처리
        drum_min = work_dur_min / total_drums
        trim_dur = setup_min + k * drum_min
        new_end = calculate_end_datetime(slot_start, trim_dur, db, chosen_eq_code)

        # 기존 태스크 단축
        task.end_datetime = new_end

        # timeline 갱신
        tl = timeline[chosen_eq_code]
        tl.remove((slot_start, slot_end))
        tl.append((slot_start, new_end))

        # 잔여 배치 생성 (remain_drums 드럼, setup 없음)
        remain_drums = total_drums - k
        remain_dur = remain_drums * drum_min
        remain_len = float(src_batch.total_length_m or 0) * remain_drums / total_drums
        new_bg = (
            f"{src_batch.batch_group}_REMAIN"
            if src_batch.batch_group
            else f"REMAIN_{src_batch.batch_id}"
        )

        rem_b = ProductionBatch(
            run_label=run_label,
            sales_order_id=src_batch.sales_order_id,
            sales_order_line=src_batch.sales_order_line,
            item_code=src_batch.item_code,
            routing_code=src_batch.routing_code,
            process_name=src_batch.process_name,
            equipment_code=chosen_eq_code,
            batch_seq=src_batch.batch_seq,
            drum_length_m=src_batch.drum_length_m,
            drum_count=remain_drums,
            total_length_m=remain_len,
            extra_length_m=src_batch.extra_length_m,
            sq_mm2=src_batch.sq_mm2,
            core_count=src_batch.core_count,
            core_colors=src_batch.core_colors,
            sheath_color=src_batch.sheath_color,
            customer_name=src_batch.customer_name,
            due_date=src_batch.due_date,
            customer_priority=src_batch.customer_priority,
            line_speed_mpm=src_batch.line_speed_mpm,
            setup_time_min=0,
            estimated_duration_min=remain_dur,
            status="planned",
            remarks=f"[선점분할 잔여] 원배치={src_batch.batch_id} ({k}/{total_drums}드럼 선점)",
            product_group=src_batch.product_group,
            voltage=src_batch.voltage,
            conductor_material=src_batch.conductor_material,
            stranding_type=src_batch.stranding_type,
            batch_group=new_bg,
            spec_raw=src_batch.spec_raw,
        )
        db.add(rem_b)
        db.flush()
        remainder_batches.append(rem_b)

        # 원 배치 drum_count / length / duration 를 완료분(k)으로 갱신
        orig_total_m = float(src_batch.total_length_m or 0)
        src_batch.drum_count = k
        src_batch.total_length_m = orig_total_m * k / total_drums
        src_batch.estimated_duration_min = trim_dur - setup_min
        db.flush()

        break  # 보통 한 슬롯만 처리

    return remainder_batches


# ── 메인 함수 ─────────────────────────────────────────────────────────────


def resolve_base_date(run_label: str, base_date: datetime | None = None) -> datetime:
    """run_label/base_date 조합으로 CP-SAT 기준일시를 결정.

    왜 공용 헬퍼로 뽑았는가:
      기존에는 cp_sat_schedule 진입부 (line 842 근처), schedule_optimizer
      의 긴급수주 재최적화 (line 2153) 등 여러 곳에서 동일 폴백 로직이
      복제되어 있었다. warm_start_hints 를 auto_schedule 에서 자동 생성할
      때 `_datetime_to_wmin(existing_task.start_datetime, base_date)` 를
      호출해야 하므로, **cp_sat_schedule 이 내부적으로 쓸 base_date 와
      정확히 동일한 값** 으로 미리 결정해두는 단일 진실 공급원이 필요하다.

    규칙:
      - base_date 가 명시 전달되면 그대로 반환.
      - None 이면 run_label 접두부 YYYYMMDD 로 08:00 생성.
      - 파싱 실패 시 KST 당일 08:00 으로 폴백.
    """
    if base_date is not None:
        return base_date
    try:
        dp = run_label.split("_")[0]
        return datetime(int(dp[:4]), int(dp[4:6]), int(dp[6:8]), 8, 0, 0)
    except Exception:
        from zoneinfo import ZoneInfo

        kst = datetime.now(ZoneInfo("Asia/Seoul"))
        return kst.replace(hour=8, minute=0, second=0, microsecond=0, tzinfo=None)


def _datetime_to_wmin(dt: datetime, base_date: datetime) -> int:
    """datetime 을 CP-SAT 내부 working-minutes 축(하루 840분, 08:00~22:00) 으로 변환.

    Why (C1 fix): CP-SAT 모델의 시간축은 **working-minutes** 로, 하루 840 분
    (_WORK_MIN_PER_DAY, 08:00~22:00) 만 카운트하고 야간/주말은 제외한다.
    `_due_work_min` 은 날짜 단위(wd * _WORK_MIN_PER_DAY) 만 처리하므로 시각/분
    해상도가 없다. frozen task 의 start_datetime / end_datetime 은 hour/minute
    까지 포함한 datetime 이라 정확한 변환을 위해 **시각부 분 offset** 까지
    계산해야 frozen 위치가 솔버 축에서 올바른 지점에 박힌다.

    과거 구현은 `(dt - base_date).total_seconds() // 60` 로 wall-clock delta 를
    반환해 시간축이 어긋났고, 솔버가 frozen 위치를 "눈에 보이는 것보다 훨씬 뒤"
    로 인식해 INFEASIBLE 또는 비합리적 배치 이동을 야기했다.

    변환 규칙:
      - dt < base_date: 0 반환 (이미 지난 시각. `_ALWAYS_FROZEN_STATUSES` 경로에서
        completed 로 판정되어 모델에서 제외되어야 정상; 방어적으로 clamp).
      - dt ≥ base_date: 날짜부(working days) × 840 + 시각부(08:00 기준 분 offset).
      - 시각부가 [08:00, 22:00) 밖이면 경계로 clamp:
          · dt.hour < 8 → 그날 시작점 (0)
          · dt.hour ≥ 22 → 그날 끝 (840)

    가정:
      - tz naive (KST) — 기존 코드 전반의 패턴.
      - within-day offset 은 08:00 기준 선형 offset 으로만 계산하고 휴식
        (점심/저녁/간식) 은 무시한다. CP-SAT 축은 순서 결정용으로 충분하며
        실제 배치는 calendar_engine 이 휴식을 반영한다.
    """
    if dt < base_date:
        return 0
    day_off = _work_days_between(base_date.date(), dt.date()) * _WORK_MIN_PER_DAY
    if dt.hour < 8:
        time_off = 0
    elif dt.hour >= 22:
        time_off = _WORK_MIN_PER_DAY
    else:
        time_off = (dt.hour - 8) * 60 + dt.minute
    return day_off + time_off


def cp_sat_schedule(
    run_label: str,
    db: Session,
    *,
    base_date: datetime | None = None,
    random_seed: int = 0,
    frozen_group_keys: set[str] | None = None,
    sheath_color_hard: bool = True,
    tardiness_hard: bool = True,
    time_limit_sec: int | None = None,
    warm_start_hints: dict[str, dict] | None = None,
    # ── Task 1.1 (Rev 3 리팩터): 파러티 하니스용 결정론 훅 ──────────────
    # 세 파라미터 모두 **기본값이 None** 이어서, 기존 호출자 8 곳은 한 줄도
    # 고칠 필요가 없다. Task 1.4 (parity harness) 와 Task 1.5 (CI gate) 에서
    # 이 훅들을 사용해 pre/post 리팩터 bit-exact 비교를 수행한다.
    solver_input_override: SolverInput | None = None,
    num_search_workers: int | None = None,
    run_id_override: str | None = None,
) -> dict:
    """
    CP-SAT 기반 자동 배치.

    CP-SAT → 전체 그룹의 처리 순서 결정
    캘린더 그리디 → 그 순서대로 실제 시작/종료 시각 계산 및 DB 저장

    Args:
        random_seed: CP-SAT 솔버의 random_seed. retry wrapper 가 시도 번호를
            전달해 결정론적 동일 해가 반복되는 것을 방지한다 (기본 0).
        frozen_group_keys: 재최적화 시 고정할 batch_group 집합. 각 그룹의
            start/end/equipment 는 기존 DB ScheduleTask 값으로 박힌다. 긴급수주
            추가 후 전역 재최적화에서 "이미 진행중/완료/base_date 이전 scheduled"
            배치가 움직이지 않도록 보장. None 또는 빈 set 이면 기존 동작 유지.
        sheath_color_hard: 시스(저압/고압) 색상 클러스터 내 인접 그룹을 hard
            constraint 로 강제할지 여부 (기본 True — 긴급수주 반영 시 "블록
            배치에서 색상 우선" 사용자 결정사항). True 일 때:
              1) 같은 (설비 카테고리, 주차, 색상) 클러스터로 묶인 그룹들의
                 인접 쌍에 대해 gk_b.start ≥ gk_a.end + color_changeover_min
                 을 model.add() 로 강제.
              2) 동일 인접 쌍은 같은 설비 선택을 강제 (cluster 의 의미가
                 "같은 설비에서 연속" 이므로).
            False 면 기존 soft penalty(chain_terms)만 유지되는 기존 동작.
            호출측(auto_schedule)이 전달하지 않으면 True 가 적용된다.
        tardiness_hard: 납기 초과를 hard constraint 로 강제할지 여부 (기본 True —
            P9-B "Tardiness A 엄격" 사용자 결정). True 일 때:
              - `model.add(e <= due_wmin)` 로 납기 직접 강제. 단 1분도 초과 불가.
              - tardiness 목적함수 항이 제거되므로 objective = idle + CHAIN*chain_diff.
              - INFEASIBLE 시 호출부(_reschedule_affected_groups_cpsat)가
                (tardiness_hard=False, sheath_color_hard=False) 등으로 단계적 완화.
            False 면 기존 weight-based soft(tardiness * _TARDINESS_WEIGHT) 동작 유지.
            단, `_CHAIN_WEIGHT=120` 은 두 모드 모두 공통 적용 (색상 교체가 tardiness
            와 동일 분 단위로 경쟁 가능하도록).
        time_limit_sec: 솔버 wall-time 상한(초). None 이면 `_SOLVER_TIME_LIMIT_SEC` (30)
            기본. 증분 경로는 10 으로 낮추어 UX 체감 개선 권장. 전역 재최적화는
            60 까지 허용 가능. 값은 `max(1, int(v))` 로 clamp.
        warm_start_hints: 자유 변수에 주입할 웜스타트 힌트 dict.
            형식: `{batch_group: {"start_wmin": int, "equipment_code": str}}`.
            - `add_hint()` 는 hard constraint 가 아닌 "탐색 시작점" — 더 나은 해가
              있으면 솔버가 자유롭게 이동한다 (품질은 목적함수로 결정).
            - `frozen_group_keys` 와 배타 — 이미 hard-pinned 된 그룹의 힌트는
              무시(redundant). start_vars 에 없는 그룹도 skip (stale key 방어).
            - start_wmin 이 horizon 범위를 벗어나면 skip. 설비 코드가 eligible
              에 없으면 시간만 주입. `add_hint()` 는 silent-fail 이므로 힌트가
              현 제약에 맞지 않아도 솔버는 죽지 않고 전역 탐색으로 대체.
            - 효과: ERP 재업로드·증분 시나리오에서 이전 해의 대부분 feasibility 를
              유지한 채 변경 부분만 재탐색 → 실측 2.5~5× speedup 기대.
            - `result["warm_start_applied"]` / `warm_start_skipped` 카운터로 관측.

    Returns:
        {"total_tasks", "violations", "warnings", "solver_status", "objective_value",
         "solver_wall_time_s", "solver_n_groups", "solver_num_workers",
         "warm_start_applied", "warm_start_skipped"}
    """
    result: dict[str, Any] = {
        "total_tasks": 0,
        "violations": [],
        "warnings": [],
        "solver_status": "UNKNOWN",
        "objective_value": 0,
        # 웜스타트 주입 결과 관측 카운터 — warm_start_hints 미사용 시 0/0.
        "warm_start_applied": 0,
        "warm_start_skipped": 0,
    }

    # Task 1.1 (Rev 3): `run_id_override` 는 파러티 하니스용 결정론 훅 placeholder.
    # 현재 `cp_sat_schedule` 은 자체 UUID 생성 경로가 없어 (uuid.uuid4 호출 無)
    # override 를 쓸 대상이 없다 — 값만 result 에 전달해 caller/harness 가 동일
    # 해시 키를 붙일 수 있게 한다. Task 1.2 (run_id persistence) 이후 실제
    # UUID 생성 지점이 생기면 여기서 그 값을 대체하도록 확장.
    if run_id_override is not None:
        result["run_id"] = run_id_override

    # ── 1-3. DB 로드 또는 override rebind ────────────────────────────────
    # Task 1.1 (Rev 3): `solver_input_override` 가 None 이면 기존 DB 로드 블록을
    # 한 바이트도 바꾸지 않고 그대로 수행 (parity 안전). override 가 주어지면
    # 같은 변수 이름으로 필드를 rebind 하여 아래 §4 이후 코드를 그대로 재사용.
    if solver_input_override is None:
        # ── 1. 배치 로드 ──────────────────────────────────────────────────────
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
        batches.sort(
            key=lambda b: (
                PROCESS_ORDER.get(b.process_name, 50),
                b.batch_seq or 0,
                b.due_date or date.max,
                b.customer_priority or 99,
                -(float(b.sq_mm2 or 0)),
            )
        )

        if not batches:
            result["warnings"].append("배치 없음 — Stage 1을 먼저 실행하세요")
            return result

        # WIP 스킵
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
                skip_set = _WIP_SKIP_PROCESSES.get(
                    wip_stage_map[b.wip_matched_id], set()
                )
                if b.process_name in skip_set:
                    b.status = "wip_complete"
                    wip_skipped += 1
                    continue
            schedulable.append(b)
        batches = schedulable
        if wip_skipped:
            result["wip_skipped"] = wip_skipped

        # ── 2. 기준일시 ───────────────────────────────────────────────────────
        if base_date is None:
            try:
                dp = run_label.split("_")[0]
                base_date = datetime(int(dp[:4]), int(dp[4:6]), int(dp[6:8]), 8, 0, 0)
            except Exception:
                from zoneinfo import ZoneInfo

                kst = datetime.now(ZoneInfo("Asia/Seoul"))
                base_date = kst.replace(
                    hour=8, minute=0, second=0, microsecond=0
                ).replace(tzinfo=None)

        # ── 3. 마스터 데이터 로드 ─────────────────────────────────────────────
        equipment_list = db.query(EquipmentMaster).all()
        equipment_by_process: dict[str, list[EquipmentMaster]] = {}
        for eq in equipment_list:
            equipment_by_process.setdefault(eq.process_name, []).append(eq)

        # SpeedMaster 한 번 로드. speed_map 은 (eq, sq) lookup, color_setup_map 은
        # "색상 교체 시간은 설비 파라미터" 라서 sq 와 무관한 equipment_code → setup_color_min
        # 인덱스. 기존 코드는 색상 교체가 발생할 때마다 SpeedMaster 를 재조회(N+1)
        # 하여 원격 Supabase 왕복이 누적됐음 — 한 번의 메모리 조회로 대체.
        _speed_rows = db.query(SpeedMaster).all()
        speed_map: dict[tuple, SpeedMaster] = {
            (sr.equipment_code, float(sr.cross_section or 0)): sr for sr in _speed_rows
        }
        # 동일 설비에 여러 sq row 가 있으면 setup_color_min 은 첫 non-null 을 채택
        # (현 DB 스키마상 설비별로 일정하다는 전제 — 과거 조회 로직 `.first()` 와 동치).
        color_setup_map: dict[str, float | None] = {}
        for sr in _speed_rows:
            code = sr.equipment_code
            if code not in color_setup_map:
                color_setup_map[code] = sr.setup_color_min
            elif color_setup_map[code] is None and sr.setup_color_min is not None:
                color_setup_map[code] = sr.setup_color_min

        # ConstraintConfig 프리페치 (4-2 색상교체 fallback 등에서 재사용)
        constraint_params = ConstraintParams.load(db)

        # 용접 시간 (4-4): ConstraintParams 통합 경로로 조회 (하위 호환 default 유지)
        welding_min = constraint_params.get(
            "4-4", "welding_min", default=_DEFAULT_WELDING_MIN
        )

        # SQ → 소선경 매핑 (연선 셋업 3-tier 계산용)
        sq_to_wire_d: dict[int, float] = {
            int(d.cross_section): float(d.wire_diameter)
            for d in db.query(DrumLotMaster).all()
            if d.wire_diameter is not None
        }
    else:
        # Override 경로 — 필드 이름을 로컬 변수로 rebind. `batches` 만 list()
        # 로 사본화 (§4 이후 로직이 in-place 로 섞일 여지 방어). 나머지 dict 도
        # 얕은 복사 — ORM 값(ConstraintParams/EquipmentMaster 등) 자체는 공유.
        batches = list(solver_input_override.batches)
        wip_skipped = solver_input_override.wip_skipped
        if wip_skipped:
            result["wip_skipped"] = wip_skipped
        # "배치 없음" 게이트 — 원본 L1098 과 동일 메시지. override 의 batches 가
        # 비면 여기서 조기 반환해 parity 유지.
        if not batches:
            result["warnings"].append("배치 없음 — Stage 1을 먼저 실행하세요")
            return result
        # `base_date` 는 SolverInput 이 이미 확정값을 담고 있음 (run_label
        # 파생 또는 명시). 호출측 base_date kwarg 는 무시 — override 가 진실.
        base_date = solver_input_override.base_date
        equipment_list = solver_input_override.equipment_list
        equipment_by_process = dict(solver_input_override.equipment_by_process)
        speed_map = dict(solver_input_override.speed_map)
        color_setup_map = dict(solver_input_override.color_setup_map)
        constraint_params = solver_input_override.constraint_params
        welding_min = solver_input_override.welding_min
        sq_to_wire_d = dict(solver_input_override.sq_to_wire_d)

    # ── 4. 그루핑 ─────────────────────────────────────────────────────────
    batch_groups: OrderedDict[str, list[ProductionBatch]] = OrderedDict()
    for b in batches:
        key = b.batch_group or f"_single_{b.batch_id}"
        batch_groups.setdefault(key, []).append(b)

    # ── 5. 그룹별 메타 계산 ───────────────────────────────────────────────
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
            result["warnings"].append(
                f"배치그룹 {gk}: 공정 '{rep.process_name}' SQ={rep.sq_mm2} — 적합한 설비 없음"
            )
            continue

        work_dur = _compute_group_duration(gb, eligible, speed_map)
        setup_min = float(rep.setup_time_min or 0)
        drum_wind = _get_drum_winding_min(
            eligible[0].equipment_code, rep.sq_mm2, speed_map
        )
        # CP-SAT 내부 duration: 실제 근무 분 그대로 사용 (최소 1분)
        # 종전 840분 단위 올림은 모든 작업이 같은 크기로 보여 EDD 정렬이 불가능했음
        cpsat_dur_raw = max(1, int(math.ceil(work_dur + setup_min + drum_wind)))

        # Round 2 HIGH #7 (conservative): 멀티설비 분배 대상은 실제 배치 단계에서
        # `_schedule_multi_equipment` 로 N 설비 병렬 실행 → wall-clock duration 은
        # 대략 raw / N_split. CP-SAT 모델은 단일 interval 가정이라 raw 를 그대로
        # 쓰면 "분할 가능 그룹이 실제보다 오래 걸린다" 고 오인해 후속 배치를 뒤로
        # 밀게 됨. 여기서 근사치 N_split 로 나눠 solver 가 실제 wall-clock 을
        # 반영하게 함. 완전한 분할 모델링(N intervals per group)은 별도 phase.
        _is_multi_pre, _total_drums_pre = _is_multi_equip_group(
            gk,
            gb,
            eligible,
            {},  # pre-phase: sq_to_equip 비어있어 초기 판단
        )
        if _is_multi_pre and _total_drums_pre >= 2 and len(eligible) >= 2:
            _n_split = max(1, min(_total_drums_pre, len(eligible)))
            # 실제 setup 은 분할되지 않으므로 work_dur 만 나눔 + setup/drum_wind 유지
            _work_per_split = work_dur / _n_split
            cpsat_dur = max(1, int(math.ceil(_work_per_split + setup_min + drum_wind)))
        else:
            cpsat_dur = cpsat_dur_raw

        # Round 2 HIGH #5: 설비별 duration map (solver 가 "빠른 설비 선호" 가능).
        # 동일 배치를 설비 A/B 에 할당 시 duration 차이가 유의미하면 설비 간
        # 개별 cpsat_dur_by_eq 를 interval 에 적용. 차이 < 10% 이면 평균값(cpsat_dur)
        # 사용 (모델 경량화).
        _dur_map = _compute_group_duration_map(gb, eligible, speed_map)
        # setup/drum_wind 를 더해 각 설비별 total cpsat dur 계산.
        # drum_wind 는 설비 카테고리 단위이므로 eligible[0] 의 값을 공통 사용
        # (설비간 차이는 추후 개선 항목).
        # Round 2 HIGH #7: 멀티설비 분할 대상이면 설비별 work_dur 도 N_split 로 나눔.
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
            # 빈 map (eligible 전부 speed 데이터 없음) — 단일 값으로 fallback +
            # warning 기록. hard-coded 10 fallback 은 제거 (MED #5 요구사항).
            for _eq in eligible:
                cpsat_dur_by_eq[_eq.equipment_code] = cpsat_dur
            result["warnings"].append(
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

        # Past-due 심각도 배율 — due_wmin < 0 일수록 더 큰 가중치.
        # 예: 10일 과거 → past_days = 10 → severity_mul = 1 + 10/5 = 3.0.
        # on-time 그룹은 past_days=0 → ×1.0 (기존 priority 가중치 그대로).
        #
        # divisor 는 `_WORK_MIN_PER_DAY` (840, 1근무일) — `_due_work_min` 이
        # legacy fallback 에서 `wd * _WORK_MIN_PER_DAY` 로 산출하므로 동일
        # 축. 기존 구현은 `_WORK_MIN_PER_DAY_DEFAULT` (1440, 24h full) 로
        # 나눠 past_days 가 840/1440 ≈ 0.58배 과소평가, severity 배율이
        # 설계치의 68% 수준으로 약화됐음 (c5be1a6 의 의도와 어긋남).
        # 실측: 150SQ 실 past 4일 → snapshot past_days=1.17 (= 4 × 840/1440),
        # severity 1.23 (정상 1.8). 수정 후 past_days=4.0, severity 1.8 회복.
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
            # Round 2 HIGH #5
            "cpsat_dur_by_eq": cpsat_dur_by_eq,
            "per_eq_dur_enabled": _per_eq_dur_enabled,
            "due_wmin": due_wmin,
            "weight": _weight,
            "earliest_due": earliest_due,
            # 인접 쌍 chain_terms 계산용 ordinal — None 안전
            "due_date_ord": earliest_due.toordinal() if earliest_due else None,
            "sq": int(rep.sq_mm2 or 0),
        }

    if not group_meta:
        result["warnings"].append("스케줄링 가능한 배치 그룹 없음")
        return result

    # ── 6. CP-SAT 모델 구성 ───────────────────────────────────────────────
    model = cp_model.CpModel()
    groups = list(group_meta.keys())

    # 6-a. 설비 유니버스
    all_eq_codes = sorted(
        {e.equipment_code for eqs in equipment_by_process.values() for e in eqs}
    )

    # 6-b. 결정변수: start / end / equip_bool / tardiness
    start_vars: dict[str, cp_model.IntVar] = {}
    end_vars: dict[str, cp_model.IntVar] = {}
    equip_vars: dict[str, dict[str, cp_model.IntVar]] = {}
    tardiness_vars: dict[str, cp_model.IntVar] = {}

    # Round 2 HIGH #5: 그룹별 "effective dur" — per_eq_dur_enabled 이면 설비 bool
    # 에 종속된 선형합으로 표현, 아니면 스칼라. end = start + dur 로 고정.
    # dur_vars[gk]: None (스칼라) 또는 IntVar.
    dur_vars: dict[str, Any] = {}
    for gk in groups:
        meta = group_meta[gk]
        dur = meta["cpsat_dur"]
        per_eq = meta.get("per_eq_dur_enabled", False)
        dur_by_eq = meta.get("cpsat_dur_by_eq") or {}

        if per_eq and dur_by_eq:
            # 설비별 dur: dur_var = sum(eq_bool_i * dur_i). equip_vars 는 exactly_one
            # 이므로 dur_var 는 정확히 한 설비의 dur 를 갖게 된다 (scalar product).
            _min_dur = min(dur_by_eq.values())
            _max_dur = max(dur_by_eq.values())
            dur_var = model.new_int_var(_min_dur, _max_dur, f"dur_{gk}")
            # s upper bound: horizon - min_dur (그래야 어떤 설비 선택에도 e ≤ horizon)
            s = model.new_int_var(0, _MAX_HORIZON_MIN - _min_dur, f"s_{gk}")
            e = model.new_int_var(_min_dur, _MAX_HORIZON_MIN, f"e_{gk}")
            model.add(e == s + dur_var)
            dur_vars[gk] = dur_var
        else:
            s = model.new_int_var(0, _MAX_HORIZON_MIN - dur, f"s_{gk}")
            e = model.new_int_var(dur, _MAX_HORIZON_MIN, f"e_{gk}")
            model.add(e == s + dur)
            dur_vars[gk] = None

        start_vars[gk] = s
        end_vars[gk] = e

        if tardiness_hard:
            # P9-B: 납기 hard constraint — end_var ≤ due_wmin 로 직접 강제.
            # due 가 있을 때만 제약 추가. (due_wmin == _MAX_HORIZON_MIN 인 no-due
            # 그룹은 제약 추가해도 무의미하게 통과하므로 skip — 모델 경량화.)
            #
            # Past-due 처리 (개정): 기존에는 due_wmin<0 이면 제약도 skip, tardiness_vars
            # 도 미생성 → solver 가 past-due 그룹을 "자유변수(어디 배치해도 obj 영향 0)"
            # 로 보고 임의 위치 선택 → EDD 순서 역전(납기 빠른 게 뒤로 밀림) 관찰됨.
            # 수정: past-due 는 hard 불가이지만 **soft tardiness 항은 생성** 해서
            # `weight × (end + |past|)` 이 objective 에 반영되게 함. 이렇게 하면
            # solver 가 past-due 그룹의 end 를 작게 하려 앞쪽에 배치 → EDD 실현.
            if meta.get("earliest_due") is not None:
                if meta["due_wmin"] < 0:
                    result["warnings"].append(
                        f"그룹 {gk}: 납기 {meta['earliest_due']} 이미 "
                        f"{abs(meta['due_wmin'])}min 지남 — tardiness hard 강제 skip, "
                        f"soft penalty 로 전환"
                    )
                    # Past-due: soft tardiness 생성. tard = max(0, e - due_wmin)
                    # due_wmin<0 이므로 tard = e - due_wmin = e + |past| (항상 양수)
                    tard = model.new_int_var(0, 2 * _MAX_HORIZON_MIN, f"t_past_{gk}")
                    model.add_max_equality(
                        tard, [e - meta["due_wmin"], model.new_constant(0)]
                    )
                    tardiness_vars[gk] = tard
                else:
                    model.add(e <= meta["due_wmin"])
            # placeholder — 이후 코드가 tardiness_vars[gk] 를 참조하지 않아도 안전
        else:
            # Soft 모드 (폴백용): 기존 weight-based tardiness.
            # Round 2 MED #13: due_wmin 음수 허용. `end - due_wmin` 이 음수 due 에
            # 대해 `end + |past|` 로 자연 증가 → "3일 overdue 는 1일 overdue 의
            # 3배 penalty" 실현. tard upper bound 를 2×horizon 으로 확장해
            # 음수 due_wmin 에서도 max_equality 가 안전하게 동작.
            tard = model.new_int_var(0, 2 * _MAX_HORIZON_MIN, f"t_{gk}")
            # tardiness = max(0, end - due)
            model.add_max_equality(tard, [e - meta["due_wmin"], model.new_constant(0)])
            tardiness_vars[gk] = tard

        eq_bools: dict[str, cp_model.IntVar] = {}
        for eq in meta["eligible"]:
            eq_bools[eq.equipment_code] = model.new_bool_var(
                f"eq_{gk}_{eq.equipment_code}"
            )
        equip_vars[gk] = eq_bools
        model.add_exactly_one(eq_bools.values())

        # Round 2 HIGH #5: per_eq_dur_enabled 이면 dur_var == sum(bool_i * dur_i).
        # exactly_one 이 보장되어 있으므로 선형합 = 선택된 설비의 dur.
        if dur_vars.get(gk) is not None:
            _dur_by_eq_local = meta["cpsat_dur_by_eq"]
            model.add(
                dur_vars[gk]
                == sum(
                    eq_bools[_ec] * int(_dur_by_eq_local[_ec])
                    for _ec in eq_bools.keys()
                )
            )

    # 6-b-2. Frozen groups — 기존 ScheduleTask 로 start/end/equipment 고정
    # Why: 긴급수주 재최적화 시 이미 진행 중/완료/base_date 이전 'scheduled' 배치는
    # 움직이면 안 된다 (실제 생산 중인 블록을 이동시키면 작업 중단/폐기 비용 발생).
    # 호출자가 frozen_group_keys 로 대상을 명시하면 해당 그룹의 start_var/end_var/
    # equip_var 를 DB 값으로 박아 솔버가 나머지 그룹만 자유변수로 최적화.
    # 방어적 동작: DB 에 해당 task 없으면 warning 만 기록하고 skip (stale key 대응).
    if frozen_group_keys:
        # run_label 범위 ScheduleTask 를 한번에 로드 (N+1 쿼리 방지)
        frozen_batches_q = (
            db.query(ProductionBatch, ScheduleTask)
            .join(ScheduleTask, ScheduleTask.batch_id == ProductionBatch.batch_id)
            .filter(
                ProductionBatch.run_label == run_label,
                ProductionBatch.batch_group.in_(list(frozen_group_keys)),
                ScheduleTask.run_label == run_label,
                ScheduleTask.start_datetime.isnot(None),
                ScheduleTask.end_datetime.isnot(None),
                ScheduleTask.equipment_code.isnot(None),
            )
            .all()
        )
        # batch_group → 대표 ScheduleTask (첫번째 매치). 여러 batch 가 한 group 에
        # 속해도 group-level start/end/equip 은 대표값으로 고정.
        frozen_task_by_gk: dict[str, ScheduleTask] = {}
        for _pb, _tk in frozen_batches_q:
            _bg = _pb.batch_group
            if _bg and _bg not in frozen_task_by_gk:
                frozen_task_by_gk[_bg] = _tk

        for _gk in frozen_group_keys:
            if _gk not in group_meta:
                # group_meta 에 없음 — 이미 스케줄링 불가(설비 없음) 또는 WIP skip
                result["warnings"].append(
                    f"frozen_group_keys: '{_gk}' 은(는) group_meta 에 없어 고정 불가 (skip)"
                )
                continue
            _tk = frozen_task_by_gk.get(_gk)
            if _tk is None:
                result["warnings"].append(
                    f"frozen_group_keys: '{_gk}' 에 해당하는 ScheduleTask 없음 (skip)"
                )
                continue

            _fixed_start_wmin = _datetime_to_wmin(_tk.start_datetime, base_date)
            _fixed_eq_code = _tk.equipment_code

            # 변수 domain 범위를 벗어나면 모델이 INFEASIBLE → clamp 후 warning
            _dur = group_meta[_gk]["cpsat_dur"]
            if _fixed_start_wmin > _MAX_HORIZON_MIN - _dur:
                result["warnings"].append(
                    f"frozen_group_keys: '{_gk}' start 가 horizon 초과 → 고정 skip"
                )
                continue

            model.add(start_vars[_gk] == _fixed_start_wmin)
            # end 는 (start + dur) 로 이미 묶여있으므로 end 고정은 start 고정과 동치.
            # 방어적으로 end 도 같이 박되 실패하지 않도록 별도 equality 불필요.
            # 단, cpsat_dur 와 실제 DB duration 이 다를 수 있어 end 를 명시 고정하면
            # INFEASIBLE 가능 → start 만 박는다.

            # 설비 고정: 해당 설비 bool=1, 나머지=0
            if _fixed_eq_code in equip_vars[_gk]:
                for _ec, _bv in equip_vars[_gk].items():
                    if _ec == _fixed_eq_code:
                        model.add(_bv == 1)
                    else:
                        model.add(_bv == 0)
            else:
                # eligible 에 없는 설비로 실행 중 → eligible 확장 없이 warning
                # (eligible 재계산은 spec 범위 밖 — 재최적화가 해당 그룹 재배치 시도)
                result["warnings"].append(
                    f"frozen_group_keys: '{_gk}' 의 고정 설비 '{_fixed_eq_code}' 가 "
                    f"eligible 에 없음 → 설비 고정 skip (시간만 고정)"
                )

    # ── 6-b2. 웜스타트 힌트 주입 (자유 변수 대상) ────────────────────────
    # 왜 여기: frozen_group_keys 의 hard-pin 이 먼저 적용된 후에 주입해야
    # 배타성이 자연스럽다 (pinned 그룹에 힌트 주는 건 no-op). 또한 모든
    # start_vars/equip_vars 선언이 끝난 시점이라 dict 조회가 안전.
    #
    # add_hint() 특성:
    #   - hard constraint 가 아닌 "탐색 시작점" 제안. 더 나은 해 발견 시 자유 이동.
    #   - 힌트가 현 제약과 충돌하면 silent-fail (솔버는 죽지 않고 전역 탐색으로).
    #   - 포트폴리오 워커 간 공유되어 여러 워커가 근방에서 병렬 탐색.
    #
    # ERP 재업로드·증분 시나리오에서 이전 해의 대부분 feasibility 를 유지한 채
    # 변경 부분만 재탐색 → 실측 2.5~5× speedup 기대 (PoC 데이터 측정 필요).
    if warm_start_hints:
        _frozen_set = frozen_group_keys or set()
        _applied = 0
        _skipped = 0
        for _gk, _snap in warm_start_hints.items():
            # 이미 hard-pin — 힌트 redundant
            if _gk in _frozen_set:
                _skipped += 1
                continue
            # 모델에 없는 그룹 (스테일 키)
            if _gk not in start_vars:
                _skipped += 1
                continue
            if not isinstance(_snap, dict):
                _skipped += 1
                continue
            _hint_applied_one = False
            # 시작 시각 힌트 — horizon 범위 체크 후 주입
            _start_wmin = _snap.get("start_wmin")
            if isinstance(_start_wmin, int):
                _dur = group_meta[_gk]["cpsat_dur"]
                if 0 <= _start_wmin <= _MAX_HORIZON_MIN - _dur:
                    try:
                        model.add_hint(start_vars[_gk], _start_wmin)
                        _hint_applied_one = True
                    except Exception:
                        # add_hint 가 어떤 이유로든 실패해도 전체 optimize 를
                        # 깨뜨리면 안 됨 — silent degrade.
                        pass
            # 설비 힌트 — eligible 에 있을 때만
            _eq_code = _snap.get("equipment_code")
            if _eq_code and _eq_code in equip_vars.get(_gk, {}):
                try:
                    for _ec, _bv in equip_vars[_gk].items():
                        model.add_hint(_bv, 1 if _ec == _eq_code else 0)
                    _hint_applied_one = True
                except Exception:
                    pass
            if _hint_applied_one:
                _applied += 1
            else:
                _skipped += 1
        result["warm_start_applied"] = _applied
        result["warm_start_skipped"] = _skipped

    # 6-c. 설비 충돌 방지 (no_overlap)
    # Round 2 HIGH #5: per_eq_dur_enabled 이면 interval size 는 설비별 상수 dur_i.
    # 해당 설비 bool=1 일 때만 interval active 이므로 (optional_interval + bv), 각
    # 설비 interval 이 자신의 고유 dur 를 사용 → solver 가 "빠른 설비에 가면
    # overlap 덜 발생" 을 정확히 인식. end_vars[gk] 는 여전히 sum 기반 dur_var 와
    # 연동되므로 선택된 설비의 interval 만 e 와 일치 (나머지는 비활성).
    itv_vars: dict[tuple[str, str], Any] = {}
    for gk in groups:
        meta = group_meta[gk]
        dur_scalar = meta["cpsat_dur"]
        per_eq_enabled = meta.get("per_eq_dur_enabled", False)
        dur_by_eq = meta.get("cpsat_dur_by_eq") or {}
        for eq_code, bv in equip_vars[gk].items():
            # Per-equipment interval size: 활성 설비의 고유 dur.
            _itv_size = (
                int(dur_by_eq.get(eq_code, dur_scalar))
                if per_eq_enabled
                else dur_scalar
            )
            # end_vars[gk] 와 선택된 설비의 interval 만 정합 — 비활성 interval 은
            # start/size/end 값 검증 안됨 (CP-SAT optional 의미). 단 안전을 위해
            # 고정 size interval 을 위한 별도 end helper 사용:
            if per_eq_enabled:
                # optional interval 은 size=상수 일 때 자체 end IntVar 를 요구.
                # start 는 공통 start_vars[gk] 사용, end 는 helper 생성.
                _e_eq = model.new_int_var(
                    _itv_size, _MAX_HORIZON_MIN, f"e_{gk}_{eq_code}"
                )
                # bv=1 일 때만 (s + size == _e_eq AND _e_eq == end_vars[gk]) 강제.
                # bv=0 이면 interval 비활성이므로 _e_eq 값 임의 — 제약 없음.
                model.add(_e_eq == start_vars[gk] + _itv_size).only_enforce_if(bv)
                model.add(_e_eq == end_vars[gk]).only_enforce_if(bv)
                itv = model.new_optional_interval_var(
                    start_vars[gk], _itv_size, _e_eq, bv, f"itv_{gk}_{eq_code}"
                )
            else:
                itv = model.new_optional_interval_var(
                    start_vars[gk], dur_scalar, end_vars[gk], bv, f"itv_{gk}_{eq_code}"
                )
            itv_vars[(gk, eq_code)] = itv

    for eq_code in all_eq_codes:
        itvs = [
            itv_vars[(gk, eq_code)]
            for gk in groups
            if eq_code in equip_vars.get(gk, {})
        ]
        if len(itvs) >= 2:
            model.add_no_overlap(itvs)

    # 6-d. 공정 선후관계: 앞 공정 첫 드럼 완료 후 뒤 공정 시작
    proc_groups_by_sq: dict[tuple[str, int], list[str]] = {}
    for gk in groups:
        meta = group_meta[gk]
        proc_groups_by_sq.setdefault((meta["rep"].process_name, meta["sq"]), []).append(
            gk
        )

    for gk in groups:
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

    # 6-e. CORE → ST 선행 (AL6BO 첫 드럼 → 54BO 시작)
    for core_gk in [gk for gk in groups if _is_core_group(gk)]:
        main_sq = _extract_core_main_sq(core_gk)
        if main_sq is None:
            continue
        core_meta = group_meta[core_gk]
        lot_c = max(sum(int(b.drum_count or 1) for b in core_meta["batches"]), 1)
        first_drum = max(1, math.ceil(core_meta["cpsat_dur"] / lot_c))
        for st_gk in [
            gk for gk in groups if gk.startswith("ST-") and _st_sq(gk) == main_sq
        ]:
            model.add(start_vars[st_gk] >= start_vars[core_gk] + first_drum)

    # 6-f. 목적함수: 파이프라인 유휴 최소화 + 색상 체인 최소화 (+ soft 모드에선 tardiness)
    # 유휴 = succ_end - pred_end (≥ 0, 6-d 하드 제약으로 보장). 납기 가중치(수십~수백)
    # 대비 훨씬 낮은 _IDLE_WEIGHT 로 soft 최적화 — 파이프라인이 빠른 공정일수록
    # 솔버가 start_vars 를 늦춰서 pred_end 와 succ_end 를 정렬시킨다.
    idle_terms: list = []
    for _gk in groups:
        _pred_proc = PREDECESSOR_PROCESS.get(group_meta[_gk]["rep"].process_name)
        if not _pred_proc:
            continue
        for _pred_gk in proc_groups_by_sq.get((_pred_proc, group_meta[_gk]["sq"]), []):
            _idle = model.new_int_var(0, _MAX_HORIZON_MIN, f"idle_{_pred_gk}_{_gk}")
            model.add(_idle == end_vars[_gk] - end_vars[_pred_gk])
            idle_terms.append(_idle)

    # objective 초기화:
    #   tardiness_hard=True  → idle + past-due tardiness (on-time 은 hard 제약)
    #   tardiness_hard=False → sum(weight*tardiness) + idle (기존 soft 유지)
    # 시스 색상 교체 비용은 6-g 에서 sequence-dependent gap 으로 duration 에 직접
    # 반영 (soft penalty 가 아닌 hard interval gap). Solver 가 실제 wall-clock
    # 을 정확히 인식 → tardiness 와의 tradeoff 를 모든 시스 그룹 쌍 단위로 평가.
    #
    # Past-due 가 tardiness_hard=True 에서도 tardiness_vars 에 포함됨 (위 개정).
    # objective 에서도 해당 항을 반영해야 solver 가 past-due 그룹을 앞으로 배치.
    if tardiness_hard:
        _objective = _IDLE_WEIGHT * sum(idle_terms) if idle_terms else 0
        # Past-due: tardiness_vars 에 담긴 그룹만 weight-soft penalty (hard 는 못 검)
        if tardiness_vars:
            _objective = _objective + sum(
                group_meta[gk]["weight"] * tardiness_vars[gk] for gk in tardiness_vars
            )
    else:
        _objective = sum(
            meta["weight"] * tardiness_vars[gk] for gk, meta in group_meta.items()
        )
        if idle_terms:
            _objective = _objective + _IDLE_WEIGHT * sum(idle_terms)

    # 6-f-hard. 시스 색상 체인 Hard Constraint (sheath_color_hard=True 일 때만)
    #
    # Why: 긴급수주 반영 시 사용자 결정사항 — "블록 배치에서 색상 우선을 강제".
    # 기존 6-g 의 soft penalty(chain_terms, weight=1) 는 tardiness_weight 에 밀려
    # 실질적으로 무력해지는 경우가 있었음. Hard 승격 시:
    #   1) 같은 클러스터(설비 카테고리 + 주차 버킷 + 색상) 내 정렬된 인접 쌍이
    #      반드시 같은 설비에서 선행(gk_a → gk_b) 으로 순차 배치.
    #   2) 두 작업 사이 간격 ≥ color_changeover_min (ConstraintConfig 4-2
    #      sheath_color_min, 기본 120 분). 같은 색상이므로 이론상 교체 0 분이 맞지만,
    #      클러스터 단위 연속성을 보장하기 위한 최소 gap 으로만 사용.
    #
    # 방어 로직:
    #   - build_sheath_clusters 빈 list → 제약 추가 없이 pass
    #   - frozen 그룹 pair → skip (start 이미 고정됨, 추가 제약 불필요)
    #   - 인접 쌍 중 하나라도 start_vars/equip_vars 에 없으면 skip
    #   - 클러스터 group_keys 가 1개 이하 → skip (인접 쌍 없음)
    if sheath_color_hard:
        from app.services.sheath_cluster import (
            build_sheath_clusters as _build_sheath_clusters_hard,
        )

        _clusters_hard = _build_sheath_clusters_hard(group_meta)
        # ConstraintConfig 4-2 에서 색상 교체 시간 조회. SpeedMaster 값 없이
        # fallback 경로만 써서 설비별 편차 무시 (클러스터 단위 gap 의미).
        _color_gap_min = int(
            round(
                resolve_color_change_min(
                    sm_color_min=None,
                    params=constraint_params,
                )
            )
        )
        _frozen_set = frozen_group_keys or set()

        for _cluster in _clusters_hard:
            _gks_ord = _cluster.group_keys
            if len(_gks_ord) < 2:
                continue
            for _i in range(len(_gks_ord) - 1):
                _gk_a = _gks_ord[_i]
                _gk_b = _gks_ord[_i + 1]
                # 둘 다 모델에 있는지 확인 (group_meta 에서 제외된 키 방어)
                if _gk_a not in start_vars or _gk_b not in start_vars:
                    continue
                if _gk_a not in equip_vars or _gk_b not in equip_vars:
                    continue
                # 두 그룹 모두 frozen 이면 start 이미 고정 → 추가 제약은 중복/모순 위험
                if _gk_a in _frozen_set and _gk_b in _frozen_set:
                    continue
                # Hard: gk_b 는 gk_a 종료 후 color_gap 이상 이후에 시작
                model.add(start_vars[_gk_b] >= end_vars[_gk_a] + _color_gap_min)
                # 같은 설비 강제: 두 그룹이 공통으로 eligible 한 설비 bool 을 동기화.
                # 교집합 eq 가 없는 경우(서로 다른 설비 후보) → gap 제약만 적용.
                _shared_eqs = equip_vars[_gk_a].keys() & equip_vars[_gk_b].keys()
                for _eq in _shared_eqs:
                    model.add(equip_vars[_gk_a][_eq] == equip_vars[_gk_b][_eq])

    # 6-g. 시스 색상 Sequence-Dependent Setup (정식 모델링).
    #
    # Why: 기존 chain_terms (|start_a - start_b| soft) 은 proximity 만 minimize 하여
    # 실제 "같은 설비에서 다른 색상 인접 시 +color_gap 분" 을 solver 가 인식 못 함.
    # Post-solve 캘린더 엔진만 color_change_min 을 duration 에 더해 solver 목적
    # 함수와 실제 스케줄이 괴리 (solver 는 갈→회→갈→회 와 갈갈→회회 를 동일 비용
    # 으로 착각). 결과: objective 동점 해 중 색상 흩어진 해가 빈번히 선택됨.
    #
    # 수정: 시스 공정 그룹 쌍(gk_a, gk_b) 중 색상 다르고 공통 eligible 설비 있는
    # 경우에 대해, "같은 설비 + 순서" booleans 로 conditional gap 제약을 부여한다.
    #   same_eq = OR_{ec ∈ shared}(equip_a[ec] ∧ equip_b[ec])
    #   a_before_b ∈ {0,1} (solver 자율 선택)
    #   same_eq=1 ∧ a_before_b=1  ⇒  start_b ≥ end_a + color_gap
    #   same_eq=1 ∧ a_before_b=0  ⇒  start_a ≥ end_b + color_gap
    # 효과:
    #   - Solver 가 실제 교체 시간(120 분) 을 duration 으로 반영 → tardiness
    #     vs 색상 묶음 tradeoff 를 정확히 평가.
    #   - 납기 여유 있으면 같은 색상 연속 배치를 자연 선호 (makespan/idle 감소).
    #   - 여유 없으면 색상 포기 (tardiness 피하기 위해).
    #   - AddCircuit / successor-var 기반 완전 TSP 모델 대비 단순. O(n²) 쌍에
    #     per-pair bool 2-3 개 + conditional constraint 4 개 → n≈60 기준 ~3K 변수.
    #
    # Note: 같은 색상 pair 는 gap 0 이면 no_overlap 만으로 충분하므로 skip (모델
    # 경량화). 다른 색상에만 제약 추가.
    _sheath_color_gap_min = int(
        round(
            resolve_color_change_min(
                sm_color_min=None,
                params=constraint_params,
            )
        )
    )
    _sheath_gks_all = [
        _g
        for _g, _m in group_meta.items()
        if _m["rep"].process_name in ("저압시스", "고압시스")
    ]
    for _i in range(len(_sheath_gks_all)):
        for _j in range(_i + 1, len(_sheath_gks_all)):
            _gk_a = _sheath_gks_all[_i]
            _gk_b = _sheath_gks_all[_j]
            _color_a = (group_meta[_gk_a]["rep"].sheath_color or "").strip()
            _color_b = (group_meta[_gk_b]["rep"].sheath_color or "").strip()
            # 색상 미지정/동일: 실물 교체 없음 → 제약 불필요 (no_overlap 으로 충분).
            if not _color_a or not _color_b or _color_a == _color_b:
                continue
            # 공통 eligible 설비 없으면 same_eq 가 항상 0 → 제약 항상 비활성 → skip.
            _shared_eqs_color = set(equip_vars[_gk_a].keys()) & set(
                equip_vars[_gk_b].keys()
            )
            if not _shared_eqs_color:
                continue
            # same_eq bool: 공통 설비 중 하나에서 둘 다 활성화됐는지.
            # exactly_one(equip_vars[g]) 이 각 그룹에 강제되어 있으므로 설비별
            # both_on 의 합 ≤ 1 (둘이 같은 설비에 있거나 없거나).
            _both_bools = []
            for _ec in _shared_eqs_color:
                _both = model.new_bool_var(f"sh_both_{_gk_a}_{_gk_b}_{_ec}")
                model.add_bool_and(
                    [equip_vars[_gk_a][_ec], equip_vars[_gk_b][_ec]]
                ).only_enforce_if(_both)
                model.add_bool_or(
                    [equip_vars[_gk_a][_ec].Not(), equip_vars[_gk_b][_ec].Not()]
                ).only_enforce_if(_both.Not())
                _both_bools.append(_both)
            _same_eq = model.new_bool_var(f"sh_same_eq_{_gk_a}_{_gk_b}")
            model.add(_same_eq == sum(_both_bools))
            # a_before_b: no_overlap 이 둘 중 한 방향을 강제하지만, 어느 방향
            # 인지를 솔버가 선택할 수 있도록 bool 로 bind. objective(+gap) 을
            # 고려해 solver 가 자연스럽게 최적 순서 결정.
            _a_before_b = model.new_bool_var(f"sh_order_{_gk_a}_{_gk_b}")
            model.add(
                start_vars[_gk_b] >= end_vars[_gk_a] + _sheath_color_gap_min
            ).only_enforce_if([_same_eq, _a_before_b])
            model.add(
                start_vars[_gk_a] >= end_vars[_gk_b] + _sheath_color_gap_min
            ).only_enforce_if([_same_eq, _a_before_b.Not()])

    # 6-g-tiebreak. 시스 그룹 makespan bias (색상 묶음 tie-breaker).
    #
    # Why: 위 sequence-dependent gap 제약은 실제 wall-clock 을 반영하지만 objective
    # 에 makespan 항이 없으면 "납기 여유 많고 설비 여유 많은" 경우 동일 objective
    # 의 grouped/scattered 해가 공존 → solver 가 non-deterministic 으로 scattered
    # 선택 가능 (예: 4 배치 {흑,흑,청,청} 모두 tardiness=0 feasible → 흑청흑청도 유효).
    # 작은 가중치로 시스 그룹의 end_var 합을 목적함수에 더해 "빨리 끝내는 해" 를
    # 선호시키면 자연스럽게 grouped 선택 (gap 적은 해 = makespan 작은 해).
    # weight=1 → tardiness(1e5~1e7 per min) 대비 5 orders 작아 실제 tradeoff 훼손 X,
    # objective tie 상황에서만 작동.
    _sheath_end_terms = [end_vars[_g] for _g in _sheath_gks_all]
    if _sheath_end_terms:
        _objective = _objective + sum(_sheath_end_terms)

    # 6-g-slack. On-time 그룹 slack-weighted completion — 납기 임박 우선 정렬.
    #
    # Why: 기존 on-time 그룹은 `e ≤ due_wmin` hard constraint 만 걸리고 objective
    # 항이 없어 슬랙 차이가 무시됨 → "납기 여유 있는 그룹이 납기 임박 그룹보다
    # 앞에 배치" 현상 (KBI PoC 관찰: 300SQ 납기 4/30 이 150SQ 납기 4/17 보다
    # 먼저 같은 설비에 배치). EDD pair tie-breaker (weight=1/pair) 만으론 idle /
    # chain / transition 항에 밀려 역전 발생 가능.
    #
    # 구현: on-time 그룹 각각에 `w × end` 항 추가. w 는 슬랙에 반비례 → 임박한
    # 그룹일수록 end 를 작게 하려는 힘 강함. 연선/절연/시스 등 모든 후속 공정에
    # 일괄 적용 (요구사항: "연선 뿐만 아니라 절연 등 후속공정에도 모두").
    #
    # 필터:
    #   - no-due (`due_wmin == _MAX_HORIZON_MIN`): 납기 없는 그룹은 skip.
    #   - past-due (`due_wmin < 0`): 기존 `_TARDINESS_WEIGHT × tardiness_vars` 가
    #     담당 — 중복 부과 방지.
    #
    # 가중치 스케일:
    #   `_IDLE_WEIGHT=1` < slack_w (수~수백/min) < `_TARDINESS_WEIGHT=1e5/min`.
    #   past-due penalty 를 이기지 못해 안전, idle_terms 와 경쟁 가능.
    _slack_terms: list = []
    # 진단 스냅샷용 — (gk, weight, end_var) 로 기여도를 사후 분해할 수 있도록 보존.
    _slack_terms_meta: list = []
    for _gk, _meta in group_meta.items():
        _due = _meta["due_wmin"]
        if _due == _MAX_HORIZON_MIN:
            continue
        if _due < 0:
            continue
        _slack_min = max(1, int(_due))
        _w = max(1, _SLACK_WEIGHT_BASE // _slack_min)
        _slack_terms.append(_w * end_vars[_gk])
        _slack_terms_meta.append((_gk, _w, end_vars[_gk]))
    if _slack_terms:
        _objective = _objective + sum(_slack_terms)

    # 6-h. EDD 전역 penalty — "같은 공정 + 공유 설비 후보" 인 그룹 쌍에서
    # 납기 빠른 쪽이 뒤에 시작하면 `_EDD_PAIR_WEIGHT` penalty.
    #
    # Why: past-due tardiness 는 overdue 그룹만 앞으로 끌고, slack penalty 는
    # on-time 그룹 간 duration 차이가 크면 WSPT(짧은 작업 먼저) 이익에 밀림.
    # 관찰 사례: 150SQ 33000m 납기 4/17 이 300SQ 9500m 납기 4/30 보다 뒤에
    # 배치. 두 그룹 모두 on-time 이라 tardiness 0, slack w_150≈4 / w_300≈2 의
    # 2× 차이보다 WSPT duration 차이(51h/16h = 3×) 가 더 강해 역전 발생.
    # → EDD 위반 penalty 를 충분히 크게 해 duration 이익을 압도하게 만듦.
    # 대상 축소 (폭주 방지):
    #   1) 같은 공정 — 다른 공정끼리는 precedence 가 이미 순서 결정
    #   2) 공통 eligible 설비 존재 — 같은 설비에 놓일 가능성 있어야 순서가 의미
    #   3) due_wmin 이 _MAX_HORIZON_MIN (no-due) 또는 동일한 쌍은 skip
    _edd_pair_terms: list = []
    # Hybrid C: past-due ↔ on-time 혼합 쌍 별도 리스트 (heavy weight 적용).
    _edd_mixed_pastdue_terms: list = []
    _process_gks: dict[str, list[str]] = {}
    for _gk, _meta in group_meta.items():
        _process_gks.setdefault(_meta["rep"].process_name, []).append(_gk)
    for _proc, _gks_proc in _process_gks.items():
        for _i in range(len(_gks_proc)):
            for _j in range(_i + 1, len(_gks_proc)):
                _gk_a = _gks_proc[_i]
                _gk_b = _gks_proc[_j]
                _due_a = group_meta[_gk_a]["due_wmin"]
                _due_b = group_meta[_gk_b]["due_wmin"]
                # no-due 또는 동일 due 는 EDD 의미 없음
                if _due_a == _MAX_HORIZON_MIN or _due_b == _MAX_HORIZON_MIN:
                    continue
                if _due_a == _due_b:
                    continue
                # 공통 eligible 설비 없으면 경쟁 관계 아님 → skip
                if not (set(equip_vars[_gk_a].keys()) & set(equip_vars[_gk_b].keys())):
                    continue
                # 납기 빠른 쪽(earlier)이 뒤에 시작하면 wrong = 1
                if _due_a < _due_b:
                    _earlier, _later = _gk_a, _gk_b
                else:
                    _earlier, _later = _gk_b, _gk_a
                _wrong = model.new_bool_var(f"edd_wrong_{_earlier}__{_later}")
                model.add(start_vars[_earlier] > start_vars[_later]).only_enforce_if(
                    _wrong
                )
                model.add(start_vars[_earlier] <= start_vars[_later]).only_enforce_if(
                    _wrong.Not()
                )
                # 쌍 분류: "past-due ↔ on-time" 혼합이면 heavy weight.
                # earlier 는 납기 빠른 쪽 (= due_wmin 작은 쪽). past-due 는 음수, on-time
                # 은 양수. `earlier 가 past-due 이고 later 가 on-time` 이면 혼합 case.
                _earlier_due = group_meta[_earlier]["due_wmin"]
                _later_due = group_meta[_later]["due_wmin"]
                _is_mixed_pastdue = _earlier_due < 0 <= _later_due
                if _is_mixed_pastdue:
                    _edd_mixed_pastdue_terms.append(_wrong)
                else:
                    _edd_pair_terms.append(_wrong)
    if _edd_pair_terms:
        _objective = _objective + _EDD_PAIR_WEIGHT * sum(_edd_pair_terms)
    if _edd_mixed_pastdue_terms:
        _objective = _objective + _EDD_MIXED_PASTDUE_WEIGHT * sum(
            _edd_mixed_pastdue_terms
        )

    # Round 2 HIGH #6: 연선 setup 3-tier soft penalty.
    # 같은 설비에 배치된 두 연선 그룹의 SQ 가 다르면 `_TRANSITION_WEIGHT` 분 비용
    # 부과 → solver 가 "같은 SQ 들을 한 설비에 모으는" 배치를 선호. adjacency
    # 단위 sequence-dependent setup 의 정확 모델링은 아니지만 (circuit constraint
    # 필요) 현재 group=single-interval 구조에서 실용적 proxy.
    #
    # 적용 범위: rep.process_name == "연선" (CORE 포함). 납기 ordinal 이 14일 이상
    # 벌어진 쌍은 skip (멀리 있으면 sequence 영향 희미). 납기 ordinal 없으면 skip.
    #
    # 각 (gk_a, gk_b) 쌍에 대해:
    #   transition_bool = AND(equip_a == equip_b, sq_a != sq_b)
    # 두 그룹이 같은 설비에 배치 AND SQ 다름 시 1, 아니면 0.
    # 경량화: SQ 같으면 penalty 0 이므로 쌍 skip. SQ 다를 때만 bool var 생성.
    transition_terms: list = []
    _stranding_gks = [
        _g for _g, _m in group_meta.items() if _m["rep"].process_name == "연선"
    ]
    for _i in range(len(_stranding_gks)):
        for _j in range(_i + 1, len(_stranding_gks)):
            _gk_a = _stranding_gks[_i]
            _gk_b = _stranding_gks[_j]
            _meta_a = group_meta[_gk_a]
            _meta_b = group_meta[_gk_b]
            # 동일 SQ → setup 0 or 30 — penalty 의미 없음 (현재 모델에서는 동급)
            if _meta_a["sq"] == _meta_b["sq"]:
                continue
            # 공통 eligible 설비 없으면 same_equip 불가 → skip
            _shared = set(equip_vars[_gk_a].keys()) & set(equip_vars[_gk_b].keys())
            if not _shared:
                continue
            # 납기 14일 초과 벌어지면 sequence 영향 미미 → skip
            _due_a = _meta_a.get("due_date_ord")
            _due_b = _meta_b.get("due_date_ord")
            if _due_a is not None and _due_b is not None and abs(_due_b - _due_a) > 14:
                continue
            # same_equip bool: 공통 설비 _s 에 대해 eq_a[s] AND eq_b[s] 가 한 번이라도
            # 참이면 1. 각 공통 설비별 AND 변수 만들고 OR 로 합산.
            _same_eq_bools: list = []
            for _ec in _shared:
                _both = model.new_bool_var(f"both_{_gk_a}_{_gk_b}_{_ec}")
                # _both = eq_a[ec] AND eq_b[ec]
                model.add_bool_and(
                    [equip_vars[_gk_a][_ec], equip_vars[_gk_b][_ec]]
                ).only_enforce_if(_both)
                model.add_bool_or(
                    [equip_vars[_gk_a][_ec].Not(), equip_vars[_gk_b][_ec].Not()]
                ).only_enforce_if(_both.Not())
                _same_eq_bools.append(_both)
            # 설비 exactly_one 이므로 _same_eq_bools 중 최대 1개만 참 → sum = OR
            _trans = model.new_bool_var(f"trans_{_gk_a}_{_gk_b}")
            model.add(_trans == sum(_same_eq_bools))
            transition_terms.append(_trans)

    if transition_terms:
        _objective = _objective + _TRANSITION_WEIGHT * sum(transition_terms)

    model.minimize(_objective)

    # ── 7. 솔버 실행 ──────────────────────────────────────────────────────
    solver = cp_model.CpSolver()
    # time_limit_sec override — 증분 경로는 10s, 전역 재최적화는 60s 등 호출자
    # 시나리오에 따라 조정. None/<=0 이면 기본값 유지 (하위호환).
    _time_limit = (
        int(time_limit_sec)
        if (time_limit_sec and int(time_limit_sec) > 0)
        else _SOLVER_TIME_LIMIT_SEC
    )
    solver.parameters.max_time_in_seconds = _time_limit
    # 워커 수: 환경변수 기반 해상도. 운영 기본 8, CI/테스트는 1로 강제 (결정론).
    # Task 1.1 (Rev 3): `num_search_workers` kwarg 가 주어지면 env 해상도를
    # override — parity harness 가 워커 수를 1 로 고정해 비결정성을 제거.
    # 유효값은 >= 1 로 clamp, None/<=0 이면 기존 `_resolve_num_workers()` 사용.
    if num_search_workers is not None and int(num_search_workers) > 0:
        _num_workers = max(1, int(num_search_workers))
    else:
        _num_workers = _resolve_num_workers()
    solver.parameters.num_search_workers = _num_workers
    solver.parameters.log_search_progress = False
    # 재시도 시 다른 탐색 경로를 시도하도록 seed 변동 (Fix P0-4B)
    solver.parameters.random_seed = int(random_seed)

    # ── Phase 1 개선: 수렴 가속 파라미터 ──────────────────────────────────
    # 왜 이 세 파라미터를 추가하는가:
    #   (1) linearization_level=2 — 정수 스케줄링 문제에서 LP 이완 정확도를
    #       상승시켜 분기한정(branch-and-bound) 가지치기 효율을 높임. 최적성은
    #       유지되고 수렴만 빨라진다 (OR-Tools 기본 1 → 2).
    #   (2) cp_model_probing_level=2 — constraint propagation 을 강하게 돌려
    #       INFEASIBLE 을 조기에 탐지. Level 2/3 완화 모드 전환을 앞당겨
    #       재시도 누적 시간을 단축.
    #   (3) relative_gap_limit — Level 1 (hard 납기 + hard 색상) 에서만 적용.
    #       이 모드는 FEASIBLE 이면 납기/색상 제약이 100% 만족되므로, 소프트
    #       목적함수(idle + chain_diff) 를 2% 이내로 근사해도 운영상 동등.
    #       완화 모드(Level 2/3) 에서는 품질이 중요하므로 gap 미적용.
    solver.parameters.linearization_level = 2
    solver.parameters.cp_model_probing_level = 2
    if tardiness_hard and sheath_color_hard:
        solver.parameters.relative_gap_limit = 0.02

    # 계측: solve() wall-time. 성능 개선 판정의 baseline 데이터 소스.
    # result 에 직접 기록 → 상위 호출자가 Prometheus/로그로 집계 가능.
    _solve_t0 = time.perf_counter()
    status = solver.solve(model)
    _solve_wall_s = time.perf_counter() - _solve_t0
    status_name = solver.status_name(status)
    result["solver_status"] = status_name
    result["solver_wall_time_s"] = round(_solve_wall_s, 3)
    result["solver_n_groups"] = len(groups)
    result["solver_num_workers"] = _num_workers

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        result["warnings"].append(
            f"CP-SAT 솔버 실패 ({status_name}) — 그리디 폴백으로 전환합니다"
        )
        return result

    result["objective_value"] = int(solver.objective_value)

    # ── 진단 스냅샷: solver 입력(group_meta) + 출력(start/end/equip/tardiness) +
    # 주요 objective 항의 실제 기여값을 JSON 한 벌로 저장. 원인 분석 시
    # "어떤 그룹이 왜 그 위치에 갔는가" 를 사후에 재현할 수 있도록.
    _write_solver_snapshot(
        run_label=run_label,
        base_date=base_date,
        group_meta=group_meta,
        frozen_group_keys=frozen_group_keys,
        solver=solver,
        solver_status_name=status_name,
        start_vars=start_vars,
        end_vars=end_vars,
        equip_vars=equip_vars,
        tardiness_vars=tardiness_vars,
        edd_pair_vars=_edd_pair_terms,
        slack_terms_meta=_slack_terms_meta,
        idle_terms=idle_terms,
        transition_terms=transition_terms,
        sheath_end_terms=_sheath_end_terms,
        edd_mixed_pastdue_vars=_edd_mixed_pastdue_terms,
    )

    # ── 8. CP-SAT 순서대로 캘린더 그리디로 실제 배치 ─────────────────────
    #
    # CP-SAT는 "어떤 순서로, 어떤 설비에" 처리할지만 결정한다.
    # 실제 시작/종료 시각은 항상 캘린더 인식 엔진(_find_available_slot +
    # calculate_end_datetime)으로 계산하므로 겹침이 발생하지 않는다.
    #
    # 처리 순서: CP-SAT start_vars 값 오름차순
    #   → 납기 빠른 그룹이 앞에 오도록 솔버가 결정한 순서
    # 처리 순서: 공정 선후관계 → 납기일 오름차순(EDD) → 고객 우선순위
    # ── 소선경 클러스터별 최초 납기 계산 (ST- 연선 그룹 연속 배치용) ─────────
    # schedule_optimizer의 wire_d_earliest와 동일한 로직
    wire_d_earliest: dict[float, date] = {}
    for gk in groups:
        if gk.startswith("ST-"):
            wd = sq_to_wire_d.get(_st_sq(gk), 0.0)
            if wd > 0:
                ed = group_meta[gk]["earliest_due"]
                if ed and (wd not in wire_d_earliest or ed < wire_d_earliest[wd]):
                    wire_d_earliest[wd] = ed

    # 처리 순서 결정:
    #   CORE/AL-CORE: 공정순 최우선 (ST- 선행)
    #   ST- 연선: 공정순 → 소선경 클러스터 최초납기 → 소선경값 → 그룹 EDD
    #     (같은 소선경 그룹을 연속 배치 → 선재교체 비용 최소화)
    #   시스(저압/고압): 공정순 → 주 버킷(H1/H2) → 색상 → 실제 EDD
    #     Why: 시스는 **납기 최우선, 그 다음 색상 우선**. 납기 주차 bucket
    #     안에서만 색상을 묶어 교체 비용 최소화. 주차가 다르면 납기 순서 유지.
    #     (색상을 1차로 두면 회 W15 그룹이 갈 W18 뒤로 밀려 11일+ 지연 발생)
    #     solver 가 tardiness 최소화로 주차 윈도우를 보장하므로, post-solve
    #     ordering 은 (주차, 색상) 로 안전하게 정렬 가능.
    #     schedule_optimizer._group_sort_key 의 시스 분기와 동일 규칙.
    #   그 외 공정(절연 등): 공정순 → EDD → 고객 우선순위
    #       절연(proc=2)이 시스(proc=4)보다 항상 먼저 스케줄링 → 파이프라인 데이터 등록 보장
    #
    # 모든 분기가 동일 길이 7-tuple 을 반환한다 (color_rank / due_wk 필드에
    # 비시스 분기는 중립값을 채움). solver.value(start_vars) 는 색상 키가
    # 이미 클러스터링을 확정하므로 제거.
    _COLOR_RANK_PAD = 99  # 비시스: 색상 키 무효
    _DUE_WK_PAD = 999999  # 비시스: 주 버킷 무효

    # ── 시스 색상 묶음 기반 정렬 ─────────────────────────────────────────────
    # 1차 키: 묶음의 pred_ready_wmin (선행공정 first-drum 완료 시각, working-min).
    #        solver start_vars 값으로 선행공정 그룹의 대략적 완료 시점을 계산해
    #        "설비가 빨리 사용 가능한 묶음" 부터 처리 → 설비 idle 최소화.
    # 2차 키: latest_due (같은 pred_ready 이면 납기 순)
    # 3차 키: color_rank (같은 납기면 색상 체인 유도)
    from app.services.sheath_cluster import (
        build_sheath_clusters,
        cluster_sort_key,
    )

    # 각 그룹의 pred_ready_wmin 계산 (솔버 결과 기반 근사)
    for _gk, _meta in group_meta.items():
        _rep = _meta["rep"]
        _pred_proc = PREDECESSOR_PROCESS.get(_rep.process_name)
        _meta["pred_ready_wmin"] = 0
        if not _pred_proc:
            continue
        _pred_sq = _meta["sq"]
        _pred_candidates = [
            g
            for g, m in group_meta.items()
            if m["rep"].process_name == _pred_proc and m["sq"] == _pred_sq
        ]
        if not _pred_candidates:
            continue
        _best: int | None = None
        for _pg in _pred_candidates:
            _pg_start = solver.value(start_vars[_pg])
            _pg_dur = group_meta[_pg]["cpsat_dur"]
            _pg_drums = max(int(group_meta[_pg]["rep"].drum_count or 1), 1)
            _pg_first_drum = _pg_start + max(1, _pg_dur // _pg_drums)
            if _best is None or _pg_first_drum < _best:
                _best = _pg_first_drum
        _meta["pred_ready_wmin"] = _best or 0

    _sheath_clusters = build_sheath_clusters(group_meta)
    _sorted_clusters = sorted(
        _sheath_clusters, key=lambda c: cluster_sort_key(c, group_meta)
    )
    _cluster_rank: dict[str, tuple[int, int]] = {}
    for ci, _cluster in enumerate(_sorted_clusters):
        for gi, _gk in enumerate(_cluster.group_keys):
            _cluster_rank[_gk] = (ci, gi)

    def _solved_order_key(gk: str):
        meta = group_meta[gk]
        rep = meta["rep"]
        proc_level = PROCESS_ORDER.get(rep.process_name, 50)
        earliest = meta["earliest_due"] or date.max
        cust_prio = rep.customer_priority or 99

        # CORE/AL-CORE: ST- 선행 공정이므로 반드시 먼저 실행 (date.min으로 최우선)
        if _is_core_group(gk):
            return (
                proc_level,
                date.min,  # ST- 그룹보다 항상 앞에 오도록
                -1.0,
                _COLOR_RANK_PAD,
                _DUE_WK_PAD,
                earliest,
                cust_prio,
            )
        # ST- 연선: 소선경 클러스터 연속 배치
        if gk.startswith("ST-") and rep.process_name == "연선":
            wd = sq_to_wire_d.get(_st_sq(gk), 0.0)
            cluster_due = wire_d_earliest.get(wd, date.max)
            return (
                proc_level,
                cluster_due,  # 소선경 클러스터 최초 납기
                wd,  # 소선경값 (같은 클러스터 내 연속 배치)
                _COLOR_RANK_PAD,
                _DUE_WK_PAD,
                earliest,
                cust_prio,
            )
        # 시스: CP-SAT 의 start_var 를 primary 로 사용 (solver 의 tardiness/idle
        # 최소화 결정을 존중) → 빈 설비 idle 을 achievable future 클러스터로 채우게.
        # Why: cluster_rank (latest_due primary) 는 overdue 클러스터를 앞에 두어
        # achievable 클러스터의 idle-gap 활용 기회를 놓친다. 예: W17H1 흑 25 의
        # pred 가 4/9 ready 지만 SH-A120 의 4/7~4/10 gap 대신 W16H2 흑 뒤(4/21)
        # 에 배치되어 납기 초과. solver 는 chain_terms 로 color chain 도 선호하므로
        # start_var 순서는 (a) 공정 선후, (b) tardiness 최소, (c) color chain 을
        # 모두 반영한 결정이다.
        # Tiebreak: cluster_rank 로 같은 시각에 여러 그룹이 올 때 색상 체인 유지.
        if _is_sheath_group(gk, meta["batches"]):
            rank = _cluster_rank.get(gk, (10**9, 10**9))
            start_val = int(solver.value(start_vars[gk]))
            return (
                proc_level,
                date.max,
                0.0,
                start_val,
                rank[0],
                rank[1],
                earliest,
                cust_prio,
            )
        # 절연 등 기타 공정: EDD 순
        return (
            proc_level,
            earliest,
            0.0,
            _COLOR_RANK_PAD,
            _DUE_WK_PAD,
            earliest,
            cust_prio,
        )

    solved_order = sorted(groups, key=_solved_order_key)

    # CP-SAT가 선택한 설비
    cpsat_eq: dict[str, str] = {
        gk: next(ec for ec, bv in equip_vars[gk].items() if solver.value(bv) == 1)
        for gk in groups
    }

    # 상태 추적 딕셔너리
    predecessor_map: dict[tuple, int] = {}
    tasks_created: list[ScheduleTask] = []
    last_batch_on_equip: dict[str, ProductionBatch] = {}
    sq_to_equip: dict[tuple[str, int], str] = {}
    process_end_by_sq: dict[tuple[str, int], datetime] = {}
    process_first_output_by_sq: dict[tuple[str, int], datetime] = {}
    core_first_drum_by_main_sq: dict[int, datetime] = {}

    # ── 기존 scheduled 태스크를 timeline에 pre-load ───────────────────────
    # 긴급수주 추가 후 재스케줄링 시 이미 확정된 블록과의 겹침을 방지한다.
    timeline: dict[str, list] = {}
    existing_tasks = (
        db.query(ScheduleTask)
        .filter(
            ScheduleTask.run_label == run_label,
            ScheduleTask.equipment_code.isnot(None),
            ScheduleTask.start_datetime.isnot(None),
            ScheduleTask.end_datetime.isnot(None),
        )
        .all()
    )
    for et in existing_tasks:
        timeline.setdefault(et.equipment_code, []).append(
            (et.start_datetime, et.end_datetime)
        )
    first_insul_output: datetime | None = None
    preempted_remainder: list[ProductionBatch] = []  # 선점 분할된 잔여 배치

    # 시스 묶음 기반 append 정책용 — group_key → cluster_id / 설비별 직전 cluster
    _gk_to_cluster_id: dict[str, str] = {}
    for _c in _sorted_clusters:
        for _gk_iter in _c.group_keys:
            _gk_to_cluster_id[_gk_iter] = _c.cluster_id
    _prev_cluster_on_eq: dict[str, str] = {}

    for gk in solved_order:
        meta = group_meta[gk]
        rep = meta["rep"]
        gb = meta["batches"]
        chosen_eq_code = cpsat_eq[gk]
        sq_int = meta["sq"]
        sq_key = (rep.process_name, sq_int)

        # 이 그룹이 멀티설비 분배 대상인지 판단
        # (sq_to_equip은 이미 배치된 SQ→설비 매핑을 반영하므로 순서 의존적으로 정확함)
        is_multi, total_drums = _is_multi_equip_group(
            gk, gb, meta["eligible"], sq_to_equip
        )

        if is_multi:
            # ── 멀티설비: _schedule_multi_equipment에 위임 ────────────────
            header_batch = next((b for b in gb if b.batch_seq == -1), None)
            split_ok = _schedule_multi_equipment(
                group_key=gk,
                group_batches=gb,
                eligible=meta["eligible"],
                total_drums=total_drums,
                header_batch=header_batch,
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
                result["total_tasks"] += 1
                continue
            # split 실패 시 단일설비로 폴백 (아래 로직 계속)

        # ── 단일설비 배치 ─────────────────────────────────────────────────
        eligible = meta["eligible"]

        # 연선 셋업 3-tier / 그 외 동일SQ 스킵
        actual_setup = meta["setup_min"]
        prev_batch = last_batch_on_equip.get(chosen_eq_code)
        if prev_batch and rep.process_name == "연선":
            compound_min = float(
                speed_map.get((chosen_eq_code, float(rep.sq_mm2 or 0)), None)
                and speed_map[
                    (chosen_eq_code, float(rep.sq_mm2 or 0))
                ].setup_compound_min
                or 0
            )
            actual_setup = _get_stranding_setup_min(
                float(prev_batch.sq_mm2) if prev_batch.sq_mm2 else None,
                float(rep.sq_mm2) if rep.sq_mm2 else None,
                sq_to_wire_d,
                spec_min=meta["setup_min"],
                compound_min=compound_min,
            )
        elif prev_batch and prev_batch.sq_mm2 and rep.sq_mm2:
            if float(prev_batch.sq_mm2) == float(rep.sq_mm2):
                actual_setup = 0.0

        # 색상 교체 — 메모리 dict lookup (사전에 color_setup_map 으로 일괄 로드).
        # 기존에는 매번 `db.query(SpeedMaster).filter(...).first()` 라 배치 수 × 색상
        # 교체마다 원격 Supabase 왕복이 발생. 대형 런(수백 배치, 수십 색상 교체)
        # 에서 수 초~수십 초의 누적 지연 원인이었다.
        color_change_min = 0.0
        if prev_batch and rep.process_name in ("저압시스", "고압시스", "HFCO시스"):
            pc = (prev_batch.sheath_color or "").strip()
            cc = (rep.sheath_color or "").strip()
            if pc and cc and pc != cc:
                sm_c_val = color_setup_map.get(chosen_eq_code)
                color_change_min = resolve_color_change_min(
                    sm_color_min=sm_c_val,
                    params=constraint_params,
                )

        total_dur = (
            meta["work_dur"] + actual_setup + meta["drum_wind"] + color_change_min
        )

        # 공정 선후관계 earliest 계산
        earliest = base_date
        pred_proc = PREDECESSOR_PROCESS.get(rep.process_name)
        if pred_proc:
            all_sqs = {int(b.sq_mm2 or 0) for b in gb}
            if len(all_sqs) > 1:
                valid = [
                    t
                    for sq_i in all_sqs
                    if (t := process_first_output_by_sq.get((pred_proc, sq_i)))
                    and t < datetime.max
                ]
                if valid:
                    earliest = max(earliest, min(valid))
            else:
                pf = process_first_output_by_sq.get((pred_proc, next(iter(all_sqs))))
                if pf and pf > earliest:
                    earliest = pf
            if rep.process_name == "고압시스":
                earliest += timedelta(hours=20)

        if gk.startswith(("A100_", "A120_")):
            if first_insul_output and first_insul_output > earliest:
                earliest = first_insul_output

        if gk.startswith("ST-") and rep.process_name == "연선":
            try:
                main_sq = int(gk.split("-")[1])
            except (IndexError, ValueError):
                main_sq = sq_int
            cf = core_first_drum_by_main_sq.get(main_sq)
            if cf and cf > earliest:
                earliest = cf

        _is_st = gk.startswith("ST-") and rep.process_name == "연선"
        _skip_ind = (
            rep.process_name
            in ("저압절연", "고압절연", "저압시스", "고압시스", "연합", "T/P")
            or _is_st
        )
        if not _skip_ind:
            for b in gb:
                pt = predecessor_map.get((b.sales_order_id, b.sales_order_line))
                if pt:
                    ptask = next((t for t in tasks_created if t.task_id == pt), None)
                    if ptask and ptask.end_datetime > earliest:
                        earliest = ptask.end_datetime

        # ── 시스 묶음 기반 append 정책 ──────────────────────────────────────────
        # 묶음 내부(같은 cluster_id): 무조건 append → 색상 체인 유지
        # 묶음 경계(다른 cluster_id): 조건부 append → 납기 초과 예상이면 earliest
        #   그대로 두고 _find_available_slot 이 빈 공간 사용 (납기 보호)
        # 색상 교체 비용(수십 분)은 납기 위반(일 단위) 대비 작으므로 경계에서는
        # 납기 우선. 묶음 내부는 교체 없음이 자명하므로 무조건 append.
        if rep.process_name in ("저압시스", "고압시스"):
            current_cluster_id = _gk_to_cluster_id.get(gk)
            eq_slots = timeline.get(chosen_eq_code, [])
            prev_cluster = _prev_cluster_on_eq.get(chosen_eq_code)
            if eq_slots and current_cluster_id:
                last_end = max(slot[1] for slot in eq_slots)
                append_earliest = max(earliest, last_end)
                if prev_cluster == current_cluster_id:
                    earliest = append_earliest
                else:
                    append_end = calculate_end_datetime(
                        append_earliest, total_dur, db, chosen_eq_code
                    )
                    due = meta["earliest_due"]
                    if not due or append_end.date() <= due:
                        earliest = append_earliest

        # ── 시스 weekend-aware: 긴 batch 가 주말에 걸치면 다음 업무일 시작 ─────
        # 실측 버그: 금요일 20:00 시작 갈 400SQ 가 주말 60h idle 후 월요일 11:00
        # 종료 → 뒤따르는 8 개 묶음 모두 +1~+4 일 late. 긴 batch(>4h)가 금요일
        # 오후 늦게 시작해 주말에 걸치는 것으로 예측되면 earliest 를 다음 월요일
        # 08:00 으로 밀어 주말 걸침 회피. 설비 idle 약간 증가하나 뒤따르는 묶음
        # 전체 지연 회피로 순이득.
        if rep.process_name in ("저압시스", "고압시스"):
            # 임시 end 계산: 현재 earliest 에 append 시 언제 끝나는가
            _tentative_end = calculate_end_datetime(
                earliest, total_dur, db, chosen_eq_code
            )
            _cross_weekend = False
            _d = earliest.date()
            while _d < _tentative_end.date():
                if _d.weekday() >= 5:  # 토/일
                    _cross_weekend = True
                    break
                _d += timedelta(days=1)
            if _cross_weekend and total_dur > 240:
                # 긴 batch 가 주말 걸침 → 다음 월요일 08:00 으로 이동
                _e = earliest
                while _e.weekday() >= 5:
                    _e = _e.replace(
                        hour=8, minute=0, second=0, microsecond=0
                    ) + timedelta(days=1)
                if _e.weekday() == 4 and _e.hour >= 17:  # 금 17시 이후면 월요일로
                    _e = _e.replace(
                        hour=8, minute=0, second=0, microsecond=0
                    ) + timedelta(days=(7 - _e.weekday()) % 7 or 3)
                # Due 초과 우려시만 적용 (납기 지킬 수 있는 그룹만 회피)
                _new_end = calculate_end_datetime(_e, total_dur, db, chosen_eq_code)
                due = meta["earliest_due"]
                if not due or _new_end.date() <= due:
                    earliest = _e

        # ── 긴급/중요 배치: 납기 위반 예상 시 선점 분할 시도 ────────────────────
        if (
            _priority_label(rep.customer_priority) in ("urgent", "critical")
            and meta["earliest_due"]
        ):
            slots_sim = timeline.get(chosen_eq_code, [])
            sim_start = _find_available_slot(
                earliest, total_dur, slots_sim, db, chosen_eq_code
            )
            sim_end = calculate_end_datetime(sim_start, total_dur, db, chosen_eq_code)
            if sim_end.date() > meta["earliest_due"] and sim_start > earliest:
                # 납기 초과 + earliest보다 늦게 시작 → 선점 가능 여부 시도
                rem_list = _try_preempt_for_urgent(
                    earliest=earliest,
                    chosen_eq_code=chosen_eq_code,
                    run_label=run_label,
                    timeline=timeline,
                    db=db,
                    urgent_priority=int(rep.customer_priority or 7),
                    predecessor_map=predecessor_map,
                )
                if rem_list:
                    preempted_remainder.extend(rem_list)
                    result["warnings"].append(
                        f"선점분할: 배치그룹 {gk} 납기 {meta['earliest_due']} 맞추기 위해 "
                        f"{rem_list[0].batch_group} 잔여 {rem_list[0].drum_count}드럼 후처리 예약"
                    )

        # 캘린더 인식 슬롯 탐색 — 겹침 완전 방지
        slots = timeline.get(chosen_eq_code, [])
        best_start = _find_available_slot(
            earliest, total_dur, slots, db, chosen_eq_code
        )
        end_dt = calculate_end_datetime(best_start, total_dur, db, chosen_eq_code)

        # ── 파이프라인 유휴 최소 역산 — start 지연 방식 ──────────────────────
        # T_succ_end = T_pred_end + 후공정 1드럼 소요, 블록 폭(total_dur)은 고정.
        # lot_count: 헤더 있으면 그 drum_count, 없으면 gb 합산.
        _header_p = next((b for b in gb if b.batch_seq == -1), None)
        if _header_p is not None:
            _lot_count_p = max(int(_header_p.drum_count or 1), 1)
        else:
            _lot_count_p = max(sum(int(b.drum_count or 1) for b in gb), 1)
        _per_drum_p = meta["work_dur"] / _lot_count_p if _lot_count_p > 0 else 0.0
        best_start, end_dt = align_start_to_predecessor_end(
            process_name=rep.process_name,
            pred_proc=pred_proc,
            group_sqs={int(b.sq_mm2 or 0) for b in gb},
            process_end_by_sq=process_end_by_sq,
            current_start=best_start,
            current_end=end_dt,
            duration_min=total_dur,
            tail_offset_min=_per_drum_p,
            slots=slots,
            db=db,
            equipment_code=chosen_eq_code,
        )

        # 정각 올림
        if end_dt.minute > 0 or end_dt.second > 0:
            end_dt = end_dt.replace(minute=0, second=0, microsecond=0) + timedelta(
                hours=1
            )

        # 체인 하이라이트 — 대표 order 의 상류 task id 를 predecessor 로 기록
        rep_pred_task_id = predecessor_map.get(
            (rep.sales_order_id, rep.sales_order_line)
        )

        task = ScheduleTask(
            batch_id=rep.batch_id,
            equipment_code=chosen_eq_code,
            start_datetime=best_start,
            end_datetime=end_dt,
            setup_time_min=actual_setup,
            status="scheduled",
            run_label=run_label,
            batch_group=gk,
            predecessor_task_id=rep_pred_task_id,
        )
        db.add(task)
        db.flush()

        # timeline 갱신 (이후 그룹이 이 슬롯을 피할 수 있도록)
        timeline.setdefault(chosen_eq_code, []).append((best_start, end_dt))

        # 파이프라인 첫 드럼 출력 시각 갱신
        header_batch = next((b for b in gb if b.batch_seq == -1), None)
        if header_batch:
            lot_count = max(int(header_batch.drum_count or 1), 1)
        else:
            # CORE 그룹 포함, 헤더 없는 그룹 모두 drum_count 합산 (len(gb) 아님)
            lot_count = max(sum(int(b.drum_count or 1) for b in gb), 1)
        first_drum_min = actual_setup + (meta["work_dur"] / lot_count)
        first_output_dt = calculate_end_datetime(
            best_start, first_drum_min, db, chosen_eq_code
        )

        proc_sq_key = (rep.process_name, sq_int)
        if not _is_core_group(gk):
            if (
                proc_sq_key not in process_first_output_by_sq
                or first_output_dt < process_first_output_by_sq[proc_sq_key]
            ):
                process_first_output_by_sq[proc_sq_key] = first_output_dt
        else:
            msq = _extract_core_main_sq(gk)
            if msq and (
                msq not in core_first_drum_by_main_sq
                or first_output_dt < core_first_drum_by_main_sq[msq]
            ):
                core_first_drum_by_main_sq[msq] = first_output_dt

        if rep.process_name == "저압절연":
            if first_insul_output is None or first_output_dt < first_insul_output:
                first_insul_output = first_output_dt

        if (
            proc_sq_key not in process_end_by_sq
            or end_dt > process_end_by_sq[proc_sq_key]
        ):
            process_end_by_sq[proc_sq_key] = end_dt

        for b in gb:
            predecessor_map[(b.sales_order_id, b.sales_order_line)] = task.task_id
            b.equipment_code = chosen_eq_code
            b.status = "scheduled"

        if rep.process_name == "연선" and not _is_core_group(gk):
            sq_to_equip[sq_key] = chosen_eq_code

        last_batch_on_equip[chosen_eq_code] = gb[-1]
        tasks_created.append(task)

        # 시스 묶음 기반 append 정책 — 현재 그룹의 cluster_id 로 갱신
        if rep.process_name in ("저압시스", "고압시스"):
            _cid = _gk_to_cluster_id.get(gk)
            if _cid:
                _prev_cluster_on_eq[chosen_eq_code] = _cid

        # 납기 위반 기록 — hard constraint 위반이므로 error 격상
        if meta["earliest_due"] and end_dt.date() > meta["earliest_due"]:
            late_days = (end_dt.date() - meta["earliest_due"]).days
            result["violations"].append(
                {
                    "batch_id": rep.batch_id,
                    "task_id": task.task_id,
                    "type": "delivery",
                    "severity": "error",
                    "detail": f"납기 {meta['earliest_due']} 초과 → 완료 {end_dt.date()} (+{late_days}일)",
                }
            )

        log_decision(
            db=db,
            run_label=run_label,
            stage="stage2",
            action_type="auto_assign",
            batch_id=rep.batch_id,
            task_id=task.task_id,
            reason=f"CP-SAT 순서 → {chosen_eq_code} @ {best_start:%Y-%m-%d %H:%M}",
        )
        result["total_tasks"] += 1

    # ── 9. 선점 잔여 배치 후속 배치 ───────────────────────────────────────────
    # 선점 분할로 생성된 잔여 배치들을 같은 설비에서 순서대로 스케줄링한다.
    # (이미 긴급 배치 슬롯이 timeline에 등록되어 있으므로 겹치지 않는다.)
    for rem_b in preempted_remainder:
        eq_code = rem_b.equipment_code
        if not eq_code:
            continue
        # 밀어낸 단드럼 배치는 setup_time을 유지; 분할 잔여는 setup=0 (이미 설정됨)
        rem_setup = float(rem_b.setup_time_min or 0)
        work_dur = float(rem_b.estimated_duration_min or 0)
        total_rem_dur = work_dur + rem_setup
        slots_rem = timeline.get(eq_code, [])
        rem_start = _find_available_slot(
            base_date, total_rem_dur, slots_rem, db, eq_code
        )
        rem_end = calculate_end_datetime(rem_start, total_rem_dur, db, eq_code)
        if rem_end.minute > 0 or rem_end.second > 0:
            rem_end = rem_end.replace(minute=0, second=0, microsecond=0) + timedelta(
                hours=1
            )

        # 체인 하이라이트 — 잔여 배치도 같은 (order, line) 의 predecessor 계보 유지
        rem_pred_task_id = predecessor_map.get(
            (rem_b.sales_order_id, rem_b.sales_order_line)
        )

        rem_task = ScheduleTask(
            batch_id=rem_b.batch_id,
            equipment_code=eq_code,
            start_datetime=rem_start,
            end_datetime=rem_end,
            setup_time_min=rem_setup,
            status="scheduled",
            run_label=run_label,
            batch_group=rem_b.batch_group,
            predecessor_task_id=rem_pred_task_id,
        )
        db.add(rem_task)
        db.flush()

        timeline.setdefault(eq_code, []).append((rem_start, rem_end))
        rem_b.status = "scheduled"
        rem_b.equipment_code = eq_code
        result["total_tasks"] += 1

    return result
