/**
 * GanttGrid — 간트 그리드의 정적 레이어 (날짜 헤더 + 주말 음영 + 수직 그리드 라인).
 *
 * 분리 이유 (Week 7 Task 7B.2):
 *   - 행(TaskRow)과 작업 블록(GanttTaskBlock)의 변경에는 영향받지 않는 "축"의 책임만 격리.
 *   - 각 컴포넌트가 자기 영역만 알면 되도록 props 를 좁힘 → memo 효율 향상.
 *
 * NOTE: 기존 SchedulerView 의 내부 helper 컴포넌트 (DateHeader/WeekendOverlay/GridLines) 의
 * 동작/스타일은 1:1 보존. 단지 모듈만 분리한 것이며 픽셀 레벨 회귀 없음을 목표로 한다.
 */
"use client";

import { useMemo } from "react";
import {
  SIDEBAR_WIDTH,
  DATE_HEADER_HEIGHT,
  timeToXAdj,
  isWeekend,
  generateDays,
} from "../../utils/ganttUtils";

interface DateHeaderProps {
  rangeStart: number;
  rangeEnd: number;
  dayWidth: number;
  weekendWidth: number;
  timelineWidth: number;
}

export function DateHeader({
  rangeStart,
  rangeEnd,
  dayWidth,
  weekendWidth,
}: DateHeaderProps) {
  // Issue 3: rangeStart의 자정(floor)부터 날짜를 생성하여 첫 레이블이 항상 보이도록 함
  const midnightRangeStart = useMemo(() => {
    const d = new Date(rangeStart);
    d.setHours(0, 0, 0, 0);
    return d.getTime();
  }, [rangeStart]);

  const days = useMemo(
    () => generateDays(midnightRangeStart, rangeEnd),
    [midnightRangeStart, rangeEnd],
  );

  // dayWidth 기반 동적 시간 눈금 — 하루 너비가 충분하면 시간 눈금 표시
  const hourMarkers = useMemo(() => {
    const MS_PER_HOUR = 60 * 60 * 1000;
    let hourStep: number;
    if (dayWidth >= 600)
      hourStep = 1; // 1시간 단위
    else if (dayWidth >= 300)
      hourStep = 2; // 2시간 단위
    else if (dayWidth >= 150)
      hourStep = 4; // 4시간 단위
    else if (dayWidth >= 80)
      hourStep = 8; // 8시간 단위
    else if (dayWidth >= 50)
      hourStep = 12; // 12시간 단위
    else return []; // 너무 좁으면 시간 눈금 안 보임

    const markers: { left: number; label: string }[] = [];
    const startDate = new Date(rangeStart);
    startDate.setMinutes(0, 0, 0);
    const remainder = startDate.getHours() % hourStep;
    if (remainder !== 0) {
      startDate.setHours(startDate.getHours() + (hourStep - remainder));
    }
    let ts = startDate.getTime();
    while (ts <= rangeEnd) {
      const d = new Date(ts);
      // 자정(0시)은 이미 날짜 레이블로 표시되므로 건너뜀
      if (d.getHours() !== 0) {
        const left =
          timeToXAdj(ts, rangeStart, dayWidth, weekendWidth) + SIDEBAR_WIDTH;
        markers.push({
          left,
          label: `${String(d.getHours()).padStart(2, "0")}:00`,
        });
      }
      ts += hourStep * MS_PER_HOUR;
    }
    return markers;
  }, [rangeStart, rangeEnd, dayWidth, weekendWidth]);

  return (
    <div
      style={{
        height: DATE_HEADER_HEIGHT,
        position: "sticky",
        top: 0,
        zIndex: 10,
        width: "100%",
        display: "flex",
        backgroundColor: "#FFFFFF",
        borderBottom: "1px solid #E5E7EB",
      }}
    >
      {/* 사이드바 헤더 — 가로 스크롤 시 좌측 고정 */}
      <div
        style={{
          width: SIDEBAR_WIDTH,
          minWidth: SIDEBAR_WIDTH,
          position: "sticky",
          left: 0,
          zIndex: 11,
          backgroundColor: "#F9FAFB",
          borderRight: "1px solid #E5E7EB",
          display: "flex",
          alignItems: "center",
          paddingLeft: 8,
        }}
      >
        <span className="text-xs font-semibold text-gray-500">설비</span>
      </div>

      {/* 날짜 레이블 영역 — 사이드바 오른쪽부터 클리핑 */}
      <div style={{ position: "relative", flex: 1, overflow: "hidden" }}>
        {days.map((day, idx) => {
          const left = timeToXAdj(
            day.timestamp,
            rangeStart,
            dayWidth,
            weekendWidth,
          );
          const weekend = isWeekend(day.date);
          const colWidth = weekend ? weekendWidth : dayWidth;
          const dow = day.date.getDay(); // 0=Sun, 1=Mon ... 6=Sat
          const DOW_KO = ["일", "월", "화", "수", "목", "금", "토"];
          const dowLabel = DOW_KO[dow];
          // ISO 주차: 월요일 기준
          const isMonday = dow === 1;
          const weekNumber = isMonday
            ? (() => {
                const d = new Date(day.date);
                d.setHours(0, 0, 0, 0);
                d.setDate(d.getDate() + 3 - ((d.getDay() + 6) % 7));
                const week1 = new Date(d.getFullYear(), 0, 4);
                return (
                  1 +
                  Math.round(
                    ((d.getTime() - week1.getTime()) / 86400000 -
                      3 +
                      ((week1.getDay() + 6) % 7)) /
                      7,
                  )
                );
              })()
            : null;

          return (
            <div
              key={idx}
              style={{
                position: "absolute",
                left,
                top: 0,
                bottom: 0,
                display: "flex",
                flexDirection: "column",
                justifyContent: "center",
                paddingLeft: weekend && colWidth < 20 ? 1 : 4,
                borderLeft: "1px solid #E5E7EB",
                width: colWidth,
                overflow: "hidden",
                // 주말 헤더 셀에 빗금 패턴 적용
                ...(weekend
                  ? {
                      backgroundColor: "rgba(200,200,200,0.15)",
                      backgroundImage:
                        "repeating-linear-gradient(135deg,rgba(160,160,160,0.2) 0px,rgba(160,160,160,0.2) 1.5px,transparent 1.5px,transparent 9px)",
                    }
                  : {}),
              }}
            >
              {/* 주말 접힘 상태면 텍스트 숨김 */}
              {!(weekend && colWidth < 20) && (
                <>
                  {/* 날짜 + 주차 (월요일에만) */}
                  <span
                    className="text-[10px] font-semibold leading-tight"
                    style={{ color: weekend ? "#C41230" : "#374151" }}
                  >
                    {day.date.getMonth() + 1}/{day.date.getDate()}
                    {weekNumber !== null && dayWidth >= 32 && (
                      <span
                        style={{
                          marginLeft: 3,
                          fontSize: 8,
                          fontWeight: 500,
                          color: "#9CA3AF",
                        }}
                      >
                        W{weekNumber}
                      </span>
                    )}
                  </span>
                  {/* 요일 */}
                  <span
                    className="text-[9px] leading-tight"
                    style={{ color: weekend ? "#E57373" : "#9CA3AF" }}
                  >
                    {dowLabel}
                  </span>
                </>
              )}
            </div>
          );
        })}

        {/* 시간 눈금 */}
        {hourMarkers.map((marker, idx) => (
          <div
            key={`h-${idx}`}
            style={{
              position: "absolute",
              left: marker.left - SIDEBAR_WIDTH,
              top: 0,
              bottom: 0,
              display: "flex",
              alignItems: "flex-end",
              paddingLeft: 3,
              paddingBottom: 2,
              borderLeft: "1px dashed #D1D5DB",
            }}
          >
            <span className="text-[8px]" style={{ color: "#9CA3AF" }}>
              {marker.label}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

interface WeekendOverlayProps {
  rangeStart: number;
  rangeEnd: number;
  dayWidth: number;
  weekendWidth: number;
  totalHeight: number;
}

export function WeekendOverlay({
  rangeStart,
  rangeEnd,
  dayWidth,
  weekendWidth,
  totalHeight,
}: WeekendOverlayProps) {
  const weekendCols = useMemo(() => {
    const cols: { left: number; width: number }[] = [];
    const days = generateDays(rangeStart, rangeEnd);
    for (const day of days) {
      if (isWeekend(day.date)) {
        // SIDEBAR_WIDTH 없이 타임라인 내 상대 좌표로 배치
        const left = timeToXAdj(
          day.timestamp,
          rangeStart,
          dayWidth,
          weekendWidth,
        );
        cols.push({ left, width: weekendWidth });
      }
    }
    return cols;
  }, [rangeStart, rangeEnd, dayWidth, weekendWidth]);

  return (
    <>
      {weekendCols.map((col, idx) => (
        <div
          key={idx}
          data-weekend="true"
          style={{
            position: "absolute",
            top: 0,
            left: col.left,
            width: col.width,
            height: totalHeight,
            // 연한 회색 베이스 + 대각선 빗금 — "비가동 구간" 표현
            backgroundColor: "rgba(200, 200, 200, 0.18)",
            backgroundImage:
              "repeating-linear-gradient(" +
              "135deg," +
              "rgba(160,160,160,0.22) 0px," +
              "rgba(160,160,160,0.22) 1.5px," +
              "transparent 1.5px," +
              "transparent 9px" +
              ")",
            pointerEvents: "none",
            zIndex: 0,
          }}
        />
      ))}
    </>
  );
}

interface GridLinesProps {
  rangeStart: number;
  rangeEnd: number;
  dayWidth: number;
  weekendWidth: number;
  totalHeight: number;
}

export function GridLines({
  rangeStart,
  rangeEnd,
  dayWidth,
  weekendWidth,
  totalHeight,
}: GridLinesProps) {
  const days = useMemo(
    () => generateDays(rangeStart, rangeEnd),
    [rangeStart, rangeEnd],
  );

  return (
    <>
      {days.map((day, idx) => {
        // SIDEBAR_WIDTH 없이 타임라인 내 상대 좌표로 배치
        const left = timeToXAdj(
          day.timestamp,
          rangeStart,
          dayWidth,
          weekendWidth,
        );
        return (
          <div
            key={idx}
            style={{
              position: "absolute",
              top: 0,
              left,
              width: 1,
              height: totalHeight,
              backgroundColor: "#E5E7EB",
              pointerEvents: "none",
              zIndex: 0,
            }}
          />
        );
      })}
    </>
  );
}
