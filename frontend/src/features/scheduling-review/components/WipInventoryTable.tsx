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
  { key: "used_m", label: "사용(m)", align: "right", width: 70 },
  { key: "remaining_m", label: "잔여(m)", align: "right", width: 70 },
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
        style={{ color: "var(--color-text-primary)" }}
      >
        {title}
      </h4>

      <div
        className="overflow-hidden"
        style={{ border: "1px solid var(--neutral-300)" }}
      >
        {items.length === 0 ? (
          <div
            className="flex items-center justify-center py-8 text-[11px]"
            style={{
              backgroundColor: "var(--color-bg-muted)",
              color: "var(--color-text-tertiary)",
            }}
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
                <tr style={{ backgroundColor: "var(--neutral-100)" }}>
                  {COL_DEFS.map((col, i) => (
                    <th
                      key={col.key}
                      className="text-[10px] font-semibold px-2 py-2"
                      style={{
                        color: "var(--color-text-secondary)",
                        textAlign: col.align as "left" | "right",
                        borderBottom: "2px solid var(--neutral-300)",
                        borderRight:
                          i < COL_DEFS.length - 1
                            ? "1px solid var(--neutral-200)"
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
                        backgroundColor: isActive
                          ? "var(--kbi-red-tint-5)"
                          : "var(--bg-surface)",
                        cursor: hasMatch ? "pointer" : "default",
                        transition: "background-color 150ms",
                      }}
                      onMouseEnter={(e) => {
                        if (!isActive) {
                          e.currentTarget.style.backgroundColor = hasMatch
                            ? "var(--kbi-red-tint-5)"
                            : "var(--color-bg-muted)";
                        }
                      }}
                      onMouseLeave={(e) => {
                        if (!isActive) {
                          e.currentTarget.style.backgroundColor =
                            "var(--bg-surface)";
                        }
                      }}
                    >
                      {COL_DEFS.map((col, colIdx) => {
                        const isStageCol = col.key === "process_stage";
                        const isUsedCol = col.key === "used_m";
                        const isRemainingCol = col.key === "remaining_m";

                        let display: string;
                        if (isStageCol) {
                          display = String(item.process_stage ?? "").replace(
                            "재고",
                            "",
                          );
                        } else if (isUsedCol) {
                          display =
                            item.used_m > 0
                              ? `${Math.round(item.used_m).toLocaleString()}`
                              : "-";
                        } else if (isRemainingCol) {
                          const remaining = item.total_length_m - item.used_m;
                          display =
                            remaining > 0
                              ? `${Math.round(remaining).toLocaleString()}`
                              : remaining === 0 && item.used_m > 0
                                ? "0"
                                : "-";
                        } else {
                          const raw = item[col.key as keyof WipItem];
                          display =
                            typeof raw === "number"
                              ? raw.toLocaleString()
                              : String(raw ?? "");
                        }

                        return (
                          <td
                            key={col.key}
                            className="px-2"
                            style={{
                              height: 34,
                              borderBottom: "1px solid var(--neutral-100)",
                              borderRight:
                                colIdx < COL_DEFS.length - 1
                                  ? "1px solid var(--neutral-100)"
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
                                  String(item.process_stage ?? "").includes(
                                    "절연",
                                  )
                                    ? {
                                        backgroundColor: "var(--status-info-bg)",
                                        color: "var(--status-info-text)",
                                      }
                                    : {
                                        backgroundColor: "var(--status-success-bg-soft)",
                                        color: "var(--status-success-text-deep)",
                                      }
                                }
                              >
                                {display}
                              </span>
                            ) : isUsedCol ? (
                              <span
                                className="text-[11px] font-medium"
                                style={{
                                  color:
                                    item.used_m > 0
                                      ? "var(--status-danger-text)"
                                      : "var(--color-text-tertiary)",
                                }}
                              >
                                {display}
                              </span>
                            ) : isRemainingCol ? (
                              <span
                                className="text-[11px] font-medium"
                                style={{
                                  color:
                                    item.total_length_m - item.used_m > 0
                                      ? "var(--status-success-text)"
                                      : "var(--color-text-tertiary)",
                                }}
                              >
                                {display}
                              </span>
                            ) : (
                              <span
                                className="block truncate text-[11px]"
                                style={{
                                  textAlign: col.align as "left" | "right",
                                  fontWeight: isActive ? 600 : 400,
                                  color: isActive
                                    ? "var(--color-brand-primary)"
                                    : undefined,
                                }}
                              >
                                {display || (
                                  <span style={{ color: "var(--neutral-200)" }}>–</span>
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
