/**
 * SchedulerBanners — 스케줄러 페이지 상단/하단 transient 배너 묶음.
 *
 * 책임 (Week 7 Task 7B.1 분리):
 *   - 자동배열 결과 알림 (성공/오류/연결실패) — 색상은 메시지 prefix 기반.
 *   - 로딩 / 에러 배너 (스케줄 데이터 fetch 상태)
 *   - 편집 모드 진입 안내 배너
 *   - 저장 완료 토스트 (showSavedToast)
 *   - 선행 공정 이동 경고 토스트
 *
 * 묶은 이유: 모두 페이지 헤더 직후 또는 fixed 위치의 안내 메시지로,
 * 별도 라이프사이클(자동 사라짐, 사용자 dismiss) 만 다르고 시각적으로 같은 영역.
 * page.tsx 상단 ~150 줄을 단일 컴포넌트로 압축.
 */
"use client";

interface SchedulerBannersProps {
  // 자동배열 결과
  autoScheduleResult: string | null;
  onDismissAutoSchedule: () => void;
  // 데이터 로드 상태
  isLoading: boolean;
  error: string | null;
  // 편집 모드
  isEditMode: boolean;
  // 저장 완료
  showSavedToast: boolean;
  // 선행 공정 경고
  predecessorToast: string | null;
  onDismissPredecessor: () => void;
}

export function SchedulerBanners({
  autoScheduleResult,
  onDismissAutoSchedule,
  isLoading,
  error,
  isEditMode,
  showSavedToast,
  predecessorToast,
  onDismissPredecessor,
}: SchedulerBannersProps) {
  const isAutoScheduleError =
    autoScheduleResult?.startsWith("오류") ||
    autoScheduleResult?.startsWith("연결");

  return (
    <>
      {/* 자동배열 결과 알림 */}
      {autoScheduleResult && (
        <div
          className="flex items-start justify-between gap-2 px-4 py-2 border-b text-[11px]"
          style={{
            backgroundColor: isAutoScheduleError
              ? "var(--kbi-red-tint-5)"
              : "var(--status-success-bg-soft)",
            borderColor: isAutoScheduleError ? "var(--kbi-red-tint-20)" : "var(--status-success-border-soft)",
            color: isAutoScheduleError ? "var(--status-danger-text)" : "var(--status-success-text)",
          }}
        >
          <span>{autoScheduleResult}</span>
          <button
            onClick={onDismissAutoSchedule}
            className="shrink-0 text-gray-400 hover:text-gray-600"
          >
            ✕
          </button>
        </div>
      )}

      {/* 로딩 / 에러 배너 */}
      {isLoading && (
        <div className="flex items-center justify-center gap-2 py-1.5 bg-blue-50 border-b border-blue-200">
          <div
            className="w-3 h-3 border-2 border-blue-400 border-t-transparent rounded-full"
            style={{ animation: "spin 1s linear infinite" }}
          />
          <span className="text-xs text-blue-600">
            스케줄 데이터 로드 중...
          </span>
        </div>
      )}

      {error && (
        <div className="flex items-center gap-2 px-4 py-1.5 bg-red-50 border-b border-red-200">
          <span className="text-xs text-red-600">{error}</span>
          <span className="text-xs text-gray-400">
            -- 백엔드 서버 연결을 확인하세요 (localhost:8000)
          </span>
        </div>
      )}

      {/* 편집 모드 표시 배너 */}
      {isEditMode && (
        <div className="flex items-center justify-center gap-2 py-1 bg-amber-50 border-b border-amber-200">
          <div
            className="w-2 h-2 rounded-full animate-pulse"
            style={{ backgroundColor: "var(--color-brand-primary)" }}
          />
          <span
            className="text-xs font-medium"
            style={{ color: "var(--kbi-brown)" }}
          >
            수정 모드 -- 작업 바를 드래그하여 이동하거나 하단 패널의 수주를
            드래그하여 배정하세요. 완료 후 저장하기를 클릭하세요.
          </span>
        </div>
      )}

      {/* 저장 완료 토스트 */}
      {showSavedToast && (
        <div
          className="fixed bottom-6 right-6 z-50 flex items-center gap-2 px-4 py-3 rounded-lg shadow-lg text-white text-sm font-medium"
          style={{ backgroundColor: "var(--status-success)" }}
        >
          <span>저장 완료 -- 새 버전이 생성되었습니다.</span>
        </div>
      )}

      {/* 선행 공정 이동 경고 토스트 */}
      {predecessorToast && (
        <div
          className="fixed bottom-6 left-1/2 z-50 flex items-center gap-2 px-4 py-3 rounded-lg shadow-lg text-sm font-medium"
          style={{
            backgroundColor: "var(--status-warning-bg)",
            color: "var(--status-warning-text)",
            border: "1px solid var(--status-warning-border)",
            transform: "translateX(-50%)",
          }}
        >
          <svg
            width="14"
            height="14"
            viewBox="0 0 16 16"
            fill="currentColor"
            style={{ color: "var(--status-warning)", flexShrink: 0 }}
          >
            <path d="M8 1L1 14h14L8 1zm0 2.5l5.5 9.5h-11L8 3.5zM7.25 7v3.5h1.5V7h-1.5zm0 4.5v1.5h1.5v-1.5h-1.5z" />
          </svg>
          <span>{predecessorToast}</span>
          <button
            onClick={onDismissPredecessor}
            className="ml-2 text-amber-400 hover:text-amber-600"
          >
            ✕
          </button>
        </div>
      )}
    </>
  );
}
