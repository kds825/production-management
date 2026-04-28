/**
 * SchedulingResultTable 의 CRUD helper (Task 1.19, F-5).
 *
 * 추출 대상:
 * - CrudMode type
 * - generateTempId
 * - CrudButton
 */

const PRIMARY = "var(--color-brand-primary)";

/** CRUD 모드 */
export type CrudMode = "view" | "edit" | "add" | "delete";

/** 새 행 추가 시 사용할 임시 ID 생성 */
let tempIdCounter = 0;
export function generateTempId(): string {
  tempIdCounter += 1;
  return `new-${Date.now()}-${tempIdCounter}`;
}

// ── CRUD 버튼 공용 컴포넌트 ──

export function CrudButton({
  label,
  isActive,
  onClick,
  disabled = false,
  confirm = false,
}: {
  label: string;
  isActive: boolean;
  onClick: () => void;
  disabled?: boolean;
  confirm?: boolean;
}) {
  const activeStyle = {
    backgroundColor: confirm
      ? PRIMARY
      : isActive
        ? PRIMARY
        : "var(--bg-surface)",
    color: confirm
      ? "var(--bg-surface)"
      : isActive
        ? "var(--bg-surface)"
        : disabled
          ? "var(--neutral-300)"
          : "var(--neutral-text-primary)",
    border: `1px solid ${isActive || confirm ? PRIMARY : "var(--color-border-default)"}`,
    opacity: disabled ? 0.5 : 1,
    cursor: disabled ? "not-allowed" : "pointer",
  } as const;

  return (
    <button
      onClick={disabled ? undefined : onClick}
      className="text-small font-medium px-2 py-1 rounded transition-colors"
      style={activeStyle}
      onMouseEnter={(e) => {
        if (disabled) return;
        if (!isActive && !confirm) {
          e.currentTarget.style.borderColor = PRIMARY;
          e.currentTarget.style.color = PRIMARY;
        }
      }}
      onMouseLeave={(e) => {
        if (disabled) return;
        if (!isActive && !confirm) {
          e.currentTarget.style.borderColor = "var(--color-border-default)";
          e.currentTarget.style.color = "var(--neutral-text-primary)";
        }
      }}
    >
      {label}
    </button>
  );
}
