"""InsulationPhrasingProvider — 저압절연 / 고압절연.

❸ 섹션 = WipMatchBlock (절연재고 코어 활용). 4심 계산법 (constraint 10-5)
은 본 카드 ❶ 자연어에 노출 (`batch_helpers.py:213-215` core_count >= 4 시
SpeedMaster 4C 키 적용 / `cp_sat/constraints/product/__init__.py:7
four_core_calc`).
"""

from __future__ import annotations

from app.application.decisions.phrasing import (
    DecisionLineSeverity,
    _korean_eunneun,
)


class InsulationPhrasingProvider:
    process_key = "insulation"

    _TEMPLATES: dict[str, str] = {
        "5-1": "{equipment}{eunneun} {min}~{max}SQ 절연 작업이 가능합니다 → {sq}SQ 적합",
        "3-1": "{equipment}{eunneun} {color_group} 색상그룹 설비입니다 → {color} 통과",
        "10-2": "{equipment}{eunneun} {allowed_compound} 컴파운드 재고 보유 → {current} 적합",
        "10-5": "4심 작업 — SpeedMaster 4C 키 적용 (선속 {line_speed} m/min)",
        "2-1_match": "절연 코어 재공 매칭 → 절연 작업 절감",
        "2-1_no_match": "매칭 가능한 절연 코어 재고 없음",
    }

    _IMPACT_TEMPLATES: dict[str, str] = {
        "wip_loss_save": "♻️ 코어 재공 손실률 {pct:.1f}% (양호)",
        "wip_loss_warn": "♻️ 코어 재공 손실률 {pct:.1f}%",
        "wip_loss_fail": "♻️ 코어 재공 손실률 {pct:.1f}% (높음)",
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
            return "절연 코어 재공 매칭 없음"
        return f"절연 코어 재공 SM-{sm_id} 활용"

    def gantt_sort_label(self) -> str:
        return "① 색상그룹 → ② 납기 → ③ 컴파운드 종류"

    _FILTER_OUT_TEMPLATES: dict[str, str] = {
        "5-1": "{equipment} {min}~{max}SQ 외 ({sq}SQ)",
        "3-1": "{equipment} 색상그룹 {allowed} 외 ({color} 거부)",
        "10-2": "{equipment} {allowed_compound} 컴파운드 재고 부족",
        "shortage": "코어 재공 길이 부족 ({length_m}m)",
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
        cores = int(getattr(batch, "core_count", 1) or 1)
        loss = float(getattr(audit, "wip_loss_pct", 0) or 0)
        if cores >= 4:
            return f"✓ 4심 (SpeedMaster 4C 키) · 코어 재공 손실 {loss:.1f}%"
        return f"✓ 절연 — 코어 재공 손실 {loss:.1f}%"
