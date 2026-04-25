"""batch_grouping 패키지 — 시스 그룹 키 합성.

설계 의도:
- 시스(저압/고압) 배치를 batch_group 으로 묶을 때 사용하는 키 함수 단독 분리.
- create_batches (grouper.py) 와 재집계 경로에서 동일 함수를 호출 → 키 정의가
  한 곳에 집중되어 정렬 정책 변경 시 회귀 위험 최소화.
- A120/A100 설비 라우팅 룰 변경은 본 모듈 + constants 만 수정하면 된다.
"""

from datetime import date

from app.services.batch_grouping.constants import _A120_COLORS


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
