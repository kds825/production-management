"use client";

/**
 * UtilizationRow.tsx
 *
 * 각 설비 row 아래에 렌더링되는 얇은(20px) 일별 가동률 행.
 * 각 날짜 칸에 해당 설비에 배정된 작업의 합산 시간을 표시하고,
 * 색상 바로 가동률 수준을 시각화한다.
 *  - ≥80%: 적색(#DC2626)
 *  - 50~80%: 황색(#F59E0B)
 *  - <50%: 녹색(#16A34A)
 */

import { useMemo } from "react";
import { useTimelineContext } from "dnd-timeline";
import type { ScheduleTask } from "../types";
import { getUtilizationColor } from "../utils/colorCoding";

const WORKING_HOURS_PER_DAY = 8;
const ROW_HEIGHT = 20;

interface UtilizationRowProps {
  equipmentId: string;
  tasks: ScheduleTask[];
  range: { start: number; end: number };
}

interface DayCell {
  left: number;
  width: number;
  hours: number;
  label: string;
  color: string;
}

export function UtilizationRow({
  equipmentId,
  tasks,
  range,
}: UtilizationRowProps) {
  const { valueToPixels, sidebarWidth } = useTimelineContext();

  // 해당 설비에 배정된 작업만 필터링
  const eqTasks = useMemo(
    () => tasks.filter((t) => t.equipment_id === equipmentId),
    [tasks, equipmentId],
  );

  const cells = useMemo<DayCell[]>(() => {
    const result: DayCell[] = [];
    const cursor = new Date(range.start);
    cursor.setHours(0, 0, 0, 0);

    while (cursor.getTime() <= range.end) {
      const dayStart = cursor.getTime();
      const dayEnd = dayStart + 24 * 60 * 60 * 1000;

      // 이 날짜에 중첩되는 작업들의 시간 합산
      let totalHours = 0;
      for (const task of eqTasks) {
        const tStart = new Date(task.start).getTime();
        const tEnd = new Date(task.end).getTime();

        const overlapStart = Math.max(tStart, dayStart);
        const overlapEnd = Math.min(tEnd, dayEnd);
        if (overlapEnd > overlapStart) {
          totalHours += (overlapEnd - overlapStart) / (1000 * 60 * 60);
        }
      }

      const ratio = totalHours / WORKING_HOURS_PER_DAY;
      const clampedRatio = Math.min(ratio, 1);

      const left = valueToPixels(dayStart) + sidebarWidth;
      const right = valueToPixels(dayEnd) + sidebarWidth;
      const width = right - left;

      if (width > 0) {
        result.push({
          left,
          width,
          hours: totalHours,
          label: totalHours > 0 ? `${totalHours.toFixed(1)}h` : "",
          color: getUtilizationColor(clampedRatio),
        });
      }

      cursor.setDate(cursor.getDate() + 1);
    }

    return result;
  }, [eqTasks, range, valueToPixels, sidebarWidth]);

  return (
    <div
      style={{
        display: "flex",
        width: "100%",
        position: "relative",
        height: ROW_HEIGHT,
        backgroundColor: "#F9FAFB",
        borderBottom: "1px solid #E5E7EB",
      }}
    >
      {/* 사이드바 영역 대응 스페이서 — 수평 스크롤 시 sticky 고정 */}
      <div
        style={{
          position: "sticky",
          left: 0,
          width: sidebarWidth,
          flexShrink: 0,
          backgroundColor: "#F3F4F6",
          borderRight: "1px solid #E5E7EB",
          zIndex: 3,
          display: "flex",
          alignItems: "center",
          paddingLeft: 6,
        }}
      >
        <span style={{ fontSize: 8, color: "#9CA3AF", fontWeight: 500 }}>
          가동률
        </span>
      </div>

      {/* 날짜별 가동률 셀 (절대 위치 기반, sidebarWidth만큼 offset) */}
      <div style={{ position: "relative", flex: 1 }}>
        {cells.map((cell, idx) => (
          <div
            key={idx}
            style={{
              position: "absolute",
              // 셀의 left에서 sidebarWidth를 빼서 flex 자식 기준으로 정렬
              left: cell.left - sidebarWidth,
              width: cell.width,
              height: ROW_HEIGHT,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              borderLeft: "1px solid #E5E7EB",
              overflow: "hidden",
            }}
          >
            {/* 가동률 배경 바 */}
            {cell.hours > 0 && (
              <div
                style={{
                  position: "absolute",
                  bottom: 0,
                  left: 0,
                  width: `${Math.min((cell.hours / WORKING_HOURS_PER_DAY) * 100, 100)}%`,
                  height: "100%",
                  backgroundColor: cell.color,
                  opacity: 0.2,
                }}
              />
            )}
            {/* 시간 텍스트 */}
            {cell.label && (
              <span
                style={{
                  position: "relative",
                  fontSize: 8,
                  fontWeight: 600,
                  color: cell.color,
                  zIndex: 1,
                }}
              >
                {cell.label}
              </span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
