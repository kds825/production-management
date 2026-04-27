"use client";

/**
 * DeadlineMarker.tsx
 *
 * 타임라인 위에 납기일 수직 점선을 오버레이로 렌더링한다.
 * - 작업이 납기일을 초과하는 경우 적색 (var(--color-danger)), 그 외 녹색 (var(--status-success)).
 * - "납기" 레이블을 상단에 표시한다.
 * - ganttUtils의 timeToX를 사용하여 타임스탬프 -> 픽셀 위치를 계산한다.
 */

import { useMemo } from "react";
import type { ScheduleTask } from "../types";
import { timeToX, SIDEBAR_WIDTH } from "../utils/ganttUtils";

interface DeadlineMarkerProps {
  tasks: ScheduleTask[];
  rangeStart: number;
  rangeEnd: number;
  dayWidth: number;
  totalHeight: number;
}

interface MarkerInfo {
  left: number;
  color: string;
  label: string;
  taskId: string;
}

export function DeadlineMarker({
  tasks,
  rangeStart,
  rangeEnd,
  dayWidth,
  totalHeight,
}: DeadlineMarkerProps) {
  const markers = useMemo<MarkerInfo[]>(() => {
    const result: MarkerInfo[] = [];

    for (const task of tasks) {
      if (!task.delivery_date) continue;

      const deliveryTs = new Date(task.delivery_date).getTime();

      // 타임라인 범위를 벗어난 납기일은 렌더링하지 않음
      if (deliveryTs < rangeStart || deliveryTs > rangeEnd) continue;

      const taskEnd = new Date(task.end).getTime();
      // 납기일 초과 여부에 따라 색상 결정
      const color =
        taskEnd > deliveryTs ? "var(--color-danger)" : "var(--status-success)";

      const left = timeToX(deliveryTs, rangeStart, dayWidth) + SIDEBAR_WIDTH;

      result.push({ left, color, label: "납기", taskId: task.id });
    }

    return result;
  }, [tasks, rangeStart, rangeEnd, dayWidth]);

  if (markers.length === 0) return null;

  return (
    <>
      {markers.map((marker) => (
        <div
          key={marker.taskId}
          style={{
            position: "absolute",
            left: marker.left,
            top: 0,
            height: totalHeight,
            width: 0,
            pointerEvents: "none",
            zIndex: 5,
          }}
        >
          {/* 수직 점선 */}
          <div
            style={{
              position: "absolute",
              top: 0,
              bottom: 0,
              left: 0,
              width: 2,
              borderLeft: `2px dashed ${marker.color}`,
            }}
          />
          {/* 상단 레이블 */}
          <div
            style={{
              position: "absolute",
              top: 2,
              left: 3,
              backgroundColor: marker.color,
              color: "var(--color-text-inverse)",
              fontSize: 9,
              fontWeight: 700,
              padding: "1px 3px",
              borderRadius: 2,
              whiteSpace: "nowrap",
            }}
          >
            {marker.label}
          </div>
        </div>
      ))}
    </>
  );
}
