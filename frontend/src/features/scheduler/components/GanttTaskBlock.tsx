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
import { timeToX, ROW_HEIGHT } from "../utils/ganttUtils";

interface GanttTaskBlockProps {
  task: ScheduleTask;
  rangeStart: number;
  dayWidth: number;
}

const MS_PER_DAY = 24 * 60 * 60 * 1000;
const MS_PER_HOUR = 60 * 60 * 1000;

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
}: GanttTaskBlockProps) {
  // 시스 공정이면 sheath_color(task.color) 기반 색상 사용, 아니면 제품 그룹 색상
  const sheathOverride = getSheathColor(task.equipment_id, task.color);
  const baseColor = sheathOverride ?? getTaskColor(task.product);
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
    previewOffsetMs !== 0 ? (previewOffsetMs / MS_PER_DAY) * dayWidth : 0;

  const left = timeToX(startTs, rangeStart, dayWidth) + previewOffsetPx;
  const width =
    timeToX(endTs, rangeStart, dayWidth) -
    timeToX(startTs, rangeStart, dayWidth);

  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: task.id,
    data: { type: "task", task, equipmentId: task.equipment_id },
    disabled: false,
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

  const handleClick = useCallback(
    (e: React.MouseEvent) => {
      // 리사이즈 핸들에서 발생한 이벤트는 무시
      if ((e.target as HTMLElement).closest("[data-resize-handle]")) return;
      e.stopPropagation();
      // 이미 선택된 경우 선택 해제, 아니면 선택
      selectTask(selectedTaskId === task.id ? null : task.id);
    },
    [selectTask, selectedTaskId, task.id],
  );

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

  const priorityStyle = getPriorityStyle(task.priority);
  const statusStyle = getStatusStyle(task.status, baseColor);
  const HANDLE_W = 6;

  const barStyle: React.CSSProperties = {
    ...statusStyle,
    ...priorityStyle,
    borderRadius: 4,
    boxShadow: isDragging
      ? "0 4px 12px rgba(0,0,0,0.3)"
      : "0 1px 3px rgba(0,0,0,0.15)",
    userSelect: "none",
    overflow: "hidden",
    height: ROW_HEIGHT - 8,
    opacity: isDragging ? 0 : 1,
    transition: isDragging ? "none" : "box-shadow 0.15s ease",
    position: "relative",
    display: "flex",
    alignItems: "stretch",
  };

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

  const isSelected = selectedTaskId === task.id;

  // --- 시간 구성 팝오버 ---
  const [showTimePopover, setShowTimePopover] = useState(false);
  const blockRef = useRef<HTMLDivElement>(null);

  const totalDurationHrs = ((endTs - startTs) / MS_PER_HOUR).toFixed(1);
  const setupMin = task.setup_time_min ?? task.changeover_min ?? 0;
  const colorChangeMin = task.color_change_min ?? 0;
  const totalChangeoverMin = setupMin + colorChangeMin;
  // 작업일수로부터 부동시간 추정 (월-목 2h/day, 금 10h/day)
  const workDays = Math.max(1, Math.ceil((endTs - startTs) / MS_PER_DAY));
  const idleHrsEstimate = workDays * 2; // 평균 근사
  const actualWorkHrs = Math.max(
    0,
    parseFloat(totalDurationHrs) - idleHrsEstimate - totalChangeoverMin / 60,
  );

  const handleBlockClick = useCallback(
    (e: React.MouseEvent) => {
      if ((e.target as HTMLElement).closest("[data-resize-handle]")) return;
      e.stopPropagation();
      // 선택 + 팝오버 토글
      if (selectedTaskId === task.id) {
        setShowTimePopover((prev) => !prev);
      } else {
        selectTask(task.id);
        setShowTimePopover(true);
      }
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

  return (
    <div
      ref={(node) => {
        setNodeRef(node);
        (blockRef as React.MutableRefObject<HTMLDivElement | null>).current =
          node;
      }}
      data-draggable
      data-task-id={task.id}
      data-equipment-id={task.equipment_id}
      style={{
        position: "absolute",
        left,
        top: 4,
        width: Math.max(width, 30),
        zIndex: isDragging ? 20 : isSelected ? 10 : 2,
        transition: previewOffsetPx !== 0 ? "left 0.15s ease-out" : "none",
        outline: isSelected ? "2px solid #FBBF24" : "none",
        outlineOffset: 1,
        borderRadius: 4,
      }}
      onClick={handleBlockClick}
      onDoubleClick={handleDoubleClick}
      onContextMenu={handleContextMenu}
    >
      <div style={barStyle}>
        {/* 좌측 리사이즈 핸들 — dnd 없음 */}
        {isEditMode && (
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

        {/* 규격교체 세그먼트 — 좌측에 어두운 줄무늬 영역으로 표시 */}
        {hasChangeover && (
          <div
            style={{
              width: changeoverPx,
              flexShrink: 0,
              alignSelf: "stretch",
              // 줄무늬 패턴: 어두운 반투명 색상 + 대각선 스트라이프
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
            {/* 폭이 충분할 때만 교체 시간 텍스트 표시 */}
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

        {/* 가운데 — dnd listeners 여기에만 */}
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
            cursor: isDragging ? "grabbing" : "grab",
          }}
        >
          {/* 수주 ID — 블록 상단에 작게 표시 */}
          {task.order_id && width >= 50 && (
            <span
              className="text-[8px] truncate leading-tight"
              style={{
                color: "rgba(255,255,255,0.7)",
                textShadow: "0 1px 1px rgba(0,0,0,0.4)",
                letterSpacing: "0.02em",
              }}
            >
              #{task.order_id}
            </span>
          )}
          {/* 블록이 충분히 넓으면(>60px) 공장 수동 계획표 스타일로 2행 표시:
              1행: "{spec} {color}"  (예: "95SQ 갈")
              2행: "{volume_m}m"     (예: "1200m")
              좁으면 기존 1행 스타일 유지 */}
          {width > 60 ? (
            <>
              <span
                className="text-white text-[10px] font-semibold truncate leading-tight"
                style={{ textShadow: "0 1px 2px rgba(0,0,0,0.4)" }}
              >
                {task.spec || task.product}
                {task.color ? ` ${task.color}` : ""}
              </span>
              <span
                className="text-white/80 text-[9px] truncate leading-tight"
                style={{ textShadow: "0 1px 1px rgba(0,0,0,0.3)" }}
              >
                {volumeLabel}
              </span>
            </>
          ) : (
            <>
              <span
                className="text-white text-[10px] font-semibold truncate leading-tight"
                style={{ textShadow: "0 1px 2px rgba(0,0,0,0.4)" }}
              >
                {task.product}
                {task.spec ? ` ${task.spec}` : ""}
              </span>
              <span
                className="text-white/80 text-[9px] truncate leading-tight"
                style={{ textShadow: "0 1px 1px rgba(0,0,0,0.3)" }}
              >
                {task.color && `${task.color} `}
                {task.core_count > 0 && `${task.core_count}C `}
                {volumeLabel}
              </span>
            </>
          )}
        </div>

        {/* 우측 리사이즈 핸들 — dnd 없음 */}
        {isEditMode && (
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

        {/* 우선순위 배지 */}
        {task.priority !== "normal" && (
          <div
            className="absolute top-0.5 right-1 text-[8px] font-bold text-white bg-red-600 rounded px-0.5"
            style={{ lineHeight: "1.2", zIndex: 3 }}
          >
            {task.priority === "critical" ? "긴급" : "우선"}
          </div>
        )}
      </div>

      {/* 시간 구성 팝오버 — Portal로 overflow:hidden 회피 */}
      {showTimePopover &&
        isSelected &&
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
            <div>실제 작업: {actualWorkHrs.toFixed(1)}h</div>
            {setupMin > 0 && <div>규격 교체: {setupMin}분</div>}
            {colorChangeMin > 0 && <div>색상 교체: {colorChangeMin}분</div>}
            <div>
              부동시간: ~{idleHrsEstimate}h ({workDays}일)
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
