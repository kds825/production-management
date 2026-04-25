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

/**
 * Week 4 Task 4B.4 — 토스트 부가 메타데이터.
 *
 * 현재는 `runId` 만 사용 — 백엔드 X-Run-Id 헤더 (Week 2 Task 2A.4) 를 운영자가
 * 토스트에서 복사하기 위함. 향후 traceId / debugUrl 등 추가 시 동일 인터페이스 확장.
 *
 * 기존 호출처(action 만 사용) 와 호환되도록 toastStore 의 `show` 시그니처는
 * 4번째 인자(action) 위치를 보존하고, meta 는 5번째 옵셔널 인자로 추가한다.
 *
 * Week 5 Task 5B.2 — `kind: "reason-prompt"` 추가.
 *   드래그-드롭 직후 운영자에게 사유를 묻는 sticky 토스트 (60s) 의 트리거.
 *   `changeSetId` 는 칩 클릭 시 PATCH /api/change-sets/{id}/reason 의 path
 *   파라미터로 사용된다. `onResolved` 는 토스트가 "스킵" 또는 "성공" 으로
 *   닫힐 때마다 호출돼 헤더 배지를 즉시 갱신한다 (배지 폴링 대신 push).
 */
export interface ToastMeta {
  runId?: string | null;
  kind?: "reason-prompt";
  changeSetId?: string;
  onResolved?: () => void;
}

export interface ToastItem {
  id: string;
  message: string;
  variant: ToastVariant;
  durationMs: number;
  // optional — 기존 호출처는 영향 없음.
  action?: ToastAction;
  // Week 4 Task 4B.4 — 에러 토스트 코릴레이션 ID 등 부가 정보. 기존 호출처는 영향 없음.
  meta?: ToastMeta;
}

interface ToastState {
  toasts: ToastItem[];
  show: (
    msg: string,
    variant?: ToastVariant,
    durationMs?: number,
    action?: ToastAction,
    meta?: ToastMeta,
  ) => void;
  dismiss: (id: string) => void;
  // 테스트/전역 cleanup 용 헬퍼. 기존 호출처는 사용하지 않아도 무방.
  dismissAll: () => void;
}

export const useToastStore = create<ToastState>((set, get) => ({
  toasts: [],
  show: (message, variant = "info", durationMs = 4000, action, meta) => {
    const id = `toast-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
    set((s) => ({
      toasts: [...s.toasts, { id, message, variant, durationMs, action, meta }],
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
