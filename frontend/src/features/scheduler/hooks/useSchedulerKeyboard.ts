/**
 * useSchedulerKeyboard — scheduler 페이지 전역 키보드 단축키.
 *
 * 현재 책임 (Week 7 Task 7B.1 분리):
 *   - ESC: compareMode 가 활성일 때 closeCompareMode 실행. INPUT/TEXTAREA/contentEditable
 *     포커스 시에는 무시 — 모달 닫기 등 기존 키 핸들링과 충돌 방지.
 *
 * 향후: SchedulerView 자체의 ESC(chain-highlight 해제) 와 통합할 가능성 있음.
 * 단, 현재 두 핸들러는 라이프사이클(페이지 vs 뷰)이 달라 유지.
 */
"use client";

import { useEffect } from "react";

interface UseSchedulerKeyboardOptions {
  /** compareMode 가 활성 상태인지. true 일 때만 ESC 핸들러가 등록됨. */
  compareModeEnabled: boolean;
  /** ESC 입력 시 호출 — 일반적으로 store.closeCompareMode 를 전달. */
  onEscapeCompareMode: () => void;
}

export function useSchedulerKeyboard({
  compareModeEnabled,
  onEscapeCompareMode,
}: UseSchedulerKeyboardOptions): void {
  useEffect(() => {
    if (!compareModeEnabled) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      const target = e.target as HTMLElement | null;
      if (
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.isContentEditable)
      ) {
        return;
      }
      onEscapeCompareMode();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [compareModeEnabled, onEscapeCompareMode]);
}
