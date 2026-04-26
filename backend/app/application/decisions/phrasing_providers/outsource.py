"""OutsourcePhrasingProvider — 외주 분기 (2-2 룰).

❸ 섹션 = OutsourceHandoffBlock (발주 → 외주 작업 → 입고 → 후공정).
카드 외곽선 info-500 1px + 헤더 좌측 ribbon "📦 외주 (협력사명)" + ❹
Gantt 시간 축 일 단위 (memory feedback_ui_quality_no_ai_slop 정합).

외주 룰 (2-2):
  (1) SQ ≤ 10
  (2) TFR-8(... + SQ == 16
  (3) 아이마켓코리아 + TFR-GV
"""

from __future__ import annotations

from app.application.decisions.phrasing import (
    DecisionLineSeverity,
    _format_days,
    _korean_eunneun,
)


class OutsourcePhrasingProvider:
    process_key = "outsource"

    _TEMPLATES: dict[str, str] = {
        "2-2_sq_le_10": "SQ {sq} ≤ 10 → 사내 설비 작업 불가, 외주 자동분류",
        "2-2_tfr8_16": "TFR-8 16SQ 고온 사양 → 외주 전용 분류",
        "2-2_imarket_tfrgv": "아이마켓코리아 + TFR-GV → 고객 지정 외주",
        "vendor": "협력사 {vendor_name}{eunneun} {lead_days}일 lead time",
    }

    _IMPACT_TEMPLATES: dict[str, str] = {
        "lead_save": "📦 외주 lead {days}일 (단납)",
        "lead_warn": "📦 외주 lead {days}일",
        "lead_fail": "📦 외주 lead {days}일 (지연 risk)",
        "due_slack_save": "📅 납기 여유 {days}",
        "due_slack_warn": "📅 납기 여유 {days} (D-day 임박)",
        "due_slack_fail": "📅 납기 {days} (지연)",
    }

    _SEVERITY_THRESHOLDS = {
        "outsource_lead_days": {"save": 2, "warn": 5, "fail": 100},
        "due_slack_days": {"save": 1.0, "warn": -0.5, "fail": -10.0},
    }

    def adequacy_line(self, *, anchor: str, params: dict) -> str:
        try:
            tpl = self._TEMPLATES[anchor]
            ctx = {
                **params,
                "eunneun": _korean_eunneun(str(params.get("vendor_name", ""))),
            }
            return tpl.format(**ctx)
        except KeyError as e:
            return f"[결정 근거 누락: anchor={anchor}, missing={e!s}]"

    def impact_line(self, *, kind: str, params: dict) -> str:
        value = float(params.get("value", 0))
        sev = self.severity_for(kind=kind, value=value)
        if kind == "outsource_lead_days":
            tpl = self._IMPACT_TEMPLATES.get(f"lead_{sev}")
            return (tpl or "[lead tpl 누락]").format(days=int(value))
        if kind == "due_slack_days":
            tpl = self._IMPACT_TEMPLATES.get(f"due_slack_{sev}")
            return (tpl or "[due_slack tpl 누락]").format(days=_format_days(value))
        return f"[unknown impact kind: {kind}]"

    def handoff_line(self, *, params: dict) -> str:
        vendor = params.get("vendor_name", "외주 협력사")
        return (
            f"발주 → {vendor} 외주 작업 ({params.get('lead_days', '?')}일) → "
            f"입고 → 후공정"
        )

    def gantt_sort_label(self) -> str:
        return "① 외주 발주일 → ② 협력사별 capacity → ③ 입고일"

    _FILTER_OUT_TEMPLATES: dict[str, str] = {
        "vendor_capacity": "{vendor_name} capacity 부족 (현재 {current}/{capacity})",
        "lead_too_long": "lead time {days}일 > 납기 여유 {slack}",
    }

    def filter_out_reason(self, *, code: str, params: dict) -> str:
        tpl = self._FILTER_OUT_TEMPLATES.get(code)
        if tpl is None:
            return f"({code}) {params.get('detail', '미상')}"
        try:
            return tpl.format(**params)
        except KeyError as e:
            return f"[filter_out 누락: code={code}, missing={e!s}]"

    def severity_for(self, *, kind: str, value: float) -> DecisionLineSeverity:
        thr = self._SEVERITY_THRESHOLDS.get(kind)
        if not thr:
            return "ok"
        if kind == "due_slack_days":
            if value >= thr["save"]:
                return "save"
            if value >= thr["warn"]:
                return "warn"
            return "fail"
        if value <= thr["save"]:
            return "save"
        if value <= thr["warn"]:
            return "warn"
        return "fail"

    def verdict_summary(self, *, batch, audit, solver, schedule_task) -> str:
        lead = int(getattr(audit, "outsource_lead_days", 0) or 0)
        vendor = getattr(audit, "vendor_name", "외주")
        return f"📦 {vendor} 외주 — lead {lead}일"
