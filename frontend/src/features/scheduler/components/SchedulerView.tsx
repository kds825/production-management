"use client";

import { useMemo, useState, useCallback, useRef } from "react";
import {
  TimelineContext,
  useTimelineContext,
  useRow,
  groupItemsToSubrows,
  useWheelStrategy,
  type OnRangeChanged,
  type ResizeEndEvent,
  type DragEndEvent,
} from "dnd-timeline";

import { useScheduleStore } from "../store/scheduleStore";
import { useDragHandlers } from "../hooks/useDragHandlers";
import { EquipmentSidebar } from "./EquipmentSidebar";
import { TaskItem } from "./TaskItem";
import { DeadlineMarker } from "./DeadlineMarker";
import { DependencyArrows } from "./DependencyArrows";
import { UtilizationRow } from "./UtilizationRow";
import type {
  ScheduleTask,
  TimelineItem,
  TimelineRow,
  ViewFilterType,
} from "../types";

// ----- 상수 -----
const SIDEBAR_WIDTH = 160; // px
const ROW_HEIGHT = 44; // px

/** 토요일(6) / 일요일(0) 여부 */
function isWeekend(date: Date): boolean {
  const d = date.getDay();
  return d === 0 || d === 6;
}

/** 현재 달의 시작(start)/끝(end) Date 반환 */
function getCurrentMonthRange(): { start: Date; end: Date } {
  const now = new Date();
  const start = new Date(now.getFullYear(), now.getMonth(), 1, 0, 0, 0, 0);
  const end = new Date(
    now.getFullYear(),
    now.getMonth() + 1,
    0,
    23,
    59,
    59,
    999,
  );
  return { start, end };
}

// ----- Row 컴포넌트 (dnd-timeline의 useRow 사용) -----
interface TimelineRowContainerProps {
  row: TimelineRow;
  items: TimelineItem[];
  range: { start: number; end: number };
}

function TimelineRowContainer({
  row,
  items,
  range,
}: TimelineRowContainerProps) {
  const { setNodeRef, rowStyle, rowWrapperStyle, rowSidebarStyle } = useRow({
    id: row.id,
  });

  const equipment = useScheduleStore((s) =>
    s.equipment.find((eq) => eq.id === row.id),
  );
  const openContextMenu = useScheduleStore((s) => s.openContextMenu);

  // 이 row에 속한 items만 필터링 후 서브로우 그룹화
  const rowItems = useMemo(
    () => items.filter((item) => item.rowId === row.id),
    [items, row.id],
  );

  const groupedSubrows = useMemo(
    () => groupItemsToSubrows(rowItems, range),
    [rowItems, range],
  );

  const subrows = groupedSubrows[row.id] ?? [];

  // 빈 타임라인 영역 우클릭 → 빈 영역 컨텍스트 메뉴
  const handleRowContextMenu = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      openContextMenu({
        x: e.clientX,
        y: e.clientY,
        type: "empty",
        equipmentId: row.id,
      });
    },
    [openContextMenu, row.id],
  );

  return (
    <div
      style={{
        ...rowWrapperStyle,
        // dnd-timeline의 기본값 "inline-flex"를 "flex"로 덮어써서
        // 각 row가 수평 전체 너비를 차지하도록 강제한다 (Gantt Y축 레이아웃)
        display: "flex",
        width: "100%",
        borderBottom: "1px solid #E5E7EB",
        minHeight: ROW_HEIGHT,
      }}
    >
      {/* 사이드바: 설비 정보 — position: sticky로 수평 스크롤 시에도 고정 */}
      <div
        style={{
          ...rowSidebarStyle,
          height: Math.max(ROW_HEIGHT, (subrows.length || 1) * ROW_HEIGHT),
          position: "sticky",
          left: 0,
          zIndex: 3,
          backgroundColor: "#FFFFFF",
          borderRight: "1px solid #E5E7EB",
          flexShrink: 0,
        }}
      >
        {equipment ? (
          <EquipmentSidebar equipment={equipment} />
        ) : (
          <div className="flex items-center px-2 h-full">
            <span className="text-xs text-gray-400 truncate">{row.id}</span>
          </div>
        )}
      </div>

      {/* 타임라인 영역 */}
      <div
        ref={setNodeRef}
        style={{
          ...rowStyle,
          height: Math.max(ROW_HEIGHT, (subrows.length || 1) * ROW_HEIGHT),
          position: "relative",
        }}
        onContextMenu={handleRowContextMenu}
      >
        {subrows.length > 0
          ? subrows.map((subrow, idx) => (
              <div
                key={`${row.id}-subrow-${idx}`}
                style={{
                  position: "absolute",
                  top: idx * ROW_HEIGHT,
                  left: 0,
                  right: 0,
                  height: ROW_HEIGHT,
                }}
              >
                {subrow.map((item) => (
                  <TaskItem key={item.id} item={item} />
                ))}
              </div>
            ))
          : null}
      </div>
    </div>
  );
}

// ----- 주말 배경 오버레이 -----
function WeekendOverlay({ range }: { range: { start: number; end: number } }) {
  const { valueToPixels, sidebarWidth } = useTimelineContext();

  const weekendColumns = useMemo(() => {
    const cols: { left: number; width: number }[] = [];
    const start = new Date(range.start);
    const end = new Date(range.end);

    const cursor = new Date(start);
    cursor.setHours(0, 0, 0, 0);

    while (cursor <= end) {
      if (isWeekend(cursor)) {
        const dayStart = cursor.getTime();
        const dayEnd = dayStart + 24 * 60 * 60 * 1000;

        const left =
          valueToPixels(Math.max(dayStart, range.start)) + sidebarWidth;
        const right = valueToPixels(Math.min(dayEnd, range.end)) + sidebarWidth;

        cols.push({ left, width: right - left });
      }
      cursor.setDate(cursor.getDate() + 1);
    }

    return cols;
  }, [range, valueToPixels, sidebarWidth]);

  return (
    <>
      {weekendColumns.map((col, idx) => (
        <div
          key={idx}
          style={{
            position: "absolute",
            top: 0,
            bottom: 0,
            left: col.left,
            width: col.width,
            backgroundColor: "#F3F4F6",
            pointerEvents: "none",
            zIndex: 0,
          }}
        />
      ))}
    </>
  );
}

// ----- 날짜 헤더 -----
function DateHeader({ range }: { range: { start: number; end: number } }) {
  const { valueToPixels, sidebarWidth } = useTimelineContext();

  const days = useMemo(() => {
    const result: { label: string; left: number; isWeekend: boolean }[] = [];
    const cursor = new Date(range.start);
    cursor.setHours(0, 0, 0, 0);

    while (cursor.getTime() <= range.end) {
      const ts = cursor.getTime();
      const left = valueToPixels(ts) + sidebarWidth;
      result.push({
        label: `${cursor.getMonth() + 1}/${cursor.getDate()}`,
        left,
        isWeekend: isWeekend(cursor),
      });
      cursor.setDate(cursor.getDate() + 1);
    }
    return result;
  }, [range, valueToPixels, sidebarWidth]);

  return (
    <div
      className="relative border-b border-gray-200 bg-white"
      style={{ height: 32, position: "sticky", top: 56, zIndex: 10 }}
    >
      {/* 사이드바 헤더 */}
      <div
        className="absolute left-0 top-0 bottom-0 flex items-center px-2 bg-gray-50 border-r border-gray-200"
        style={{ width: sidebarWidth }}
      >
        <span className="text-xs font-semibold text-gray-500">설비</span>
      </div>

      {/* 날짜 레이블 */}
      {days.map((day, idx) => (
        <div
          key={idx}
          style={{
            position: "absolute",
            left: day.left,
            top: 0,
            bottom: 0,
            display: "flex",
            alignItems: "center",
            paddingLeft: 4,
            borderLeft: "1px solid #E5E7EB",
          }}
        >
          <span
            className="text-[9px]"
            style={{ color: day.isWeekend ? "#C41230" : "#6B7280" }}
          >
            {day.label}
          </span>
        </div>
      ))}
    </div>
  );
}

// ----- 타임라인 내부 (useTimelineContext 사용) -----
interface TimelineInnerProps {
  rows: TimelineRow[];
  items: TimelineItem[];
  tasks: ScheduleTask[];
  range: { start: number; end: number };
}

function TimelineInner({ rows, items, tasks, range }: TimelineInnerProps) {
  const { setTimelineRef, style } = useTimelineContext();
  const containerRef = useRef<HTMLDivElement>(null);

  // 안정적인 ref 콜백 — 무한 렌더링 방지
  const combinedRef = useCallback(
    (el: HTMLDivElement | null) => {
      containerRef.current = el;
      (setTimelineRef as (el: HTMLElement | null) => void)(el);
    },
    [setTimelineRef],
  );

  // SVG 오버레이 크기는 컨테이너에서 동적으로 가져온다
  const containerWidth = containerRef.current?.scrollWidth ?? 2000;
  const totalHeight = rows.length * ROW_HEIGHT;

  return (
    <>
      <DateHeader range={range} />
      <div
        ref={combinedRef}
        style={{
          ...style,
          position: "relative",
        }}
        className="border border-gray-200 rounded-b-lg overflow-x-auto overflow-y-hidden"
      >
        {/* 주말 배경 */}
        <WeekendOverlay range={range} />

        {/* 행 목록 */}
        <div style={{ position: "relative", zIndex: 1 }}>
          {rows.map((row) => (
            <div key={row.id}>
              <TimelineRowContainer row={row} items={items} range={range} />
              {/* 설비별 일별 가동률 행 */}
              <UtilizationRow
                equipmentId={row.id}
                tasks={tasks}
                range={range}
              />
            </div>
          ))}

          {rows.length === 0 && (
            <div className="flex items-center justify-center h-32 text-sm text-gray-400">
              설비 데이터를 불러오는 중...
            </div>
          )}
        </div>

        {/* 납기일 마커 오버레이 */}
        <DeadlineMarker tasks={tasks} range={range} />

        {/* 의존 관계 화살표 오버레이 */}
        <DependencyArrows
          tasks={tasks}
          rows={rows}
          totalHeight={totalHeight}
          totalWidth={containerWidth}
        />
      </div>
    </>
  );
}

/** 뷰 필터에 따라 표시할 rows 계산 */
function useFilteredRows(
  rows: TimelineRow[],
  filterType: ViewFilterType,
  filterValue: string[],
  equipment: { id: string; process_type: string }[],
): TimelineRow[] {
  return useMemo(() => {
    if (filterType === "all") return rows;

    if (filterType === "voltage") {
      // filterValue에 포함된 equipment id만 표시
      if (filterValue.length === 0) return rows;
      return rows.filter((r) => filterValue.includes(r.id));
    }

    if (filterType === "process") {
      // 선택된 공정만 표시 (선택 없으면 전체)
      if (filterValue.length === 0) return rows;
      const eqMap = new Map(equipment.map((e) => [e.id, e.process_type]));
      return rows.filter((r) => {
        const pt = eqMap.get(r.id) ?? "";
        return filterValue.includes(pt);
      });
    }

    return rows;
  }, [rows, filterType, filterValue, equipment]);
}

// ----- 메인 SchedulerView -----
export function SchedulerView() {
  const rows = useScheduleStore((s) => s.rows);
  const items = useScheduleStore((s) => s.items);
  const tasks = useScheduleStore((s) => s.tasks);
  const viewFilter = useScheduleStore((s) => s.viewFilter);
  const equipment = useScheduleStore((s) => s.equipment);

  // 필터 적용된 rows
  const filteredRows = useFilteredRows(
    rows,
    viewFilter.filterType,
    viewFilter.filterValue,
    equipment,
  );

  const defaultRange = useMemo(() => {
    const { start, end } = getCurrentMonthRange();
    return { start: start.getTime(), end: end.getTime() };
  }, []);

  const [range, setRange] = useState(defaultRange);

  const handleRangeChanged: OnRangeChanged = useCallback((updateFn) => {
    setRange((prev) => updateFn(prev));
  }, []);

  const { onDragEnd: handleDragEnd, onResizeEnd: handleResizeEnd } =
    useDragHandlers();

  // onResizeEnd는 TimelineContext에 직접 전달 (필수)
  const onResizeEnd = useCallback(
    (event: ResizeEndEvent) => {
      handleResizeEnd(event);
    },
    [handleResizeEnd],
  );

  // onDragEnd는 TimelineContext의 DndContext에 전달
  const onDragEnd = useCallback(
    (event: DragEndEvent) => {
      handleDragEnd(event);
    },
    [handleDragEnd],
  );

  return (
    <div
      className="flex flex-col flex-1 overflow-auto"
      style={{ backgroundColor: "#FAFAFA" }}
    >
      <TimelineContext
        range={range}
        onRangeChanged={handleRangeChanged}
        onResizeEnd={onResizeEnd}
        onDragEnd={onDragEnd}
        usePanStrategy={useWheelStrategy}
        sidebarWidth={SIDEBAR_WIDTH}
        resizeHandleWidth={8}
      >
        <TimelineInner
          rows={filteredRows}
          items={items}
          tasks={tasks}
          range={range}
        />
      </TimelineContext>
    </div>
  );
}
