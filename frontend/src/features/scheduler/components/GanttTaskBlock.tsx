"use client";

import { useCallback, useEffect, useRef, useState, memo } from "react";
import { createPortal } from "react-dom";
import { useDraggable } from "@dnd-kit/core";
import type { ScheduleTask } from "../types";
import { useScheduleStore } from "../store/scheduleStore";
import {
  getTaskColor,
  getPriorityStyle,
  getStatusStyle,
} from "../utils/colorCoding";
import { getSqColor } from "@/shared/constants/brand";
import {
  timeToXAdj,
  ROW_HEIGHT,
  computeTimeBreakdown,
} from "../utils/ganttUtils";

/** frozen 배치(진행중/완료)는 드래그 불가 */
function isFrozenStatus(status: string): boolean {
  return status === "in_progress" || status === "completed";
}

interface GanttTaskBlockProps {
  task: ScheduleTask;
  rangeStart: number;
  dayWidth: number;
  weekendWidth?: number;
  /** Y축 lane 번호 (0부터). 동일 설비에서 시간 겹치는 블록 시 1 이상으로 분리된다. */
  lane?: number;
  /** 한 lane 의 세로 높이(px). 기본 ROW_HEIGHT. */
  laneHeight?: number;
  /**
   * 고스트 모드(Task 22). Cascade preview 의 제안된 위치를 반투명 dashed 로 오버레이한다.
   * - 클릭/드래그/리사이즈/팝오버 비활성 (pointer-events:none).
   * - opacity 0.5 + var(--color-warning) dashed border.
   * - data-testid 에 "ghost-" 프리픽스 부여.
   */
  ghost?: boolean;
  /**
   * 간트 블록에 focus-ring outline 을 표시한다(모달 row hover 연동). Task 22.
   */
  focused?: boolean;
}

const MS_PER_HOUR = 60 * 60 * 1000;
const NEW_BATCH_WINDOW_MS = 5 * 60 * 1000; // 5분 이내 생성된 배치는 "신규"로 간주

/**
 * 주말(토 00:00 ~ 월 00:00)을 건너뛰어 연속 평일 구간 배열을 반환한다.
 * 블록이 금요일을 넘어 이어지면 금요일 자정(토 00:00)에서 끊고
 * 다음 월요일 00:00에 새 구간을 시작한다.
 */
function splitByWeekends(
  startTs: number,
  endTs: number,
): { start: number; end: number }[] {
  const segments: { start: number; end: number }[] = [];
  let cur = startTs;

  while (cur < endTs) {
    const day = new Date(cur).getDay(); // 0=Sun, 6=Sat

    // 주말이면 월요일 00:00으로 이동
    if (day === 6) {
      const mon = new Date(cur);
      mon.setHours(0, 0, 0, 0);
      mon.setDate(mon.getDate() + 2);
      cur = mon.getTime();
      continue;
    }
    if (day === 0) {
      const mon = new Date(cur);
      mon.setHours(0, 0, 0, 0);
      mon.setDate(mon.getDate() + 1);
      cur = mon.getTime();
      continue;
    }

    // 다음 토요일 00:00 계산 (6 - day: Mon=5, Tue=4, ..., Fri=1)
    const nextSat = new Date(cur);
    nextSat.setHours(0, 0, 0, 0);
    nextSat.setDate(nextSat.getDate() + (6 - day));

    const segEnd = Math.min(endTs, nextSat.getTime());
    if (segEnd > cur) segments.push({ start: cur, end: segEnd });

    cur = segEnd;
    // 토요일에 도달하면 월요일로 점프
    if (cur < endTs && cur === nextSat.getTime()) {
      const mon = new Date(cur);
      mon.setDate(mon.getDate() + 2);
      cur = mon.getTime();
    }
  }

  return segments.length > 0 ? segments : [{ start: startTs, end: endTs }];
}

/** 시스 공정(SH-*) 설비의 sheath_color → 블록 배경색 매핑 */
const SHEATH_COLOR_MAP: Record<string, string> = {
  흑: "#374151",
  갈: "#92400E",
  회: "#6B7280",
  청: "#1E40AF",
  녹: "#065F46",
  황: "#B45309",
  "흑/적": "#C41230",
};

/**
 * 시스 설비 판별: equipment_id 가 "SH-" 로 시작하면 시스 공정으로 간주.
 * 기존에는 명시 set(SH-A100/SH-A120)을 사용했으나 SH-A150(고압시스), SH-B100 등
 * 신규 설비가 추가될 때마다 라벨/색상이 회귀하는 버그가 있어 prefix 검사로 전환.
 */
function isSheathEquipment(equipmentId: string | undefined | null): boolean {
  return typeof equipmentId === "string" && equipmentId.startsWith("SH-");
}

/** 시스 설비일 때 task.color 기반 배경색 반환, 아니면 null */
function getSheathColor(equipmentId: string, color: string): string | null {
  if (!isSheathEquipment(equipmentId)) return null;
  if (!color) return null;
  // 정확한 키 매칭 우선
  if (SHEATH_COLOR_MAP[color]) return SHEATH_COLOR_MAP[color];
  // 부분 매칭: 색상 문자열에 키워드가 포함되어 있으면 적용
  for (const [key, hex] of Object.entries(SHEATH_COLOR_MAP)) {
    if (color.includes(key)) return hex;
  }
  return null;
}

function snapToHour(ts: number): number {
  return Math.round(ts / MS_PER_HOUR) * MS_PER_HOUR;
}

export const GanttTaskBlock = memo(function GanttTaskBlock({
  task,
  rangeStart,
  dayWidth,
  weekendWidth,
  lane,
  laneHeight,
  ghost,
  focused,
}: GanttTaskBlockProps) {
  const ww = weekendWidth ?? dayWidth;
  // lane 스태킹: 동일 행에서 시간 겹치는 블록은 lane 별로 Y축 분리 배치.
  // 기본값(lane=0) 인 경우 top=4 로 기존 단일 lane 렌더링과 동일.
  const laneIdx = lane ?? 0;
  const laneH = laneHeight ?? ROW_HEIGHT;
  const laneTop = laneIdx * laneH + 4;
  // 색상 우선순위:
  //   1. 시스 공정(SH-A100/SH-A120): sheath_color 기반 고정색
  //   2. sq_mm2 있으면 SQ별 색상 (공정 흐름 추적용)
  //   3. 제품 그룹 해시 색상 (폴백)
  const sheathOverride = getSheathColor(task.equipment_id, task.color);
  const sqColor = !sheathOverride ? getSqColor(task.sq_mm2) : null;
  const baseColor = sheathOverride ?? sqColor ?? getTaskColor(task.product);
  const openTaskFormModal = useScheduleStore((s) => s.openTaskFormModal);
  const openContextMenu = useScheduleStore((s) => s.openContextMenu);
  const updateTask = useScheduleStore((s) => s.updateTask);
  const isEditMode = useScheduleStore((s) => s.isEditMode);
  const previewOffsetMs = useScheduleStore(
    (s) => s.previewOffsets[task.id] ?? 0,
  );
  // Chain highlight — 선택된 체인 外 블록을 dim.
  // boolean 만 반환 → Zustand Object.is 로 true/false 뒤집힐 때만 재렌더.
  // Set identity 변화가 있어도 결과 boolean 이 같으면 재렌더 없음.
  const isDimmed = useScheduleStore((s) => {
    if (s.selectedChainIds === null) return false;
    return !s.selectedChainIds.has(task.id);
  });

  const startTs =
    task.start instanceof Date
      ? task.start.getTime()
      : new Date(task.start).getTime();
  const endTs =
    task.end instanceof Date
      ? task.end.getTime()
      : new Date(task.end).getTime();

  // preview offset 적용: 드래그 중 밀려야 하는 만큼 시각적으로 이동
  const MS_PER_DAY = 24 * 60 * 60 * 1000;
  const previewOffsetPx =
    previewOffsetMs !== 0
      ? timeToXAdj(startTs + previewOffsetMs, rangeStart, dayWidth, ww) -
        timeToXAdj(startTs, rangeStart, dayWidth, ww)
      : 0;

  const left = timeToXAdj(startTs, rangeStart, dayWidth, ww) + previewOffsetPx;
  const width =
    timeToXAdj(endTs, rangeStart, dayWidth, ww) -
    timeToXAdj(startTs, rangeStart, dayWidth, ww);

  // frozen 배치(진행중/완료)는 드래그 불가
  const isFrozen = isFrozenStatus(task.status);
  // batch_group 소속 여부: handleDragEnd 에서 type 으로 분기 (Task 4.3 소비)
  const isBatchGroupTask = task.batch_group != null;
  // WIP 매칭된 배치는 드래그 불가 (unassign 불가와 동일 조건)
  const wipMatched = task.wip_matched_id != null;

  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: task.id,
    data: {
      type: isBatchGroupTask ? "batch_group_task" : "task",
      task,
      equipmentId: task.equipment_id,
      batch_group: task.batch_group ?? null,
    },
    disabled: isFrozen || !isEditMode || wipMatched,
  });

  // --- 리사이즈 ---
  const resizing = useRef<"left" | "right" | null>(null);
  const resizeStartX = useRef(0);
  const origStart = useRef(startTs);
  const origEnd = useRef(endTs);

  const handleResizeStart = useCallback(
    (side: "left" | "right", e: React.MouseEvent) => {
      if (!isEditMode) return;
      e.stopPropagation();
      e.preventDefault();
      resizing.current = side;
      resizeStartX.current = e.clientX;
      origStart.current = startTs;
      origEnd.current = endTs;

      const onMove = (ev: MouseEvent) => {
        const dx = ev.clientX - resizeStartX.current;
        const dtMs = (dx / dayWidth) * MS_PER_DAY;
        if (resizing.current === "right") {
          const newEnd = snapToHour(origEnd.current + dtMs);
          if (newEnd >= origStart.current + MS_PER_HOUR) {
            updateTask(task.id, { end: new Date(newEnd) });
          }
        } else {
          const newStart = snapToHour(origStart.current + dtMs);
          if (newStart <= origEnd.current - MS_PER_HOUR) {
            updateTask(task.id, { start: new Date(newStart) });
          }
        }
      };

      const onUp = () => {
        resizing.current = null;
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
      };

      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
      document.body.style.cursor = side === "right" ? "e-resize" : "w-resize";
      document.body.style.userSelect = "none";
    },
    [isEditMode, startTs, endTs, dayWidth, task.id, updateTask],
  );

  const selectTask = useScheduleStore((s) => s.selectTask);
  const selectedTaskId = useScheduleStore((s) => s.selectedTaskId);

  const handleDoubleClick = useCallback(
    (e: React.MouseEvent) => {
      if (!isEditMode) return;
      e.stopPropagation();
      e.preventDefault();
      openTaskFormModal({ mode: "edit", taskId: task.id });
    },
    [isEditMode, openTaskFormModal, task.id],
  );

  const handleContextMenu = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      e.stopPropagation();
      openContextMenu({
        x: e.clientX,
        y: e.clientY,
        type: "task",
        taskId: task.id,
      });
    },
    [openContextMenu, task.id],
  );

  // 납기 초과 여부: 배치 종료 시각이 납기일(자정)을 넘으면 지연
  const isLate = (() => {
    if (!task.delivery_date) return false;
    const dd =
      task.delivery_date instanceof Date
        ? task.delivery_date
        : new Date(task.delivery_date);
    const dueMidnight = new Date(dd);
    dueMidnight.setHours(23, 59, 59, 999);
    return endTs > dueMidnight.getTime();
  })();
  const lateDays =
    isLate && task.delivery_date
      ? Math.ceil(
          (endTs -
            (() => {
              const dd =
                task.delivery_date instanceof Date
                  ? task.delivery_date
                  : new Date(task.delivery_date!);
              const m = new Date(dd);
              m.setHours(23, 59, 59, 999);
              return m.getTime();
            })()) /
            (24 * 60 * 60 * 1000),
        )
      : 0;

  const priorityStyle = getPriorityStyle(task.priority);
  const statusStyle = getStatusStyle(task.status, baseColor);
  const HANDLE_W = 6;

  const handleDotStyle: React.CSSProperties = {
    position: "absolute",
    top: "50%",
    transform: "translateY(-50%)",
    width: 2,
    height: 12,
    borderRadius: 1,
    backgroundColor: "rgba(255,255,255,0.5)",
  };

  const volumeLabel = `${task.volume_m.toLocaleString()}m`;

  const isKcmil = /KCMIL/i.test(task.spec ?? "");
  const isGonaehwa = task.batch_group?.endsWith("_고내화") ?? false;

  // 연선 공정 틀 수: 헤더 배치의 drum_count(lot_count)를 직접 사용
  // 고압 연선(KCMIL 규격)은 틀 단위 없이 m만 표시
  const lotLabel = (() => {
    if (isKcmil) return null;
    if (task.lot_count != null && task.lot_count > 0)
      return `${task.lot_count}틀`;
    // 폴백: notes에서 추출
    const m = task.notes?.match(/\d+건\s+(\d+)틀/);
    return m ? `${m[1]}틀` : null;
  })();

  // 블록 상단 규격 라벨:
  //   시스 설비(SH-*): 색상(흑/갈/회…) · SQ 목록 [D 방식]
  //   고압 제품(spec에 KCMIL 포함): "500KCMIL" / "1C × 500KCMIL"
  //   CORE/AL-CORE 배치(T6BO 중심선): spec에서 원본 SQ 추출 (sq_mm2=35 무시)
  //   1코어: "50SQ"
  //   다심(2코어 이상): "4C × 50SQ"
  //   SQ 정보 없으면: spec → product 순 폴백
  //
  // 시스 판별은 prefix("SH-") 로 통일한다. 과거에 고정 set 을 쓰던 시절
  // SH-A150/SH-B100 이 set 에 빠져 D 라벨을 잃고 KCMIL 폴백으로 떨어지는
  // 회귀가 발생했기 때문이다.
  const specLabel = (() => {
    if (isSheathEquipment(task.equipment_id) && task.color) {
      // D 방식: 색상 · 규격 목록 (1-3개 전부, 4개 이상 축약)
      const specList = (task.spec_list ?? []).filter(Boolean);
      if (specList.length > 0) {
        const visible = specList.slice(0, 3).map((s) => s.replace(/SQ$/, ""));
        const sqPart =
          specList.length <= 3
            ? `${visible.join("·")} SQ`
            : `${visible.join("·")} SQ +${specList.length - 3}종`;
        return `${task.color} · ${sqPart}`;
      }
      // 폴백: spec_list 미전달 시 단일 SQ 로
      const single = task.sq_mm2 ? ` · ${task.sq_mm2}SQ` : "";
      return task.color + single;
    }
    if (isKcmil && task.spec) {
      const m = task.spec.match(/(\d+(?:\.\d+)?)\s*KCMIL/i);
      if (m) {
        const kcmilStr = `${m[1]}KCMIL`;
        return task.core_count > 1
          ? `${task.core_count}C × ${kcmilStr}`
          : kcmilStr;
      }
    }
    // CORE/AL-CORE 배치(T6BO 중심선): sq_mm2=35(설비 매칭용)이므로 spec에서 원본 SQ 추출
    const isCoreGroup =
      task.batch_group?.startsWith("CORE-") ||
      task.batch_group?.startsWith("AL-CORE-");
    if (isCoreGroup && task.spec) {
      const m = task.spec.match(/(\d+(?:\.\d+)?)\s*SQ/i);
      if (m) {
        const sqStr = `${parseInt(m[1])}SQ`;
        return task.core_count > 1 ? `${task.core_count}C × ${sqStr}` : sqStr;
      }
    }
    if (task.sq_mm2) {
      const sqStr = `${task.sq_mm2}SQ`;
      return task.core_count > 1 ? `${task.core_count}C × ${sqStr}` : sqStr;
    }
    return task.spec || task.product;
  })();

  const isSelected = selectedTaskId === task.id;

  // 증분 업데이트 후 신규 생성된 배치 여부 — created_at 기준 5분 이내
  // Date.now()를 렌더 중에 쓰면 SSR/CSR 값이 달라 hydration mismatch 발생 → useEffect로 클라이언트에서만 계산
  const [isNew, setIsNew] = useState(false);
  useEffect(() => {
    if (!task.created_at) return;
    setIsNew(Date.now() - new Date(task.created_at).getTime() < NEW_BATCH_WINDOW_MS);
  }, [task.created_at]);

  // --- 시간 구성 팝오버 (호버) ---
  const [showTimePopover, setShowTimePopover] = useState(false);
  const blockRef = useRef<HTMLDivElement>(null);

  const totalDurationHrs = ((endTs - startTs) / MS_PER_HOUR).toFixed(1);
  const setupMin = task.setup_time_min ?? task.changeover_min ?? 0;
  const colorChangeMin = task.color_change_min ?? 0;
  const timeBreakdown = computeTimeBreakdown(
    startTs,
    endTs,
    setupMin + colorChangeMin,
    task.equipment_id,
  );

  const handleBlockClick = useCallback(
    (e: React.MouseEvent) => {
      if ((e.target as HTMLElement).closest("[data-resize-handle]")) return;
      e.stopPropagation();
      selectTask(selectedTaskId === task.id ? null : task.id);
    },
    [selectTask, selectedTaskId, task.id],
  );

  // --- 규격교체 구간 계산 ---
  // changeover_min을 px 폭으로 변환. 전체 블록 폭 대비 비율로 계산하되,
  // 블록이 너무 좁을 경우 표시하지 않는다 (width < 40px).
  const totalDurationMs = endTs - startTs;
  const changeoverMs = (task.changeover_min ?? 0) * 60 * 1000;
  const hasChangeover = changeoverMs > 0 && totalDurationMs > 0 && width >= 40;
  // 규격교체 세그먼트 폭: 전체 폭에서 비율로 계산 (최소 6px, 최대 전체 폭의 40%)
  const changeoverPx = hasChangeover
    ? Math.min(
        Math.max((changeoverMs / totalDurationMs) * width, 6),
        width * 0.4,
      )
    : 0;

  // --- 주말 분할 세그먼트 ---
  const segments = splitByWeekends(startTs, endTs);
  // 각 세그먼트의 x 위치는 startTs 기준 상대 좌표로 계산 (previewOffset은 외부 div에 적용)
  const startX = timeToXAdj(startTs, rangeStart, dayWidth, ww);

  // 라벨 overlay 를 어느 segment 위에 그릴지 — 가장 넓은 segment 기준.
  // 왜: 주말/야간 건너뛴 블록은 첫 segment 가 7~8px 조각일 때가 있고, 그러면
  // 기존 "isFirst segment 안 flex:1 라벨" 이 0-width 로 축소되어 시각적으로
  // 사라진다 (예: SH-A150/B100 고압시스 블록). 가장 넓은 segment 위에 절대
  // 배치하는 overlay 로 이동해 항상 보이도록 한다.
  const widestSegIdx = segments.reduce(
    (m, s, i, arr) => (s.end - s.start > arr[m].end - arr[m].start ? i : m),
    0,
  );
  const widestSeg = segments[widestSegIdx];
  const widestSegLeft =
    timeToXAdj(widestSeg.start, rangeStart, dayWidth, ww) - startX;
  const widestSegW = Math.max(
    timeToXAdj(widestSeg.end, rangeStart, dayWidth, ww) -
      timeToXAdj(widestSeg.start, rangeStart, dayWidth, ww),
    2,
  );

  // Task 22 — ghost mode: 실제 블록 위에 반투명 dashed overlay 로 제안된 변경을 보여준다.
  // DnD / click / context menu / popover 를 모두 비활성화하여 "표시만" 하는 레이어로 사용.
  // focused: 모달 row hover 시 focus-ring outline (ghost 여부와 무관하게 동작).
  const rootDataTestId = ghost ? `ghost-${task.id}` : `gantt-block-${task.id}`;
  const interactiveHandlers = ghost
    ? {}
    : {
        onClick: handleBlockClick,
        onDoubleClick: handleDoubleClick,
        onContextMenu: handleContextMenu,
        onMouseEnter: () => setShowTimePopover(true),
        onMouseLeave: () => setShowTimePopover(false),
      };
  const ghostStyle: React.CSSProperties = ghost
    ? {
        opacity: 0.5,
        border: "2px dashed var(--color-warning)",
        borderRadius: 4,
        pointerEvents: "none",
        backgroundColor: "transparent",
      }
    : {};
  // focus-ring outline (ghost 가 아닌 실선 블록에만 적용). outline 은 layout 영향 없음.
  const focusOutline: React.CSSProperties = focused
    ? {
        outline: "2px solid var(--color-brand-primary)",
        outlineOffset: 2,
        zIndex: 15,
      }
    : {};

  return (
    <div
      ref={
        ghost
          ? undefined
          : (node) => {
              setNodeRef(node);
              (blockRef as { current: HTMLDivElement | null }).current = node;
            }
      }
      data-draggable={ghost ? undefined : true}
      data-task-id={task.id}
      data-testid={rootDataTestId}
      data-equipment-id={task.equipment_id}
      data-batch-group={task.batch_group ?? ""}
      data-sq-mm2={String(task.sq_mm2 ?? "")}
      data-spec-list-length={String((task.spec_list ?? []).length)}
      data-ghost={ghost ? "true" : undefined}
      style={{
        position: "absolute",
        left,
        top: laneTop,
        width: Math.max(width, 30),
        // laneHeight prop 이 주어지면 그에 맞춘다. SchedulerView 에서 LANE_HEIGHT
        // 상수를 통해 전달하므로 laneHeight/ROW_HEIGHT 가 달라져도 블록 높이가
        // 깨지지 않는다.
        height: laneH - 8,
        zIndex: ghost ? 6 : isDragging ? 20 : isSelected ? 10 : 2,
        opacity: isDimmed ? 0.4 : 1,
        // left 트랜지션은 preview 중에만, opacity 는 dim 전환 시 항상 부드럽게.
        // null 필터링으로 단일/복합 transition 을 동적으로 구성.
        transition: [
          previewOffsetPx !== 0 ? "left 0.15s ease-out" : null,
          "opacity 120ms ease-out",
        ]
          .filter(Boolean)
          .join(", "),
        cursor: ghost
          ? "default"
          : !isEditMode || isFrozen
            ? "default"
            : isDragging
              ? "grabbing"
              : "grab",
        ...ghostStyle,
        ...focusOutline,
      }}
      {...interactiveHandlers}
    >
      {/* 신규 배치 글로우 애니메이션 keyframes — 컴포넌트당 한 번만 주입 */}
      {isNew && (
        <style>{`
          @keyframes newBatchGlow {
            0%, 100% { box-shadow: 0 1px 3px rgba(0,0,0,0.15); }
            50% { box-shadow: 0 0 8px 2px rgba(234, 179, 8, 0.6), 0 1px 3px rgba(0,0,0,0.15); }
          }
        `}</style>
      )}
      {segments.map((seg, idx) => {
        const isFirst = idx === 0;
        const isLast = idx === segments.length - 1;
        const isSingle = segments.length === 1;
        const segLeft =
          timeToXAdj(seg.start, rangeStart, dayWidth, ww) - startX;
        const segW = Math.max(
          timeToXAdj(seg.end, rangeStart, dayWidth, ww) -
            timeToXAdj(seg.start, rangeStart, dayWidth, ww),
          2,
        );
        const radius = isSingle
          ? 4
          : isFirst
            ? "4px 0 0 4px"
            : isLast
              ? "0 4px 4px 0"
              : 0;

        // frozen 배치는 좌측 3px 컬러 보더로 구분
        // completed: dark green (#065F46), in_progress: KBI red (#C41230)
        const frozenBorderColor =
          task.status === "completed"
            ? "#065F46"
            : task.status === "in_progress"
              ? "#C41230"
              : undefined;

        const segBarStyle: React.CSSProperties = {
          ...statusStyle,
          ...priorityStyle,
          borderRadius: radius,
          boxShadow: isDragging
            ? "0 4px 12px rgba(0,0,0,0.3)"
            : "0 1px 3px rgba(0,0,0,0.15)",
          userSelect: "none",
          overflow: "hidden",
          height: ROW_HEIGHT - 8,
          opacity: isDragging ? 0 : 1,
          transition: isDragging ? "none" : "box-shadow 0.15s ease",
          position: "absolute",
          left: segLeft,
          top: 0,
          width: segW,
          display: "flex",
          alignItems: "stretch",
          outline: isSelected ? "2px solid #FBBF24" : "none",
          outlineOffset: 1,
          // frozen 배치 좌측 강조 보더 — 첫 세그먼트만 적용
          ...(isFirst && frozenBorderColor
            ? { borderLeft: `3px solid ${frozenBorderColor}` }
            : {}),
          // 납기 초과 배치 — 하단 빨간 테두리로 강조
          ...(isLate ? { borderBottom: "3px solid #FF0000" } : {}),
          // 신규 배치 글로우 — 첫 세그먼트에만, 드래그 중에는 비활성
          ...(isFirst && isNew && !isDragging
            ? { animation: "newBatchGlow 1s ease-in-out 2" }
            : {}),
        };

        return (
          <div key={idx} style={segBarStyle}>
            {/* 좌측 리사이즈 핸들 — 첫 세그먼트만 */}
            {isFirst && isEditMode && (
              <div
                data-resize-handle="left"
                style={{
                  width: HANDLE_W,
                  flexShrink: 0,
                  cursor: "w-resize",
                  position: "relative",
                  zIndex: 5,
                }}
                onMouseDown={(e) => handleResizeStart("left", e)}
              >
                <div style={{ ...handleDotStyle, left: 1 }} />
              </div>
            )}

            {/* 규격교체 세그먼트 — 첫 세그먼트만 */}
            {isFirst && hasChangeover && (
              <div
                style={{
                  width: changeoverPx,
                  flexShrink: 0,
                  alignSelf: "stretch",
                  background:
                    "repeating-linear-gradient(45deg, rgba(0,0,0,0.35) 0px, rgba(0,0,0,0.35) 3px, rgba(0,0,0,0.15) 3px, rgba(0,0,0,0.15) 6px)",
                  borderRight: "1px solid rgba(255,255,255,0.3)",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  overflow: "hidden",
                  pointerEvents: "none",
                }}
                title={`규격교체: ${task.changeover_min}분`}
              >
                {changeoverPx >= 18 && (
                  <span
                    style={{
                      fontSize: 7,
                      color: "rgba(255,255,255,0.9)",
                      fontWeight: 700,
                      textShadow: "0 1px 2px rgba(0,0,0,0.5)",
                      writingMode:
                        changeoverPx < 28 ? "vertical-rl" : "horizontal-tb",
                      whiteSpace: "nowrap",
                    }}
                  >
                    교체
                  </span>
                )}
              </div>
            )}

            {/* DnD listeners 전용 placeholder — 라벨은 블록 레벨 overlay 로 분리
                (좁은 첫 segment 에서 라벨이 0-width 로 사라지는 문제 회피). */}
            {isFirst ? (
              <div
                {...listeners}
                {...attributes}
                style={{
                  flex: 1,
                  minWidth: 0,
                  cursor: "inherit",
                }}
              />
            ) : (
              <div style={{ flex: 1, minWidth: 0 }} />
            )}

            {/* 우측 리사이즈 핸들 — 마지막 세그먼트만 */}
            {isLast && isEditMode && (
              <div
                data-resize-handle="right"
                style={{
                  width: HANDLE_W,
                  flexShrink: 0,
                  cursor: "e-resize",
                  position: "relative",
                  zIndex: 5,
                }}
                onMouseDown={(e) => handleResizeStart("right", e)}
              >
                <div style={{ ...handleDotStyle, right: 1 }} />
              </div>
            )}

            {/* 납기 초과 오버레이 — 블록 전체에 반투명 붉은색 */}
            {isLate && (
              <div
                style={{
                  position: "absolute",
                  inset: 0,
                  backgroundColor: "rgba(220,38,38,0.14)",
                  borderRadius: "inherit",
                  pointerEvents: "none",
                  zIndex: 1,
                }}
              />
            )}

            {/* 납기 초과 배지 — 마지막 세그먼트, 블록 폭 30px 이상 */}
            {isLast && isLate && segW >= 30 && (
              <div
                style={{
                  position: "absolute",
                  bottom: 2,
                  right: isLast && isEditMode ? HANDLE_W + 2 : 2,
                  zIndex: 5,
                  padding: "1px 3px",
                  borderRadius: 3,
                  fontSize: 8,
                  fontWeight: 700,
                  lineHeight: 1.4,
                  backgroundColor: "#FF0000",
                  color: "#fff",
                  whiteSpace: "nowrap",
                  pointerEvents: "none",
                }}
              >
                {segW >= 60 ? `+${lateDays}일 지연` : "지연"}
              </div>
            )}
          </div>
        );
      })}

      {/* 라벨 overlay — 가장 넓은 segment 위에 절대 배치.
          왜 overlay 방식: 주말 건너뛴 첫 segment 가 7-8px 로 좁을 때 (SH-A150/B100
          고압시스 케이스) flex 내부 truncate span 이 0-width 로 축소되어 글자가
          보이지 않는다. overlay 로 분리하면 widestSegW 기준으로 항상 펼쳐진다.
          pointerEvents:none 으로 DnD/click 방해 없음. */}
      <div
        style={{
          position: "absolute",
          left: widestSegLeft,
          top: 0,
          width: widestSegW,
          height: ROW_HEIGHT - 8,
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          paddingLeft: 4,
          paddingRight: 4,
          gap: 1,
          pointerEvents: "none",
          overflow: "hidden",
          zIndex: 4,
        }}
      >
        <span
          className="text-white text-[10px] font-semibold leading-tight"
          style={{
            // 진한 빨강(#C41230) 배경에서도 흰 글자가 묻히지 않도록 얇은 블랙
            // 스트로크 + 드롭섀도우를 겹침
            textShadow: "0 0 2px rgba(0,0,0,0.9), 0 1px 2px rgba(0,0,0,0.6)",
            display: "flex",
            alignItems: "center",
            gap: 3,
            minWidth: 0,
          }}
        >
          {isGonaehwa && (
            <span
              style={{
                flexShrink: 0,
                fontSize: 8,
                fontWeight: 700,
                lineHeight: 1.4,
                padding: "0px 3px",
                borderRadius: 3,
                backgroundColor: "rgba(234,88,12,0.85)",
                color: "#fff",
              }}
            >
              고
            </span>
          )}
          <span
            className="truncate"
            title={specLabel}
            style={{
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
              minWidth: 0,
              flex: 1,
            }}
          >
            {specLabel}
          </span>
        </span>
        {widestSegW >= 40 && (
          <span
            className="text-white/80 text-[9px] truncate leading-tight"
            style={{ textShadow: "0 1px 1px rgba(0,0,0,0.3)" }}
          >
            {lotLabel ? `${lotLabel} · ` : ""}
            {volumeLabel}
          </span>
        )}
      </div>

      {/* 시간 구성 팝오버 — Portal로 overflow:hidden 회피 */}
      {showTimePopover &&
        typeof document !== "undefined" &&
        createPortal(
          <div
            style={{
              position: "fixed",
              left: blockRef.current
                ? blockRef.current.getBoundingClientRect().left
                : 0,
              top: blockRef.current
                ? blockRef.current.getBoundingClientRect().bottom + 4
                : 0,
              zIndex: 9999,
              background: "#1F2937",
              color: "#F9FAFB",
              borderRadius: 6,
              padding: "8px 12px",
              fontSize: 11,
              lineHeight: 1.6,
              boxShadow: "0 4px 12px rgba(0,0,0,0.3)",
              minWidth: 200,
              pointerEvents: "auto",
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <div style={{ fontWeight: 600, marginBottom: 4 }}>
              작업 시간 구성
            </div>
            {isLate && (
              <div
                style={{
                  marginBottom: 6,
                  padding: "3px 6px",
                  borderRadius: 4,
                  backgroundColor: "#7F1D1D",
                  color: "#FCA5A5",
                  fontWeight: 700,
                  fontSize: 10,
                }}
              >
                납기 초과 +{lateDays}일 — 납기:{" "}
                {task.delivery_date instanceof Date
                  ? task.delivery_date.toLocaleDateString("ko-KR")
                  : new Date(task.delivery_date!).toLocaleDateString("ko-KR")}
              </div>
            )}
            {lotLabel && (
              <div
                style={{ marginBottom: 4, color: "#FCD34D", fontWeight: 600 }}
              >
                작업지시: {lotLabel} ({task.volume_m.toLocaleString()}m)
              </div>
            )}
            <div>실제 작업: {timeBreakdown.actualWork.toFixed(1)}h</div>
            {setupMin > 0 && <div>규격 교체: {setupMin}분</div>}
            {colorChangeMin > 0 && <div>색상 교체: {colorChangeMin}분</div>}
            <div
              style={{
                borderTop: "1px solid #374151",
                marginTop: 4,
                paddingTop: 4,
                fontSize: 10,
                color: "#9CA3AF",
              }}
            >
              <div style={{ fontWeight: 600, color: "#F9FAFB" }}>
                유휴시간 상세 ({timeBreakdown.totalIdleHrs}h)
              </div>
              {timeBreakdown.gapHrs > 0 && (
                <div>주말·야간 gap: {timeBreakdown.gapHrs}h</div>
              )}
              {timeBreakdown.breakHrs > 0 && (
                <div>
                  평일 break: {timeBreakdown.breakHrs}h (점심·저녁·간식)
                </div>
              )}
              {timeBreakdown.details.length > 0 && (
                <div style={{ marginTop: 2 }}>
                  {timeBreakdown.details.slice(0, 4).join(", ")}
                  {timeBreakdown.details.length > 4 &&
                    ` 외 ${timeBreakdown.details.length - 4}일`}
                </div>
              )}
            </div>
            <div
              style={{
                borderTop: "1px solid #374151",
                marginTop: 4,
                paddingTop: 4,
                fontWeight: 600,
              }}
            >
              총 기간: {totalDurationHrs}h
            </div>
          </div>,
          document.body,
        )}
    </div>
  );
});
