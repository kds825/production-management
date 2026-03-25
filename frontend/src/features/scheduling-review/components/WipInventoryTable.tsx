"use client";

import type { WipItem } from "../types";

interface WipInventoryTableProps {
  title: string;
  items: WipItem[];
}

const COL_DEFS = [
  { key: "processGroup", label: "구분", align: "left", width: 60 },
  { key: "product", label: "품목명", align: "left", width: 100 },
  { key: "spec", label: "규격", align: "left", width: 100 },
  { key: "color", label: "색상", align: "left", width: 60 },
  { key: "stock", label: "재고", align: "right", width: 70 },
  { key: "convertedQty", label: "환산수량", align: "right", width: 70 },
] as const;

const totalWidth = COL_DEFS.reduce((sum, c) => sum + c.width, 0);

export function WipInventoryTable({ title, items }: WipInventoryTableProps) {
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
                {items.map((item) => (
                  <tr
                    key={item.id}
                    style={{ backgroundColor: "#FFFFFF" }}
                    onMouseEnter={(e) => {
                      (e.currentTarget as HTMLElement).style.backgroundColor =
                        "#FEF9F9";
                    }}
                    onMouseLeave={(e) => {
                      (e.currentTarget as HTMLElement).style.backgroundColor =
                        "#FFFFFF";
                    }}
                  >
                    {COL_DEFS.map((col, colIdx) => {
                      const raw = item[col.key as keyof WipItem];
                      const display =
                        typeof raw === "number"
                          ? raw.toLocaleString()
                          : String(raw ?? "");
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
                          }}
                        >
                          <span
                            className="block truncate text-[11px]"
                            style={{
                              textAlign: col.align as "left" | "right",
                            }}
                          >
                            {display || "\u2014"}
                          </span>
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
