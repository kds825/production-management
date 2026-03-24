"use client";

import { Header } from "@/shared/components/Header";
import { SchedulerView } from "@/features/scheduler/components/SchedulerView";
import { useScheduleData } from "@/features/scheduler/hooks/useScheduleData";

export default function SchedulerPage() {
  const { isLoading, error } = useScheduleData();

  function handleAddTask() {
    // TODO: Task 5에서 작업 추가 모달 구현 예정
    alert("작업 추가 기능은 준비 중입니다.");
  }

  return (
    <div
      className="flex flex-col h-screen overflow-hidden"
      style={{ backgroundColor: "#FAFAFA" }}
    >
      {/* 상단 헤더 */}
      <Header onAddTask={handleAddTask} />

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

      {/* 메인 스케줄러 */}
      <main className="flex-1 overflow-hidden flex flex-col p-3 gap-2">
        <SchedulerView />
      </main>
    </div>
  );
}
