"""도메인 전역 상수.

공정 순서, 공정 선후관계, 솔버 가중치 baseline 등 여러 모듈
(schedule_optimizer, cp_sat_optimizer, solver/*, equipment 라우트 등)에서
공유하는 값을 한 곳에 정의한다.

Week 9 까지는 cp_sat_optimizer / schedule_optimizer 모듈에서 동일 이름으로
re-export 되어 기존 import path 가 유지된다 (D7-C invariant).
"""

# 공정 순서: 숫자가 작을수록 상류 공정 (신선→연선→절연→연합/T·P→시스)
PROCESS_ORDER: dict[str, int] = {
    "신선": 0,
    "연선": 1,
    "저압절연": 2,
    "고압절연": 2,
    "연합": 3,
    "T/P": 3,
    "저압시스": 4,
    "고압시스": 4,
    "HFCO시스": 4,
}


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


# 하루 근무 시간(분): 08:00~22:00 (CP-SAT 시간축 legacy 단위).
# 실제 가용 분은 calendar_engine 기반 _working_minutes_between 이 계산하므로
# 이 상수는 폴백(legacy path) 과 horizon 계산의 근사치로만 사용된다.
_WORK_MIN_PER_DAY = 14 * 60  # 840분


# 납기 초과 가중치 — tardiness_hard=False 모드에서만 사용.
# tardiness_hard=True (기본) 에서는 model.add(e <= due_wmin) 로 직접 강제.
# _DUE_HARD_WEIGHT: 아이들(1)/체인(120)/선점 등 다른 목적함수 항들을 압도해
# 실질적 hard 로 동작시킨다 (soft 폴백 경로용).
_DUE_HARD_WEIGHT = 100000


# 색상 교체 cost 가중치 (분 단위). resolve_color_change_min 의 기본값(120min)과
# 일치시켜 chain_diff(boolean: 동색 0, 이색 1) 곱한 값이 실제 교체 시간과 동등
# scale 로 경쟁하게 함. 1 분 tardiness ≒ 1 분 idle ≒ 색상 1회 교체(120min).
# 기존 값(1)은 tardiness_weight(10만~1000만) 대비 사실상 무력했음 (P9-B 교정).
_CHAIN_WEIGHT = 120


# Round 2 HIGH #6: 연선 setup 3-tier (동일SQ 0 / 동일소선경 30 / 이소선경 210) 의
# 평균치. spec-level (다른 소선경) 전이만이 실제로 고비용이므로 avg(0, 30, 210) ≈ 80
# 대신 "다른 SQ 인접 시 피해야 할 비용" 의 대표값으로 180 min 사용 (spec 이 압도적).
# Solver 는 "같은 설비에서 인접 두 연선 그룹이 SQ 가 다르면 180 min penalty" 로
# 인식 → 같은 SQ 연속 처리를 선호. 이는 sequence-dependent setup 의 정확 모델링이
# 아닌 soft proxy 이지만, 현재 모델 구조 (group=single interval) 에서 실용적 절충안.
# 완전한 circuit-constraint 기반 모델링은 별도 phase.
_TRANSITION_WEIGHT = 180
