"use client";

import { useCallback } from "react";
import { useDraggable } from "@dnd-kit/core";
import type { ScheduleTask } from "../types";
import { useScheduleStore } from "../store/scheduleStore";
import {
  getTaskColor,
  getPriorityStyle,
  getStatusStyle,
} from "../utils/colorCoding";
import { timeToX, ROW_HEIGHT } from "../utils/ganttUtils";

interface GanttTaskBlockProps {
  task: ScheduleTask;
  rangeStart: number;
  dayWidth: number;
}

/**
 * Gantt 차트 내 개별 작업 블록.
 * 절대 위치(position: absolute)로 배치되며, 시작/종료 시각 기반으로 left/width를 계산한다.
 * @dnd-kit/core의 useDraggable로 드래그 가능.
 */
export function GanttTaskBlock({
  task,
  rangeStart,
  dayWidth,
}: GanttTaskBlockProps) {
  const baseColor = getTaskColor(task.product);
  const openTaskFormModal = useScheduleStore((s) => s.openTaskFormModal);
  const openContextMenu = useScheduleStore((s) => s.openContextMenu);
  const isEditMode = useScheduleStore((s) => s.isEditMode);

  const startTs =
    task.start instanceof Date
      ? task.start.getTime()
      : new Date(task.start).getTime();
  const endTs =
    task.end instanceof Date
      ? task.end.getTime()
      : new Date(task.end).getTime();

  const left = timeToX(startTs, rangeStart, dayWidth);
  const width = timeToX(endTs, rangeStart, dayWidth) - left;

  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: task.id,
    data: {
      type: "task",
      task,
      equipmentId: task.equipment_id,
    },
    disabled: false,
  });

  // 더블클릭 -> 수정 모달 열기 (편집 모드에서만)
  const handleDoubleClick = useCallback(
    (e: React.MouseEvent) => {
      if (!isEditMode) return;
      e.stopPropagation();
      e.preventDefault();
      openTaskFormModal({ mode: "edit", taskId: task.id });
    },
    [isEditMode, openTaskFormModal, task.id],
  );

  // 우클릭 -> 작업 컨텍스트 메뉴 열기
  const handleContextMenu = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      e.stopPropagation();
      openContextMenu({
        x: e.clientX,
        y: e.clientY,
        type: "task",
        taskId: task.id,
      });
    },
    [isEditMode, openContextMenu, task.id],
  );

  const priorityStyle = getPriorityStyle(task.priority);
  const statusStyle = getStatusStyle(task.status, baseColor);

  const barStyle: React.CSSProperties = {
    ...statusStyle,
    ...priorityStyle,
    borderRadius: "4px",
    boxShadow: isDragging
      ? "0 4px 12px rgba(0,0,0,0.3)"
      : "0 1px 3px rgba(0,0,0,0.15)",
    cursor: isDragging ? "grabbing" : "grab",
    userSelect: "none",
    overflow: "hidden",
    height: ROW_HEIGHT - 8,
    opacity: isDragging ? 0.5 : 1,
    transition: isDragging ? "none" : "box-shadow 0.15s ease",
  };

  // 볼륨을 미터 단위로 표시 (km이 아닌 m 사용)
  const volumeLabel = `${task.volume_m.toLocaleString()}m`;

  return (
    <div
      ref={setNodeRef}
      {...listeners}
      {...attributes}
      data-draggable
      style={{
        position: "absolute",
        left,
        top: 4,
        width: Math.max(width, 30),
        zIndex: isDragging ? 20 : 2,
      }}
      onDoubleClick={handleDoubleClick}
      onContextMenu={handleContextMenu}
    >
      <div style={barStyle}>
        <div
          className="flex flex-col justify-center px-2 h-full gap-px"
          style={{ minWidth: 0 }}
        >
          {/* 제품명 + 규격 */}
          <span
            className="text-white text-[10px] font-semibold truncate leading-tight"
            style={{ textShadow: "0 1px 2px rgba(0,0,0,0.4)" }}
          >
            {task.product}
            {task.spec ? ` ${task.spec}` : ""}
          </span>

          {/* 색상 코어수 + 물량 */}
          <span
            className="text-white/80 text-[9px] truncate leading-tight"
            style={{ textShadow: "0 1px 1px rgba(0,0,0,0.3)" }}
          >
            {task.color && `${task.color} `}
            {task.core_count > 0 && `${task.core_count}C `}
            {volumeLabel}
          </span>
        </div>

        {/* 우선순위 배지 */}
        {task.priority !== "normal" && (
          <div
            className="absolute top-0.5 right-0.5 text-[8px] font-bold text-white bg-red-600 rounded px-0.5"
            style={{ lineHeight: "1.2" }}
          >
            {task.priority === "critical" ? "긴급" : "우선"}
          </div>
        )}
      </div>
    </div>
  );
}
