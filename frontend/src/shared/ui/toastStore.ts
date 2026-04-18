import { create } from "zustand";

export type ToastVariant = "success" | "error" | "warning" | "info";

/**
 * 토스트 옵션 액션 — Undo 같은 long-duration 토스트에서 사용.
 *
 * 버튼 클릭 시:
 * 1) 전달된 `onClick` 실행 (예: revertChangeSet API 호출).
 * 2) 자동으로 해당 토스트 dismiss — 사용자가 명시적 닫기를 또 누를 필요 없게.
 */
export interface ToastAction {
  label: string;
  onClick: () => void;
}

export interface ToastItem {
  id: string;
  message: string;
  variant: ToastVariant;
  durationMs: number;
  // optional — 기존 호출처는 영향 없음.
  action?: ToastAction;
}

interface ToastState {
  toasts: ToastItem[];
  show: (
    msg: string,
    variant?: ToastVariant,
    durationMs?: number,
    action?: ToastAction,
  ) => void;
  dismiss: (id: string) => void;
  // 테스트/전역 cleanup 용 헬퍼. 기존 호출처는 사용하지 않아도 무방.
  dismissAll: () => void;
}

export const useToastStore = create<ToastState>((set, get) => ({
  toasts: [],
  show: (message, variant = "info", durationMs = 4000, action) => {
    const id = `toast-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
    set((s) => ({
      toasts: [...s.toasts, { id, message, variant, durationMs, action }],
    }));
    // duration 이 0 이하면 auto-dismiss 비활성 — 호출처에서 수동 dismiss 요구하는 패턴.
    if (durationMs > 0) {
      setTimeout(() => get().dismiss(id), durationMs);
    }
  },
  dismiss: (id) => {
    set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) }));
  },
  dismissAll: () => {
    set({ toasts: [] });
  },
}));
