"use client";

interface BatchCalculateButtonProps {
  isCalculating: boolean;
  isCalculated: boolean;
  /** 계산 실패 시 에러 메시지 */
  calcError?: string | null;
  onCalculate: () => void;
  disabled?: boolean;
}

const PRIMARY = "#C41230";
const PRIMARY_HOVER = "#9E0E27";
const SUCCESS = "#16A34A";
const ERROR_BG = "#DC2626";
const ERROR_HOVER = "#B91C1C";

export function BatchCalculateButton({
  isCalculating,
  isCalculated,
  calcError,
  onCalculate,
  disabled,
}: BatchCalculateButtonProps) {
  const isDisabled = disabled || isCalculating;

  let bgColor = PRIMARY;
  let hoverColor = PRIMARY_HOVER;
  let label = "생산 배치 계산";
  let textColor = "#FFFFFF";
  let cursor = "pointer";
  let clickable = true;

  if (calcError) {
    // 에러 상태 — 재시도 가능
    bgColor = ERROR_BG;
    hoverColor = ERROR_HOVER;
    label = "계산 실패 - 재시도";
    clickable = true;
  } else if (isCalculated) {
    bgColor = SUCCESS;
    hoverColor = SUCCESS;
    label = "계산 완료 \u2713";
    clickable = false;
  } else if (isDisabled) {
    bgColor = "#E5E7EB";
    hoverColor = "#E5E7EB";
    textColor = "#9CA3AF";
    cursor = "not-allowed";
    clickable = false;
  }

  const handleClick = () => {
    if (clickable && !isCalculating) {
      onCalculate();
    }
  };

  return (
    <div className="flex justify-center py-4">
      <button
        onClick={handleClick}
        disabled={!clickable && isDisabled}
        className="text-sm font-semibold py-3 rounded-lg transition-colors"
        style={{
          backgroundColor: bgColor,
          color: textColor,
          cursor: clickable ? cursor : "default",
          maxWidth: 320,
          width: "100%",
        }}
        onMouseEnter={(e) => {
          if (clickable || calcError) {
            e.currentTarget.style.backgroundColor = hoverColor;
          }
        }}
        onMouseLeave={(e) => {
          if (clickable || calcError) {
            e.currentTarget.style.backgroundColor = bgColor;
          }
        }}
      >
        {isCalculating ? (
          <span className="flex items-center justify-center gap-2">
            <svg
              className="animate-spin"
              style={{ width: 16, height: 16 }}
              viewBox="0 0 24 24"
              fill="none"
            >
              <circle
                cx="12"
                cy="12"
                r="10"
                stroke="currentColor"
                strokeWidth="3"
                strokeLinecap="round"
                style={{ opacity: 0.25 }}
              />
              <path
                d="M12 2a10 10 0 0 1 10 10"
                stroke="currentColor"
                strokeWidth="3"
                strokeLinecap="round"
                style={{ opacity: 0.75 }}
              />
            </svg>
            배치 계산 중...
          </span>
        ) : (
          label
        )}
      </button>
    </div>
  );
}
