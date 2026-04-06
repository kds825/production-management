"use client";

import type { WipItem } from "../types";

interface WipInventoryTableProps {
  title: string;
  items: WipItem[];
  /** WIP 행 클릭 시 매칭된 모든 배치 ID 목록 전달 */
  onWipClick?: (matchedBatchIds: string[]) => void;
  /** 현재 하이라이트된 WIP ID */
  activeWipId?: string | null;
  /** 여러 종류의 WIP이 섞여있을 때 구분 컬럼 표시 */
  showProcessStage?: boolean;
}

const BASE_COL_DEFS = [
  { key: "spec", label: "규격", align: "left", width: 100 },
  { key: "color", label: "선심색상", align: "left", width: 70 },
  { key: "stock", label: "조장(m)", align: "right", width: 65 },
  { key: "count", label: "드럼수", align: "right", width: 55 },
  { key: "status", label: "상태", align: "center", width: 65 },
] as const;

const STAGE_COL = {
  key: "process_stage",
  label: "구분",
  align: "left",
  width: 65,
} as const;

export function WipInventoryTable({
  title,
  items,
  onWipClick,
  activeWipId,
  showProcessStage,
}: WipInventoryTableProps) {
  const COL_DEFS = showProcessStage
    ? [STAGE_COL, ...BASE_COL_DEFS]
    : [...BASE_COL_DEFS];
  const totalWidth = COL_DEFS.reduce((sum, c) => sum + c.width, 0);

  return (
    <div>
      <h4
        className="text-[11px] font-semibold mb-2"
        style={{ color: "#111827" }}
      >
        {title}
      </h4>

      <div className="overflow-hidden" style={{ border: "1px solid #D1D5DB" }}>
        {items.length === 0 ? (
          <div
            className="flex items-center justify-center py-8 text-[11px]"
            style={{ backgroundColor: "#FAFAFA", color: "#9CA3AF" }}
          >
            재공 데이터가 없습니다
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
                <tr style={{ backgroundColor: "#F5F7FA" }}>
                  {COL_DEFS.map((col, i) => (
                    <th
                      key={col.key}
                      className="text-[10px] font-semibold px-2 py-2"
                      style={{
                        color: "#64748B",
                        textAlign: col.align as "left" | "right",
                        borderBottom: "2px solid #D1D5DB",
                        borderRight:
                          i < COL_DEFS.length - 1
                            ? "1px solid #E2E8F0"
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
                {items.map((item) => {
                  const isActive = activeWipId === item.id;
                  const batchIds = item.matchedBatchIds?.length
                    ? item.matchedBatchIds
                    : item.matchedBatchId
                      ? [item.matchedBatchId]
                      : [];
                  const hasMatch = batchIds.length > 0;
                  return (
                    <tr
                      key={item.id}
                      data-wip-id={item.id}
                      onClick={() => {
                        if (hasMatch && onWipClick) {
                          onWipClick(activeWipId === item.id ? [] : batchIds);
                        }
                      }}
                      style={{
                        backgroundColor: isActive ? "#FEF2F2" : "#FFFFFF",
                        cursor: hasMatch ? "pointer" : "default",
                        transition: "background-color 150ms",
                      }}
                      onMouseEnter={(e) => {
                        if (!isActive) {
                          e.currentTarget.style.backgroundColor = hasMatch
                            ? "#FEF9F9"
                            : "#FAFAFA";
                        }
                      }}
                      onMouseLeave={(e) => {
                        if (!isActive) {
                          e.currentTarget.style.backgroundColor = "#FFFFFF";
                        }
                      }}
                    >
                      {COL_DEFS.map((col, colIdx) => {
                        const raw = item[col.key as keyof WipItem];
                        const isStageCol = col.key === "process_stage";
                        const display = isStageCol
                          ? String(raw ?? "").replace("재고", "")
                          : typeof raw === "number"
                            ? raw.toLocaleString()
                            : String(raw ?? "");
                        const isStatusCol = col.key === "status";
                        const statusUsed = isStatusCol && raw === "사용완료";
                        return (
                          <td
                            key={col.key}
                            className="px-2"
                            style={{
                              height: 34,
                              borderBottom: "1px solid #F0F2F5",
                              borderRight:
                                colIdx < COL_DEFS.length - 1
                                  ? "1px solid #F0F2F5"
                                  : "none",
                              verticalAlign: "middle",
                              overflow: "hidden",
                              textAlign: col.align as
                                | "left"
                                | "right"
                                | "center",
                            }}
                          >
                            {isStageCol ? (
                              <span
                                className="inline-block text-[9px] font-semibold px-1.5 py-0.5 rounded"
                                style={
                                  String(raw ?? "").includes("절연")
                                    ? {
                                        backgroundColor: "#EFF6FF",
                                        color: "#1D4ED8",
                                      }
                                    : {
                                        backgroundColor: "#ECFDF5",
                                        color: "#065F46",
                                      }
                                }
                              >
                                {display}
                              </span>
                            ) : isStatusCol ? (
                              <div className="flex flex-col items-center gap-0.5">
                                <span
                                  className="inline-block text-[9px] font-semibold px-1.5 py-0.5 rounded"
                                  style={
                                    statusUsed
                                      ? {
                                          backgroundColor: "#FEE2E2",
                                          color: "#B91C1C",
                                        }
                                      : {
                                          backgroundColor: "#DCFCE7",
                                          color: "#15803D",
                                        }
                                  }
                                >
                                  {display || "–"}
                                </span>
                                {batchIds.length > 1 && (
                                  <span
                                    className="inline-block text-[8px] font-bold px-1 rounded"
                                    style={{
                                      backgroundColor: "#FEF3C7",
                                      color: "#92400E",
                                    }}
                                  >
                                    {batchIds.length}건
                                  </span>
                                )}
                              </div>
                            ) : (
                              <span
                                className="block truncate text-[11px]"
                                style={{
                                  textAlign: col.align as "left" | "right",
                                  fontWeight: isActive ? 600 : 400,
                                  color: isActive ? "#C41230" : undefined,
                                }}
                              >
                                {display || (
                                  <span style={{ color: "#CBD5E1" }}>–</span>
                                )}
                              </span>
                            )}
                          </td>
                        );
                      })}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
      {items.some(
        (i) => (i.matchedBatchIds?.length ?? 0) > 0 || !!i.matchedBatchId,
      ) && (
        <p className="text-[9px] text-gray-400 mt-1">
          클릭하면 매칭된 배치로 이동합니다
        </p>
      )}
    </div>
  );
}
