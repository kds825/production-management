"use client";

import { useCallback } from "react";
import { Header } from "@/shared/components/Header";
import { SchedulerView } from "@/features/scheduler/components/SchedulerView";
import { ViewFilter } from "@/features/scheduler/components/ViewFilter";
import { OrderInbox } from "@/features/scheduler/components/OrderInbox";
import { ConstraintAlert } from "@/features/scheduler/components/ConstraintAlert";
import { ContextMenu } from "@/features/scheduler/components/ContextMenu";
import { TaskFormModal } from "@/features/scheduler/components/TaskFormModal";
import { useScheduleData } from "@/features/scheduler/hooks/useScheduleData";
import { useScheduleStore } from "@/features/scheduler/store/scheduleStore";

export default function SchedulerPage() {
  const { isLoading, error } = useScheduleData();
  const openTaskFormModal = useScheduleStore((s) => s.openTaskFormModal);

  const handleAddTask = useCallback(() => {
    openTaskFormModal({ mode: "create" });
  }, [openTaskFormModal]);

  return (
    <div
      className="flex flex-col h-screen overflow-hidden"
      style={{ backgroundColor: "#FAFAFA" }}
    >
      {/* 상단 헤더 */}
      <Header onAddTask={handleAddTask} />

      {/* 뷰 필터 */}
      <ViewFilter />

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
