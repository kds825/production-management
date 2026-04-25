"use client";

import {
  useMemo,
  useCallback,
  useState,
  useEffect,
  useRef,
  memo,
  Fragment,
} from "react";
import { useDroppable } from "@dnd-kit/core";

import { useScheduleStore } from "../store/scheduleStore";
import { EquipmentSidebar } from "./EquipmentSidebar";
import { GanttTaskBlock } from "./GanttTaskBlock";
import { TodayMarker } from "./TodayMarker";
import { ChainHighlightOverlay } from "./ChainHighlightOverlay";
import { DecisionCard } from "./DecisionCard";
import { useTimelineNavigation } from "../../../shared/hooks/useTimelineNavigation";
import type { ScheduleTask, Equipment, ViewFilterType } from "../types";
import type { CascadePreviewResponse, PushEntry } from "../api/cascade.types";
import { buildDiffIndex, taskStableKey } from "../utils/diffIndex";
import {
  SIDEBAR_WIDTH,
  ROW_HEIGHT,
  DATE_HEADER_HEIGHT,
  DAY_WIDTH_MAP,
  timeToXAdj,
  xToTimeAdj,
  timelineWidthAdj,
  isWeekend,
  generateDays,
} from "../utils/ganttUtils";
import { assignLanes, getLaneCount } from "../utils/laneAssign";

const WEEKEND_COLLAPSED_WIDTH = 8;

/**
 * 한 lane(Y축 칸) 의 높이. ROW_HEIGHT 와 동일하게 두어
 * 단일 lane(겹침 없음) 인 경우 기존 레이아웃과 동일하게 보이고,
 * 겹치는 블록이 있으면 lane 개수만큼 row 가 세로로 늘어난다.
 */
export const LANE_HEIGHT = ROW_HEIGHT;

/**
 * Task 22 — cascade preview 응답에서 주어진 task 에 대한 제안(push 또는 pull) 을 찾는다.
 * push/pull 은 같은 `PushEntry` 구조를 공유하므로 단일 타입으로 반환.
 *
 * 왜 helper 로 분리: SchedulerView 내부 loop 에서 불필요한 배열 순회 중복을 방지하고
 * 단위 테스트 (ghost-overlay) 에서 격리된 계약 검증이 가능해짐.
 */
export function getProposalFor(
  taskId: string,
  preview: CascadePreviewResponse | null | undefined,
): PushEntry | null {
  if (!preview) return null;
  return (
    preview.pushes.find((p) => p.task_id === taskId) ??
    preview.pulls.find((p) => p.task_id === taskId) ??
    null
  );
}

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
  weekendWidth: number;
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
  /** Task 22 — cascade preview 가 열려 있으면 해당 응답을 전달. 각 블록별 고스트 렌더 판단용. */
  previewOverlay?: CascadePreviewResponse | null;
  /** Task 22 — 모달 row hover 시 설정되는 focus task id (gantt 블록 outline 연동). */
  focusedTaskId?: string | null;
  /**
   * Wave 3 — compareMode 상태.
   * SchedulerView 에서 useMemo 로 1회 계산해 모든 row 에 동일 참조를 내려보낸다.
   * null 이면 compareMode OFF → diff 렌더 경로 완전 스킵 (기존 cascade preview 동작 유지).
   */
  compareModeEnabled?: boolean;
  /** Wave 3 — task_id(stable key) → DiffIndexEntry 맵. compareMode OFF 시 null. */
  diffByKey?: Map<string, import("../utils/diffIndex").DiffIndexEntry> | null;
  /** Wave 3 — pills 필터 상태. compareMode OFF 시 undefined 허용. */
  diffFilters?: { added: boolean; moved: boolean; removed: boolean };
  /** Wave 3 — 현재 run 에서 삭제된 tasks (이 row 의 equipment 에 해당하는 것만 렌더). */
  removedTasks?: import("../types/diff").RunCompareAddedOrRemovedTask[];
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
  weekendWidth,
  sharedSelection,
  onSelectionStart,
  onSelectionMove,
  onSelectionEnd,
  activeDragGroup,
  activeDragSq,
  activeDragMaterial,
  previewOverlay,
  focusedTaskId,
  compareModeEnabled,
  diffByKey,
  diffFilters,
  removedTasks,
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

  // --- Y축 lane 스태킹 (안전망) ---
  // 동일 설비 행에서 시간 겹치는 블록은 lane 을 분리해 세로로 쌓는다.
  // 백엔드(CP-SAT Task 7-9)가 겹침을 방지하지만, 엣지 케이스 대비 UI 최후 방어선.
  const { laneMap, laneCount } = useMemo(() => {
    if (rowTasks.length === 0) {
      return { laneMap: {} as Record<string, number>, laneCount: 1 };
    }
    const inputs = rowTasks.map((t) => ({
      id: t.id,
      start:
        t.start instanceof Date
          ? t.start.getTime()
          : new Date(t.start).getTime(),
      end: t.end instanceof Date ? t.end.getTime() : new Date(t.end).getTime(),
    }));
    const out = assignLanes(inputs);
    const lm: Record<string, number> = {};
    for (const a of out) lm[String(a.id)] = a.lane;
    return { laneMap: lm, laneCount: getLaneCount(out) };
  }, [rowTasks]);

  const rowPixelHeight = Math.max(ROW_HEIGHT, laneCount * LANE_HEIGHT);

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
      const rawTs = xToTimeAdj(relX, rangeStart, dayWidth, weekendWidth);
      // 1시간 단위로 스냅 (반내림)
      return Math.floor(rawTs / MS_PER_HOUR) * MS_PER_HOUR;
    },
    [rangeStart, dayWidth, weekendWidth],
  );

  // X좌표를 1시간 스냅된 픽셀 위치로 변환
  const snapXToHour = useCallback(
    (clientX: number) => {
      const rect = timelineRef.current?.getBoundingClientRect();
      if (!rect) return 0;
      const relX = clientX - rect.left;
      const rawTs = xToTimeAdj(relX, rangeStart, dayWidth, weekendWidth);
      const snappedTs = Math.floor(rawTs / MS_PER_HOUR) * MS_PER_HOUR;
      return timeToXAdj(snappedTs, rangeStart, dayWidth, weekendWidth);
    },
    [rangeStart, dayWidth, weekendWidth],
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
          const startTs = xToTimeAdj(minX, rangeStart, dayWidth, weekendWidth);
          const endTs = xToTimeAdj(maxX, rangeStart, dayWidth, weekendWidth);
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
        minHeight: rowPixelHeight,
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
          height: rowPixelHeight,
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
          height: rowPixelHeight,
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
        {rowTasks.map((task) => {
          // Task 22 — 원본(실선) 블록 + 제안된 위치에 반투명 dashed 고스트 블록.
          // preview 가 해당 task 에 대한 push/pull 제안을 포함하면 ghost 를 덧붙인다.
          const proposal = getProposalFor(String(task.id), previewOverlay);
          const isFocused = focusedTaskId === String(task.id);
          const lane = laneMap[String(task.id)] ?? 0;

          // Wave 3 — compareMode 에서 diff 인덱스 조회.
          // stable key 매핑 (백엔드 /runs/compare 의 task_id 포맷과 1:1 일치 필수):
          //   - sales_order_id ← task.order_id            (batch.sales_order_id)
          //   - sales_order_line ← task.sales_order_line  (batch.sales_order_line)
          //   - process_name ← task.process_name          (batch.process_name: 연선/저압절연/저압시스 …)
          //   - batch_seq ← task.process_step             (batch.batch_seq: -1 헤더 / 0+ 개별수주)
          // 왜 task.product 를 쓰지 않는가: product 는 제품군(예: "TFR-CV-WB") 이라 공정명과 다르다.
          // batch_group/batch_id 는 오직 sales_order_id 가 비어있는 헤더 배치 fallback 용.
          const diffEntry =
            compareModeEnabled && diffByKey
              ? diffByKey.get(
                  taskStableKey({
                    sales_order_id: task.order_id,
                    sales_order_line: task.sales_order_line,
                    process_name: task.process_name,
                    batch_seq: task.process_step,
                    batch_group: task.batch_group,
                    batch_id: task.batch_id,
                  }),
                )
              : undefined;

          // 현재 위치 블록의 outline kind — added/moved 만 outline 렌더, removed 는 현재 run 에 없으므로 제외.
          const diffOverlay: "added" | "moved" | undefined =
            diffEntry?.kind === "added"
              ? "added"
              : diffEntry?.kind === "moved"
                ? "moved"
                : undefined;
          // unchanged 블록 (compareMode ON + diff 매칭 없음) 은 dim 으로 맥락만 유지.
          const dimmed = Boolean(compareModeEnabled) && !diffEntry;

          // 필터 pills 에 따른 overlay 가시성.
          //   - 현재 위치 블록은 unchanged(매칭 없음) 일 때 항상 렌더(dimmed)
          //   - added/moved 는 해당 필터 ON 일 때만 overlay 표시
          const showDiffOverlay =
            !compareModeEnabled ||
            (diffOverlay === "added" && diffFilters?.added) ||
            (diffOverlay === "moved" && diffFilters?.moved) ||
            !diffOverlay;

          // compareMode.enabled 일 때는 cascade preview ghost 비활성 (상호배타).
          const showProposalGhost = !compareModeEnabled && proposal;

          return (
            <Fragment key={task.id}>
              <GanttTaskBlock
                task={task}
                rangeStart={rangeStart}
                dayWidth={dayWidth}
                weekendWidth={weekendWidth}
                lane={lane}
                laneHeight={LANE_HEIGHT}
                focused={isFocused}
                diffOverlay={
                  compareModeEnabled && showDiffOverlay
                    ? diffOverlay
                    : undefined
                }
                diffDeltaHours={
                  diffEntry?.kind === "moved"
                    ? diffEntry.start_delta_hours
                    : undefined
                }
                diffEquipmentChanged={
                  diffEntry?.kind === "moved"
                    ? diffEntry.equipment_changed
                    : undefined
                }
                dimmed={dimmed}
              />
              {/* moved 의 과거 위치 ghost — moved 필터 ON 이고 old_start/old_end 파싱 성공했을 때만 */}
              {compareModeEnabled &&
                diffFilters?.moved &&
                diffEntry?.kind === "moved" &&
                diffEntry.old_start &&
                diffEntry.old_end && (
                  <GanttTaskBlock
                    task={{
                      ...task,
                      id: `${task.id}-past-ghost`,
                      start: diffEntry.old_start,
                      end: diffEntry.old_end,
                      equipment_id:
                        diffEntry.old_equipment || task.equipment_id,
                    }}
                    rangeStart={rangeStart}
                    dayWidth={dayWidth}
                    weekendWidth={weekendWidth}
                    lane={lane}
                    laneHeight={LANE_HEIGHT}
                    ghost
                    ghostReason="diff_moved_past"
                  />
                )}
              {/* 기존 cascade proposal ghost — compareMode OFF 일 때만 렌더 (상호배타) */}
              {showProposalGhost && proposal && (
                <GanttTaskBlock
                  task={{
                    ...task,
                    start: new Date(proposal.new_start),
                    end: new Date(proposal.new_end),
                  }}
                  rangeStart={rangeStart}
                  dayWidth={dayWidth}
                  weekendWidth={weekendWidth}
                  lane={lane}
                  laneHeight={LANE_HEIGHT}
                  ghost
                />
              )}
            </Fragment>
          );
        })}

        {/* Wave 3 — Removed tasks ghost.
            이 row 의 equipment 에 속하고 start/end 가 파싱 가능한 항목만 렌더.
            합성된 ScheduleTask 는 실제 tasks 배열에는 없으므로 lane=0 고정. */}
        {compareModeEnabled &&
          diffFilters?.removed &&
          removedTasks &&
          removedTasks
            .filter(
              (r) =>
                r.equipment === equipment.id &&
                r.start != null &&
                r.end != null,
            )
            .map((r) => {
              const synthStart = new Date(r.start!);
              const synthEnd = new Date(r.end!);
              // fail-fast: 잘못된 ISO 는 렌더 skip (NaN x좌표 방지).
              if (
                Number.isNaN(synthStart.getTime()) ||
                Number.isNaN(synthEnd.getTime())
              ) {
                return null;
              }
              const synthesizedTask: ScheduleTask = {
                id: `removed-${r.task_id}`,
                order_id: r.sales_order_id || "",
                equipment_id: r.equipment || "",
                product: r.process_name || "",
                spec: "",
                core_count: 1,
                color: "#9CA3AF",
                start: synthStart,
                end: synthEnd,
                volume_m: 0,
                line_speed_m_per_min: 0,
                priority: "normal",
                status: "removed",
                predecessors: [],
                notes: "",
                changeover_min: 0,
                batch_group: r.batch_group || undefined,
                customer: r.customer_name || undefined,
              };
              return (
                <GanttTaskBlock
                  key={synthesizedTask.id}
                  task={synthesizedTask}
                  rangeStart={rangeStart}
                  dayWidth={dayWidth}
                  weekendWidth={weekendWidth}
                  lane={0}
                  laneHeight={LANE_HEIGHT}
                  ghost
                  ghostReason="diff_removed"
                />
              );
            })}
      </div>
    </div>
  );
});

// ----- 날짜 헤더 -----

interface DateHeaderProps {
  rangeStart: number;
  rangeEnd: number;
  dayWidth: number;
  weekendWidth: number;
  timelineWidth: number;
}

function DateHeader({
  rangeStart,
  rangeEnd,
  dayWidth,
  weekendWidth,
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
  }, [rangeStart, rangeEnd, dayWidth]);

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

// ----- 주말 배경 오버레이 -----

interface WeekendOverlayProps {
  rangeStart: number;
  rangeEnd: number;
  dayWidth: number;
  weekendWidth: number;
  totalHeight: number;
}

function WeekendOverlay({
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

// ----- 수직 그리드 라인 -----

interface GridLinesProps {
  rangeStart: number;
  rangeEnd: number;
  dayWidth: number;
  weekendWidth: number;
  totalHeight: number;
}

function GridLines({
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
  /**
   * Task 22 — cascade preview 가 제시한 변경. 전달되면 각 task 의 원본(실선) 옆에
   * 반투명(50%) dashed border 고스트를 렌더한다. null/undefined 면 고스트 비활성.
   */
  previewOverlay?: CascadePreviewResponse | null;
  /**
   * Task 22 — 모달 행 hover 시 설정되는 focus task id. 해당 gantt 블록에
   * focus-ring outline 을 입혀 어떤 블록이 하이라이트 대상인지 시각 연결한다.
   */
  focusedTaskId?: string | null;
}

export function SchedulerView({
  activeDragGroup,
  activeDragSq,
  activeDragMaterial,
  previewOverlay,
  focusedTaskId,
}: SchedulerViewProps = {}) {
  const equipment = useScheduleStore((s) => s.equipment);
  const tasks = useScheduleStore((s) => s.tasks);
  const viewFilter = useScheduleStore((s) => s.viewFilter);
  const zoomLevel = useScheduleStore((s) => s.zoomLevel);
  const dayWidthScale = useScheduleStore((s) => s.dayWidthScale);
  const range = useScheduleStore((s) => s.range);
  // Wave 3 — compareMode 상태 구독.
  // diffByKey 는 diffResponse 가 바뀔 때만 재계산 (메모이제이션 포인트).
  const compareMode = useScheduleStore((s) => s.compareMode);
  const diffByKey = useMemo(
    () => buildDiffIndex(compareMode.diffResponse ?? null),
    [compareMode.diffResponse],
  );

  // Week 4 Task 4B.3 — Decision Card 인라인 슬롯.
  // 클릭으로 선택된 task 의 batch_id 를 찾아 해당 행 아래에만 카드 1개를 렌더한다.
  // 왜 별도 store 슬라이스를 두지 않는가:
  //   selectTask() 가 이미 chain 하이라이트 용도로 selectedTaskId 를 관리하므로
  //   "선택된 배치" 의미를 1소스로 유지 — 동일 클릭이 양쪽 효과 동시 트리거.
  const selectedTaskId = useScheduleStore((s) => s.selectedTaskId);
  const selectedTask = useMemo(
    () =>
      selectedTaskId
        ? (tasks.find((t) => t.id === selectedTaskId) ?? null)
        : null,
    [selectedTaskId, tasks],
  );
  // batch_id 가 없는 task (예: 미배정 임시 블록) 는 카드 미표시 — 백엔드 트레이스 매칭 불가.
  const selectedBatchId =
    selectedTask?.batch_id != null ? String(selectedTask.batch_id) : null;

  // 필터 적용
  const filteredEquipment = useFilteredEquipment(
    equipment,
    viewFilter.filterType,
    viewFilter.filterValue,
  );

  // 빈 설비(배치 없는 행) 숨김 상태
  const [hideEmpty, setHideEmpty] = useState(true);
  const [showHiddenList, setShowHiddenList] = useState(false);

  // 태스크가 있는 설비 ID 집합
  const equipmentWithTasks = useMemo(
    () => new Set(tasks.map((t) => t.equipment_id)),
    [tasks],
  );

  const visibleEquipment = useMemo(
    () =>
      hideEmpty
        ? filteredEquipment.filter((eq) => equipmentWithTasks.has(eq.id))
        : filteredEquipment,
    [filteredEquipment, hideEmpty, equipmentWithTasks],
  );

  const hiddenEquipment = useMemo(
    () =>
      hideEmpty
        ? filteredEquipment.filter((eq) => !equipmentWithTasks.has(eq.id))
        : [],
    [filteredEquipment, hideEmpty, equipmentWithTasks],
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

  // 주말 숨김 토글
  const [hideWeekends, setHideWeekends] = useState(false);

  const MS_PER_DAY = 24 * 60 * 60 * 1000;
  const totalDays = Math.max((rangeEnd - rangeStart) / MS_PER_DAY, 1);
  const availableWidth = Math.max(containerWidth - SIDEBAR_WIDTH, 100);
  // dayWidth: (줌 레벨 기본값 × scale) 와 fit-to-container 중 큰 값
  // - 주말 접힘 시: 주말 컬럼이 WEEKEND_COLLAPSED_WIDTH를 차지하므로
  //   평일만으로 나머지 너비를 채워야 빈 공간이 생기지 않음
  let dayWidth: number;
  if (hideWeekends) {
    const allDays = generateDays(rangeStart, rangeEnd);
    const weekdayCount = Math.max(
      allDays.filter((d) => !isWeekend(d.date)).length,
      1,
    );
    const weekendCount = allDays.length - weekdayCount;
    const fitWidth = Math.max(
      (availableWidth - weekendCount * WEEKEND_COLLAPSED_WIDTH) / weekdayCount,
      1,
    );
    dayWidth = Math.max(DAY_WIDTH_MAP[zoomLevel] * dayWidthScale, fitWidth);
  } else {
    dayWidth = Math.max(
      DAY_WIDTH_MAP[zoomLevel] * dayWidthScale,
      availableWidth / totalDays,
    );
  }
  const weekendWidth = hideWeekends ? WEEKEND_COLLAPSED_WIDTH : dayWidth;
  const timelineWidth = timelineWidthAdj(
    rangeStart,
    rangeEnd,
    dayWidth,
    weekendWidth,
  );
  const totalContentWidth = SIDEBAR_WIDTH + timelineWidth;

  // 전체 높이 — 각 설비 행의 실제 lane 수를 반영해야 주말/그리드/오늘 마커가
  // 늘어난 row 전체를 덮을 수 있다. 기본은 ROW_HEIGHT, 겹침 있는 행은 laneCount*LANE_HEIGHT.
  const totalHeight = useMemo(() => {
    let sum = 0;
    for (const eq of visibleEquipment) {
      const eqTasks = tasks.filter((t) => {
        if (t.equipment_id !== eq.id) return false;
        const tStart =
          t.start instanceof Date
            ? t.start.getTime()
            : new Date(t.start).getTime();
        const tEnd =
          t.end instanceof Date ? t.end.getTime() : new Date(t.end).getTime();
        return tEnd >= rangeStart && tStart <= rangeEnd;
      });
      if (eqTasks.length === 0) {
        sum += ROW_HEIGHT;
        continue;
      }
      const out = assignLanes(
        eqTasks.map((t) => ({
          id: t.id,
          start:
            t.start instanceof Date
              ? t.start.getTime()
              : new Date(t.start).getTime(),
          end:
            t.end instanceof Date ? t.end.getTime() : new Date(t.end).getTime(),
        })),
      );
      sum += Math.max(ROW_HEIGHT, getLaneCount(out) * LANE_HEIGHT);
    }
    return sum;
  }, [visibleEquipment, tasks, rangeStart, rangeEnd]);

  // 패닝 훅 — overflow-auto 컨테이너에 연결
  const scrollContainerRef = useTimelineNavigation(dayWidth);

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
    // Task 9 Step 3 — "순수 click" (pointer 이동 < 5px) 으로 판정되면 선택된 task 를 해제.
    // range-drag (>= 5px) 은 그대로 유지해 우클릭 prefill 경로를 보존.
    // 블록 click 은 GanttRow:223 의 `data-draggable` 필터로 sharedSelection 자체가 새로 set
    // 되지 않으므로 이 deselect 경로를 타지 않는다.
    const sel = sharedSelection;
    if (sel && Math.abs(sel.currentX - sel.startX) < 5) {
      const { selectedTaskId, selectTask } = useScheduleStore.getState();
      if (selectedTaskId !== null) selectTask(null);
      setSharedSelection(null);
    }
    // sel 이 없거나 range-drag 인 경우엔 기존처럼 선택 유지 (우클릭 대기용).
  }, [sharedSelection]);

  // Task 9 Step 2 — Esc 로 chain-highlight 해제.
  // INPUT/TEXTAREA/contentEditable 에서 Esc 는 무시 (모달 닫기 등 기존 동작 방해 방지).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      const target = e.target as HTMLElement | null;
      if (
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.isContentEditable)
      ) {
        return;
      }
      const { selectedTaskId, selectTask } = useScheduleStore.getState();
      if (selectedTaskId !== null) selectTask(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
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
            weekendWidth={weekendWidth}
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
                weekendWidth={weekendWidth}
                totalHeight={Math.max(totalHeight, 128)}
              />

              {/* 수직 그리드 라인 */}
              <GridLines
                rangeStart={rangeStart}
                rangeEnd={rangeEnd}
                dayWidth={dayWidth}
                weekendWidth={weekendWidth}
                totalHeight={Math.max(totalHeight, 128)}
              />

              {/* 오늘 마커 */}
              <TodayMarker
                rangeStart={rangeStart}
                rangeEnd={rangeEnd}
                dayWidth={dayWidth}
                weekendWidth={weekendWidth}
                totalHeight={Math.max(totalHeight, 128)}
              />
            </div>

            {/* 설비 행 — data-testid="timeline-bg" 는 E2E Step 3.5 편의용 (기능 영향 없음) */}
            <div
              data-testid="timeline-bg"
              style={{ position: "relative", zIndex: 1 }}
            >
              {visibleEquipment.map((eq) => {
                // Week 4 Task 4B.3 — 선택된 task 가 이 행의 설비에 속하면 행 아래 슬롯에 카드.
                // 다른 행으로 이동된 batch 는 selectedTask.equipment_id 가 바뀌므로 자동으로
                // 카드도 따라간다 (행 1개 = 카드 0~1개 invariant).
                const showCardHere =
                  selectedTask !== null &&
                  selectedBatchId !== null &&
                  selectedTask.equipment_id === eq.id;
                return (
                  <Fragment key={eq.id}>
                    <GanttRow
                      equipment={eq}
                      tasks={tasks}
                      rangeStart={rangeStart}
                      rangeEnd={rangeEnd}
                      dayWidth={dayWidth}
                      weekendWidth={weekendWidth}
                      timelineWidth={timelineWidth}
                      sharedSelection={sharedSelection}
                      onSelectionStart={handleSelectionStart}
                      onSelectionMove={handleSelectionMove}
                      onSelectionEnd={handleSelectionEnd}
                      activeDragGroup={activeDragGroup}
                      activeDragSq={activeDragSq}
                      activeDragMaterial={activeDragMaterial}
                      previewOverlay={previewOverlay}
                      focusedTaskId={focusedTaskId}
                      compareModeEnabled={compareMode.enabled}
                      diffByKey={compareMode.enabled ? diffByKey : null}
                      diffFilters={compareMode.filters}
                      removedTasks={
                        compareMode.enabled
                          ? (compareMode.diffResponse?.removed_tasks ?? [])
                          : []
                      }
                    />
                    {showCardHere && <DecisionCard batchId={selectedBatchId} />}
                  </Fragment>
                );
              })}

              {visibleEquipment.length === 0 && (
                <div className="flex items-center justify-center h-32 text-sm text-gray-400">
                  {equipment.length === 0
                    ? "설비 데이터를 불러오는 중..."
                    : "선택한 필터에 해당하는 설비가 없습니다"}
                </div>
              )}

              {/* Task 9 — chain highlight 오버레이. store 의 selectedChainIds/selectedArrows 를
                  구독해 좌표 SVG 를 렌더. selectedChainIds === null 이면 조기 리턴. */}
              <ChainHighlightOverlay
                tasks={tasks}
                visibleEquipment={visibleEquipment}
                rangeStart={rangeStart}
                dayWidth={dayWidth}
                weekendWidth={weekendWidth}
                totalWidth={timelineWidth + SIDEBAR_WIDTH}
                totalHeight={totalHeight}
              />
            </div>
          </div>
        </div>{" "}
        {/* totalContentWidth wrapper */}
      </div>

      {/* 숨김 설비 토글 바 */}
      {filteredEquipment.length > 0 && (
        <div
          className="flex items-center gap-2 px-3 py-1.5 mt-1 rounded-md flex-wrap"
          style={{ backgroundColor: "#F3F4F6", border: "1px solid #E5E7EB" }}
        >
          {/* 주말 열 접기/펴기 */}
          <button
            onClick={() => setHideWeekends((v) => !v)}
            className="flex items-center gap-1.5 text-[11px] font-medium transition-colors"
            style={{ color: hideWeekends ? "#C41230" : "#6B7280" }}
          >
            <span>{hideWeekends ? "▶" : "▼"}</span>
            <span>{hideWeekends ? "주말 접힘" : "주말 펼침"}</span>
          </button>

          <span className="text-gray-300 select-none">|</span>

          <button
            onClick={() => {
              setHideEmpty((v) => !v);
              setShowHiddenList(false);
            }}
            className="flex items-center gap-1.5 text-[11px] font-medium text-gray-600 hover:text-gray-900 transition-colors"
          >
            <span>{hideEmpty ? "▶" : "▼"}</span>
            <span>
              {hideEmpty
                ? `빈 설비 ${hiddenEquipment.length}개 숨김`
                : "빈 설비 표시 중"}
            </span>
          </button>

          {hideEmpty && hiddenEquipment.length > 0 && (
            <>
              <span className="text-gray-300 select-none">|</span>
              <button
                onClick={() => setShowHiddenList((v) => !v)}
                className="text-[11px] text-blue-500 hover:text-blue-700 transition-colors"
              >
                {showHiddenList ? "목록 닫기" : "목록 보기"}
              </button>
            </>
          )}

          {hideEmpty && showHiddenList && hiddenEquipment.length > 0 && (
            <div className="flex flex-wrap gap-1 ml-1">
              {hiddenEquipment.map((eq) => (
                <span
                  key={eq.id}
                  className="text-[10px] px-1.5 py-0.5 rounded"
                  style={{ backgroundColor: "#E5E7EB", color: "#6B7280" }}
                  title={eq.process_type}
                >
                  {eq.name}
                </span>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
