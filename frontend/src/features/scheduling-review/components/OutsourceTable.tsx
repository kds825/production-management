"use client";

import type { OutsourcedOrder } from "../types";
import { formatDeliveryDate } from "@/shared/utils/batchGrouping";

interface OutsourceTableProps {
  orders: OutsourcedOrder[];
}

const COL_DEFS = [
  { key: "order_id", label: "수주번호", align: "left", width: 110 },
  { key: "product_group", label: "품목", align: "left", width: 120 },
  { key: "spec_raw", label: "규격", align: "left", width: 130 },
  { key: "sheath_color", label: "색상", align: "left", width: 80 },
  { key: "customer_name", label: "거래처", align: "left", width: 120 },
  { key: "due_date", label: "납기", align: "left", width: 90 },
  { key: "drum_length_m", label: "조장(M)", align: "right", width: 80 },
  { key: "drum_count", label: "개수(ea)", align: "right", width: 80 },
  { key: "total_length_m", label: "수량(M)", align: "right", width: 90 },
  { key: "reason", label: "외주사유", align: "left", width: 140 },
] as const;

const totalWidth = COL_DEFS.reduce((sum, c) => sum + c.width, 0);

function getCellValue(
  col: (typeof COL_DEFS)[number],
  order: OutsourcedOrder,
): string {
  if (col.key === "due_date") return formatDeliveryDate(order.due_date);
  const raw = order[col.key as keyof OutsourcedOrder];
  if (typeof raw === "number") return raw.toLocaleString();
  return String(raw ?? "");
}

export function OutsourceTable({ orders }: OutsourceTableProps) {
  if (orders.length === 0) return null;

  return (
    <div>
      {/* Header */}
      <div className="flex items-center gap-3 mb-2">
        <h3
          className="text-sm font-semibold"
          style={{ color: "var(--color-text-primary)" }}
        >
          외주 생산 ({orders.length}건)
        </h3>
        <span className="text-[11px]" style={{ color: "var(--status-warning-text)" }}>
          외주 업체에 발주되는 항목입니다
        </span>
      </div>

      {/* Table */}
      <div
        className="rounded-lg overflow-hidden"
        style={{
          border: "1px solid var(--status-warning)",
          backgroundColor: "var(--status-warning-bg)",
        }}
      >
        <div className="overflow-x-auto">
          <table
            style={{
              width: totalWidth,
              minWidth: "100%",
              borderCollapse: "collapse",
              tableLayout: "fixed",
            }}
          >
            <colgroup>
              {COL_DEFS.map((col) => (
                <col key={col.key} style={{ width: col.width }} />
              ))}
            </colgroup>
            <thead>
              <tr style={{ backgroundColor: "var(--status-warning-bg)" }}>
                {COL_DEFS.map((col, i) => (
                  <th
                    key={col.key}
                    className="text-[10px] font-semibold px-3 py-2"
                    style={{
                      color: "var(--status-warning-text)",
                      textAlign: col.align as "left" | "right",
                      borderBottom: "1px solid var(--status-warning-border)",
                      borderRight:
                        i < COL_DEFS.length - 1 ? "1px solid var(--status-warning-border)" : "none",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {col.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {orders.map((order, rowIdx) => {
                const rowBg = rowIdx % 2 === 0 ? "var(--status-warning-bg)" : "var(--status-warning-bg)";
                return (
                  <tr
                    key={`${order.order_id}-${order.order_line}`}
                    style={{ backgroundColor: rowBg }}
                    onMouseEnter={(e) => {
                      (e.currentTarget as HTMLElement).style.backgroundColor =
                        "var(--status-warning-border)";
                    }}
                    onMouseLeave={(e) => {
                      (e.currentTarget as HTMLElement).style.backgroundColor =
                        rowBg;
                    }}
                  >
                    {COL_DEFS.map((col, colIdx) => (
                      <td
                        key={col.key}
                        className="px-3"
                        style={{
                          height: 36,
                          borderBottom: "1px solid var(--status-warning-border)",
                          borderRight:
                            colIdx < COL_DEFS.length - 1
                              ? "1px solid var(--status-warning-bg)"
                              : "none",
                          borderLeft:
                            colIdx === 0 ? "3px solid var(--status-warning)" : "none",
                          verticalAlign: "middle",
                          overflow: "hidden",
                        }}
                      >
                        <span
                          className="block truncate text-[11px]"
                          style={{
                            textAlign: col.align as "left" | "right",
                            color: "#78350F",
                          }}
                        >
                          {getCellValue(col, order) || (
                            <span style={{ color: "#CBD5E1" }}>–</span>
                          )}
                        </span>
                      </td>
                    ))}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
