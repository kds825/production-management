"""SheathPhrasingProvider — 저압시스 (A120/A100) / 고압시스 / HFCO시스.

❸ 섹션 = 전·후 공정 hand-off (재공 박스 X — memory feedback_tfrg_color
원칙). TFR-GV 변형은 prev label = "연선 (절연 스킵 · TFR-GV)", 단선 접지선
(SQ ≤25) 변형은 prev label = "신선 (연선·절연 스킵)" 으로 치환.

severity 임계는 plan §B.6 표:
- color_change: 0=save / 1~179=warn / 180+=fail
- spec_change : 0=save / 1+=fail (kind fixed danger)
- predecessor_gap: <30=save / 30~60=warn / 60+=fail
- due_slack_days: ≥+1.0=save / -0.5~+0.5=warn / <-0.5=fail
"""

from __future__ import annotations

from app.application.decisions.phrasing import (
    DecisionLineSeverity,
    _format_days,
    _format_kst,
    _format_minutes,
    _korean_eunneun,
)


class SheathPhrasingProvider:
    """시스 공정 (저압/고압/HFCO 모든 변형) 자연어."""

    process_key = "sheath"

    # ── ❶ adequacy 자연어 템플릿 ───────────────────────────────────────
    _TEMPLATES: dict[str, str] = {
        # 5-1: SQ 적합성
        "5-1": "{equipment}{eunneun} {min}~{max}SQ 시스 작업이 가능합니다 → {sq}SQ 적합",
        # 10-3: 시스재질 적합성 (PVC/CV/HFFR)
        "10-3": "{equipment}{eunneun} {allowed} 시스재질을 다룹니다 → 본 작업 {current} 적합",
        # 3-3: 색상묶음 적합성 (A120 / A100)
        "3-3": "{equipment}{eunneun} {eq_category} 묶음 설비입니다 → {color} 통과",
        # 4-2: 색상교체 시간
        "4-2_save": "직전 묶음이 동일 {color}이라 색상교체 시간이 들지 않습니다",
        "4-2_warn": "직전 묶음이 {prev_color}이라 색상교체 시간 {minutes}분이 발생합니다",
    }

    # ── ❷ impact 자연어 템플릿 ─────────────────────────────────────────
    _IMPACT_TEMPLATES: dict[str, str] = {
        "color_change_save": "🎨 색상교체 0분 (절약)",
        "color_change_warn": "🎨 색상교체 {minutes}분",
        "spec_change_save": "📏 규격교체 0분 (절약)",
        "spec_change_fail": "📏 규격교체 {duration}",
        "due_slack_save": "📅 납기 여유 {days}",
        "due_slack_warn": "📅 납기 여유 {days} (D-day 임박)",
        "due_slack_fail": "📅 납기 {days} (지연)",
        "predecessor_gap_save": "⏱️ 전공정과 갭 {minutes}분",
        "predecessor_gap_warn": "⏱️ 전공정과 갭 {minutes}분 (idle)",
        "predecessor_gap_fail": "⏱️ 전공정과 갭 {duration} (idle)",
    }

    # ── severity 임계 (plan §B.6 표 — class attr; ConstraintConfig override 후속) ─
    _SEVERITY_THRESHOLDS: dict[str, dict[str, float]] = {
        "color_change": {"save": 0, "warn": 179, "fail": 180},
        # spec_change 는 kind fixed danger (값 0이면 save, 그 외 fail)
        "spec_change": {"save": 0, "warn": 0, "fail": 1},
        "predecessor_gap": {"save": 29, "warn": 60, "fail": 61},
        # due_slack_days: 큰 값일수록 좋음 — 부등호 방향 반대
        "due_slack_days": {"save": 1.0, "warn": -0.5, "fail": -10.0},
    }

    # ── ❶ adequacy_line ────────────────────────────────────────────────
    def adequacy_line(self, *, anchor: str, params: dict) -> str:
        try:
            tpl = self._TEMPLATES[anchor]
            ctx = {
                **params,
                "eunneun": _korean_eunneun(str(params.get("equipment", ""))),
            }
            return tpl.format(**ctx)
        except KeyError as e:
            # anchor key 누락 또는 params 키 누락 → 안전 폴백
            return f"[결정 근거 누락: anchor={anchor}, missing={e!s}]"

    # ── ❷ impact_line ──────────────────────────────────────────────────
    def impact_line(self, *, kind: str, params: dict) -> str:
        # severity 결정 → tpl key 합성
        value = float(params.get("value", 0))
        sev = self.severity_for(kind=kind, value=value)
        # due_slack_days 는 normalized days, 그 외는 value 그대로
        if kind == "due_slack_days":
            tpl_key = f"due_slack_{sev}"
            ctx = {"days": _format_days(value)}
        elif kind == "color_change":
            tpl_key = f"color_change_{sev}"
            ctx = {"minutes": int(value)}
        elif kind == "spec_change":
            tpl_key = f"spec_change_{sev}"
            ctx = {"duration": _format_minutes(value)}
        elif kind == "predecessor_gap":
            tpl_key = f"predecessor_gap_{sev}"
            ctx = (
                {"minutes": int(value)}
                if sev != "fail"
                else {"duration": _format_minutes(value)}
            )
        else:
            return f"[unknown impact kind: {kind}]"

        tpl = self._IMPACT_TEMPLATES.get(tpl_key)
        if tpl is None:
            return f"[impact tpl missing: {tpl_key}]"
        try:
            return tpl.format(**ctx)
        except KeyError as e:
            return f"[impact 누락: kind={kind}, missing={e!s}]"

    # ── ❸ handoff_line — TFR-GV / 단선 접지선 변형 분기 ────────────────
    def handoff_line(self, *, params: dict) -> str:
        """params: predecessor (string), is_tfrgv (bool), is_bare_ground (bool)."""
        is_tfrgv = bool(params.get("is_tfrgv"))
        is_bare = bool(params.get("is_bare_ground"))
        if is_bare:
            return "전공정: 신선 (연선·절연 스킵 — 단선 접지선 SQ≤25)"
        if is_tfrgv:
            return "전공정: 연선 (절연 스킵 · TFR-GV)"
        return f"전공정: {params.get('predecessor', '절연')}"

    # ── ❹ gantt 정렬 라벨 (memory feedback_sheath_sort_order 3차) ───────
    def gantt_sort_label(self) -> str:
        return "① 납기 가까운 순 → ② 전공정 ready 시각 → ③ 색상 인접 순"

    # ── ❻ filter_out 사유 ──────────────────────────────────────────────
    _FILTER_OUT_TEMPLATES: dict[str, str] = {
        "5-1": "{equipment} {min}~{max}SQ 작업범위 외 ({sq}SQ)",
        "10-3": "{equipment} {allowed} 시스재질 전용 ({current} 거부)",
        "3-3": "{equipment} {eq_category} 묶음 ({color} 미포함)",
        "spec_change": "직전이 {prev_sq}SQ → 규격교체 {duration} 필요",
        "calendar_gap": "캘린더 미가용 시간대 ({slot})",
        "tardy_risk": "납기 지연 risk — {detail}",
    }

    def filter_out_reason(self, *, code: str, params: dict) -> str:
        tpl = self._FILTER_OUT_TEMPLATES.get(code)
        if tpl is None:
            return f"({code}) {params.get('detail', '미상')}"
        try:
            return tpl.format(**params)
        except KeyError as e:
            return f"[filter_out 누락: code={code}, missing={e!s}]"

    # ── severity_for ───────────────────────────────────────────────────
    def severity_for(self, *, kind: str, value: float) -> DecisionLineSeverity:
        thr = self._SEVERITY_THRESHOLDS.get(kind)
        if not thr:
            return "ok"
        # due_slack_days 는 큰 값이 좋음 — 반전
        if kind == "due_slack_days":
            if value >= thr["save"]:
                return "save"
            if value >= thr["warn"]:
                return "warn"
            return "fail"
        # 일반: 값이 작을수록 좋음
        if kind == "spec_change":
            return "save" if value == 0 else "fail"
        if value <= thr["save"]:
            return "save"
        if value <= thr["warn"]:
            return "warn"
        return "fail"

    # ── verdict_summary — 카드 펼치지 않고도 의사결정 가능한 1줄 ───────
    def verdict_summary(self, *, batch, audit, solver, schedule_task) -> str:
        # 핵심 신호 3개를 1줄로 요약 (CEO R2)
        # params 가 없을 때 안전한 stub — 실제 build_card.py 가 채워 호출
        color_min = int(getattr(audit, "color_change_min", 0) or 0)
        spec_min = int(getattr(audit, "spec_change_min", 0) or 0)
        slack = float(getattr(audit, "due_slack_days", 0) or 0)
        # 가장 위험한 신호 1개를 leading icon 으로
        if spec_min > 0 or slack < -0.5:
            icon = "⚠"
        elif color_min >= 180:
            icon = "⚠"
        elif color_min == 0 and spec_min == 0 and slack >= 1.0:
            icon = "✓"
        else:
            icon = "·"
        # 시작 시각이 있으면 prefix 로
        prefix = ""
        if schedule_task is not None and getattr(schedule_task, "start_at", None):
            prefix = f"{_format_kst(schedule_task.start_at)} · "
        return (
            f"{icon} {prefix}색상교체 {color_min}분 · 규격교체 {spec_min}분 · "
            f"납기 {_format_days(slack)}"
        )
