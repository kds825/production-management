"use client";

import type { WipItem } from "../types";

interface WipInventoryTableProps {
  title: string;
  items: WipItem[];
  /** WIP 행 클릭 시 매칭 배치로 스크롤 */
  onWipClick?: (matchedBatchId: string) => void;
  /** 현재 하이라이트된 WIP ID */
  activeWipId?: string | null;
}

const COL_DEFS = [
  { key: "spec", label: "규격", align: "left", width: 100 },
  { key: "color", label: "선심색상", align: "left", width: 70 },
  { key: "stock", label: "조장(m)", align: "right", width: 65 },
  { key: "count", label: "드럼수", align: "right", width: 55 },
  { key: "status", label: "상태", align: "center", width: 65 },
] as const;

const totalWidth = COL_DEFS.reduce((sum, c) => sum + c.width, 0);

export function WipInventoryTable({
  title,
  items,
  onWipClick,
  activeWipId,
}: WipInventoryTableProps) {
  return (
    <div>
      <h4
        className="text-[11px] font-semibold mb-2"
        style={{ color: "#111827" }}
      >
        {title}
      </h4>

      <div
        className="rounded-lg overflow-hidden"
        style={{ border: "1px solid #E5E7EB" }}
      >
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
                <tr style={{ backgroundColor: "#F9FAFB" }}>
                  {COL_DEFS.map((col, i) => (
                    <th
                      key={col.key}
                      className="text-[10px] font-semibold px-2 py-2"
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
                {items.map((item) => {
                  const isActive = activeWipId === item.id;
                  const hasMatch = !!item.matchedBatchId;
                  return (
                    <tr
                      key={item.id}
                      data-wip-id={item.id}
                      onClick={() => {
                        if (hasMatch && onWipClick && item.matchedBatchId) {
                          onWipClick(item.matchedBatchId);
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
                        const display =
                          typeof raw === "number"
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
                              borderBottom: "1px solid #E5E7EB",
                              borderRight:
                                colIdx < COL_DEFS.length - 1
                                  ? "1px solid #F3F4F6"
                                  : "none",
                              verticalAlign: "middle",
                              overflow: "hidden",
                              textAlign: col.align as "left" | "right" | "center",
                            }}
                          >
                            {isStatusCol ? (
                              <span
                                className="inline-block text-[9px] font-semibold px-1.5 py-0.5 rounded"
                                style={
                                  statusUsed
                                    ? { backgroundColor: "#FEE2E2", color: "#B91C1C" }
                                    : { backgroundColor: "#DCFCE7", color: "#15803D" }
                                }
                              >
                                {display || "–"}
                              </span>
                            ) : (
                              <span
                                className="block truncate text-[11px]"
                                style={{
                                  textAlign: col.align as "left" | "right",
                                  fontWeight: isActive ? 600 : 400,
                                  color: isActive ? "#C41230" : undefined,
                                }}
                              >
                                {display || <span style={{ color: "#CBD5E1" }}>–</span>}
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
      {items.some((i) => i.matchedBatchId) && (
        <p className="text-[9px] text-gray-400 mt-1">
          클릭하면 매칭된 배치로 이동합니다
        </p>
      )}
    </div>
  );
}
