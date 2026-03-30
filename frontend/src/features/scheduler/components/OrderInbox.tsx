"use client";

import { useEffect, useState } from "react";
import { useDraggable } from "@dnd-kit/core";
import { useScheduleStore } from "../store/scheduleStore";
import type { Order } from "../types";
import { MOCK_BATCHES_BY_GROUP } from "@/features/plan-register/mock/mockBatchData";
import type { ProductionBatch } from "@/features/scheduler/types";

/** YYYYMMDD → YYYY-MM-DD 변환 */
function formatDeliveryDate(yyyymmdd: string): string {
  if (yyyymmdd.length !== 8) return yyyymmdd;
  return `${yyyymmdd.slice(0, 4)}-${yyyymmdd.slice(4, 6)}-${yyyymmdd.slice(6, 8)}`;
}

/** ProductionBatch → Order 변환 */
function batchToOrder(batch: ProductionBatch): Order {
  return {
    id: batch.id,
    order_number: batch.id,
    product: batch.product,
    spec: batch.spec,
    core_count: 0,
    color: batch.color,
    customer: batch.customer,
    delivery_date: formatDeliveryDate(batch.delivery_date),
    total_length_m: batch.total_length_m,
    priority: "normal",
    equipment_group: batch.equipment_group,
  };
}

/** 각 설비 그룹에서 첫 3개씩 뽑아 mock 수주 목록 생성 (~12개) */
const MOCK_UNSCHEDULED_ORDERS: Order[] = [
  ...MOCK_BATCHES_BY_GROUP["연선"].slice(0, 3).map(batchToOrder),
  ...MOCK_BATCHES_BY_GROUP["B100"].slice(0, 3).map(batchToOrder),
  ...MOCK_BATCHES_BY_GROUP["A100"].slice(0, 3).map(batchToOrder),
  ...MOCK_BATCHES_BY_GROUP["A120"].slice(0, 3).map(batchToOrder),
];

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
    연선: "#6366F1",
    B100: "#0891B2",
    A100: "#059669",
    A120: "#D97706",
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
    연선: "#A5B4FC",
    B100: "#67E8F9",
    A100: "#6EE7B7",
    A120: "#FCD34D",
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
          style={{ color: "#4A2C2A" }}
        >
          {order.product}
        </span>
        <EquipmentGroupBadge group={group} />
      </div>

      <div className="text-[10px] text-gray-500 truncate mb-0.5">
        {order.spec || "\u2014"}
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
          style={{ backgroundColor: "#FEF2F2", color: "#C41230" }}
          onMouseEnter={(e) =>
            (e.currentTarget.style.backgroundColor = "#FEE2E2")
          }
          onMouseLeave={(e) =>
            (e.currentTarget.style.backgroundColor = "#FEF2F2")
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
  const unscheduledOrders = useScheduleStore((s) => s.unscheduledOrders);
  const setUnscheduledOrders = useScheduleStore((s) => s.setUnscheduledOrders);
  const [activeTab, setActiveTab] = useState<EquipmentGroup | "전체">("전체");

  // mock 데이터 제거 — 미배정 작업은 DB 기반 (Stage 2 미실행 시 표시 없음)

  if (unscheduledOrders.length === 0) {
    return (
      <div className="flex items-center justify-center px-4 py-3">
        <span className="text-[11px] text-gray-400">
          미배정 작업이 없습니다
        </span>
      </div>
    );
  }

  // 그룹별 카운트 집계
  const groupCounts = EQUIPMENT_GROUPS.reduce(
    (acc, g) => {
      acc[g] = unscheduledOrders.filter(
        (o) => deriveEquipmentGroup(o) === g,
      ).length;
      return acc;
    },
    {} as Record<EquipmentGroup, number>,
  );

  // 현재 탭에 맞는 주문 필터
  const visibleOrders =
    activeTab === "전체"
      ? unscheduledOrders
      : unscheduledOrders.filter((o) => deriveEquipmentGroup(o) === activeTab);

  const tabColorMap: Record<EquipmentGroup, string> = {
    연선: "#6366F1",
    B100: "#0891B2",
    A100: "#059669",
    A120: "#D97706",
  };

  return (
    <div style={{ minHeight: 0 }}>
      {/* 탭 바 */}
      <div className="flex items-center gap-1 px-3 pt-2 pb-1 border-b border-gray-100">
        <button
          onClick={() => setActiveTab("전체")}
          className="text-[10px] font-medium px-2 py-0.5 rounded-full transition-colors"
          style={{
            backgroundColor: activeTab === "전체" ? "#1F2937" : "#F3F4F6",
            color: activeTab === "전체" ? "#FFFFFF" : "#6B7280",
          }}
        >
          전체 {unscheduledOrders.length}
        </button>
        {EQUIPMENT_GROUPS.filter((g) => groupCounts[g] > 0).map((g) => (
          <button
            key={g}
            onClick={() => setActiveTab(g)}
            className="text-[10px] font-medium px-2 py-0.5 rounded-full transition-colors"
            style={{
              backgroundColor: activeTab === g ? tabColorMap[g] : "#F3F4F6",
              color: activeTab === g ? "#FFFFFF" : "#6B7280",
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
        {visibleOrders.map((order) => (
          <OrderCard key={order.id} order={order} disableDrag={isAnimating} />
        ))}
        {visibleOrders.length === 0 && (
          <span className="text-[11px] text-gray-400 self-center">
            해당 그룹의 미배정 작업이 없습니다
          </span>
        )}
      </div>
    </div>
  );
}
