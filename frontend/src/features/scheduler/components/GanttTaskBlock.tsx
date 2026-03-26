"use client";

import { useCallback, useRef } from "react";
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

const MS_PER_DAY = 24 * 60 * 60 * 1000;
const MS_PER_HOUR = 60 * 60 * 1000;

function snapToHour(ts: number): number {
  return Math.round(ts / MS_PER_HOUR) * MS_PER_HOUR;
}

export function GanttTaskBlock({
  task,
  rangeStart,
  dayWidth,
}: GanttTaskBlockProps) {
  const baseColor = getTaskColor(task.product);
  const openTaskFormModal = useScheduleStore((s) => s.openTaskFormModal);
  const openContextMenu = useScheduleStore((s) => s.openContextMenu);
  const updateTask = useScheduleStore((s) => s.updateTask);
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
    data: { type: "task", task, equipmentId: task.equipment_id },
    disabled: false,
  });

  // --- 리사이즈 ---
  const resizing = useRef<"left" | "right" | null>(null);
  const resizeStartX = useRef(0);
  const origStart = useRef(startTs);
  const origEnd = useRef(endTs);

  const handleResizeStart = useCallback(
    (side: "left" | "right", e: React.MouseEvent) => {
      if (!isEditMode) return;
      e.stopPropagation();
      e.preventDefault();
      resizing.current = side;
      resizeStartX.current = e.clientX;
      origStart.current = startTs;
      origEnd.current = endTs;

      const onMove = (ev: MouseEvent) => {
        const dx = ev.clientX - resizeStartX.current;
        const dtMs = (dx / dayWidth) * MS_PER_DAY;
        if (resizing.current === "right") {
          const newEnd = snapToHour(origEnd.current + dtMs);
          if (newEnd >= origStart.current + MS_PER_HOUR) {
            updateTask(task.id, { end: new Date(newEnd) });
          }
        } else {
          const newStart = snapToHour(origStart.current + dtMs);
          if (newStart <= origEnd.current - MS_PER_HOUR) {
            updateTask(task.id, { start: new Date(newStart) });
          }
        }
      };

      const onUp = () => {
        resizing.current = null;
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
      };

      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
      document.body.style.cursor = side === "right" ? "e-resize" : "w-resize";
      document.body.style.userSelect = "none";
    },
    [isEditMode, startTs, endTs, dayWidth, task.id, updateTask],
  );

  const handleDoubleClick = useCallback(
    (e: React.MouseEvent) => {
      if (!isEditMode) return;
      e.stopPropagation();
      e.preventDefault();
      openTaskFormModal({ mode: "edit", taskId: task.id });
    },
    [isEditMode, openTaskFormModal, task.id],
  );

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
    [openContextMenu, task.id],
  );

  const priorityStyle = getPriorityStyle(task.priority);
  const statusStyle = getStatusStyle(task.status, baseColor);
  const HANDLE_W = 6;

  const barStyle: React.CSSProperties = {
    ...statusStyle,
    ...priorityStyle,
    borderRadius: 4,
    boxShadow: isDragging
      ? "0 4px 12px rgba(0,0,0,0.3)"
      : "0 1px 3px rgba(0,0,0,0.15)",
    userSelect: "none",
    overflow: "hidden",
    height: ROW_HEIGHT - 8,
    opacity: isDragging ? 0.5 : 1,
    transition: isDragging ? "none" : "box-shadow 0.15s ease",
    position: "relative",
    display: "flex",
    alignItems: "stretch",
  };

  const handleDotStyle: React.CSSProperties = {
    position: "absolute",
    top: "50%",
    transform: "translateY(-50%)",
    width: 2,
    height: 12,
    borderRadius: 1,
    backgroundColor: "rgba(255,255,255,0.5)",
  };

  const volumeLabel = `${task.volume_m.toLocaleString()}m`;

  return (
    <div
      ref={setNodeRef}
      data-draggable
      data-task-id={task.id}
      data-equipment-id={task.equipment_id}
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
        {/* 좌측 리사이즈 핸들 — dnd 없음 */}
        {isEditMode && (
          <div
            style={{
              width: HANDLE_W,
              flexShrink: 0,
              cursor: "w-resize",
              position: "relative",
              zIndex: 5,
            }}
            onMouseDown={(e) => handleResizeStart("left", e)}
          >
            <div style={{ ...handleDotStyle, left: 1 }} />
          </div>
        )}

        {/* 가운데 — dnd listeners 여기에만 */}
        <div
          {...listeners}
          {...attributes}
          style={{
            flex: 1,
            minWidth: 0,
            display: "flex",
            flexDirection: "column",
            justifyContent: "center",
            paddingLeft: 4,
            paddingRight: 4,
            gap: 1,
            cursor: isDragging ? "grabbing" : "grab",
          }}
        >
          <span
            className="text-white text-[10px] font-semibold truncate leading-tight"
            style={{ textShadow: "0 1px 2px rgba(0,0,0,0.4)" }}
          >
            {task.product}
            {task.spec ? ` ${task.spec}` : ""}
          </span>
          <span
            className="text-white/80 text-[9px] truncate leading-tight"
            style={{ textShadow: "0 1px 1px rgba(0,0,0,0.3)" }}
          >
            {task.color && `${task.color} `}
            {task.core_count > 0 && `${task.core_count}C `}
            {volumeLabel}
          </span>
        </div>

        {/* 우측 리사이즈 핸들 — dnd 없음 */}
        {isEditMode && (
          <div
            style={{
              width: HANDLE_W,
              flexShrink: 0,
              cursor: "e-resize",
              position: "relative",
              zIndex: 5,
            }}
            onMouseDown={(e) => handleResizeStart("right", e)}
          >
            <div style={{ ...handleDotStyle, right: 1 }} />
          </div>
        )}

        {/* 우선순위 배지 */}
        {task.priority !== "normal" && (
          <div
            className="absolute top-0.5 right-1 text-[8px] font-bold text-white bg-red-600 rounded px-0.5"
            style={{ lineHeight: "1.2", zIndex: 3 }}
          >
            {task.priority === "critical" ? "긴급" : "우선"}
          </div>
        )}
      </div>
    </div>
  );
}
