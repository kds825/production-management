"use client";

interface BatchCalculateButtonProps {
  isCalculating: boolean;
  isCalculated: boolean;
  onCalculate: () => void;
  disabled?: boolean;
}

const PRIMARY = "#C41230";
const PRIMARY_HOVER = "#9E0E27";
const SUCCESS = "#16A34A";

export function BatchCalculateButton({
  isCalculating,
  isCalculated,
  onCalculate,
  disabled,
}: BatchCalculateButtonProps) {
  const isDisabled = disabled || isCalculating;

  let bgColor = PRIMARY;
  let hoverColor = PRIMARY_HOVER;
  let label = "생산 배치 계산";
  let textColor = "#FFFFFF";
  let cursor = "pointer";

  if (isCalculated) {
    bgColor = SUCCESS;
    hoverColor = SUCCESS;
    label = "계산 완료 \u2713";
  } else if (isDisabled) {
    bgColor = "#E5E7EB";
    hoverColor = "#E5E7EB";
    textColor = "#9CA3AF";
    cursor = "not-allowed";
  }

  return (
    <div className="flex justify-center py-4">
      <button
        onClick={isDisabled || isCalculated ? undefined : onCalculate}
        disabled={isDisabled || isCalculated}
        className="text-sm font-semibold py-3 rounded-lg transition-colors"
        style={{
          backgroundColor: bgColor,
          color: textColor,
          cursor: isCalculated ? "default" : cursor,
          maxWidth: 320,
          width: "100%",
        }}
        onMouseEnter={(e) => {
          if (!isDisabled && !isCalculated) {
            e.currentTarget.style.backgroundColor = hoverColor;
          }
        }}
        onMouseLeave={(e) => {
          if (!isDisabled && !isCalculated) {
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
