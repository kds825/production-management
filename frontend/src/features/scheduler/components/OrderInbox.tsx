"use client";

import { useState } from "react";
import { useDraggable, useDroppable, useDndContext } from "@dnd-kit/core";
import { ArrowDownTrayIcon } from "@heroicons/react/24/outline";
import { useScheduleStore } from "../store/scheduleStore";
import type { InboxItem, Order } from "../types";
import { BatchGroupCard } from "./BatchGroupCard";

/** 장비 그룹 목록 — 순서 고정 */
const EQUIPMENT_GROUPS = ["연선", "B100", "A100", "A120"] as const;
type EquipmentGroup = (typeof EQUIPMENT_GROUPS)[number];

/**
 * 수주의 product/spec 필드로부터 장비 그룹을 파생한다.
 * 백엔드 equipment_group이 있으면 그것을 우선 사용한다.
 *
 * 규칙:
 * - equipment_group 필드가 있으면 그대로 사용
 * - product에 "CV" 포함 → A120 (고압 피복 공정)
 * - spec SQ 크기 기반:
 *   - ≤50SQ → B100
 *   - >50SQ → A100
 *   - 파싱 실패 → 연선 (기본)
 */
function deriveEquipmentGroup(order: Order): EquipmentGroup {
  if (
    order.equipment_group &&
    (EQUIPMENT_GROUPS as readonly string[]).includes(order.equipment_group)
  ) {
    return order.equipment_group as EquipmentGroup;
  }

  const product = (order.product || "").toUpperCase();
  if (product.includes("CV")) {
    return "A120";
  }

  // spec에서 SQ 앞 숫자 파싱 (예: "95SQ" → 95)
  const sqMatch = (order.spec || "").match(/^(\d+)SQ$/i);
  if (sqMatch) {
    const sq = parseInt(sqMatch[1], 10);
    if (sq <= 50) return "B100";
    return "A100";
  }

  // 파싱 실패 → 연선으로 분류
  return "연선";
}

/** 장비 그룹 배지 스타일 */
function EquipmentGroupBadge({ group }: { group: EquipmentGroup }) {
  const colorMap: Record<EquipmentGroup, string> = {
    연선: "var(--viz-tab-yeonseon-bg)",
    B100: "var(--viz-tab-b100-bg)",
    A100: "var(--status-success-text)",
    A120: "var(--status-warning)",
  };
  return (
    <span
      className="text-[9px] font-semibold px-1.5 py-0.5 rounded-full text-white"
      style={{ backgroundColor: colorMap[group] }}
    >
      {group}
    </span>
  );
}

/** 날짜 포맷 헬퍼 (YYYY-MM-DD -> M/D) */
function fmtDate(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

/** 길이를 m 단위로 표시 (천 단위 구분자 포함) */
function fmtLength(m: number): string {
  return `${m.toLocaleString()}m`;
}

interface OrderCardProps {
  order: Order;
  /** Panel is mid-animation — disable drag to prevent ghost artifacts */
  disableDrag?: boolean;
}

/** 수주 카드 — @dnd-kit useDraggable로 드래그 가능. 외부에서도 재사용 가능하도록 export. */
export function OrderCard({ order, disableDrag = false }: OrderCardProps) {
  const openTaskFormModal = useScheduleStore((s) => s.openTaskFormModal);
  const isEditMode = useScheduleStore((s) => s.isEditMode);
  const group = deriveEquipmentGroup(order);

  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: `order-${order.id}`,
    data: {
      type: "order",
      order,
      // equipment_group을 드래그 데이터에 포함하여 drag constraint에서 활용
      equipment_group: group,
    },
    // Drag always enabled (warning shown if not in edit mode), disabled during panel animation only
    disabled: disableDrag,
  });

  function handleAddClick() {
    openTaskFormModal({
      mode: "create",
      prefill: {},
    });
  }

  const borderColorMap: Record<EquipmentGroup, string> = {
    연선: "var(--viz-tab-yeonseon-accent)",
    B100: "var(--viz-tab-b100-accent)",
    A100: "var(--viz-tab-a100-accent)",
    A120: "var(--status-warning-border)",
  };

  return (
    <div
      ref={setNodeRef}
      {...(!disableDrag ? { ...listeners, ...attributes } : {})}
      data-draggable
      className="p-2.5 bg-white rounded-lg transition-shadow flex-shrink-0"
      style={{
        width: 200,
        border: `1.5px solid ${borderColorMap[group]}`,
        userSelect: "none",
        cursor: disableDrag ? "default" : isDragging ? "grabbing" : "grab",
        opacity: isDragging ? 0.5 : 1,
        boxShadow: isDragging
          ? "0 4px 12px rgba(0,0,0,0.2)"
          : "0 1px 2px rgba(0,0,0,0.05)",
      }}
    >
      <div className="flex items-start justify-between gap-1 mb-1">
        <span
          className="text-[10px] font-semibold truncate flex-1"
          style={{ color: "var(--kbi-brown)" }}
        >
          {order.product}
        </span>
        <EquipmentGroupBadge group={group} />
      </div>

      <div className="text-[10px] text-gray-500 truncate mb-0.5">
        {order.spec || "–"}
        {order.core_count > 0 && ` \u00B7 ${order.core_count}C`}
        {order.color && ` \u00B7 ${order.color}`}
      </div>

      <div className="flex items-center justify-between">
        <span className="text-[9px] text-gray-400 truncate">
          {order.customer}
        </span>
        <span className="text-[9px] text-gray-500 flex-shrink-0 ml-1">
          납기 {fmtDate(order.delivery_date)}
        </span>
      </div>

      <div className="flex items-center justify-between mt-1 pt-1 border-t border-gray-100">
        <span className="text-[9px] font-medium text-gray-600">
          {fmtLength(order.total_length_m)}
        </span>
        <span className="text-[9px] text-gray-400">{order.order_number}</span>
      </div>

      {/* 클릭으로도 작업 추가 */}
      {isEditMode && (
        <button
          onClick={handleAddClick}
          className="mt-1.5 w-full text-[9px] font-medium text-center py-1 rounded transition-colors"
          style={{
            backgroundColor: "var(--kbi-red-tint-5)",
            color: "var(--color-brand-primary)",
          }}
          onMouseEnter={(e) =>
            (e.currentTarget.style.backgroundColor = "var(--kbi-red-tint-12)")
          }
          onMouseLeave={(e) =>
            (e.currentTarget.style.backgroundColor = "var(--kbi-red-tint-5)")
          }
        >
          + 작업 배정
        </button>
      )}
    </div>
  );
}

interface OrderInboxProps {
  /** Pass true while the enclosing CollapsiblePanel is animating */
  isAnimating?: boolean;
}

/**
 * OrderInbox: 미배정 작업 목록.
 * 장비 그룹별 탭으로 분류하여 렌더링 — CollapsiblePanel 내부에 배치됩니다.
 */
export function OrderInbox({ isAnimating = false }: OrderInboxProps) {
  const unscheduledItems = useScheduleStore((s) => s.unscheduledItems);
  const [activeTab, setActiveTab] = useState<EquipmentGroup | "전체">("전체");

  // Task 3.1: inbox dropzone — 드래그된 task/batch_group_task를 미배정으로 되돌리는 drop target.
  // 실제 drop 처리 로직은 Task 4.3에서 scheduler page에서 연결한다.
  const { setNodeRef: setDropzoneRef, isOver } = useDroppable({
    id: "inbox-dropzone",
    data: { type: "inbox-dropzone" },
  });

  // active drag의 type을 감지하여 배너 노출 여부를 결정한다.
  const dndContext = useDndContext();
  const activeType = dndContext.active?.data.current?.type as
    | string
    | undefined;
  const isTaskDrag = activeType === "task" || activeType === "batch_group_task";

  // mock 데이터 제거 — 미배정 작업은 DB 기반 (Stage 2 미실행 시 표시 없음)

  // Task 5.4: 렌더링/필터/카운트를 InboxItem union-aware로 전환.
  // Task 4.2가 도입한 orderItems 어댑터는 이 파일 내부에서는 더 이상 쓰이지 않아 제거.
  // TaskFormModal / scheduleStore 는 각자 자체적으로 unscheduledItems에서 order kind를 추출한다.
  // union-aware 그룹 분류: order는 deriveEquipmentGroup, batch_group은 첫 공정의 equipment_group.
  // batch_group의 equipment_group이 EQUIPMENT_GROUPS에 없으면 어느 탭에도 집계되지 않음 (전체 탭에만 표시).
  function itemEquipmentGroup(item: InboxItem): EquipmentGroup | null {
    if (item.kind === "order") {
      return deriveEquipmentGroup(item.order);
    }
    const firstProcess = item.group.processes[0];
    const g = firstProcess?.equipment_group;
    if (g && (EQUIPMENT_GROUPS as readonly string[]).includes(g)) {
      return g as EquipmentGroup;
    }
    return null;
  }

  if (unscheduledItems.length === 0) {
    return (
      <div className="flex items-center justify-center px-4 py-3">
        <span className="text-[11px] text-gray-400">
          미배정 작업이 없습니다
        </span>
      </div>
    );
  }

  // 그룹별 카운트 집계 (union-aware)
  const groupCounts = EQUIPMENT_GROUPS.reduce(
    (acc, g) => {
      acc[g] = unscheduledItems.filter(
        (i) => itemEquipmentGroup(i) === g,
      ).length;
      return acc;
    },
    {} as Record<EquipmentGroup, number>,
  );

  // 현재 탭에 맞는 항목 필터 (union-aware)
  const visibleItems: InboxItem[] =
    activeTab === "전체"
      ? unscheduledItems
      : unscheduledItems.filter((i) => itemEquipmentGroup(i) === activeTab);

  const tabColorMap: Record<EquipmentGroup, string> = {
    연선: "var(--viz-tab-yeonseon-bg)",
    B100: "var(--viz-tab-b100-bg)",
    A100: "var(--status-success-text)",
    A120: "var(--status-warning)",
  };

  return (
    <div style={{ minHeight: 0 }}>
      {/* Task 3.1: 드래그 중 노출되는 미배정 drop zone 배너.
          isTaskDrag가 true일 때만 DOM에 마운트하여 불필요한 droppable 등록을 방지한다. */}
      {isTaskDrag && (
        <div
          ref={setDropzoneRef}
          role="status"
          aria-live="polite"
          className={
            "mb-2 flex items-center gap-2 px-3 py-2 rounded-md text-xs " +
            "border-2 border-dashed " +
            "text-[color:var(--color-brand-primary)] " +
            "bg-[color:var(--color-bg-muted)] " +
            (isOver
              ? "border-[color:var(--color-brand-primary)] opacity-100"
              : "border-[color:var(--color-border-default)] opacity-80")
          }
        >
          <ArrowDownTrayIcon width={14} height={14} aria-hidden />
          <span>여기에 놓으면 미배정 작업으로 이동합니다</span>
        </div>
      )}
      {/* 탭 바 */}
      <div className="flex items-center gap-1 px-3 pt-2 pb-1 border-b border-gray-100">
        <button
          onClick={() => setActiveTab("전체")}
          className="text-[10px] font-medium px-2 py-0.5 rounded-full transition-colors"
          style={{
            backgroundColor:
              activeTab === "전체"
                ? "var(--color-text-primary)"
                : "var(--color-bg-muted)",
            color:
              activeTab === "전체"
                ? "var(--color-text-inverse)"
                : "var(--color-text-secondary)",
          }}
        >
          전체 {unscheduledItems.length}
        </button>
        {EQUIPMENT_GROUPS.filter((g) => groupCounts[g] > 0).map((g) => (
          <button
            key={g}
            onClick={() => setActiveTab(g)}
            className="text-[10px] font-medium px-2 py-0.5 rounded-full transition-colors"
            style={{
              backgroundColor:
                activeTab === g ? tabColorMap[g] : "var(--color-bg-muted)",
              color:
                activeTab === g
                  ? "var(--color-text-inverse)"
                  : "var(--color-text-secondary)",
            }}
          >
            {g} {groupCounts[g]}
          </button>
        ))}
      </div>

      {/* 카드 리스트 */}
      <div
        className="flex flex-row gap-2 px-3 py-2 overflow-x-auto"
        style={{ minHeight: 0 }}
      >
        {visibleItems.map((item) =>
          item.kind === "order" ? (
            <OrderCard
              key={`order-${item.order.id}`}
              order={item.order}
              disableDrag={isAnimating}
            />
          ) : (
            <BatchGroupCard
              key={`bg-${item.group.batch_group}`}
              group={item.group}
            />
          ),
        )}
        {visibleItems.length === 0 && (
          <span className="text-[11px] text-gray-400 self-center">
            해당 그룹의 미배정 작업이 없습니다
          </span>
        )}
      </div>
    </div>
  );
}
