/**
 * severity → PwC samildevkit 디자인 토큰 매핑.
 *
 * 백엔드 phrasing.severity_for(kind, value) 결과 ('ok'|'save'|'warn'|'fail')
 * 를 Tailwind class string 으로 변환. 운영자가 50건 카드 봐도 같은 색 = 같은
 * 종류 신호로 학습되는 일관성 보장.
 *
 * 메모리 `feedback_ui_quality_no_ai_slop` — pwc-design 토큰만, raw hex 금지.
 */

import type { Severity } from "./decisionCardTypes";

export const SEVERITY_BG: Record<Severity, string> = {
  ok: "bg-pwc-status-default-bg",
  save: "bg-pwc-status-success-bg",
  warn: "bg-pwc-status-warning-bg",
  fail: "bg-pwc-status-danger-bg",
};

export const SEVERITY_TEXT: Record<Severity, string> = {
  ok: "text-pwc-status-default-text",
  save: "text-pwc-status-success-text",
  warn: "text-pwc-status-warning-text",
  fail: "text-pwc-status-danger-text",
};

export const SEVERITY_BORDER: Record<Severity, string> = {
  ok: "border-pwc-gray-200",
  save: "border-pwc-status-success-text",
  warn: "border-pwc-status-warning-text",
  fail: "border-pwc-status-danger-text",
};

/** 운영자 시각 mark — verdict_summary prefix '✓' / '⚠' / '·' 와 일치. */
export const SEVERITY_MARK: Record<Severity, string> = {
  ok: "·",
  save: "✓",
  warn: "⚠",
  fail: "⚠",
};
