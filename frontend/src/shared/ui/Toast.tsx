"use client";
import {
  CheckCircleIcon,
  ClipboardDocumentIcon,
  ExclamationTriangleIcon,
  InformationCircleIcon,
  XCircleIcon,
  XMarkIcon,
} from "@heroicons/react/24/outline";
import { useState } from "react";
import { apiFetch } from "@/shared/api/client";
import { useToastStore, type ToastVariant } from "./toastStore";

/**
 * Week 5 Task 5B.2 — 사유 칩 4종. 스펙 §8d 명시: `기타` 금지.
 * 백엔드 ALLOWED_REASONS 와 1:1 일치해야 한다 (PATCH 422 회피).
 */
const REASON_CHIPS = [
  "납기 변경",
  "현장 긴급",
  "설비 고장",
  "자재 부족",
] as const;
type ReasonChip = (typeof REASON_CHIPS)[number];

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

/**
 * Week 5 Task 5B.2 — 사유 칩 row.
 *
 * 사유 칩 클릭 → PATCH /api/change-sets/{id}/reason → 200 시 1초 녹색 플래시 후
 * dismiss. 422/500 등 실패 시 인라인 에러 표시 (별도 ErrorToast 생성 금지: 4B.4
 * 룰 준수). 부모(t.meta.onResolved) 가 있으면 dismiss 시점에 호출 — 헤더 배지가
 * 즉시 갱신되도록.
 *
 * 분리 이유: useState 가 토스트별로 필요해 ToastContainer 내부 .map 에서는
 * 사용 불가 (Hooks 규칙).
 */
function ReasonPromptChips({
  changeSetId,
  flashed,
  onCommitted,
}: {
  changeSetId: string;
  flashed: boolean;
  onCommitted: () => void;
}) {
  const [submitting, setSubmitting] = useState<ReasonChip | null>(null);
  const [error, setError] = useState<string | null>(null);

  const onPick = async (reason: ReasonChip) => {
    if (submitting !== null) return;
    setSubmitting(reason);
    setError(null);
    try {
      await apiFetch(`/change-sets/${changeSetId}/reason`, {
        method: "PATCH",
        body: JSON.stringify({ reason }),
      });
      // 1초 플래시 후 dismiss — 부모가 onCommitted 에서 setTimeout 으로 처리.
      onCommitted();
    } catch (e) {
      setError(e instanceof Error ? e.message : "사유 저장 실패");
      setSubmitting(null);
    }
  };

  return (
    <div className="flex flex-col gap-1.5 mt-1">
      <div className="flex flex-wrap gap-1">
        {REASON_CHIPS.map((chip) => {
          const isActive = submitting === chip;
          const isDimmed = submitting !== null && !isActive;
          return (
            <button
              key={chip}
              type="button"
              onClick={() => void onPick(chip)}
              disabled={submitting !== null || flashed}
              aria-label={`사유 선택: ${chip}`}
              className={[
                "text-[11px] px-2 py-1 rounded border transition-colors",
                "border-[color:var(--color-border-default)]",
                "text-[color:var(--color-text-primary)]",
                "hover:bg-[color:var(--color-bg-hover)]",
                "disabled:cursor-not-allowed",
                isDimmed ? "opacity-40" : "",
                isActive ? "bg-[color:var(--color-bg-muted)]" : "",
              ].join(" ")}
            >
              {chip}
            </button>
          );
        })}
      </div>
      {error ? (
        <span className="text-[10px] text-[color:var(--color-danger)]">
          {error}
        </span>
      ) : null}
    </div>
  );
}

/**
 * 단일 토스트 아이템 — chip variant 시 useState 가 필요하므로 컴포넌트로 분리.
 */
function ToastItemView({
  toast,
  onDismiss,
}: {
  toast: ReturnType<typeof useToastStore.getState>["toasts"][number];
  onDismiss: () => void;
}) {
  const t = toast;
  const Icon = iconMap[t.variant];
  const runId = t.meta?.runId ?? null;
  const isReasonPrompt = t.meta?.kind === "reason-prompt";
  // 1초 녹색 플래시 후 dismiss — 칩 클릭 직후 시각 피드백.
  const [flashSuccess, setFlashSuccess] = useState(false);

  // Multi-line layout (runId / chips) 일 때 아이콘 상단 정렬.
  const containerAlign =
    runId || isReasonPrompt ? "items-start" : "items-center";
  const successBorder = flashSuccess
    ? "border-[color:var(--color-success)] text-[color:var(--color-success)]"
    : variantClass[t.variant];

  return (
    <div
      role="status"
      className={`flex ${containerAlign} gap-2 min-w-[280px] max-w-[420px] px-3 py-2 rounded-lg shadow-lg bg-[color:var(--color-bg-elevated)] border-l-4 ${successBorder} transition-colors`}
    >
      <Icon width={16} height={16} />
      <div className="flex-1 flex flex-col gap-1">
        <span className="text-xs text-[color:var(--color-text-primary)]">
          {flashSuccess ? "사유가 기록되었습니다." : t.message}
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
        {isReasonPrompt && t.meta?.changeSetId && !flashSuccess ? (
          <ReasonPromptChips
            changeSetId={t.meta.changeSetId}
            flashed={flashSuccess}
            onCommitted={() => {
              setFlashSuccess(true);
              // 1초 플래시 후 자동 닫힘. onResolved 는 dismiss 시점에 호출돼
              // 헤더 배지가 즉시 1건 줄어든 값을 fetch 하도록 트리거.
              window.setTimeout(() => {
                t.meta?.onResolved?.();
                onDismiss();
              }, 1000);
            }}
          />
        ) : null}
      </div>
      {t.action ? (
        <button
          // Undo 등 action 버튼. 실행 후 자동 dismiss — 사용자가 또 ×를 누를 필요 없게.
          onClick={() => {
            t.action!.onClick();
            onDismiss();
          }}
          className="text-xs font-medium px-2 py-1 rounded border border-current hover:bg-[color:var(--color-bg-hover)] transition"
        >
          {t.action.label}
        </button>
      ) : null}
      {/* reason-prompt 토스트는 "스킵 = X 또는 자동 만료" 계약 — X 클릭 시 onResolved
          를 호출해 배지 갱신 (사유 미기록 +1 상태 유지 → 다시 0건 동기화 트리거). */}
      <button
        onClick={() => {
          if (isReasonPrompt) t.meta?.onResolved?.();
          onDismiss();
        }}
        aria-label="닫기"
        className="text-[color:var(--color-text-secondary)]"
      >
        <XMarkIcon width={14} height={14} />
      </button>
    </div>
  );
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
      {toasts.map((t) => (
        <ToastItemView key={t.id} toast={t} onDismiss={() => dismiss(t.id)} />
      ))}
    </div>
  );
}
