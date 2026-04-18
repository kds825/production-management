"use client";

import { useMemo } from "react";
import { useShallow } from "zustand/react/shallow";
import type { ScheduleTask, Equipment } from "../types";
import { useScheduleStore } from "../store/scheduleStore";
import { assignLanes, getLaneCount } from "../utils/laneAssign";
import { timeToXAdj, SIDEBAR_WIDTH, ROW_HEIGHT } from "../utils/ganttUtils";
// SchedulerView.tsx:41 `const LANE_HEIGHT = ROW_HEIGHT;` — alias. Overlay 도 동일 alias 사용해 Y 누적 일치.
import { LANE_HEIGHT } from "./SchedulerView";

const ARROW_COLOR = "#1F2937";
const ARROW_WIDTH = 1.5;
const ARROW_OPACITY = 0.85;
const ARROW_DASH = "4,3";
const MARKER_SIZE = 5;
const Y_STRAIGHT_TOL = 12;

interface ChainHighlightOverlayProps {
  tasks: readonly ScheduleTask[];
  visibleEquipment: readonly Equipment[];
  rangeStart: number;
  dayWidth: number;
  weekendWidth: number;
  totalWidth: number;
  totalHeight: number;
}

interface Endpoint {
  xLeft: number;
  xRight: number;
  yCenter: number;
}

export function ChainHighlightOverlay({
  tasks,
  visibleEquipment,
  rangeStart,
  dayWidth,
  weekendWidth,
  totalWidth,
  totalHeight,
}: ChainHighlightOverlayProps) {
  const { chainIds, arrows } = useScheduleStore(
    useShallow((s) => ({
      chainIds: s.selectedChainIds,
      arrows: s.selectedArrows,
    })),
  );

  // 조기 리턴 — 선택 없거나 체인 크기 1 이하면 오버레이 자체를 렌더하지 않음
  // chainIds === null 은 "선택 없음 OR orphan (R6)" 두 경우 모두 포함
  const coordMap = useMemo<Map<string, Endpoint>>(() => {
    if (chainIds === null || chainIds.size === 0) return new Map();

    const result = new Map<string, Endpoint>();
    let yCursor = 0;

    for (const eq of visibleEquipment) {
      const rowTasks = tasks.filter((t) => t.equipment_id === eq.id);
      const inputs = rowTasks.map((t) => ({
        id: t.id,
        start:
          t.start instanceof Date
            ? t.start.getTime()
            : new Date(t.start).getTime(),
        end:
          t.end instanceof Date ? t.end.getTime() : new Date(t.end).getTime(),
      }));
      const assigned = assignLanes(inputs);
      const laneCount = getLaneCount(assigned);
      const rowHeight = Math.max(ROW_HEIGHT, laneCount * LANE_HEIGHT);

      for (const item of assigned) {
        const tid = String(item.id);
        if (!chainIds.has(tid)) continue;
        const xLeft =
          SIDEBAR_WIDTH +
          timeToXAdj(item.start, rangeStart, dayWidth, weekendWidth);
        const xRight =
          SIDEBAR_WIDTH +
          timeToXAdj(item.end, rangeStart, dayWidth, weekendWidth);
        const yCenter = yCursor + item.lane * LANE_HEIGHT + LANE_HEIGHT / 2;
        result.set(tid, { xLeft, xRight, yCenter });
      }
      yCursor += rowHeight;
    }
    return result;
  }, [chainIds, tasks, visibleEquipment, rangeStart, dayWidth, weekendWidth]);

  const paths = useMemo(() => {
    if (chainIds === null) return [];
    const out: { key: string; d: string }[] = [];
    for (const edge of arrows) {
      const src = coordMap.get(edge.fromId);
      const dst = coordMap.get(edge.toId);
      if (!src || !dst) continue;
      // end-to-end 연결: 선공정 끝 → 후공정 끝 (시각적 정돈 — 후공정 시작점 합류 지점이 밀집해 지저분해지는 문제 회피)
      const x1 = src.xRight;
      const y1 = src.yCenter;
      const x2 = dst.xRight;
      const y2 = dst.yCenter;
      const d =
        Math.abs(y2 - y1) < Y_STRAIGHT_TOL
          ? `M${x1},${y1} L${x2},${y2}`
          : (() => {
              const midX = x2 > x1 ? (x1 + x2) / 2 : x1 + 20;
              return `M${x1},${y1} C${midX},${y1} ${midX},${y2} ${x2},${y2}`;
            })();
      out.push({ key: `${edge.fromId}→${edge.toId}`, d });
    }
    return out;
  }, [chainIds, arrows, coordMap]);

  if (chainIds === null || chainIds.size === 0) return null;

  return (
    <svg
      data-testid="chain-highlight-overlay"
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
          id="chain-arrow-marker"
          markerWidth={MARKER_SIZE}
          markerHeight={MARKER_SIZE}
          refX={MARKER_SIZE}
          refY={MARKER_SIZE / 2}
          orient="auto"
        >
          <path
            d={`M0,0 L0,${MARKER_SIZE} L${MARKER_SIZE},${MARKER_SIZE / 2} z`}
            fill={ARROW_COLOR}
          />
        </marker>
      </defs>
      {paths.map(({ key, d }) => (
        <path
          key={key}
          d={d}
          fill="none"
          stroke={ARROW_COLOR}
          strokeWidth={ARROW_WIDTH}
          strokeOpacity={ARROW_OPACITY}
          strokeDasharray={ARROW_DASH}
          markerEnd="url(#chain-arrow-marker)"
        />
      ))}
    </svg>
  );
}
