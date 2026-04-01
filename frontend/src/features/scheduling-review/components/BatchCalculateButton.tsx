"use client";

interface BatchCalculateButtonProps {
  isCalculating: boolean;
  isCalculated: boolean;
  /** 계산 실패 시 에러 메시지 */
  calcError?: string | null;
  /** AI 분석 비동기 상태 */
  aiAnalysisStatus?: "idle" | "pending" | "done" | "error";
  onCalculate: () => void;
  disabled?: boolean;
}

const PRIMARY = "#C41230";
const PRIMARY_HOVER = "#9E0E27";
const SUCCESS = "#16A34A";
const ERROR_BG = "#DC2626";
const ERROR_HOVER = "#B91C1C";
const PENDING_BG = "#6366F1";
const PENDING_HOVER = "#4F46E5";

export function BatchCalculateButton({
  isCalculating,
  isCalculated,
  calcError,
  aiAnalysisStatus = "idle",
  onCalculate,
  disabled,
}: BatchCalculateButtonProps) {
  const isDisabled = disabled || isCalculating;
  const isPending = aiAnalysisStatus === "pending" || isCalculating;

  let bgColor = PRIMARY;
  let hoverColor = PRIMARY_HOVER;
  let label = "AI 재분석";
  let textColor = "#FFFFFF";
  let cursor = "pointer";
  let clickable = true;

  if (calcError) {
    // 에러 상태 -- 재시도 가능
    bgColor = ERROR_BG;
    hoverColor = ERROR_HOVER;
    label = "분석 실패 - 재시도";
    clickable = true;
  } else if (isPending) {
    // AI 분석 진행 중
    bgColor = PENDING_BG;
    hoverColor = PENDING_HOVER;
    label = "AI 분석 중...";
    cursor = "default";
    clickable = false;
  } else if (isCalculated) {
    // 분석 완료 -- 재분석 클릭 가능
    bgColor = SUCCESS;
    hoverColor = PRIMARY_HOVER;
    label = "AI 분석 완료 - 재분석";
    clickable = true;
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
        {isPending ? (
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
            AI 분석 중...
          </span>
        ) : (
          label
        )}
      </button>
    </div>
  );
}
