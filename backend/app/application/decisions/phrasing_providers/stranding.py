"""StrandingPhrasingProvider — 연선 공정.

❸ 섹션 = WipMatchBlock (재공 활용 — SM 동선). 5-2 정규화 룰: '압축'과
'압축연선'은 같은 물리적 연선방식 → 통합. 헤더 chip 으로 5종
{압축/원형/수밀/7연선코어/61연선} 표기.
"""

from __future__ import annotations

from app.application.decisions.phrasing import (
    DecisionLineSeverity,
    _korean_eunneun,
)


class StrandingPhrasingProvider:
    process_key = "stranding"

    _TEMPLATES: dict[str, str] = {
        "5-1": "{equipment}{eunneun} {min}~{max}SQ 연선 작업이 가능합니다 → {sq}SQ 적합",
        "5-2": "{equipment}{eunneun} {stranding_type} 전용 설비입니다 → 본 작업 적합",
        "10-1": "{equipment}{eunneun} {allowed} 재질을 다룹니다 → {current} 적합",
        "10-4": "{equipment}{eunneun} {voltage}V 전압대를 다룹니다 → 본 작업 적합",
        "2-1_match": "재공 SM-{sm_id} ({remainder_pct:.1f}% 잔량) 매칭 → 신규 SM 투입 절감",
        "2-1_no_match": "매칭 가능한 재공 없음 → 신규 SM 투입",
    }

    _IMPACT_TEMPLATES: dict[str, str] = {
        "wip_loss_save": "♻️ 재공 손실률 {pct:.1f}% (양호)",
        "wip_loss_warn": "♻️ 재공 손실률 {pct:.1f}%",
        "wip_loss_fail": "♻️ 재공 손실률 {pct:.1f}% (높음)",
    }

    _SEVERITY_THRESHOLDS = {
        "wip_loss_pct": {"save": 5.0, "warn": 10.0, "fail": 100.0},
    }

    def adequacy_line(self, *, anchor: str, params: dict) -> str:
        try:
            tpl = self._TEMPLATES[anchor]
            ctx = {
                **params,
                "eunneun": _korean_eunneun(str(params.get("equipment", ""))),
            }
            return tpl.format(**ctx)
        except KeyError as e:
            return f"[결정 근거 누락: anchor={anchor}, missing={e!s}]"

    def impact_line(self, *, kind: str, params: dict) -> str:
        value = float(params.get("value", 0))
        sev = self.severity_for(kind=kind, value=value)
        if kind == "wip_loss_pct":
            tpl = self._IMPACT_TEMPLATES.get(f"wip_loss_{sev}")
            return (tpl or "[wip_loss tpl 누락]").format(pct=value)
        return f"[unknown impact kind: {kind}]"

    def handoff_line(self, *, params: dict) -> str:
        sm_id = params.get("matched_sm_id")
        if not sm_id:
            return "재공 매칭 없음 — 신규 SM 투입"
        return f"재공 SM-{sm_id} 활용 — 잔량 {params.get('remainder_pct', 0):.1f}%"

    def gantt_sort_label(self) -> str:
        return "① 연선방식 → ② 납기 → ③ SQ"

    _FILTER_OUT_TEMPLATES: dict[str, str] = {
        "5-1": "{equipment} {min}~{max}SQ 외 ({sq}SQ)",
        "5-2": "{equipment} 연선방식 {allowed} 전용 ({current} 거부)",
        "shortage": "재공 SM-{sm_id} 길이 부족 ({length_m}m)",
        "sq_mismatch": "재공 SQ {sq}SQ 불일치",
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
        if value < thr["save"]:
            return "save"
        if value < thr["warn"]:
            return "warn"
        return "fail"

    def verdict_summary(self, *, batch, audit, solver, schedule_task) -> str:
        loss = float(getattr(audit, "wip_loss_pct", 0) or 0)
        if loss == 0:
            return "✓ 재공 미사용 — 신규 SM"
        return f"♻️ 재공 매칭 (손실률 {loss:.1f}%)"
