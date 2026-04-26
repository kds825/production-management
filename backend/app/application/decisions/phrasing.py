"""decision_card 의 deterministic 자연어 phrasing — Protocol + register + helpers.

Phase 6 (decision_card) Step 3a. 운영자(공장 생산관리담당자) 가 보는 카드의
모든 자연어는 이 모듈에서 합성된다. 핵심 원칙:

1. **Deterministic** — 같은 입력엔 같은 문구. (B) 안정화 기간엔 "어제 봤던
   거랑 다른 설명이네?" 가 운영자 신뢰를 깬다. **LLM 미사용**.

2. **공정별 분기** — 압축연선이 시스 카드에 등장하는 도메인 오류 차단.
   `_resolve_key(batch)` 가 process_name + outsource 룰로 dispatch.

3. **명시적 register** — FastAPI lifespan startup 안에서 register, 모듈
   import time 등록 X (테스트 부팅 부작용 차단).

추상화 도입은 사용자 §5 한정 면죄부에 따른 것 — 다른 영역은 메모리
`feedback_constraint_arch_simplicity` / `feedback_architecture_simplicity`
정신대로 단순 함수/dataclass 유지.
"""

from __future__ import annotations

import re
from typing import Literal, Protocol, TYPE_CHECKING, runtime_checkable

if TYPE_CHECKING:
    from app.infrastructure.models.production_batch import ProductionBatch


# ── Severity ──────────────────────────────────────────────────────────────
DecisionLineSeverity = Literal["ok", "save", "warn", "fail"]
"""severity 별도 모듈 신설 X — Engineer review 권고 (-25 LOC + 모듈 1개 절감).

값 의미:
  - "ok"    : 정보성 (펼침 default 접힘)
  - "save"  : 절감 — 셋업 0분, 납기 여유 등 (초록 액센트)
  - "warn"  : 주의 — 색상교체 분, 갭 발생 등 (주황 액센트)
  - "fail"  : 위험 — 규격교체, D-day, 손실 ≥10% 등 (빨강 액센트)
"""


# ── Protocol ──────────────────────────────────────────────────────────────
@runtime_checkable
class ProcessPhrasingProvider(Protocol):
    """공정별 자연어 템플릿 + severity 결정 + verdict_summary 1줄.

    Provider 인스턴스 1개당 한 process_key (sheath / stranding / insulation /
    outsource / default) 를 담당한다. 모든 메서드는 keyword-only argument 로
    호출되어 caller 가 시그니처 변경에 방어적이게 한다.
    """

    process_key: str

    def adequacy_line(self, *, anchor: str, params: dict) -> str:
        """❶ 왜 여기에 들어갔나 — 자연어 줄 1개. anchor 는 constraint id (#5-1 등)."""
        ...

    def impact_line(self, *, kind: str, params: dict) -> str:
        """❷ 결과 셀 — 자연어 줄 1개. kind: color_change / spec_change / due_slack 등."""
        ...

    def handoff_line(self, *, params: dict) -> str:
        """❸ 전·후 공정 hand-off (시스/외주) 또는 재공 활용 (연선/절연)."""
        ...

    def gantt_sort_label(self) -> str:
        """❹ '이 설비 묶음 정렬 기준' 라벨 1줄."""
        ...

    def filter_out_reason(self, *, code: str, params: dict) -> str:
        """❻ 다른 설비/시간 탈락 사유 — 자연어 줄 1개."""
        ...

    def severity_for(self, *, kind: str, value: float) -> DecisionLineSeverity:
        """kind × value 임계로 severity 결정. plan §B.6 표 참조."""
        ...

    def verdict_summary(self, *, batch, audit, solver, schedule_task) -> str:
        """카드 펼치지 않고도 의사결정 가능한 1줄 (CEO R2 — VerdictSummary).

        S4: schedule_task 인자 명시 — 시작/종료 시각 source-of-truth 는
        ScheduleTask.start_at/end_at (solver_decision은 per-constraint trace 일 뿐).
        """
        ...


# ── Registry ──────────────────────────────────────────────────────────────
_REGISTRY: dict[str, ProcessPhrasingProvider] = {}


def register_phrasing_provider(provider: ProcessPhrasingProvider) -> None:
    """FastAPI lifespan startup event 안에서만 호출 — import time 등록 X."""
    _REGISTRY[provider.process_key] = provider


def reset_registry() -> None:
    """테스트용 — 매 fixture 마다 깨끗한 상태에서 시작."""
    _REGISTRY.clear()


# ── Process key resolution ────────────────────────────────────────────────
_PROCESS_KEY_MAP: dict[str, str] = {
    "저압시스": "sheath",
    "고압시스": "sheath",
    "HFCO시스": "sheath",
    "연선": "stranding",
    "저압절연": "insulation",
    "고압절연": "insulation",
    # 신선/T/P/연합/중심선/단선 접지선은 default
}


def _resolve_key(batch: "ProductionBatch") -> str:
    """batch 의 phrasing key 결정. 외주 룰이 process_name 매핑보다 우선.

    Engineer review blocker 정정: process_name 단독으로 외주 판별 불가.
    `is_outsource_batch` 가 SQ ≤10 / TFR-8 16 / 아이마켓 TFR-GV 룰을 본다.
    """
    # 순환 import 방지 — 함수 내부 import
    from app.application.ingest.batch_grouper import is_outsource_batch

    if is_outsource_batch(batch):
        return "outsource"
    return _PROCESS_KEY_MAP.get(batch.process_name or "", "default")


def get_phrasing_provider(batch: "ProductionBatch") -> ProcessPhrasingProvider:
    """batch 에 매칭되는 Provider 반환. 미등록 / unknown process 는 default 폴백."""
    key = _resolve_key(batch)
    provider = _REGISTRY.get(key) or _REGISTRY.get("default")
    if provider is None:
        raise RuntimeError(
            "No phrasing provider registered. "
            "FastAPI lifespan startup 에서 register_phrasing_provider 를 호출했는지 확인."
        )
    return provider


# ── Normalization helpers — deterministic 보장 ────────────────────────────
def _format_minutes(m: int | float) -> str:
    """120 → '120분', 510 → '8시간 30분', 60 → '1시간', 0 → '0분'."""
    minutes = int(round(m))
    if minutes < 60:
        return f"{minutes}분"
    h, mm = divmod(minutes, 60)
    if mm == 0:
        return f"{h}시간"
    return f"{h}시간 {mm}분"


def _format_days(d: float) -> str:
    """+1.2일 / -0.5일 / +0.0일 — 부호 + 소수점 1자리 강제."""
    return f"{d:+.1f}일"


def _format_kst(dt) -> str:
    """KST 강제, '04-29(수) 14:00'. timezone-naive datetime 거부.

    `dt` 는 `datetime.datetime`. naive 이면 ValueError — Supabase TIMESTAMPTZ
    원칙상 모든 시간은 tz-aware 여야 한다.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    if not isinstance(dt, datetime):
        raise TypeError(f"_format_kst expects datetime, got {type(dt).__name__}")
    if dt.tzinfo is None:
        raise ValueError(
            "_format_kst received timezone-naive datetime; "
            "all decision_card payload datetimes must be tz-aware (KST)."
        )

    kst = dt.astimezone(ZoneInfo("Asia/Seoul"))
    weekday_kr = "월화수목금토일"[kst.weekday()]
    return f"{kst:%m-%d}({weekday_kr}) {kst:%H:%M}"


# ── 한국어 조사 — 받침 종성 분기 ─────────────────────────────────────────
_HANGUL_BASE = 0xAC00
_FINAL_CONSONANTS = 28  # 종성 28개


_ENGLISH_LETTER_HAS_JONGSEONG: dict[str, bool] = {
    # 한국어 발음 기준 — 받침이 있는 알파벳만 True.
    # F=에프(ㅍ), L=엘(ㄹ), M=엠(ㅁ), N=엔(ㄴ), R=알(ㄹ),
    # S=에스(ㅅ), X=엑스(ㅅ), Z=제트(ㅌ).
    # 그 외 (A=에이, B=비, V=브이, ...) 는 모음 끝 → 받침 없음.
    "f": True,
    "l": True,
    "m": True,
    "n": True,
    "r": True,
    "s": True,
    "x": True,
    "z": True,
}

# 0=영(받침), 1=일(ㄹ), 3=삼(ㅁ), 6=육(ㄱ), 7=칠(ㄹ), 8=팔(ㄹ).
# 2=이, 4=사, 5=오, 9=구 → 받침 없음.
_DIGIT_HAS_JONGSEONG: frozenset[str] = frozenset("013678")


def _has_jongseong(ch: str) -> bool:
    """한 글자가 받침을 가지는지. 한글 외 문자(영문/숫자) 는 한국어 발음 매핑."""
    if not ch:
        return False
    last = ch[-1]
    code = ord(last)
    if 0xAC00 <= code <= 0xD7A3:
        return ((code - _HANGUL_BASE) % _FINAL_CONSONANTS) != 0
    if last.isalpha():
        return _ENGLISH_LETTER_HAS_JONGSEONG.get(last.lower(), False)
    if last.isdigit():
        return last in _DIGIT_HAS_JONGSEONG
    return False


def _korean_eunneun(noun: str) -> str:
    """noun 뒤에 붙는 조사 '은/는' 결정.

    예: '시스 3호기' → '는', '외주 H공장' → '은', 'A100' → '은' (음:콘),
        'TFR-GV' → '는' (음:브이).
    """
    # 뒤에서 한글/영문/숫자만 추출 (괄호/특수문자 제거)
    m = re.search(r"[가-힣A-Za-z0-9]+$", noun.strip())
    if not m:
        return "는"  # 안전 폴백
    last_token = m.group()
    return "은" if _has_jongseong(last_token) else "는"


def _korean_iga(noun: str) -> str:
    """noun 뒤에 붙는 조사 '이/가' 결정. 받침 있으면 '이', 없으면 '가'."""
    m = re.search(r"[가-힣A-Za-z0-9]+$", noun.strip())
    if not m:
        return "가"
    return "이" if _has_jongseong(m.group()) else "가"
