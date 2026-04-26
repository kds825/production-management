"""DefaultPhrasingProvider — v1 미노출 공정 / unknown process_name 폴백.

신선 / T·P / 연합 / 중심선 등 v1 카드에 본격 노출하지 않는 공정에 대한
안전 폴백. EMPTY 카드 (DecisionCardEmpty 컴포넌트) 가 본 Provider 의
출력을 사용한다.

자연어는 매우 짧게 — "이 공정은 v1 결정 카드에서 다루지 않습니다" + raw
정보 1줄 + ERP 링크. 운영자 의견 받지 않음 (Step 6 대상에서 제외).
"""

from __future__ import annotations

from app.application.decisions.phrasing import DecisionLineSeverity


class DefaultPhrasingProvider:
    """v1 미노출 공정 / unknown process_name 안전 폴백."""

    process_key = "default"

    def adequacy_line(self, *, anchor: str, params: dict) -> str:
        return "이 공정은 v1 결정 카드에서 다루지 않습니다."

    def impact_line(self, *, kind: str, params: dict) -> str:
        return "ERP 화면에서 배치 상세를 확인해 주세요."

    def handoff_line(self, *, params: dict) -> str:
        return ""  # default 카드는 ❸ 섹션 없음

    def gantt_sort_label(self) -> str:
        return "ERP 적재 순서"

    def filter_out_reason(self, *, code: str, params: dict) -> str:
        return f"({code}) {params.get('detail', '미상')}"

    def severity_for(self, *, kind: str, value: float) -> DecisionLineSeverity:
        return "ok"

    def verdict_summary(self, *, batch, audit, solver, schedule_task) -> str:
        return "ⓘ v1 미노출 공정 — ERP 에서 확인"
