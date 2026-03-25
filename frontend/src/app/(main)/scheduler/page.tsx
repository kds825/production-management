"use client";

import { useCallback, useMemo, useState, useEffect } from "react";
import { createPortal } from "react-dom";
import {
  DndContext,
  DragOverlay,
  PointerSensor,
  useSensor,
  useSensors,
  type DragStartEvent,
  type DragEndEvent,
} from "@dnd-kit/core";

import { Header } from "@/shared/components/Header";
import { CollapsiblePanel } from "@/shared/components/CollapsiblePanel";
import {
  SchedulerView,
  equipmentMatchesGroup,
} from "@/features/scheduler/components/SchedulerView";
import { ViewFilter } from "@/features/scheduler/components/ViewFilter";
import { OrderInbox } from "@/features/scheduler/components/OrderInbox";
import { ConstraintAlert } from "@/features/scheduler/components/ConstraintAlert";
import { ContextMenu } from "@/features/scheduler/components/ContextMenu";
import { TaskFormModal } from "@/features/scheduler/components/TaskFormModal";
import { ZoomControl } from "@/features/scheduler/components/ZoomControl";
import { SyncButton } from "@/features/scheduler/components/SyncButton";
import { useScheduleData } from "@/features/scheduler/hooks/useScheduleData";
import { useScheduleStore } from "@/features/scheduler/store/scheduleStore";
import type { Order, ScheduleTask } from "@/features/scheduler/types";
import { xToTime, SIDEBAR_WIDTH } from "@/features/scheduler/utils/ganttUtils";

/** 드래그 중인 아이템 정보 */
interface ActiveDragItem {
  type: "order" | "task";
  order?: Order;
  task?: ScheduleTask;
}

export default function SchedulerPage() {
  const { isLoading, error } = useScheduleData();
  const openTaskFormModal = useScheduleStore((s) => s.openTaskFormModal);
  const isEditMode = useScheduleStore((s) => s.isEditMode);
  const toggleEditMode = useScheduleStore((s) => s.toggleEditMode);
  const saveVersion = useScheduleStore((s) => s.saveVersion);
  const showSavedToast = useScheduleStore((s) => s.showSavedToast);
  const assignOrder = useScheduleStore((s) => s.assignOrder);
  const moveTask = useScheduleStore((s) => s.moveTask);
  const equipment = useScheduleStore((s) => s.equipment);
  const zoomLevel = useScheduleStore((s) => s.zoomLevel);
  const range = useScheduleStore((s) => s.range);
  const unscheduledOrders = useScheduleStore((s) => s.unscheduledOrders);

  const [activeDrag, setActiveDrag] = useState<ActiveDragItem | null>(null);

  // 드래그 중인 아이템의 equipment_group 추론 — SchedulerView로 전달하여 비호환 행을 비활성화
  const activeDragGroup = useMemo<string | null>(() => {
    if (!activeDrag) return null;
    if (activeDrag.type === "order" && activeDrag.order) {
      return activeDrag.order.equipment_group ?? null;
    }
    if (activeDrag.type === "task" && activeDrag.task) {
      // 기존 task는 equipment_id로 설비를 찾아 그룹 추론
      const eq = equipment.find((e) => e.id === activeDrag.task!.equipment_id);
      if (!eq) return null;
      // equipment_group 키를 순서대로 시도
      for (const g of ["연선", "B100", "A100", "A120"]) {
        if (equipmentMatchesGroup(eq, g)) return g;
      }
    }
    return null;
  }, [activeDrag, equipment]);
  const [panelAnimating, setPanelAnimating] = useState(false);
  const [showEditWarning, setShowEditWarning] = useState(false);

  // 드래그 감도 설정 — 5px 이동 후 드래그 시작
  const sensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: { distance: 5 },
    }),
  );

  const handleAddTask = useCallback(() => {
    openTaskFormModal({ mode: "create" });
  }, [openTaskFormModal]);

  // 수정하기/저장하기 토글
  const handleToggleEditMode = useCallback(async () => {
    if (isEditMode) {
      await saveVersion();
    } else {
      toggleEditMode();
    }
  }, [isEditMode, saveVersion, toggleEditMode]);

  // 드래그 시작
  const handleDragStart = useCallback((event: DragStartEvent) => {
    const data = event.active.data.current;
    if (!data) return;

    if (data.type === "order") {
      // equipment_group이 drag data에 있으면 order 객체에 merge하여 activeDragGroup 계산에 활용
      const order = data.order as Order;
      const enrichedOrder: Order = data.equipment_group
        ? { ...order, equipment_group: data.equipment_group as string }
        : order;
      setActiveDrag({ type: "order", order: enrichedOrder });
    } else if (data.type === "task") {
      setActiveDrag({ type: "task", task: data.task as ScheduleTask });
    }
  }, []);

  // 드래그 종료
  const handleDragEnd = useCallback(
    (event: DragEndEvent) => {
      setActiveDrag(null);

      const { active, over } = event;
      if (!over) return;

      // 수정 모드가 아니면 경고 모달 표시
      if (!isEditMode) {
        setShowEditWarning(true);
        return;
      }

      const activeData = active.data.current;
      const overData = over.data.current;
      if (!activeData || !overData) return;

      // 드롭 대상이 설비 행인지 확인
      if (overData.type !== "equipment-row") return;

      const targetEquipmentId = overData.equipmentId as string;

      // 동적 dayWidth 계산 (컨테이너 너비 기반)
      const MS_PER_DAY = 24 * 60 * 60 * 1000;
      const totalDays = Math.max((range.end - range.start) / MS_PER_DAY, 1);

      // --- 수주를 스케줄러에 드롭 ---
      if (activeData.type === "order") {
        const order = activeData.order as Order;
        const rangeStart = range.start;

        // 드롭 위치에서 시간을 계산
        // delta.x가 드래그 시작점으로부터의 이동 거리이므로,
        // over 요소의 위치를 기반으로 시작 시각을 결정
        // 기본 동작: 현재 시각으로 배정
        let startTime: Date;

        if (event.delta) {
          // 드롭 위치의 x를 기반으로 시간 계산
          // over 요소(설비 행)의 rect를 사용하여 상대 위치 결정
          const overRect = over.rect;
          if (overRect) {
            const activatorEvent = event.activatorEvent as MouseEvent;
            if (activatorEvent) {
              const dropClientX = activatorEvent.clientX + event.delta.x;
              const relativeX = dropClientX - overRect.left;
              // overRect.width가 타임라인 영역 너비 — dayWidth를 동적 계산
              const dynamicDayWidth = overRect.width / totalDays;
              const timestamp = xToTime(relativeX, rangeStart, dynamicDayWidth);
              startTime = new Date(timestamp);
            } else {
              startTime = new Date();
            }
          } else {
            startTime = new Date();
          }
        } else {
          startTime = new Date();
        }

        assignOrder(order.id, targetEquipmentId, startTime);
        return;
      }

      // --- 기존 작업을 다른 설비로 이동 ---
      if (activeData.type === "task") {
        const task = activeData.task as ScheduleTask;
        const rangeStart = range.start;

        // 현재 작업의 duration을 유지한 채 새 위치로 이동
        const taskStartTs =
          task.start instanceof Date
            ? task.start.getTime()
            : new Date(task.start).getTime();
        const taskEndTs =
          task.end instanceof Date
            ? task.end.getTime()
            : new Date(task.end).getTime();
        const durationMs = taskEndTs - taskStartTs;

        // delta.x로 시간 이동량 계산 (동적 dayWidth)
        const overWidth = over.rect?.width || 800;
        const dynamicDayWidth = overWidth / totalDays;
        const deltaMs = (event.delta.x / dynamicDayWidth) * MS_PER_DAY;
        const newStartTs = taskStartTs + deltaMs;

        const newStart = new Date(newStartTs);
        const newEnd = new Date(newStartTs + durationMs);

        moveTask(task.id, targetEquipmentId, newStart, newEnd);
      }
    },
    [assignOrder, moveTask, zoomLevel, range, isEditMode],
  );

  return (
    <div
      className="flex flex-col h-full overflow-hidden"
      style={{ backgroundColor: "#FAFAFA" }}
    >
      {/* 상단 헤더 */}
      <Header
        onAddTask={handleAddTask}
        isEditMode={isEditMode}
        onToggleEditMode={handleToggleEditMode}
      />

      {/* 뷰 필터 + 동기화 + 줌 컨트롤 */}
      <div className="flex items-center bg-white border-b border-gray-200">
        <div className="flex-1">
          <ViewFilter />
        </div>
        <div className="px-4 py-2 shrink-0 border-l border-gray-200">
          <SyncButton />
        </div>
        <div className="px-4 py-2 shrink-0 border-l border-gray-200">
          <ZoomControl />
        </div>
      </div>

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
            style={{ backgroundColor: "#C41230" }}
          />
          <span className="text-xs font-medium" style={{ color: "#4A2C2A" }}>
            수정 모드 -- 작업 바를 드래그하여 이동하거나 하단 패널의 수주를
            드래그하여 배정하세요. 완료 후 저장하기를 클릭하세요.
          </span>
        </div>
      )}

      {/* 저장 완료 토스트 */}
      {showSavedToast && (
        <div
          className="fixed bottom-6 right-6 z-50 flex items-center gap-2 px-4 py-3 rounded-lg shadow-lg text-white text-sm font-medium"
          style={{ backgroundColor: "#16A34A" }}
        >
          <span>저장 완료 -- 새 버전이 생성되었습니다.</span>
        </div>
      )}

      {/* 메인 콘텐츠 영역 — DndContext가 OrderInbox와 SchedulerView를 모두 감싼다 */}
      <DndContext
        sensors={sensors}
        onDragStart={handleDragStart}
        onDragEnd={handleDragEnd}
      >
        <main className="flex-1 overflow-hidden flex flex-col">
          {/* 간트 차트 + 제약 조건 */}
          <div
            className="flex-1 overflow-hidden flex flex-col p-3 gap-2"
            style={{ minHeight: 300 }}
          >
            <SchedulerView activeDragGroup={activeDragGroup} />
            <ConstraintAlert />
          </div>

          {/* 하단 미배정 수주 패널 */}
          <CollapsiblePanel
            title="미배정 작업"
            count={unscheduledOrders.length}
            defaultExpanded={unscheduledOrders.length > 0}
            onAnimatingChange={setPanelAnimating}
          >
            <OrderInbox isAnimating={panelAnimating} />
          </CollapsiblePanel>
        </main>

        {/* 드래그 오버레이 — 드래그 중인 아이템 미리보기 */}
        <DragOverlay dropAnimation={null}>
          {activeDrag?.type === "order" && activeDrag.order && (
            <div
              className="px-3 py-2 rounded-lg shadow-xl text-white text-xs font-semibold"
              style={{
                backgroundColor: "#C41230",
                minWidth: 120,
                pointerEvents: "none",
              }}
            >
              {activeDrag.order.product} {activeDrag.order.spec}
              <br />
              <span className="text-white/80 text-[9px]">
                {activeDrag.order.total_length_m.toLocaleString()}m
              </span>
            </div>
          )}
          {activeDrag?.type === "task" && activeDrag.task && (
            <div
              className="px-3 py-2 rounded-lg shadow-xl text-white text-xs font-semibold"
              style={{
                backgroundColor: "#4A2C2A",
                minWidth: 120,
                pointerEvents: "none",
              }}
            >
              {activeDrag.task.product} {activeDrag.task.spec}
              <br />
              <span className="text-white/80 text-[9px]">
                {activeDrag.task.volume_m.toLocaleString()}m
              </span>
            </div>
          )}
        </DragOverlay>
      </DndContext>

      {/* 수정 모드 경고 모달 — Portal로 body 레벨에 렌더링 */}
      {showEditWarning &&
        typeof document !== "undefined" &&
        createPortal(
          <div
            style={{
              position: "fixed",
              inset: 0,
              zIndex: 99999,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              backgroundColor: "rgba(0,0,0,0.3)",
            }}
            onClick={() => setShowEditWarning(false)}
          >
            <div
              onClick={(e) => e.stopPropagation()}
              style={{
                backgroundColor: "#FFFFFF",
                borderRadius: 8,
                padding: "28px 32px",
                boxShadow: "0 8px 32px rgba(0,0,0,0.18)",
                border: "1px solid #E5E7EB",
                minWidth: 420,
                maxWidth: 500,
              }}
            >
              <p
                className="text-sm font-semibold mb-2"
                style={{ color: "#1F2937" }}
              >
                수정 모드를 활성화해주세요
              </p>
              <p className="text-xs text-gray-500 mb-5 leading-relaxed">
                작업을 배정하거나 이동하려면 우측 상단의 "수정하기" 버튼을 먼저
                눌러주세요.
              </p>
              <div className="flex justify-end gap-2">
                <button
                  onClick={() => setShowEditWarning(false)}
                  className="px-4 py-2 text-xs font-medium rounded-md transition-colors"
                  style={{
                    border: "1px solid #E5E7EB",
                    color: "#6B7280",
                  }}
                >
                  닫기
                </button>
                <button
                  onClick={() => {
                    setShowEditWarning(false);
                    toggleEditMode();
                  }}
                  className="px-4 py-2 text-xs font-medium rounded-md text-white transition-colors"
                  style={{ backgroundColor: "#C41230" }}
                >
                  수정하기
                </button>
              </div>
            </div>
          </div>,
          document.body,
        )}

      {/* 전역 오버레이 UI */}
      <ContextMenu />
      <TaskFormModal />
    </div>
  );
}
