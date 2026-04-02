"use client";

import { useMemo, useCallback, useState, useEffect, useRef, memo } from "react";
import { useDroppable } from "@dnd-kit/core";

import { useScheduleStore } from "../store/scheduleStore";
import { EquipmentSidebar } from "./EquipmentSidebar";
import { GanttTaskBlock } from "./GanttTaskBlock";
import { TodayMarker } from "./TodayMarker";
import { useTimelineNavigation } from "../../../shared/hooks/useTimelineNavigation";
import type { ScheduleTask, Equipment, ViewFilterType } from "../types";
import {
  SIDEBAR_WIDTH,
  ROW_HEIGHT,
  DATE_HEADER_HEIGHT,
  DAY_WIDTH_MAP,
  timeToX,
  getTimelineWidth,
  isWeekend,
  generateDays,
} from "../utils/ganttUtils";

// ----- Droppable Row -----

/** 공유 선택 상태 — SchedulerView에서 관리하고 GanttRow로 내려줌 */
interface SharedSelection {
  equipmentId: string;
  startX: number;
  currentX: number;
}

interface GanttRowProps {
  equipment: Equipment;
  tasks: ScheduleTask[];
  rangeStart: number;
  rangeEnd: number;
  dayWidth: number;
  timelineWidth: number;
  /** 현재 공유 선택 상태 (다른 행에서 시작된 선택 포함) */
  sharedSelection: SharedSelection | null;
  /** 이 행에서 선택 시작 시 호출 */
  onSelectionStart: (equipmentId: string, x: number) => void;
  /** 선택 이동 시 호출 */
  onSelectionMove: (x: number) => void;
  /** 선택 종료 시 호출 */
  onSelectionEnd: () => void;
  /** 드래그 중인 아이템의 장비 그룹 (drag constraint용) */
  activeDragGroup?: string | null;
  /** 드래그 중인 아이템의 SQ (mm²) */
  activeDragSq?: number | null;
  /** 드래그 중인 아이템의 도체 재질 (CU | AL) */
  activeDragMaterial?: string | null;
}

/**
 * 설비별 Gantt 행. useDroppable로 드롭 영역을 설정한다.
 * 빈 영역 좌클릭 드래그로 시간 범위를 선택하고, 우클릭으로 작업 추가 가능.
 */
const GanttRow = memo(function GanttRow({
  equipment,
  tasks,
  rangeStart,
  rangeEnd,
  dayWidth,
  sharedSelection,
  onSelectionStart,
  onSelectionMove,
  onSelectionEnd,
  activeDragGroup,
  activeDragSq,
  activeDragMaterial,
}: GanttRowProps) {
  // 이 행이 드래그 그룹과 호환되는지 판단 (SQ 범위 + 재질 제한 포함)
  const isIncompatible = activeDragGroup
    ? !equipmentMatchesGroup(
        equipment,
        activeDragGroup,
        activeDragSq ?? undefined,
        activeDragMaterial ?? undefined,
      )
    : false;

  const { isOver, setNodeRef } = useDroppable({
    id: `row-${equipment.id}`,
    data: {
      type: "equipment-row",
      equipmentId: equipment.id,
    },
    // 비호환 행은 드롭 비활성화
    disabled: isIncompatible,
  });

  const openContextMenu = useScheduleStore((s) => s.openContextMenu);
  const openTaskFormModal = useScheduleStore((s) => s.openTaskFormModal);

  // 이 설비에 할당된 작업만 필터링 + 뷰포트 컬링 (rangeStart~rangeEnd 밖 작업 제외)
  const rowTasks = useMemo(() => {
    return tasks.filter((t) => {
      if (t.equipment_id !== equipment.id) return false;
      // 뷰포트 컬링: 작업의 시간 범위가 현재 타임라인 범위와 겹치는지 확인
      const tStart =
        t.start instanceof Date
          ? t.start.getTime()
          : new Date(t.start).getTime();
      const tEnd =
        t.end instanceof Date ? t.end.getTime() : new Date(t.end).getTime();
      // 작업이 뷰포트 밖에 있으면 렌더링하지 않음
      return tEnd >= rangeStart && tStart <= rangeEnd;
    });
  }, [tasks, equipment.id, rangeStart, rangeEnd]);

  // 이 행에 대한 선택 상태만 추출
  const selection =
    sharedSelection?.equipmentId === equipment.id ? sharedSelection : null;

  const timelineRef = useRef<HTMLDivElement>(null);
  const isDragging = useRef(false);

  const MS_PER_HOUR = 60 * 60 * 1000;
  const MS_PER_DAY_LOCAL = 24 * 60 * 60 * 1000;

  // X좌표 → 타임스탬프 (1시간 단위로 스냅)
  const xToTimestamp = useCallback(
    (clientX: number) => {
      const rect = timelineRef.current?.getBoundingClientRect();
      if (!rect) return rangeStart;
      const relX = clientX - rect.left;
      const rawTs = rangeStart + (relX / dayWidth) * MS_PER_DAY_LOCAL;
      // 1시간 단위로 스냅 (반내림)
      return Math.floor(rawTs / MS_PER_HOUR) * MS_PER_HOUR;
    },
    [rangeStart, dayWidth],
  );

  // X좌표를 1시간 스냅된 픽셀 위치로 변환
  const snapXToHour = useCallback(
    (clientX: number) => {
      const rect = timelineRef.current?.getBoundingClientRect();
      if (!rect) return 0;
      const relX = clientX - rect.left;
      const rawTs = rangeStart + (relX / dayWidth) * MS_PER_DAY_LOCAL;
      const snappedTs = Math.floor(rawTs / MS_PER_HOUR) * MS_PER_HOUR;
      return ((snappedTs - rangeStart) / MS_PER_DAY_LOCAL) * dayWidth;
    },
    [rangeStart, dayWidth],
  );

  const handleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      // 좌클릭만, task 블록 위에서는 무시
      if (e.button !== 0) return;
      const target = e.target as HTMLElement;
      if (target.closest("[data-draggable]")) return;
      isDragging.current = true;
      const x = snapXToHour(e.clientX);
      onSelectionStart(equipment.id, x);
    },
    [snapXToHour, onSelectionStart, equipment.id],
  );

  const handleMouseMove = useCallback(
    (e: React.MouseEvent) => {
      if (!isDragging.current) return;
      const snapped = snapXToHour(e.clientX);
      onSelectionMove(snapped);
    },
    [snapXToHour, onSelectionMove],
  );

  const handleMouseUp = useCallback(() => {
    isDragging.current = false;
    onSelectionEnd();
  }, [onSelectionEnd]);

  // 우클릭 → 컨텍스트 메뉴 (범위 선택이 있으면 해당 시간대로 prefill)
  const handleRowContextMenu = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      if (selection && Math.abs(selection.currentX - selection.startX) > 5) {
        // 선택 범위가 있으면 해당 시간으로 prefill (이미 1시간 스냅됨)
        const minX = Math.min(selection.startX, selection.currentX);
        const maxX = Math.max(selection.startX, selection.currentX);
        const rect = timelineRef.current?.getBoundingClientRect();
        if (rect) {
          const startTs = rangeStart + (minX / dayWidth) * MS_PER_DAY_LOCAL;
          const endTs = rangeStart + (maxX / dayWidth) * MS_PER_DAY_LOCAL;
          // 최소 1시간 보장
          const finalEndTs = Math.max(endTs, startTs + MS_PER_HOUR);
          openContextMenu({
            x: e.clientX,
            y: e.clientY,
            type: "empty",
            equipmentId: equipment.id,
            clickTime: new Date(startTs),
          });
          // TaskFormModal의 prefill로도 전달
          openTaskFormModal({
            mode: "create",
            prefill: {
              equipmentId: equipment.id,
              start: new Date(startTs),
              end: new Date(finalEndTs),
            },
          });
        }
        // 우클릭 후 선택 해제
        onSelectionStart(equipment.id, 0);
        onSelectionEnd();
      } else {
        // 범위 선택 없이 우클릭
        const clickTs = xToTimestamp(e.clientX);
        openContextMenu({
          x: e.clientX,
          y: e.clientY,
          type: "empty",
          equipmentId: equipment.id,
          clickTime: new Date(clickTs),
        });
        onSelectionEnd();
      }
    },
    [
      selection,
      openContextMenu,
      openTaskFormModal,
      equipment.id,
      rangeStart,
      dayWidth,
      xToTimestamp,
      onSelectionStart,
      onSelectionEnd,
    ],
  );

  // 선택 영역 스타일
  const selectionStyle = selection
    ? {
        left: Math.min(selection.startX, selection.currentX),
        width: Math.abs(selection.currentX - selection.startX),
      }
    : null;

  return (
    <div
      style={{
        display: "flex",
        width: "100%",
        borderBottom: "1px solid #E5E7EB",
        minHeight: ROW_HEIGHT,
      }}
    >
      {/* 사이드바: 설비 정보 — sticky */}
      <div
        style={{
          width: SIDEBAR_WIDTH,
          minWidth: SIDEBAR_WIDTH,
          flexShrink: 0,
          position: "sticky",
          left: 0,
          zIndex: 3,
          backgroundColor: "#FFFFFF",
          borderRight: "1px solid #E5E7EB",
          height: ROW_HEIGHT,
        }}
      >
        <EquipmentSidebar equipment={equipment} />
      </div>

      {/* 타임라인 영역 — droppable + 범위 선택 */}
      <div
        ref={(node) => {
          setNodeRef(node);
          (
            timelineRef as React.MutableRefObject<HTMLDivElement | null>
          ).current = node;
        }}
        style={{
          position: "relative",
          flex: 1,
          // overflow:hidden으로 내부 요소가 타임라인 밖으로 나가지 않도록
          overflow: "hidden",
          height: ROW_HEIGHT,
          backgroundColor: isIncompatible
            ? "rgba(107, 114, 128, 0.12)"
            : isOver
              ? "rgba(196, 18, 48, 0.06)"
              : "transparent",
          transition: "background-color 0.15s ease",
          cursor: isIncompatible ? "not-allowed" : "crosshair",
        }}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={() => {
          if (isDragging.current) {
            isDragging.current = false;
            onSelectionEnd();
          }
        }}
        onContextMenu={handleRowContextMenu}
      >
        {/* 비호환 행 그레이 오버레이 */}
        {isIncompatible && (
          <div
            style={{
              position: "absolute",
              inset: 0,
              backgroundColor: "rgba(156, 163, 175, 0.25)",
              pointerEvents: "none",
              zIndex: 2,
            }}
          />
        )}

        {/* 범위 선택 오버레이 */}
        {selectionStyle && selectionStyle.width > 3 && (
          <div
            style={{
              position: "absolute",
              top: 0,
              bottom: 0,
              left: selectionStyle.left,
              width: selectionStyle.width,
              backgroundColor: "rgba(196, 18, 48, 0.1)",
              border: "1px solid rgba(196, 18, 48, 0.3)",
              pointerEvents: "none",
              zIndex: 1,
            }}
          />
        )}
        {rowTasks.map((task) => (
          <GanttTaskBlock
            key={task.id}
            task={task}
            rangeStart={rangeStart}
            dayWidth={dayWidth}
          />
        ))}
      </div>
    </div>
  );
});

// ----- 날짜 헤더 -----

interface DateHeaderProps {
  rangeStart: number;
  rangeEnd: number;
  dayWidth: number;
  timelineWidth: number;
}

function DateHeader({
  rangeStart,
  rangeEnd,
  dayWidth,
  timelineWidth,
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
    // dayWidth(px per day) 기준으로 적절한 시간 간격 결정
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
    // hourStep 단위로 맞춤
    const remainder = startDate.getHours() % hourStep;
    if (remainder !== 0) {
      startDate.setHours(startDate.getHours() + (hourStep - remainder));
    }
    let ts = startDate.getTime();
    while (ts <= rangeEnd) {
      const d = new Date(ts);
      // 자정(0시)은 이미 날짜 레이블로 표시되므로 건너뜀
      if (d.getHours() !== 0) {
        const left = timeToX(ts, rangeStart, dayWidth) + SIDEBAR_WIDTH;
        markers.push({
          left,
          label: `${String(d.getHours()).padStart(2, "0")}:00`,
        });
      }
      ts += hourStep * MS_PER_HOUR;
    }
    return markers;
  }, [rangeStart, rangeEnd, dayWidth]);

  return (
    <div
      className="relative border-b border-gray-200 bg-white"
      style={{
        height: DATE_HEADER_HEIGHT,
        position: "sticky",
        top: 0,
        zIndex: 10,
        width: "100%",
      }}
    >
      {/* 사이드바 헤더 */}
      <div
        className="absolute top-0 bottom-0 flex items-center px-2 bg-gray-50 border-r border-gray-200"
        style={{
          width: SIDEBAR_WIDTH,
          left: 0,
          position: "sticky",
          zIndex: 11,
        }}
      >
        <span className="text-xs font-semibold text-gray-500">설비</span>
      </div>

      {/* 날짜 레이블 */}
      {days.map((day, idx) => {
        const left =
          timeToX(day.timestamp, rangeStart, dayWidth) + SIDEBAR_WIDTH;
        const weekend = isWeekend(day.date);
        return (
          <div
            key={idx}
            style={{
              position: "absolute",
              left,
              top: 0,
              bottom: 0,
              display: "flex",
              alignItems: "center",
              paddingLeft: 4,
              borderLeft: "1px solid #E5E7EB",
              width: dayWidth,
            }}
          >
            <span
              className="text-[10px] font-medium"
              style={{ color: weekend ? "#C41230" : "#4B5563" }}
            >
              {day.date.getMonth() + 1}/{day.date.getDate()}
            </span>
          </div>
        );
      })}

      {/* 시간 줌 레벨: 2시간 단위 눈금 */}
      {hourMarkers.map((marker, idx) => (
        <div
          key={`h-${idx}`}
          style={{
            position: "absolute",
            left: marker.left,
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
  );
}

// ----- 주말 배경 오버레이 -----

interface WeekendOverlayProps {
  rangeStart: number;
  rangeEnd: number;
  dayWidth: number;
  totalHeight: number;
}

function WeekendOverlay({
  rangeStart,
  rangeEnd,
  dayWidth,
  totalHeight,
}: WeekendOverlayProps) {
  const weekendCols = useMemo(() => {
    const cols: { left: number; width: number }[] = [];
    const days = generateDays(rangeStart, rangeEnd);
    for (const day of days) {
      if (isWeekend(day.date)) {
        // SIDEBAR_WIDTH 없이 타임라인 내 상대 좌표로 배치
        const left = timeToX(day.timestamp, rangeStart, dayWidth);
        cols.push({ left, width: dayWidth });
      }
    }
    return cols;
  }, [rangeStart, rangeEnd, dayWidth]);

  return (
    <>
      {weekendCols.map((col, idx) => (
        <div
          key={idx}
          style={{
            position: "absolute",
            top: 0,
            left: col.left,
            width: col.width,
            height: totalHeight,
            backgroundColor: "rgba(173, 216, 230, 0.35)",
            pointerEvents: "none",
            zIndex: 0,
          }}
        />
      ))}
    </>
  );
}

// ----- 수직 그리드 라인 -----

interface GridLinesProps {
  rangeStart: number;
  rangeEnd: number;
  dayWidth: number;
  totalHeight: number;
}

function GridLines({
  rangeStart,
  rangeEnd,
  dayWidth,
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
        const left = timeToX(day.timestamp, rangeStart, dayWidth);
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

// ----- 공정 순서 (클라이언트 보조 정렬) -----

const PROCESS_TYPE_ORDER: Record<string, number> = {
  신선: 0,
  연선: 1,
  저압절연: 2,
  고압절연: 2,
  "T/P": 3,
  연합: 3,
  저압시스: 4,
  고압시스: 4,
  HFCO시스: 4,
};

// ----- 필터 훅 -----

function useFilteredEquipment(
  equipment: Equipment[],
  filterType: ViewFilterType,
  filterValue: string[],
): Equipment[] {
  return useMemo(() => {
    // 신선 설비는 배치 미생성이므로 간트에서 숨김
    const withoutDrawing = equipment.filter((eq) => eq.process_type !== "신선");
    let result: Equipment[];

    if (filterType === "all") {
      result = withoutDrawing;
    } else if (filterType === "voltage") {
      result =
        filterValue.length === 0
          ? withoutDrawing
          : withoutDrawing.filter((eq) => filterValue.includes(eq.id));
    } else if (filterType === "process") {
      result =
        filterValue.length === 0
          ? withoutDrawing
          : withoutDrawing.filter((eq) =>
              filterValue.includes(eq.process_type),
            );
    } else {
      result = withoutDrawing;
    }

    // 공정 순서 보조 정렬: 백엔드에서 이미 정렬되어 오지만 안전망으로 유지
    return result.slice().sort((a, b) => {
      const orderA = PROCESS_TYPE_ORDER[a.process_type] ?? 99;
      const orderB = PROCESS_TYPE_ORDER[b.process_type] ?? 99;
      return orderA - orderB;
    });
  }, [equipment, filterType, filterValue]);
}

// ----- 장비 그룹 매칭 유틸 -----

/**
 * 장비가 주어진 드래그 그룹과 호환되는지 판단한다.
 * equipment_group: "연선" | "B100" | "A100" | "A120"
 *
 * taskSq / taskMaterial이 주어지면 설비의 SQ 범위 및 재질 제한도 검증한다.
 */
export function equipmentMatchesGroup(
  equipment: Equipment,
  group: string,
  taskSq?: number,
  taskMaterial?: string,
): boolean {
  const processType = equipment.process_type;
  const name = equipment.name.toUpperCase();

  // 그룹 매칭 — process_type은 한국어 (연선, 저압절연 등)
  let groupMatch: boolean;
  switch (group) {
    case "연선":
      groupMatch = processType === "연선" || processType === "신선";
      break;
    case "B100":
      groupMatch =
        name.includes("B100") ||
        processType === "저압절연" ||
        processType === "고압절연";
      break;
    case "A100":
      groupMatch = name.includes("A100") || name.includes("A150");
      break;
    case "A120":
      groupMatch = name.includes("A120") || name.includes("B150");
      break;
    default:
      groupMatch = true;
  }
  if (!groupMatch) return false;

  // SQ 범위 검증 — 설비에 range_min/range_max가 설정된 경우만 체크
  if (
    taskSq !== undefined &&
    taskSq > 0 &&
    equipment.range_min != null &&
    equipment.range_max != null
  ) {
    if (taskSq < equipment.range_min || taskSq > equipment.range_max) {
      return false;
    }
  }

  // 재질 검증 — 설비에 material_limit이 설정되고 "ALL"이 아닌 경우만 체크
  if (
    taskMaterial &&
    equipment.material_limit &&
    equipment.material_limit !== "ALL"
  ) {
    if (equipment.material_limit !== taskMaterial) {
      return false;
    }
  }

  return true;
}

// ----- 메인 SchedulerView -----

interface SchedulerViewProps {
  /** 드래그 중인 아이템의 장비 그룹 */
  activeDragGroup?: string | null;
  /** 드래그 중인 아이템의 SQ (mm²) */
  activeDragSq?: number | null;
  /** 드래그 중인 아이템의 도체 재질 (CU | AL) */
  activeDragMaterial?: string | null;
}

export function SchedulerView({
  activeDragGroup,
  activeDragSq,
  activeDragMaterial,
}: SchedulerViewProps = {}) {
  const equipment = useScheduleStore((s) => s.equipment);
  const tasks = useScheduleStore((s) => s.tasks);
  const viewFilter = useScheduleStore((s) => s.viewFilter);
  const zoomLevel = useScheduleStore((s) => s.zoomLevel);
  const dayWidthScale = useScheduleStore((s) => s.dayWidthScale);
  const range = useScheduleStore((s) => s.range);

  // 필터 적용
  const filteredEquipment = useFilteredEquipment(
    equipment,
    viewFilter.filterType,
    viewFilter.filterValue,
  );

  // store.range 사용
  const rangeStart = range.start;
  const rangeEnd = range.end;

  const outerRef = useRef<HTMLDivElement>(null);
  const [containerWidth, setContainerWidth] = useState(0);

  useEffect(() => {
    const el = outerRef.current;
    if (!el) return;
    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) setContainerWidth(entry.contentRect.width);
    });
    observer.observe(el);
    setContainerWidth(el.clientWidth);
    return () => observer.disconnect();
  }, []);

  const MS_PER_DAY = 24 * 60 * 60 * 1000;
  const totalDays = Math.max((rangeEnd - rangeStart) / MS_PER_DAY, 1);
  const availableWidth = Math.max(containerWidth - SIDEBAR_WIDTH, 100);
  // dayWidth: (줌 레벨 기본값 × scale) 와 fit-to-container 중 큰 값
  // - +/-로 scale을 키우면 픽셀 밀도 증가 → 가로 스크롤 등장
  // - range는 그대로 유지 → 작업 컬링 범위 불변, 스크롤로 전체 탐색 가능
  const dayWidth = Math.max(DAY_WIDTH_MAP[zoomLevel] * dayWidthScale, availableWidth / totalDays);
  const timelineWidth = dayWidth * totalDays;
  const totalContentWidth = SIDEBAR_WIDTH + timelineWidth;

  // 전체 높이 (설비 수 * 행 높이)
  const totalHeight = filteredEquipment.length * ROW_HEIGHT;

  // 패닝 훅 — overflow-auto 컨테이너에 연결
  const scrollContainerRef = useTimelineNavigation();

  // Issue 2: 범위 선택 상태를 SchedulerView 레벨에서 관리
  const [sharedSelection, setSharedSelection] =
    useState<SharedSelection | null>(null);

  const handleSelectionStart = useCallback((equipmentId: string, x: number) => {
    setSharedSelection({ equipmentId, startX: x, currentX: x });
  }, []);

  const handleSelectionMove = useCallback((x: number) => {
    setSharedSelection((prev) => (prev ? { ...prev, currentX: x } : null));
  }, []);

  const handleSelectionEnd = useCallback(() => {
    // 선택 유지 (우클릭 대기용) — 우클릭 핸들러에서 clear
  }, []);

  return (
    <div
      ref={outerRef}
      className="flex flex-col flex-1 overflow-hidden"
      style={{ backgroundColor: "#FAFAFA" }}
    >
      <div
        ref={scrollContainerRef}
        className="border border-gray-200 rounded-lg overflow-y-auto overflow-x-auto"
        style={{ position: "relative" }}
      >
        {/* 전체 콘텐츠 너비 — 이 div가 가로 스크롤 범위를 결정 */}
        <div style={{ width: totalContentWidth }}>

        {/* 날짜 헤더 */}
        <DateHeader
          rangeStart={rangeStart}
          rangeEnd={rangeEnd}
          dayWidth={dayWidth}
          timelineWidth={timelineWidth}
        />

        {/* 행 영역 (설비 + 작업 블록) */}
        <div
          style={{
            position: "relative",
            minHeight: totalHeight || 128,
          }}
        >
          {/* 주말 배경 — 사이드바 너비만큼 오프셋 */}
          <div
            style={{
              position: "absolute",
              top: 0,
              left: SIDEBAR_WIDTH,
              right: 0,
              bottom: 0,
              overflow: "hidden",
              pointerEvents: "none",
            }}
          >
            <WeekendOverlay
              rangeStart={rangeStart}
              rangeEnd={rangeEnd}
              dayWidth={dayWidth}
              totalHeight={Math.max(totalHeight, 128)}
            />

            {/* 수직 그리드 라인 */}
            <GridLines
              rangeStart={rangeStart}
              rangeEnd={rangeEnd}
              dayWidth={dayWidth}
              totalHeight={Math.max(totalHeight, 128)}
            />

            {/* 오늘 마커 */}
            <TodayMarker
              rangeStart={rangeStart}
              rangeEnd={rangeEnd}
              dayWidth={dayWidth}
              totalHeight={Math.max(totalHeight, 128)}
            />
          </div>

          {/* 설비 행 */}
          <div style={{ position: "relative", zIndex: 1 }}>
            {filteredEquipment.map((eq) => (
              <GanttRow
                key={eq.id}
                equipment={eq}
                tasks={tasks}
                rangeStart={rangeStart}
                rangeEnd={rangeEnd}
                dayWidth={dayWidth}
                timelineWidth={timelineWidth}
                sharedSelection={sharedSelection}
                onSelectionStart={handleSelectionStart}
                onSelectionMove={handleSelectionMove}
                onSelectionEnd={handleSelectionEnd}
                activeDragGroup={activeDragGroup}
                activeDragSq={activeDragSq}
                activeDragMaterial={activeDragMaterial}
              />
            ))}

            {filteredEquipment.length === 0 && (
              <div className="flex items-center justify-center h-32 text-sm text-gray-400">
                {equipment.length === 0
                  ? "설비 데이터를 불러오는 중..."
                  : "선택한 필터에 해당하는 설비가 없습니다"}
              </div>
            )}
          </div>
        </div>
        </div> {/* totalContentWidth wrapper */}
      </div>
    </div>
  );
}
