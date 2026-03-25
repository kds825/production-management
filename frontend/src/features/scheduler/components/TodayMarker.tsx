"use client";

import { useMemo } from "react";
import { timeToX } from "../utils/ganttUtils";

interface TodayMarkerProps {
  rangeStart: number;
  rangeEnd: number;
  dayWidth: number;
  totalHeight: number;
}

/**
 * TodayMarker
 *
 * 오늘 날짜 위치에 1px 세로 빨간 선을 표시한다.
 * 범위 밖이면 렌더링하지 않는다.
 */
export function TodayMarker({
  rangeStart,
  rangeEnd,
  dayWidth,
  totalHeight,
}: TodayMarkerProps) {
  const todayX = useMemo(() => {
    const now = new Date();
    now.setHours(0, 0, 0, 0);
    const ts = now.getTime();
    if (ts < rangeStart || ts > rangeEnd) return null;
    return timeToX(ts, rangeStart, dayWidth);
  }, [rangeStart, rangeEnd, dayWidth]);

  if (todayX === null) return null;

  return (
    <div
      style={{
        position: "absolute",
        top: 0,
        left: todayX,
        width: 1,
        height: totalHeight,
        backgroundColor: "#C41230",
        pointerEvents: "none",
        zIndex: 5,
      }}
    />
  );
}
