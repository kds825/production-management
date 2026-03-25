"use client";

import { useMemo } from "react";
import { useRouter } from "next/navigation";
import type { SchedulingBatch, AiInsight } from "../types";
import type { ProcessGroup } from "@/shared/constants/processGroups";
import { PROCESS_STATUS_COLORS } from "@/shared/constants/processGroups";
import {
  assignBatchNumbers,
  getBatchGroupKey,
  formatDeliveryDate,
} from "@/shared/utils/batchGrouping";

interface SchedulingResultTableProps {
  yeonseoBatches: SchedulingBatch[];
  insulationBatches: SchedulingBatch[];
  sheatBatches: SchedulingBatch[];
  aiInsights: AiInsight[];
  activeTab: ProcessGroup;
  onTabChange: (tab: ProcessGroup) => void;
}

const PRIMARY = "#C41230";

const TABS: { key: ProcessGroup; label: string }[] = [
  { key: "연선", label: "연선" },
  { key: "절연", label: "절연" },
  { key: "시스", label: "시스" },
];

const COL_DEFS = [
  { key: "batch_label", label: "배치", align: "left", width: 64 },
  { key: "product", label: "품목", align: "left", width: 120 },
  { key: "spec", label: "규격", align: "left", width: 130 },
  { key: "color", label: "색상", align: "left", width: 90 },
  { key: "customer", label: "거래처", align: "left", width: 120 },
  { key: "delivery_date", label: "납품일", align: "left", width: 90 },
  { key: "length_per_unit_m", label: "조장(M)", align: "right", width: 80 },
  { key: "unit_count", label: "개수(ea)", align: "right", width: 80 },
  { key: "total_length_m", label: "수량(M)", align: "right", width: 80 },
  { key: "notes", label: "비고", align: "left", width: 120 },
  {
    key: "classification_reason",
    label: "분류근거",
    align: "left",
    width: 200,
  },
  { key: "processStatus", label: "공정상태", align: "left", width: 64 },
  { key: "convertedQty", label: "환산수량", align: "right", width: 80 },
  { key: "ai_insight", label: "AI 분석", align: "left", width: 200 },
] as const;

const BATCH_BG_EVEN = "#FFFFFF";
const BATCH_BG_ODD = "#F7F8FA";
const BATCH_BG_EVEN_HOVER = "#FEF9F9";
const BATCH_BG_ODD_HOVER = "#FDF4F4";

export function SchedulingResultTable({
  yeonseoBatches,
  insulationBatches,
  sheatBatches,
  aiInsights,
  activeTab,
  onTabChange,
}: SchedulingResultTableProps) {
  const router = useRouter();
  const batchMap: Record<ProcessGroup, SchedulingBatch[]> = {
    연선: yeonseoBatches,
    절연: insulationBatches,
    시스: sheatBatches,
  };

  const activeBatches = batchMap[activeTab];

  const batchNumbers = useMemo(
    () => assignBatchNumbers(activeBatches),
    [activeBatches],
  );

  const insightMap = useMemo(() => {
    const map = new Map<string, AiInsight>();
    for (const insight of aiInsights) {
      map.set(insight.batchId, insight);
    }
    return map;
  }, [aiInsights]);

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
    if (col.key === "ai_insight") {
      const insight = insightMap.get(batch.id);
      return insight?.reasoning ?? "";
    }
    const raw = batch[col.key as keyof SchedulingBatch];
    if (typeof raw === "number") return raw.toLocaleString();
    return String(raw ?? "");
  }

  const totalWidth = COL_DEFS.reduce((sum, c) => sum + c.width, 0);

  return (
    <section>
      {/* Tab bar */}
      <div
        className="flex items-center gap-0 mb-0"
        style={{ borderBottom: "1px solid #E5E7EB" }}
      >
        {TABS.map((tab) => {
          const isActive = tab.key === activeTab;
          return (
            <button
              key={tab.key}
              onClick={() => onTabChange(tab.key)}
              className="text-xs font-medium px-4 py-2.5 transition-colors"
              style={{
                color: isActive ? PRIMARY : "#6B7280",
                borderBottom: isActive
                  ? `2px solid ${PRIMARY}`
                  : "2px solid transparent",
                backgroundColor: "transparent",
                marginBottom: -1,
              }}
              onMouseEnter={(e) => {
                if (!isActive) {
                  e.currentTarget.style.color = "#374151";
                }
              }}
              onMouseLeave={(e) => {
                if (!isActive) {
                  e.currentTarget.style.color = "#6B7280";
                }
              }}
            >
              {tab.label}
            </button>
          );
        })}
      </div>

      {/* Table */}
      <div
        className="rounded-b-lg overflow-hidden"
        style={{
          border: "1px solid #E5E7EB",
          borderTop: "none",
        }}
      >
        {activeBatches.length === 0 ? (
          <div
            className="flex items-center justify-center py-12 text-xs text-gray-400"
            style={{ backgroundColor: "#FAFAFA" }}
          >
            해당 공정의 배치 데이터가 없습니다
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
                {activeBatches.map((batch) => {
                  const rowBg = getRowBg(batch);
                  const hoverBg = getRowHoverBg(batch);
                  const statusColor =
                    PROCESS_STATUS_COLORS[batch.processStatus] ?? "#E5E7EB";
                  const insight = insightMap.get(batch.id);
                  const isHighRisk = insight?.riskLevel === "high";

                  return (
                    <tr
                      key={batch.id}
                      style={{ backgroundColor: rowBg }}
                      onMouseEnter={(e) => {
                        (e.currentTarget as HTMLElement).style.backgroundColor =
                          hoverBg;
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
                          ) : col.key === "ai_insight" ? (
                            <span className="flex items-start gap-1">
                              {isHighRisk && (
                                <svg
                                  className="shrink-0 mt-0.5"
                                  style={{
                                    width: 12,
                                    height: 12,
                                    color: "#DC2626",
                                  }}
                                  viewBox="0 0 24 24"
                                  fill="currentColor"
                                >
                                  <path d="M12 2L1 21h22L12 2zm0 4l7.53 13H4.47L12 6zm-1 5v4h2v-4h-2zm0 6v2h2v-2h-2z" />
                                </svg>
                              )}
                              <span
                                className="block text-[10px] leading-tight"
                                style={{
                                  color: isHighRisk ? "#DC2626" : "#374151",
                                }}
                              >
                                {getCellValue(col, batch) || "\u2014"}
                              </span>
                            </span>
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

      {/* Confirm button */}
      <div className="flex justify-end mt-3">
        <button
          onClick={() => router.push("/scheduler")}
          className="text-xs font-medium px-3 py-1.5 rounded-md transition-colors"
          style={{
            backgroundColor: PRIMARY,
            color: "#FFFFFF",
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.backgroundColor = "#9E0E27";
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.backgroundColor = PRIMARY;
          }}
        >
          스케줄링 확정
        </button>
      </div>
    </section>
  );
}
