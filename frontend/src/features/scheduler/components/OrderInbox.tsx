"use client";

import { useEffect } from "react";
import { useScheduleStore } from "../store/scheduleStore";
import { apiFetch } from "@/shared/api/client";
import type { Order } from "../types";

/** 우선순위별 배지 스타일 */
function PriorityBadge({ priority }: { priority: Order["priority"] }) {
  const map: Record<Order["priority"], { label: string; color: string }> = {
    normal: { label: "일반", color: "#6B7280" },
    urgent: { label: "긴급", color: "#DC2626" },
    critical: { label: "최우선", color: "#EA580C" },
  };
  const { label, color } = map[priority];
  return (
    <span
      className="text-[9px] font-semibold px-1.5 py-0.5 rounded-full text-white"
      style={{ backgroundColor: color }}
    >
      {label}
    </span>
  );
}

/** 날짜 포맷 헬퍼 (YYYY-MM-DD → M/D) */
function fmtDate(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

/** 길이를 km / m 단위로 표시 */
function fmtLength(m: number): string {
  return m >= 1000 ? `${(m / 1000).toFixed(1)}km` : `${m}m`;
}

/** 우선순위별 카드 테두리 색상 */
function borderColor(priority: Order["priority"]): string {
  switch (priority) {
    case "critical":
      return "#EA580C";
    case "urgent":
      return "#DC2626";
    default:
      return "#E5E7EB";
  }
}

/** 수주 카드 */
function OrderCard({ order }: { order: Order }) {
  const openTaskFormModal = useScheduleStore((s) => s.openTaskFormModal);

  function handleDragStart(e: React.DragEvent) {
    e.dataTransfer.setData("orderId", order.id);
    e.dataTransfer.effectAllowed = "copy";
  }

  function handleAddClick() {
    openTaskFormModal({
      mode: "create",
      prefill: {},
    });
  }

  return (
    <div
      draggable
      onDragStart={handleDragStart}
      className="p-2.5 bg-white rounded-lg cursor-grab active:cursor-grabbing hover:shadow-md transition-shadow"
      style={{
        border: `1.5px solid ${borderColor(order.priority)}`,
        userSelect: "none",
      }}
    >
      <div className="flex items-start justify-between gap-1 mb-1">
        <span
          className="text-[10px] font-semibold truncate flex-1"
          style={{ color: "#4A2C2A" }}
        >
          {order.product}
        </span>
        <PriorityBadge priority={order.priority} />
      </div>

      <div className="text-[10px] text-gray-500 truncate mb-0.5">
        {order.spec || "—"}
        {order.core_count > 0 && ` · ${order.core_count}C`}
        {order.color && ` · ${order.color}`}
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
        <span className="text-[9px] text-gray-400">#{order.order_number}</span>
      </div>

      {/* 클릭으로도 작업 추가 */}
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
    </div>
  );
}

/** OrderInbox: 미배정 수주 목록 패널 */
export function OrderInbox() {
  const unscheduledOrders = useScheduleStore((s) => s.unscheduledOrders);
  const setUnscheduledOrders = useScheduleStore((s) => s.setUnscheduledOrders);

  // 백엔드에서 미배정 수주 로드
  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const orders = await apiFetch<Order[]>("/api/orders?scheduled=false");
        if (!cancelled) setUnscheduledOrders(orders);
      } catch {
        // API 없을 때 조용히 실패 (빈 목록 유지)
        console.warn("[OrderInbox] 미배정 수주 로드 실패");
      }
    }

    load();
    return () => {
      cancelled = true;
    };
  }, [setUnscheduledOrders]);

  return (
    <aside
      className="flex flex-col bg-gray-50 border-r border-gray-200 overflow-hidden"
      style={{ width: 264, minWidth: 264, flexShrink: 0 }}
    >
      {/* 헤더 */}
      <div
        className="flex items-center justify-between px-3 py-2.5 border-b border-gray-200 bg-white"
        style={{ minHeight: 40 }}
      >
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold" style={{ color: "#4A2C2A" }}>
            미배정 수주
          </span>
          {unscheduledOrders.length > 0 && (
            <span
              className="text-[10px] font-medium text-white px-1.5 py-0.5 rounded-full"
              style={{ backgroundColor: "#C41230" }}
            >
              {unscheduledOrders.length}
            </span>
          )}
        </div>
        <span className="text-[9px] text-gray-400">드래그하여 배정</span>
      </div>

      {/* 수주 카드 목록 */}
      <div className="flex-1 overflow-y-auto p-2 flex flex-col gap-1.5">
        {unscheduledOrders.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full gap-2 text-center py-8">
            <span className="text-2xl">📋</span>
            <span className="text-[11px] text-gray-400">
              미배정 수주가 없습니다
            </span>
          </div>
        ) : (
          unscheduledOrders.map((order) => (
            <OrderCard key={order.id} order={order} />
          ))
        )}
      </div>
    </aside>
  );
}
