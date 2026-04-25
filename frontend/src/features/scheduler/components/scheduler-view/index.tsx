/**
 * SchedulerView 컴포지션 — Toolbar / GanttGrid / TaskRow / DiffOverlay 를 합성.
 *
 * Week 7 Task 7B.2 기준 split:
 *   - GanttGrid (DateHeader/WeekendOverlay/GridLines): 정적 축 레이어
 *   - TaskRow                                       : 한 설비 행 + 모든 task 블록
 *   - DiffOverlay (RemovedGhosts)                    : 삭제된 task 합성 ghost (TaskRow 내부에서 호출)
 *   - Toolbar                                        : 하단 표시 옵션 토글
 *
 * Decision Card 슬롯 (Week 4 Task 4B.3 / Week 7 Task 7B.2):
 *   - 이 파일이 selectedTaskId 를 알고 있으므로 카드 위치를 결정한다.
 *   - 선택된 task 의 equipment_id 와 일치하는 행에 한해 TaskRow 의 decisionCardSlot prop 으로
 *     <DecisionCard/> 를 끼워 넣는다 — 카드가 행 바로 아래에 슬라이드 인.
 *   - row 1개 = 카드 0~1개 invariant 보장.
 */
"use client";

import { useMemo, useCallback, useState, useEffect, useRef } from "react";

import { useScheduleStore } from "../../store/scheduleStore";
import { TodayMarker } from "../TodayMarker";
import { ChainHighlightOverlay } from "../ChainHighlightOverlay";
import { DecisionCard } from "../DecisionCard";
import { useTimelineNavigation } from "../../../../shared/hooks/useTimelineNavigation";
import type { Equipment, ViewFilterType } from "../../types";
import type { CascadePreviewResponse } from "../../api/cascade.types";
import { buildDiffIndex } from "../../utils/diffIndex";
import {
  SIDEBAR_WIDTH,
  ROW_HEIGHT,
  DAY_WIDTH_MAP,
  timelineWidthAdj,
  isWeekend,
  generateDays,
} from "../../utils/ganttUtils";
import { assignLanes, getLaneCount } from "../../utils/laneAssign";

import { LANE_HEIGHT, WEEKEND_COLLAPSED_WIDTH } from "./constants";
import { DateHeader, WeekendOverlay, GridLines } from "./GanttGrid";
import { TaskRow, type SharedSelection } from "./TaskRow";
import { SchedulerToolbar } from "./Toolbar";

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

// ----- SchedulerView 메인 컴포넌트 -----

interface SchedulerViewProps {
  /** 드래그 중인 아이템의 장비 그룹 */
  activeDragGroup?: string | null;
  /** 드래그 중인 아이템의 SQ (mm²) */
  activeDragSq?: number | null;
  /** 드래그 중인 아이템의 도체 재질 (CU | AL) */
  activeDragMaterial?: string | null;
  /**
   * cascade preview 가 제시한 변경. 전달되면 각 task 의 원본(실선) 옆에
   * 반투명(50%) dashed border 고스트를 렌더한다. null/undefined 면 고스트 비활성.
   */
  previewOverlay?: CascadePreviewResponse | null;
  /**
   * 모달 행 hover 시 설정되는 focus task id. 해당 gantt 블록에
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
  // compareMode 상태 구독.
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

  // 빈 설비(배치 없는 행) 숨김 상태 — Toolbar 가 이 상태를 토글
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

  // 주말 숨김 토글 — Toolbar 가 이 상태를 토글
  const [hideWeekends, setHideWeekends] = useState(false);

  const MS_PER_DAY = 24 * 60 * 60 * 1000;
  const totalDays = Math.max((rangeEnd - rangeStart) / MS_PER_DAY, 1);
  const availableWidth = Math.max(containerWidth - SIDEBAR_WIDTH, 100);
  // dayWidth: (줌 레벨 기본값 × scale) 와 fit-to-container 중 큰 값
  // - 주말 접힘 시: 주말 컬럼이 WEEKEND_COLLAPSED_WIDTH 를 차지하므로
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
    // 블록 click 은 TaskRow 의 `data-draggable` 필터로 sharedSelection 자체가 새로 set
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
        {/* 전체 콘텐츠 너비 — 이 div 가 가로 스크롤 범위를 결정 */}
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
                  <TaskRow
                    key={eq.id}
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
                    decisionCardSlot={
                      showCardHere ? (
                        <DecisionCard batchId={selectedBatchId} />
                      ) : undefined
                    }
                  />
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
        <SchedulerToolbar
          hideWeekends={hideWeekends}
          onToggleWeekends={() => setHideWeekends((v) => !v)}
          hideEmpty={hideEmpty}
          onToggleHideEmpty={() => {
            setHideEmpty((v) => !v);
            setShowHiddenList(false);
          }}
          showHiddenList={showHiddenList}
          onToggleHiddenList={() => setShowHiddenList((v) => !v)}
          hiddenEquipment={hiddenEquipment}
        />
      )}
    </div>
  );
}
