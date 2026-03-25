"use client";

/**
 * RangeSelector.tsx
 *
 * 타임라인 빈 영역에서 마우스 드래그로 시간 범위를 선택하는 오버레이 컴포넌트.
 * dnd-timeline 제거 이후 ganttUtils의 xToTime을 직접 사용한다.
 *
 * 현재 Custom Gantt 구현에서는 사용되지 않는다.
 * 향후 범위 선택 기능이 필요할 때 GanttRow 내부에 마운트하여 사용할 수 있다.
 */

import { useState, useCallback, useRef } from "react";
import { useScheduleStore } from "../store/scheduleStore";
import {
  xToTime,
  DAY_WIDTH_MAP,
  getCurrentMonthRange,
} from "../utils/ganttUtils";

interface SelectionState {
  startX: number;
  currentX: number;
}

interface RangeSelectorProps {
  equipmentId: string;
}

export function RangeSelector({ equipmentId }: RangeSelectorProps) {
  const zoomLevel = useScheduleStore((s) => s.zoomLevel);
  const openContextMenu = useScheduleStore((s) => s.openContextMenu);
  const openTaskFormModal = useScheduleStore((s) => s.openTaskFormModal);

  const containerRef = useRef<HTMLDivElement>(null);
  const [selection, setSelection] = useState<SelectionState | null>(null);
  const isDragging = useRef(false);

  const dayWidth = DAY_WIDTH_MAP[zoomLevel];
  const { start: rangeStartDate } = getCurrentMonthRange();
  const rangeStart = rangeStartDate.getTime();

  const toTimestamp = useCallback(
    (clientX: number): number => {
      const containerLeft =
        containerRef.current?.getBoundingClientRect().left ?? 0;
      const pixelOffset = clientX - containerLeft;
      return xToTime(pixelOffset, rangeStart, dayWidth);
    },
    [rangeStart, dayWidth],
  );

  const handleMouseDown = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    const target = e.target as HTMLElement;
    if (
      target.closest("[data-draggable]") ||
      target.closest("[data-task-item]")
    )
      return;

    isDragging.current = true;
    const containerLeft =
      containerRef.current?.getBoundingClientRect().left ?? 0;
    const startX = e.clientX - containerLeft;

    setSelection({ startX, currentX: startX });
    e.preventDefault();
  }, []);

  const handleMouseMove = useCallback(
    (e: React.MouseEvent<HTMLDivElement>) => {
      if (!isDragging.current || !selection) return;
      const containerLeft =
        containerRef.current?.getBoundingClientRect().left ?? 0;
      const currentX = e.clientX - containerLeft;
      setSelection((prev) => (prev ? { ...prev, currentX } : null));
    },
    [selection],
  );

  const handleMouseUp = useCallback(
    (e: React.MouseEvent<HTMLDivElement>) => {
      if (!isDragging.current || !selection) return;
      isDragging.current = false;

      const containerLeft =
        containerRef.current?.getBoundingClientRect().left ?? 0;
      const endX = e.clientX - containerLeft;
      const minX = Math.min(selection.startX, endX);
      const maxX = Math.max(selection.startX, endX);

      if (maxX - minX > 10) {
        const startTs = xToTime(minX, rangeStart, dayWidth);
        const endTs = xToTime(maxX, rangeStart, dayWidth);

        openContextMenu({
          x: e.clientX,
          y: e.clientY,
          type: "empty",
          equipmentId,
          clickTime: new Date(startTs),
        });

        openTaskFormModal({
          mode: "create",
          prefill: {
            equipmentId,
            start: new Date(startTs),
            end: new Date(endTs),
          },
        });
      }

      setSelection(null);
    },
    [
      selection,
      rangeStart,
      dayWidth,
      equipmentId,
      openContextMenu,
      openTaskFormModal,
    ],
  );

  const handleMouseLeave = useCallback(() => {
    if (isDragging.current) {
      isDragging.current = false;
      setSelection(null);
    }
  }, []);

  const overlayStyle = selection
    ? {
        left: Math.min(selection.startX, selection.currentX),
        width: Math.abs(selection.currentX - selection.startX),
      }
    : null;

  return (
    <div
      ref={containerRef}
      style={{
        position: "absolute",
        inset: 0,
        zIndex: 2,
        cursor: isDragging.current ? "col-resize" : "crosshair",
        pointerEvents: "none",
      }}
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onMouseLeave={handleMouseLeave}
    >
      {overlayStyle && (
        <div
          style={{
            position: "absolute",
            top: 0,
            bottom: 0,
            left: overlayStyle.left,
            width: overlayStyle.width,
            backgroundColor: "rgba(156, 163, 175, 0.25)",
            border: "1px solid rgba(107, 114, 128, 0.4)",
            pointerEvents: "none",
          }}
        />
      )}
    </div>
  );
}
