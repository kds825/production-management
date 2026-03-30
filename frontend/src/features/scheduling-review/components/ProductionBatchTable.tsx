"use client";

import { useMemo } from "react";
import type { SchedulingBatch } from "../types";
import type { ProcessGroup } from "@/shared/constants/processGroups";
import { PROCESS_STATUS_COLORS } from "@/shared/constants/processGroups";
import {
  assignBatchNumbers,
  getBatchGroupKey,
  formatDeliveryDate,
} from "@/shared/utils/batchGrouping";

interface ProductionBatchTableProps {
  title: string;
  batches: SchedulingBatch[];
  processGroup: ProcessGroup;
  showProcessColumn?: boolean;
  /** WIP 클릭 시 하이라이트할 배치 ID */
  highlightedBatchId?: string | null;
  /** 재고 사용 배치 클릭 → WIP로 이동 */
  onBatchWipClick?: (batchId: string) => void;
}

const COL_DEFS = [
  { key: "processGroup", label: "구분", align: "left", width: 60 },
  { key: "batch_label", label: "배치", align: "left", width: 64 },
  { key: "product", label: "품목", align: "left", width: 120 },
  { key: "spec", label: "규격", align: "left", width: 130 },
  { key: "color", label: "색상", align: "left", width: 90 },
  { key: "customer", label: "거래처", align: "left", width: 120 },
  { key: "delivery_date", label: "납품일", align: "left", width: 90 },
  { key: "length_per_unit_m", label: "조장(M)", align: "right", width: 80 },
  { key: "unit_count", label: "개수(ea)", align: "right", width: 80 },
  { key: "total_length_m", label: "수량(M)", align: "right", width: 80 },
  { key: "convertedQty", label: "환산수량", align: "right", width: 80 },
  { key: "notes", label: "비고", align: "left", width: 100 },
] as const;

const BATCH_BG_EVEN = "#FFFFFF";
const BATCH_BG_ODD = "#F7F8FA";
const BATCH_BG_EVEN_HOVER = "#FEF9F9";
const BATCH_BG_ODD_HOVER = "#FDF4F4";

export function ProductionBatchTable({
  title,
  batches,
  processGroup,
  highlightedBatchId,
  onBatchWipClick,
  showProcessColumn,
}: ProductionBatchTableProps) {
  const batchNumbers = useMemo(() => assignBatchNumbers(batches), [batches]);

  function getBatchLabel(b: SchedulingBatch): string {
    const num = batchNumbers.get(getBatchGroupKey(b));
    return num != null ? `배치 ${num}` : "";
  }

  function getRowBg(b: SchedulingBatch): string {
    const num = batchNumbers.get(getBatchGroupKey(b)) ?? 1;
    return num % 2 === 0 ? BATCH_BG_ODD : BATCH_BG_EVEN;
  }

  function getRowHoverBg(b: SchedulingBatch): string {
    const num = batchNumbers.get(getBatchGroupKey(b)) ?? 1;
    return num % 2 === 0 ? BATCH_BG_ODD_HOVER : BATCH_BG_EVEN_HOVER;
  }

  function getCellValue(
    col: (typeof COL_DEFS)[number],
    batch: SchedulingBatch,
  ): string {
    if (col.key === "batch_label") return getBatchLabel(batch);
    if (col.key === "delivery_date")
      return formatDeliveryDate(batch.delivery_date);
    const raw = batch[col.key as keyof SchedulingBatch];
    if (typeof raw === "number") return raw.toLocaleString();
    return String(raw ?? "");
  }

  const totalWidth = COL_DEFS.reduce((sum, c) => sum + c.width, 0);

  return (
    <div>
      {/* Status legend */}
      <div className="flex items-center gap-4 mb-2">
        <div className="flex items-center gap-1.5">
          <span
            className="inline-block rounded-full"
            style={{
              width: 8,
              height: 8,
              backgroundColor: PROCESS_STATUS_COLORS["진행"],
            }}
          />
          <span className="text-[10px]" style={{ color: "#6B7280" }}>
            진행
          </span>
        </div>
        <div className="flex items-center gap-1.5">
          <span
            className="inline-block rounded-full"
            style={{
              width: 8,
              height: 8,
              backgroundColor: PROCESS_STATUS_COLORS["대기"],
            }}
          />
          <span className="text-[10px]" style={{ color: "#6B7280" }}>
            대기
          </span>
        </div>
      </div>

      {/* Table */}
      <div
        className="rounded-lg overflow-hidden"
        style={{ border: "1px solid #E5E7EB" }}
      >
        {batches.length === 0 ? (
          <div
            className="flex items-center justify-center py-12 text-xs text-gray-400"
            style={{ backgroundColor: "#FAFAFA" }}
          >
            배치 데이터가 없습니다
          </div>
        ) : (
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
                <tr style={{ backgroundColor: "#F9FAFB" }}>
                  {COL_DEFS.map((col, i) => (
                    <th
                      key={col.key}
                      className="text-[10px] font-semibold px-3 py-2"
                      style={{
                        color: "#6B7280",
                        textAlign: col.align as "left" | "right",
                        borderBottom: "1px solid #E5E7EB",
                        borderRight:
                          i < COL_DEFS.length - 1
                            ? "1px solid #E5E7EB"
                            : "none",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {col.label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {batches.map((batch) => {
                  const isHighlighted = highlightedBatchId === batch.id;
                  const rowBg = isHighlighted ? "#FEF2F2" : getRowBg(batch);
                  const hoverBg = isHighlighted
                    ? "#FEE2E2"
                    : getRowHoverBg(batch);
                  const statusColor =
                    PROCESS_STATUS_COLORS[batch.processStatus] ?? "#E5E7EB";

                  return (
                    <tr
                      key={batch.id}
                      data-batch-id={batch.id}
                      style={{
                        backgroundColor: rowBg,
                        transition: "background-color 300ms",
                        outline: isHighlighted ? "2px solid #C41230" : "none",
                      }}
                      onMouseEnter={(e) => {
                        (e.currentTarget as HTMLElement).style.backgroundColor =
                          hoverBg;
                      }}
                      onMouseLeave={(e) => {
                        (e.currentTarget as HTMLElement).style.backgroundColor =
                          isHighlighted ? "#FEF2F2" : getRowBg(batch);
                      }}
                    >
                      {COL_DEFS.map((col, colIdx) => (
                        <td
                          key={col.key}
                          className="px-3"
                          style={{
                            height: 38,
                            borderBottom: "1px solid #E5E7EB",
                            borderRight:
                              colIdx < COL_DEFS.length - 1
                                ? "1px solid #F3F4F6"
                                : "none",
                            borderLeft:
                              colIdx === 0
                                ? `3px solid ${statusColor}`
                                : "none",
                            verticalAlign: "middle",
                            overflow: "hidden",
                          }}
                        >
                          {col.key === "batch_label" ? (
                            <span
                              className="block truncate text-[10px] font-medium"
                              style={{ color: "#9CA3AF" }}
                            >
                              {getBatchLabel(batch)}
                            </span>
                          ) : col.key === "notes" &&
                            batch.notes === "재고 사용" ? (
                            <button
                              onClick={() => onBatchWipClick?.(batch.id)}
                              className="text-[11px] font-medium px-1.5 py-0.5 rounded transition-colors"
                              style={{
                                backgroundColor: "#FEF2F2",
                                color: "#C41230",
                                cursor: onBatchWipClick ? "pointer" : "default",
                              }}
                            >
                              재고 사용 →
                            </button>
                          ) : (
                            <span
                              className="block truncate text-[11px]"
                              style={{
                                textAlign: col.align as "left" | "right",
                              }}
                            >
                              {getCellValue(col, batch) || "\u2014"}
                            </span>
                          )}
                        </td>
                      ))}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
