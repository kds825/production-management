"use client";

/**
 * OverlapAlertBanner
 *
 * 의도: Stage 2 API 응답이 `overlap_alert: true`(SchedulerOverlapError 포착)일 때
 * 화면 최상단에 prominent 배너를 띄워 관리자 확인을 유도한다.
 * 기존 스케줄을 덮어쓰지 않았음을 사용자에게 명확히 고지하고, dismiss만 허용한다.
 *
 * 분리 근거(SoC):
 *   - ConstraintAlert는 store의 `violations`에 바인딩된 하단 패널로, 성격이 다름.
 *   - 배너는 page 레벨 ephemeral 상태(overlapAlert)로만 제어하므로 별도 컴포넌트로 둔다.
 */
interface OverlapAlertBannerProps {
  /** 배너에 표시할 메시지. null 이면 렌더링하지 않음 (부모에서 분기해도 OK). */
  message: string;
  /** 닫기 버튼 콜백 */
  onDismiss: () => void;
}

export function OverlapAlertBanner({
  message,
  onDismiss,
}: OverlapAlertBannerProps) {
  return (
    <div
      role="alert"
      aria-live="assertive"
      className="flex items-start justify-between gap-3 px-4 py-3 border-b-2"
      style={{
        backgroundColor: "var(--status-alert-bg)", // orange-100
        borderColor: "var(--status-alert-border)", // orange-400
        color: "var(--status-alert-text)", // orange-900
      }}
    >
      <div className="flex items-start gap-2 flex-1 min-w-0">
        <span aria-hidden="true" className="text-base leading-none mt-0.5">
          ⚠️
        </span>
        <div className="flex flex-col gap-0.5 min-w-0">
          <span className="text-[12px] font-semibold">
            스케줄에 겹침이 있습니다 — 관리자 확인 필요
          </span>
          <span className="text-[11px]" style={{ color: "var(--status-alert-text-soft)" }}>
            {message}
          </span>
        </div>
      </div>
      <button
        type="button"
        onClick={onDismiss}
        aria-label="겹침 경고 닫기"
        className="shrink-0 text-[11px] font-medium px-2 py-1 rounded hover:bg-orange-200 transition-colors"
        style={{ color: "var(--status-alert-text)" }}
      >
        ✕ 닫기
      </button>
    </div>
  );
}
