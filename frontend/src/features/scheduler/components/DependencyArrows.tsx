"use client";

/**
 * DependencyArrows.tsx
 *
 * 선행 작업(predecessor) -> 후행 작업(successor) 간 의존 관계를
 * SVG 화살표 오버레이로 렌더링한다.
 *
 * - 정상 관계: 회색 화살표(#9CA3AF)
 * - 순서 위반(선행이 끝나기 전 후행 시작): 적색 화살표(#DC2626)
 * - ganttUtils의 timeToX를 사용하여 타임스탬프 -> 픽셀 좌표를 계산한다.
 */

import { useMemo } from "react";
import type { ScheduleTask, Equipment } from "../types";
import { timeToX, SIDEBAR_WIDTH, ROW_HEIGHT } from "../utils/ganttUtils";

const ARROW_COLOR_NORMAL = "#9CA3AF";
const ARROW_COLOR_VIOLATION = "#DC2626";
const MARKER_SIZE = 6;

interface DependencyArrowsProps {
  tasks: ScheduleTask[];
  equipment: Equipment[];
  rangeStart: number;
  dayWidth: number;
  totalHeight: number;
  totalWidth: number;
}

interface ArrowInfo {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  color: string;
  key: string;
}

export function DependencyArrows({
  tasks,
  equipment,
  rangeStart,
  dayWidth,
  totalHeight,
  totalWidth,
}: DependencyArrowsProps) {
  const taskMap = useMemo(() => new Map(tasks.map((t) => [t.id, t])), [tasks]);

  // 설비 id -> y 중심 좌표 매핑
  const equipYMap = useMemo(() => {
    const map = new Map<string, number>();
    equipment.forEach((eq, idx) => {
      map.set(eq.id, idx * ROW_HEIGHT + ROW_HEIGHT / 2);
    });
    return map;
  }, [equipment]);

  const arrows = useMemo<ArrowInfo[]>(() => {
    const result: ArrowInfo[] = [];

    for (const task of tasks) {
      if (task.predecessors.length === 0) continue;

      const succStart = new Date(task.start).getTime();
      const succY = equipYMap.get(task.equipment_id);
      if (succY === undefined) continue;

      const x2 = timeToX(succStart, rangeStart, dayWidth) + SIDEBAR_WIDTH;
      const y2 = succY;

      for (const predId of task.predecessors) {
        const pred = taskMap.get(predId);
        if (!pred) continue;

        const predEnd = new Date(pred.end).getTime();
        const predY = equipYMap.get(pred.equipment_id);
        if (predY === undefined) continue;

        const x1 = timeToX(predEnd, rangeStart, dayWidth) + SIDEBAR_WIDTH;
        const y1 = predY;

        // 선행 완료 전 후행 시작 -> 위반
        const isViolation = predEnd > succStart;
        const color = isViolation ? ARROW_COLOR_VIOLATION : ARROW_COLOR_NORMAL;

        result.push({ x1, y1, x2, y2, color, key: `${predId}->${task.id}` });
      }
    }

    return result;
  }, [tasks, taskMap, equipYMap, rangeStart, dayWidth]);

  if (arrows.length === 0) return null;

  return (
    <svg
      style={{
        position: "absolute",
        top: 0,
        left: 0,
        width: totalWidth,
        height: totalHeight,
        pointerEvents: "none",
        zIndex: 4,
        overflow: "visible",
      }}
    >
      <defs>
        <marker
          id="arrow-normal"
          markerWidth={MARKER_SIZE}
          markerHeight={MARKER_SIZE}
          refX={MARKER_SIZE - 1}
          refY={MARKER_SIZE / 2}
          orient="auto"
        >
          <path
            d={`M0,0 L0,${MARKER_SIZE} L${MARKER_SIZE},${MARKER_SIZE / 2} z`}
            fill={ARROW_COLOR_NORMAL}
          />
        </marker>
        <marker
          id="arrow-violation"
          markerWidth={MARKER_SIZE}
          markerHeight={MARKER_SIZE}
          refX={MARKER_SIZE - 1}
          refY={MARKER_SIZE / 2}
          orient="auto"
        >
          <path
            d={`M0,0 L0,${MARKER_SIZE} L${MARKER_SIZE},${MARKER_SIZE / 2} z`}
            fill={ARROW_COLOR_VIOLATION}
          />
        </marker>
      </defs>

      {arrows.map(({ x1, y1, x2, y2, color, key }) => {
        const markerId =
          color === ARROW_COLOR_VIOLATION ? "arrow-violation" : "arrow-normal";
        // 베지어 곡선으로 자연스러운 화살표 렌더링
        const midX = (x1 + x2) / 2;
        const d = `M${x1},${y1} C${midX},${y1} ${midX},${y2} ${x2},${y2}`;

        return (
          <path
            key={key}
            d={d}
            fill="none"
            stroke={color}
            strokeWidth={1.5}
            strokeOpacity={0.75}
            markerEnd={`url(#${markerId})`}
          />
        );
      })}
    </svg>
  );
}
