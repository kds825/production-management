"use client";

import { useMemo, useState, useRef, useEffect, useCallback } from "react";
import type { SchedulingBatch } from "../types";
import type { ProcessGroup } from "@/shared/constants/processGroups";
import { PROCESS_STATUS_COLORS } from "@/shared/constants/processGroups";
import {
  assignBatchNumbers,
  getBatchGroupKey,
  formatDeliveryDate,
  sortBatchesByBatchNumber,
} from "@/shared/utils/batchGrouping";
import { useSchedulingReviewStore } from "../store/schedulingReviewStore";

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

/** 인라인 편집 가능한 컬럼 키 */
const EDITABLE_KEYS = new Set([
  "color",
  "unit_count",
  "length_per_unit_m",
  "notes",
]);

interface EditingCell {
  batchId: string; // "batch-{id}" 형식
  field: string; // COL_DEFS의 key
}

export function ProductionBatchTable({
  title,
  batches,
  processGroup,
  highlightedBatchId,
  onBatchWipClick,
  showProcessColumn,
}: ProductionBatchTableProps) {
  const batchNumbers = useMemo(() => assignBatchNumbers(batches), [batches]);

  // 배치번호 기준 오름차순 정렬 (납기 빠른 그룹 → 색상 순)
  const sortedBatches = useMemo(
    () => sortBatchesByBatchNumber(batches, batchNumbers),
    [batches, batchNumbers],
  );

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

  // ── 인라인 편집 ──
  const updateBatch = useSchedulingReviewStore((s) => s.updateBatch);
  const [editingCell, setEditingCell] = useState<EditingCell | null>(null);
  const [editValue, setEditValue] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  // 편집 셀이 변경되면 input에 포커스
  useEffect(() => {
    if (editingCell && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [editingCell]);

  const startEditing = useCallback(
    (batchId: string, field: string, currentValue: string) => {
      setEditingCell({ batchId, field });
      setEditValue(currentValue);
    },
    [],
  );

  const commitEdit = useCallback(async () => {
    if (!editingCell) return;
    const { batchId, field } = editingCell;
    // batch-{id} -> 숫자 id 추출
    const numericId = parseInt(batchId.replace("batch-", ""), 10);
    if (isNaN(numericId)) {
      setEditingCell(null);
      return;
    }

    // 숫자 필드는 number로 변환
    const isNumericField =
      field === "unit_count" || field === "length_per_unit_m";
    const finalValue = isNumericField ? Number(editValue) || 0 : editValue;

    await updateBatch(numericId, field, finalValue);
    setEditingCell(null);
  }, [editingCell, editValue, updateBatch]);

  const cancelEdit = useCallback(() => {
    setEditingCell(null);
  }, []);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Enter") {
        e.preventDefault();
        commitEdit();
      } else if (e.key === "Escape") {
        cancelEdit();
      }
    },
    [commitEdit, cancelEdit],
  );

  /** 셀이 현재 편집 중인지 판별 */
  const isCellEditing = useCallback(
    (batchId: string, field: string) =>
      editingCell?.batchId === batchId && editingCell?.field === field,
    [editingCell],
  );

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
        {sortedBatches.length === 0 ? (
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
                {sortedBatches.map((batch) => {
                  const isHighlighted = highlightedBatchId === batch.id;
                  const isWipSkipped = batch.notes === "재고 사용";
                  const rowBg = isHighlighted
                    ? "#FEF2F2"
                    : isWipSkipped
                      ? "#F3F4F6"
                      : getRowBg(batch);
                  const hoverBg = isHighlighted
                    ? "#FEE2E2"
                    : isWipSkipped
                      ? "#E5E7EB"
                      : getRowHoverBg(batch);
                  const statusColor =
                    PROCESS_STATUS_COLORS[batch.processStatus] ?? "#E5E7EB";
                  /** 재고 사용 행은 텍스트를 회색으로 -- 간트 차트 미반영 표시 */
                  const textColor = isWipSkipped ? "#9CA3AF" : undefined;

                  return (
                    <tr
                      key={batch.id}
                      data-batch-id={batch.id}
                      style={{
                        backgroundColor: rowBg,
                        transition: "background-color 300ms",
                        outline: isHighlighted ? "2px solid #C41230" : "none",
                        color: textColor,
                      }}
                      onMouseEnter={(e) => {
                        (e.currentTarget as HTMLElement).style.backgroundColor =
                          hoverBg;
                      }}
                      onMouseLeave={(e) => {
                        (e.currentTarget as HTMLElement).style.backgroundColor =
                          isHighlighted
                            ? "#FEF2F2"
                            : isWipSkipped
                              ? "#F3F4F6"
                              : getRowBg(batch);
                      }}
                    >
                      {COL_DEFS.map((col, colIdx) => {
                        const editable = EDITABLE_KEYS.has(col.key);
                        const currentlyEditing = isCellEditing(
                          batch.id,
                          col.key,
                        );

                        return (
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
                              outline: currentlyEditing
                                ? "2px solid #3B82F6"
                                : "none",
                              outlineOffset: -2,
                              cursor: editable ? "text" : "default",
                            }}
                            onDoubleClick={() => {
                              if (!editable) return;
                              // 비고 "재고 사용"은 편집 불가
                              if (
                                col.key === "notes" &&
                                batch.notes === "재고 사용"
                              )
                                return;
                              const raw =
                                batch[col.key as keyof SchedulingBatch];
                              const strVal = raw != null ? String(raw) : "";
                              startEditing(batch.id, col.key, strVal);
                            }}
                          >
                            {currentlyEditing ? (
                              <input
                                ref={inputRef}
                                type={
                                  col.key === "unit_count" ||
                                  col.key === "length_per_unit_m"
                                    ? "number"
                                    : "text"
                                }
                                value={editValue}
                                onChange={(e) => setEditValue(e.target.value)}
                                onBlur={commitEdit}
                                onKeyDown={handleKeyDown}
                                className="w-full text-[11px] bg-white px-1 py-0.5 rounded"
                                style={{
                                  textAlign: col.align as "left" | "right",
                                  outline: "none",
                                  border: "1px solid #3B82F6",
                                }}
                              />
                            ) : col.key === "batch_label" ? (
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
                                  cursor: onBatchWipClick
                                    ? "pointer"
                                    : "default",
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
    </div>
  );
}
