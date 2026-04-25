/**
 * TaskRow — 한 설비(equipment) 에 해당하는 Gantt 행 + 그 행의 모든 task 블록.
 *
 * 책임 (Single Responsibility):
 *   - droppable 영역 (useDroppable) + 빈 영역 좌클릭 드래그로 시간 범위 선택
 *   - 우클릭 컨텍스트 메뉴 (TaskFormModal prefill 포함)
 *   - viewport culling (rangeStart~rangeEnd 밖 task 제외)
 *   - lane 스태킹 (assignLanes — UI 최후 방어선; 백엔드가 겹침 방지하지만 안전망)
 *   - 각 task 블록 + 옵션 cascade preview ghost + diff overlay (compareMode)
 *
 * Decision Card 슬롯 (Week 4 Task 4B.3 / Week 7 Task 7B.2):
 *   - `decisionCardSlot` prop 으로 부모(<index>) 가 카드를 행 아래에 끼워 넣는다.
 *   - row 자체는 어떤 카드를 그릴지 모르고 "여기에 넣어라" 만 알기 때문에 SRP 보존.
 *
 * 분리 이유 (Week 7 Task 7B.2):
 *   - 기존 1,468줄 SchedulerView.tsx 에서 GanttRow 만 약 480줄을 차지.
 *   - 행 책임(드롭/선택/렌더링/diff 매칭) 과 컨테이너 책임(스크롤/필터/높이 계산) 분리.
 */
"use client";

import {
  useMemo,
  useCallback,
  useRef,
  memo,
  Fragment,
  type ReactNode,
} from "react";
import { useDroppable } from "@dnd-kit/core";

import { useScheduleStore } from "../../store/scheduleStore";
import { EquipmentSidebar } from "../EquipmentSidebar";
import { GanttTaskBlock } from "../GanttTaskBlock";
import type { ScheduleTask, Equipment } from "../../types";
import type {
  CascadePreviewResponse,
  PushEntry,
} from "../../api/cascade.types";
import { taskStableKey } from "../../utils/diffIndex";
import {
  SIDEBAR_WIDTH,
  ROW_HEIGHT,
  timeToXAdj,
  xToTimeAdj,
} from "../../utils/ganttUtils";
import { assignLanes, getLaneCount } from "../../utils/laneAssign";
import { LANE_HEIGHT } from "./constants";
import { RemovedGhosts } from "./DiffOverlay";

/**
 * cascade preview 응답에서 주어진 task 에 대한 제안(push 또는 pull) 을 찾는다.
 * push/pull 은 같은 `PushEntry` 구조를 공유하므로 단일 타입으로 반환.
 *
 * 왜 helper 로 분리: TaskRow 내부 loop 에서 불필요한 배열 순회 중복을 방지하고
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

/**
 * 장비가 주어진 드래그 그룹과 호환되는지 판단한다.
 * equipment_group: "연선" | "B100" | "A100" | "A120"
 *
 * taskSq / taskMaterial 이 주어지면 설비의 SQ 범위 및 재질 제한도 검증한다.
 */
export function equipmentMatchesGroup(
  equipment: Equipment,
  group: string,
  taskSq?: number,
  taskMaterial?: string,
): boolean {
  const processType = equipment.process_type;
  const name = equipment.name.toUpperCase();

  // 그룹 매칭 — process_type 은 한국어 (연선, 저압절연 등)
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

  // SQ 범위 검증 — 설비에 range_min/range_max 가 설정된 경우만 체크
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

  // 재질 검증 — 설비에 material_limit 이 설정되고 "ALL" 이 아닌 경우만 체크
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

/** 공유 선택 상태 — SchedulerView 에서 관리하고 TaskRow 로 내려줌 */
export interface SharedSelection {
  equipmentId: string;
  startX: number;
  currentX: number;
}

interface TaskRowProps {
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
  /** 드래그 중인 아이템의 장비 그룹 (drag constraint 용) */
  activeDragGroup?: string | null;
  /** 드래그 중인 아이템의 SQ (mm²) */
  activeDragSq?: number | null;
  /** 드래그 중인 아이템의 도체 재질 (CU | AL) */
  activeDragMaterial?: string | null;
  /** cascade preview — 각 블록별 고스트 렌더 판단용 */
  previewOverlay?: CascadePreviewResponse | null;
  /** 모달 row hover 시 설정되는 focus task id (gantt 블록 outline 연동) */
  focusedTaskId?: string | null;
  /**
   * compareMode 상태. SchedulerView 에서 useMemo 로 1회 계산해 모든 row 에 동일 참조 전달.
   * null/false 면 compareMode OFF → diff 렌더 경로 완전 스킵 (cascade preview 동작 유지).
   */
  compareModeEnabled?: boolean;
  /** task_id(stable key) → DiffIndexEntry 맵. compareMode OFF 시 null. */
  diffByKey?: Map<
    string,
    import("../../utils/diffIndex").DiffIndexEntry
  > | null;
  /** pills 필터 상태 — added/moved/removed 가시성 토글 */
  diffFilters?: { added: boolean; moved: boolean; removed: boolean };
  /** 현재 run 에서 삭제된 tasks (이 row 의 equipment 에 해당하는 것만 렌더) */
  removedTasks?: import("../../types/diff").RunCompareAddedOrRemovedTask[];
  /**
   * Week 4 Task 4B.3 / Week 7 Task 7B.2 — Decision Card 슬롯.
   * 부모(SchedulerView <index>) 가 선택된 batch 의 카드를 이 슬롯에 끼워 넣는다.
   * 행 자체는 카드 내용을 모르고 "여기에 넣어라" 만 안다 (SRP).
   */
  decisionCardSlot?: ReactNode;
}

/**
 * 설비별 Gantt 행. useDroppable 로 드롭 영역 설정.
 * 빈 영역 좌클릭 드래그로 시간 범위 선택, 우클릭으로 작업 추가 가능.
 */
export const TaskRow = memo(function TaskRow({
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
  decisionCardSlot,
}: TaskRowProps) {
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

  // 이 설비에 할당된 작업만 필터링 + 뷰포트 컬링
  const rowTasks = useMemo(() => {
    return tasks.filter((t) => {
      if (t.equipment_id !== equipment.id) return false;
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
  // 백엔드(CP-SAT Task 7-9) 가 겹침을 방지하지만, 엣지 케이스 대비 UI 최후 방어선.
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
    [rangeStart, dayWidth, weekendWidth, MS_PER_HOUR],
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
    [rangeStart, dayWidth, weekendWidth, MS_PER_HOUR],
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
          // TaskFormModal 의 prefill 로도 전달
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
      weekendWidth,
      xToTimestamp,
      onSelectionStart,
      onSelectionEnd,
      MS_PER_HOUR,
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
    <>
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
            // overflow:hidden 으로 내부 요소가 타임라인 밖으로 나가지 않도록
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
            // 원본(실선) 블록 + 제안된 위치에 반투명 dashed 고스트 블록.
            const proposal = getProposalFor(String(task.id), previewOverlay);
            const isFocused = focusedTaskId === String(task.id);
            const lane = laneMap[String(task.id)] ?? 0;

            // compareMode 에서 diff 인덱스 조회.
            // stable key 매핑 — 백엔드 /runs/compare 의 task_id 포맷과 1:1 일치 필수.
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

            // 현재 위치 블록의 outline kind — added/moved 만 outline 렌더.
            const diffOverlay: "added" | "moved" | undefined =
              diffEntry?.kind === "added"
                ? "added"
                : diffEntry?.kind === "moved"
                  ? "moved"
                  : undefined;
            // unchanged 블록 (compareMode ON + diff 매칭 없음) 은 dim 으로 맥락만 유지.
            const dimmed = Boolean(compareModeEnabled) && !diffEntry;

            // 필터 pills 에 따른 overlay 가시성.
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
                {/* moved 의 과거 위치 ghost — moved 필터 ON + old_start/old_end 파싱 성공 시 */}
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
                {/* 기존 cascade proposal ghost — compareMode OFF 일 때만 (상호배타) */}
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

          {/* compareMode 의 "삭제된 tasks" ghost — 합성된 ScheduleTask 로 렌더 */}
          {compareModeEnabled && diffFilters?.removed && removedTasks && (
            <RemovedGhosts
              equipmentId={equipment.id}
              rangeStart={rangeStart}
              dayWidth={dayWidth}
              weekendWidth={weekendWidth}
              removedTasks={removedTasks}
            />
          )}
        </div>
      </div>

      {/* Decision Card 슬롯 — 부모가 선택된 batch 의 카드를 끼워 넣는다 (Week 4 Task 4B.3). */}
      {decisionCardSlot}
    </>
  );
});
