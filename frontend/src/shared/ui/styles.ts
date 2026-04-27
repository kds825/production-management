/** 프로젝트 공통 UI 스타일 프리셋 — 외부 디자인 시스템 패키지 import 없이
 *  내용을 프로젝트 내부에 직접 구현한다. 모든 색상은 var(--color-*)를 경유.
 */

export const btnPrimary =
  "px-3 py-1.5 text-xs font-medium rounded transition-opacity " +
  "text-[color:var(--color-text-inverse)] " +
  "bg-[color:var(--color-brand-primary)] hover:opacity-90 " +
  "disabled:opacity-50 disabled:cursor-not-allowed";

export const btnSecondary =
  "px-3 py-1.5 text-xs font-medium rounded transition-colors " +
  "text-[color:var(--color-text-primary)] " +
  "bg-[color:var(--color-bg-muted)] hover:bg-[color:var(--color-border-default)] " +
  "disabled:opacity-50";

export const btnGhost =
  "px-3 py-1.5 text-xs font-medium rounded transition-colors " +
  "text-[color:var(--color-text-primary)] " +
  "hover:bg-[color:var(--color-bg-muted)]";

export const chipMuted =
  "inline-block text-tiny font-medium px-1.5 py-0.5 rounded " +
  "text-[color:var(--color-text-secondary)] " +
  "bg-[color:var(--color-bg-muted)]";

export const chipReason =
  "inline-block text-mini font-medium px-1.5 py-0.5 rounded " +
  "text-[color:var(--color-warning)] " +
  "bg-[color:var(--color-bg-muted)] " +
  "border border-[color:var(--color-warning)]";

export const menuItem =
  "w-full text-left px-3 py-2 text-xs flex items-center gap-2 transition-colors " +
  "text-[color:var(--color-text-primary)] " +
  "hover:bg-[color:var(--color-bg-muted)] " +
  "disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:bg-transparent";
