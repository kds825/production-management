"use client";

import { useCallback } from "react";
import { useItem } from "dnd-timeline";
import type { TimelineItem } from "../types";
import { KBI_BRAND, type ProductGroup } from "@/shared/constants/brand";
import { useScheduleStore } from "../store/scheduleStore";

interface TaskItemProps {
  item: TimelineItem;
}

/** 제품 그룹에서 색상 추출 */
function getTaskColor(product: string): string {
  // 제품명에서 그룹 키 매핑
  const colorMap = KBI_BRAND.colors.taskColors;
  for (const key of Object.keys(colorMap) as ProductGroup[]) {
    if (key !== "default" && product.includes(key)) {
      return colorMap[key];
    }
  }
  return colorMap.default;
}

/** 우선순위별 스타일 */
function getPriorityStyle(priority: string): React.CSSProperties {
  switch (priority) {
    case "critical":
      return {
        backgroundColor: "#DC2626",
        animation: "pulse 1.5s ease-in-out infinite",
      };
    case "urgent":
      return {
        outline: "2px solid #DC2626",
        outlineOffset: "-2px",
      };
    default:
      return {};
  }
}

/** 상태별 오버레이 스타일 */
function getStatusStyle(
  status: string,
  baseColor: string,
): React.CSSProperties {
  switch (status) {
    case "completed":
      return { backgroundColor: "#16A34A" };
    case "delayed":
      return {
        backgroundColor: baseColor,
        outline: "2px solid #DC2626",
        outlineOffset: "-2px",
      };
    case "in_progress":
      // 기본 색상을 약간 어둡게
      return { backgroundColor: baseColor, filter: "brightness(0.85)" };
    default:
      return { backgroundColor: baseColor };
  }
}

export function TaskItem({ item }: TaskItemProps) {
  const task = item.data;
  const baseColor = getTaskColor(task.product);

  const openTaskFormModal = useScheduleStore((s) => s.openTaskFormModal);
  const openContextMenu = useScheduleStore((s) => s.openContextMenu);

  const {
    setNodeRef,
    setActivatorNodeRef,
    itemStyle,
    itemContentStyle,
    isDragging,
  } = useItem({
    id: item.id,
    span: item.span,
    data: { task },
  });

  // 더블클릭 → 수정 모달 열기
  const handleDoubleClick = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();
      e.preventDefault();
      openTaskFormModal({ mode: "edit", taskId: task.id });
    },
    [openTaskFormModal, task.id],
  );

  // 우클릭 → 작업 컨텍스트 메뉴 열기
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

  const barStyle: React.CSSProperties = {
    ...statusStyle,
    ...priorityStyle,
    borderRadius: "4px",
    boxShadow: isDragging
      ? "0 4px 12px rgba(0,0,0,0.3)"
      : "0 1px 3px rgba(0,0,0,0.15)",
    cursor: isDragging ? "grabbing" : "grab",
    userSelect: "none",
    overflow: "hidden",
    minWidth: "60px",
    height: "100%",
    opacity: isDragging ? 0.7 : 1,
    transition: isDragging ? "none" : "box-shadow 0.15s ease",
  };

  // 볼륨을 읽기 좋은 단위로 변환
  const volumeLabel =
    task.volume_m >= 1000
      ? `${(task.volume_m / 1000).toFixed(1)}km`
      : `${task.volume_m}m`;

  return (
    <div ref={setNodeRef} style={itemStyle}>
      <div
        ref={setActivatorNodeRef}
        style={{ ...itemContentStyle, ...barStyle }}
        onDoubleClick={handleDoubleClick}
        onContextMenu={handleContextMenu}
      >
        {/* 내부 텍스트 */}
        <div
          className="flex flex-col justify-center px-2 h-full gap-px"
          style={{ minWidth: 0 }}
        >
          {/* 제품명 + 규격 */}
          <span
            className="text-white text-[10px] font-semibold truncate leading-tight"
            style={{ textShadow: "0 1px 2px rgba(0,0,0,0.4)" }}
          >
            {task.product}
            {task.spec ? ` ${task.spec}` : ""}
          </span>

          {/* 색상 코어수 + 물량 */}
          <span
            className="text-white/80 text-[9px] truncate leading-tight"
            style={{ textShadow: "0 1px 1px rgba(0,0,0,0.3)" }}
          >
            {task.color && `${task.color} `}
            {task.core_count > 0 && `${task.core_count}C `}
            {volumeLabel}
          </span>
        </div>

        {/* 우선순위 배지 */}
        {task.priority !== "normal" && (
          <div
            className="absolute top-0.5 right-0.5 text-[8px] font-bold text-white bg-red-600 rounded px-0.5"
            style={{ lineHeight: "1.2" }}
          >
            {task.priority === "critical" ? "긴급" : "우선"}
          </div>
        )}
      </div>
    </div>
  );
}
