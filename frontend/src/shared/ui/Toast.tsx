"use client";
import {
  CheckCircleIcon,
  ClipboardDocumentIcon,
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

/**
 * Week 4 Task 4B.4 — runId 표시용 짧은 prefix 길이.
 *
 * 운영자 가독성을 위해 첫 8자만 노출하고, 클립보드에는 full UUID 를 복사.
 * (예: "11111111" 표시, "11111111-2222-3333-4444-555555555555" 복사)
 */
const RUN_ID_DISPLAY_LENGTH = 8;

/**
 * 클립보드 복사 — navigator.clipboard 가 없는 구형 환경 (HTTPS 미적용 등) 에선
 * 조용히 실패. 프로덕션 KBI 환경은 HTTPS + Chrome 최신이라 실패 경로는 사실상 도달 불가.
 * 실패해도 운영자가 텍스트를 손으로 선택할 수 있도록 runId 자체는 항상 화면에 노출한다.
 */
async function copyRunId(runId: string): Promise<void> {
  if (typeof navigator === "undefined" || !navigator.clipboard) return;
  try {
    await navigator.clipboard.writeText(runId);
  } catch {
    // 권한 거부 / 비-HTTPS 등. 토스트 자체에 fallback UI 를 또 띄우면 재귀라 무시.
  }
}

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
        const runId = t.meta?.runId ?? null;
        // alignment 를 runId 유무에 따라 분기 — runId 가 있으면 message + footer 가
        // 2 줄이 되므로 아이콘을 상단 정렬해 시각적 균형 유지. action 버튼 정렬도 동일.
        const containerAlign = runId ? "items-start" : "items-center";
        return (
          <div
            key={t.id}
            role="status"
            className={`flex ${containerAlign} gap-2 min-w-[280px] max-w-[420px] px-3 py-2 rounded-lg shadow-lg bg-[color:var(--color-bg-elevated)] border-l-4 ${variantClass[t.variant]}`}
          >
            <Icon width={16} height={16} />
            <div className="flex-1 flex flex-col gap-1">
              <span className="text-xs text-[color:var(--color-text-primary)]">
                {t.message}
              </span>
              {runId ? (
                // Week 4 Task 4B.4 — 운영자 코릴레이션 ID. 8자 prefix 노출 + full UUID 복사.
                // text-[10px] 는 이미 chipMuted 등에서 쓰이는 보조정보용 사이즈.
                <div className="flex items-center gap-1 text-[10px] text-[color:var(--color-text-secondary)]">
                  <span>오류 코드</span>
                  <code
                    className="font-mono text-[10px] px-1 py-0.5 rounded bg-[color:var(--color-bg-muted)] text-[color:var(--color-text-primary)]"
                    title={runId}
                  >
                    {runId.slice(0, RUN_ID_DISPLAY_LENGTH)}
                  </code>
                  <button
                    type="button"
                    onClick={() => void copyRunId(runId)}
                    aria-label={`오류 코드 ${runId} 복사`}
                    className="inline-flex items-center gap-0.5 px-1 py-0.5 rounded text-[color:var(--color-text-secondary)] hover:bg-[color:var(--color-bg-muted)] transition-colors"
                  >
                    <ClipboardDocumentIcon width={12} height={12} />
                    <span>복사</span>
                  </button>
                  <span>— 담당자에게 전달</span>
                </div>
              ) : null}
            </div>
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
