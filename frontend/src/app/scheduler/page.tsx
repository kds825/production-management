"use client";

import { useCallback } from "react";
import { Header } from "@/shared/components/Header";
import { SchedulerView } from "@/features/scheduler/components/SchedulerView";
import { ViewFilter } from "@/features/scheduler/components/ViewFilter";
import { OrderInbox } from "@/features/scheduler/components/OrderInbox";
import { ConstraintAlert } from "@/features/scheduler/components/ConstraintAlert";
import { ContextMenu } from "@/features/scheduler/components/ContextMenu";
import { TaskFormModal } from "@/features/scheduler/components/TaskFormModal";
import { ZoomControl } from "@/features/scheduler/components/ZoomControl";
import { useScheduleData } from "@/features/scheduler/hooks/useScheduleData";
import { useScheduleStore } from "@/features/scheduler/store/scheduleStore";

export default function SchedulerPage() {
  const { isLoading, error } = useScheduleData();
  const openTaskFormModal = useScheduleStore((s) => s.openTaskFormModal);
  const isEditMode = useScheduleStore((s) => s.isEditMode);
  const toggleEditMode = useScheduleStore((s) => s.toggleEditMode);
  const saveVersion = useScheduleStore((s) => s.saveVersion);
  const showSavedToast = useScheduleStore((s) => s.showSavedToast);

  const handleAddTask = useCallback(() => {
    openTaskFormModal({ mode: "create" });
  }, [openTaskFormModal]);

  // 수정하기/저장하기 토글 처리:
  // - 읽기 전용 → 편집 모드로 전환
  // - 편집 모드 → 저장 후 읽기 전용으로 전환
  const handleToggleEditMode = useCallback(async () => {
    if (isEditMode) {
      await saveVersion();
    } else {
      toggleEditMode();
    }
  }, [isEditMode, saveVersion, toggleEditMode]);

  return (
    <div
      className="flex flex-col h-screen overflow-hidden"
      style={{ backgroundColor: "#FAFAFA" }}
    >
      {/* 상단 헤더 */}
      <Header
        onAddTask={handleAddTask}
        isEditMode={isEditMode}
        onToggleEditMode={handleToggleEditMode}
      />

      {/* 뷰 필터 + 줌 컨트롤 */}
      <div className="flex items-center bg-white border-b border-gray-200">
        <div className="flex-1">
          <ViewFilter />
        </div>
        <div className="px-4 py-2 shrink-0 border-l border-gray-200">
          <ZoomControl />
        </div>
      </div>

      {/* 로딩 / 에러 배너 */}
      {isLoading && (
        <div className="flex items-center justify-center gap-2 py-1.5 bg-blue-50 border-b border-blue-200">
          <div className="w-3 h-3 border-2 border-blue-400 border-t-transparent rounded-full animate-spin" />
          <span className="text-xs text-blue-600">
            스케줄 데이터 로드 중...
          </span>
        </div>
      )}

      {error && (
        <div className="flex items-center gap-2 px-4 py-1.5 bg-red-50 border-b border-red-200">
          <span className="text-xs text-red-600">⚠ {error}</span>
          <span className="text-xs text-gray-400">
            — 백엔드 서버 연결을 확인하세요 (localhost:8000)
          </span>
        </div>
      )}

      {/* 편집 모드 표시 배너 */}
      {isEditMode && (
        <div className="flex items-center justify-center gap-2 py-1 bg-amber-50 border-b border-amber-200">
          <div
            className="w-2 h-2 rounded-full animate-pulse"
            style={{ backgroundColor: "#C41230" }}
          />
          <span className="text-xs font-medium" style={{ color: "#4A2C2A" }}>
            수정 모드 — 작업 바를 드래그하여 이동하거나 크기를 조절할 수
            있습니다. 완료 후 저장하기를 클릭하세요.
          </span>
        </div>
      )}

      {/* 저장 완료 토스트 */}
      {showSavedToast && (
        <div
          className="fixed bottom-6 right-6 z-50 flex items-center gap-2 px-4 py-3 rounded-lg shadow-lg text-white text-sm font-medium"
          style={{ backgroundColor: "#16A34A" }}
        >
          <span>✓</span>
          <span>저장 완료 — 새 버전이 생성되었습니다.</span>
        </div>
      )}

      {/* 메인 콘텐츠 영역 */}
      <main className="flex-1 overflow-hidden flex">
        {/* 좌측 미배정 수주 패널 */}
        <OrderInbox />

        {/* 스케줄러 + 제약 조건 패널 */}
        <div className="flex-1 overflow-hidden flex flex-col p-3 gap-2">
          <SchedulerView />
          <ConstraintAlert />
        </div>
      </main>

      {/* 전역 오버레이 UI */}
      <ContextMenu />
      <TaskFormModal />
    </div>
  );
}
