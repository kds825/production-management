"use client";
import {
  CheckCircleIcon,
  ExclamationTriangleIcon,
  InformationCircleIcon,
  XCircleIcon,
  XMarkIcon,
} from "@heroicons/react/24/outline";
import { useToastStore, type ToastVariant } from "./toastStore";

const iconMap: Record<
  ToastVariant,
  React.ComponentType<{ width: number; height: number }>
> = {
  success: CheckCircleIcon,
  error: XCircleIcon,
  warning: ExclamationTriangleIcon,
  info: InformationCircleIcon,
};

const variantClass: Record<ToastVariant, string> = {
  success:
    "border-[color:var(--color-success)] text-[color:var(--color-success)]",
  error: "border-[color:var(--color-danger)] text-[color:var(--color-danger)]",
  warning:
    "border-[color:var(--color-warning)] text-[color:var(--color-warning)]",
  info: "border-[color:var(--color-border-default)] text-[color:var(--color-text-primary)]",
};

export function ToastContainer() {
  const toasts = useToastStore((s) => s.toasts);
  const dismiss = useToastStore((s) => s.dismiss);

  if (toasts.length === 0) return null;

  return (
    <div
      role="region"
      aria-label="알림"
      aria-live="polite"
      className="fixed bottom-4 right-4 z-[70] flex flex-col gap-2"
    >
      {toasts.map((t) => {
        const Icon = iconMap[t.variant];
        return (
          <div
            key={t.id}
            role="status"
            className={`flex items-center gap-2 min-w-[280px] max-w-[420px] px-3 py-2 rounded-lg shadow-lg bg-[color:var(--color-bg-elevated)] border-l-4 ${variantClass[t.variant]}`}
          >
            <Icon width={16} height={16} />
            <span className="text-xs flex-1 text-[color:var(--color-text-primary)]">
              {t.message}
            </span>
            {t.action ? (
              <button
                // Undo 등 action 버튼. 실행 후 자동 dismiss — 사용자가 또 ×를 누를 필요 없게.
                onClick={() => {
                  t.action!.onClick();
                  dismiss(t.id);
                }}
                className="text-xs font-medium px-2 py-1 rounded border border-current hover:bg-[color:var(--color-bg-hover)] transition"
              >
                {t.action.label}
              </button>
            ) : null}
            <button
              onClick={() => dismiss(t.id)}
              aria-label="닫기"
              className="text-[color:var(--color-text-secondary)]"
            >
              <XMarkIcon width={14} height={14} />
            </button>
          </div>
        );
      })}
    </div>
  );
}
