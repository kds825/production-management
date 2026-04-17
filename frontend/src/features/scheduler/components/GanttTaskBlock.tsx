"use client";

import { useCallback, useRef, useState, memo } from "react";
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
}

const MS_PER_HOUR = 60 * 60 * 1000;
const NEW_BATCH_WINDOW_MS = 5 * 60 * 1000; // 5분 이내 생성된 배치는 "신규"로 간주

/** created_at이 현재 시각 기준 5분 이내이면 신규 배치로 판별 */
function isNewBatch(createdAt: Date | undefined): boolean {
  if (!createdAt) return false;
  return Date.now() - createdAt.getTime() < NEW_BATCH_WINDOW_MS;
}

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

/** 시스 공정(SH-A100/SH-A120) 설비의 sheath_color → 블록 배경색 매핑 */
const SHEATH_COLOR_MAP: Record<string, string> = {
  흑: "#374151",
  갈: "#92400E",
  회: "#6B7280",
  청: "#1E40AF",
  녹: "#065F46",
  황: "#B45309",
  "흑/적": "#991B1B",
};

const SHEATH_EQUIPMENT_IDS = new Set(["SH-A100", "SH-A120"]);

/** 시스 설비일 때 task.color 기반 배경색 반환, 아니면 null */
function getSheathColor(equipmentId: string, color: string): string | null {
  if (!SHEATH_EQUIPMENT_IDS.has(equipmentId)) return null;
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

  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: task.id,
    data: { type: "task", task, equipmentId: task.equipment_id },
    disabled: isFrozen || !isEditMode,
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
  //   시스 설비(SH-A100/A120): 색상(흑/갈/회…)
  //   고압 제품(spec에 KCMIL 포함): "500KCMIL" / "1C × 500KCMIL"
  //   CORE/AL-CORE 배치(T6BO 중심선): spec에서 원본 SQ 추출 (sq_mm2=35 무시)
  //   1코어: "50SQ"
  //   다심(2코어 이상): "4C × 50SQ"
  //   SQ 정보 없으면: spec → product 순 폴백
  const specLabel = (() => {
    if (SHEATH_EQUIPMENT_IDS.has(task.equipment_id) && task.color) {
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
  const isNew = isNewBatch(task.created_at);

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

  return (
    <div
      ref={(node) => {
        setNodeRef(node);
        (blockRef as { current: HTMLDivElement | null }).current = node;
      }}
      data-draggable
      data-task-id={task.id}
      data-testid={`gantt-block-${task.id}`}
      data-equipment-id={task.equipment_id}
      data-batch-group={task.batch_group ?? ""}
      data-sq-mm2={String(task.sq_mm2 ?? "")}
      data-spec-list-length={String((task.spec_list ?? []).length)}
      style={{
        position: "absolute",
        left,
        top: laneTop,
        width: Math.max(width, 30),
        height: ROW_HEIGHT - 8,
        zIndex: isDragging ? 20 : isSelected ? 10 : 2,
        transition: previewOffsetPx !== 0 ? "left 0.15s ease-out" : "none",
        cursor:
          !isEditMode || isFrozen
            ? "default"
            : isDragging
              ? "grabbing"
              : "grab",
      }}
      onClick={handleBlockClick}
      onDoubleClick={handleDoubleClick}
      onContextMenu={handleContextMenu}
      onMouseEnter={() => setShowTimePopover(true)}
      onMouseLeave={() => setShowTimePopover(false)}
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

            {/* 가운데 콘텐츠 + dnd listeners — 첫 세그먼트만 */}
            {isFirst ? (
              <div
                {...listeners}
                {...attributes}
                style={{
                  flex: 1,
                  minWidth: 0,
                  display: "flex",
                  flexDirection: "column",
                  justifyContent: "center",
                  paddingLeft: 4,
                  paddingRight: 4,
                  gap: 1,
                  cursor: "inherit",
                }}
              >
                <span
                  className="text-white text-[10px] font-semibold leading-tight"
                  style={{
                    textShadow: "0 1px 2px rgba(0,0,0,0.4)",
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
                  <span className="truncate">{specLabel}</span>
                </span>
                {segW >= 40 && (
                  <span
                    className="text-white/80 text-[9px] truncate leading-tight"
                    style={{ textShadow: "0 1px 1px rgba(0,0,0,0.3)" }}
                  >
                    {lotLabel ? `${lotLabel} · ` : ""}
                    {volumeLabel}
                  </span>
                )}
              </div>
            ) : (
              /* 중간/마지막 세그먼트 — flex 여백 채우기 */
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
              {timeBreakdown.weekendHrs > 0 && (
                <div>주말 휴무: {timeBreakdown.weekendHrs}h (토~월 08시)</div>
              )}
              <div>
                일일 부동: {timeBreakdown.dailyIdleHrs}h (월~목 2h, 금 10h ×{" "}
                {timeBreakdown.workingDays}일)
              </div>
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
