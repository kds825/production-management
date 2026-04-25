"""도메인 — 시스 그룹 키 + 색상/공정 상수 (단일 소스).

본 모듈은 직전 `services/batch_grouping/{sheath,constants}.py` 두 파일의
합본이다. 합치는 이유:

- `_compose_sheath_group_key` 는 색상·납기·SQ 만 사용하는 pure 함수이며
  `_A120_COLORS` 상수에만 의존한다 → 두 파일을 한 도메인 모듈로 묶어
  의존 그래프 단순화.
- `_SHEATH_COLOR_RANK`, `_WIP_COVERED_PROCESSES`, `_TFR8_PATTERN` 도
  변경 빈도 낮은 도메인 룰 — 같은 위치에 모은다.
- 호출자 (cp_sat / greedy / batch_grouping / routes) 는 모두 lazy
  import 였다 (audit B 의 lazy-cycle smell). 도메인 layer 로 격상하면
  순환 위험이 사라져 lazy 가 필요 없다.

이전 위치 호환성: `services/batch_grouping/__init__.py` 가 본 모듈을
재export 하여 기존 dotted path 일부를 보존한다 (Phase 1 step 5 까지).
"""

from __future__ import annotations

import re
from datetime import date

# WIP 재고 종류별로 해당 재고가 "이미 완료된" 공정 집합
# 절연재고: 연선+절연까지 완료 → 절연 이전 공정 배치 생성 불필요
# 연선재고: 연선까지 완료 → 연선 이전 공정만 제외, 절연부터는 작업 필요
# 연합재고: 연선+절연+연합까지 완료
# 완제품:  모든 공정 완료
_WIP_COVERED_PROCESSES: dict[str, set[str]] = {
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

# 시스 색상 정렬 우선순위 (A120: 흑·청·흑/적 / A100: 갈·회·녹/황 등)
# 현장에서 자주 쓰이는 색상일수록 앞에 배치 → 긴 체인 형성 확률 ↑
_SHEATH_COLOR_RANK: dict[str, int] = {
    "흑": 1,
    "갈": 2,
    "회": 3,
    "청": 4,
    "녹": 5,
    "녹/황": 5,
    "백": 6,
    "적": 7,
    "흑/적": 8,
}

# 고내화(TFR-8(...)) 감지 패턴 — 괄호 앞 공백 허용, 대소문자 무관
# 의도: "TFR-8(830℃/120min)", "tfr-8 (...)" 등 변형도 동일 그룹으로 묶도록
# 모듈 레벨에서 한 번만 컴파일 → 배치별 호출 시 재컴파일 비용 0
_TFR8_PATTERN = re.compile(r"TFR-8\s*\(", re.IGNORECASE)

# A120 설비로 라우팅되는 시스 색상군 (현장 룰). 그 외는 A100.
_A120_COLORS = ("흑", "청", "흑/적")


def _compose_sheath_group_key(
    *,
    proc: str,
    color: str | None,
    due_date: date | None,
    sq: int | float | None,
) -> str:
    """시스 배치 그룹 키 생성 — 색상 + 반주차(H1/H2) bucket + SQ 접미.

    왜 이 조합인가:
    - **색상**: 같은 색상을 연속 생산해 교체 시간 최소화 (`_sheath_chain_key` 체인).
    - **반주차(H1/H2)**: 같은 주 내 월·목 납기 차이도 별도 그룹으로 분리 → EDD 보장.
    - **SQ 접미**: 회사 수기 양식처럼 같은 색상/주차 내에서도 규격(120/240 등)이
      다르면 별도 런으로 분할한다. 과거에는 다중 SQ가 하나의 그룹으로 합쳐져
      간트에 단일 블록으로 보였지만, 현장은 규격별 생산이 기본 단위다.
    - 슬래시(`/`)가 들어간 색상(예: 흑/적, 녹/황)은 underscore 로 치환해 키로 사용.

    색상 체인 연속성은 `_sheath_chain_key` 가 색상+납기만 보고 정렬하므로 SQ 분할
    후에도 stable sort 로 자연 보존된다 (같은 색상·같은 주차 그룹이 인접 배치됨).
    """
    color_str = (color or "").strip()
    color_key = color_str.replace("/", "_") if color_str else "기타"
    sq_key = int(sq or 0)
    if proc == "저압시스":
        if due_date:
            yr, wk, wday = due_date.isocalendar()
            half = "H1" if wday <= 3 else "H2"
            due_bucket = f"{yr}W{wk:02d}{half}"
        else:
            due_bucket = "9999W99X"
        eq_prefix = "A120" if color_str in _A120_COLORS else "A100"
        return f"{eq_prefix}_{color_key}_{due_bucket}_{sq_key}SQ"
    # 고압시스 — 색상 + SQ (실질 633SQ 단일이나 일관성 위해 접미 유지)
    return f"{proc}_{color_key}_{sq_key}SQ"
