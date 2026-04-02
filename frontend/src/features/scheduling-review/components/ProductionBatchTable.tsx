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
  { key: "processStatus", label: "공정상태", align: "left", width: 64 },
  { key: "product", label: "품목", align: "left", width: 120 },
  { key: "spec", label: "규격", align: "left", width: 130 },
  { key: "color", label: "외피색상", align: "left", width: 90 },
  { key: "core_colors", label: "선심색상", align: "left", width: 90 },
  { key: "customer", label: "거래처", align: "left", width: 120 },
  { key: "delivery_date", label: "납품일", align: "left", width: 90 },
  { key: "length_per_unit_m", label: "조장(M)", align: "right", width: 80 },
  { key: "unit_count", label: "개수(ea)", align: "right", width: 80 },
  { key: "total_length_m", label: "수량(M)", align: "right", width: 80 },
  { key: "convertedQty", label: "환산수량", align: "right", width: 80 },
  { key: "notes", label: "비고", align: "left", width: 100 },
] as const;

type ColKey = (typeof COL_DEFS)[number]["key"];

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
  batchId: string;
  field: string;
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

  const sortedBatches = useMemo(
    () => sortBatchesByBatchNumber(batches, batchNumbers),
    [batches, batchNumbers],
  );

  // ── 컬럼 표시/숨김 ──
  const [hiddenCols, setHiddenCols] = useState<Set<ColKey>>(new Set());
  const [colMenuOpen, setColMenuOpen] = useState(false);
  const colMenuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!colMenuOpen) return;
    function handleClick(e: MouseEvent) {
      if (colMenuRef.current && !colMenuRef.current.contains(e.target as Node)) {
        setColMenuOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [colMenuOpen]);

  const toggleCol = useCallback((key: ColKey) => {
    setHiddenCols((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  const visibleCols = useMemo(
    () => COL_DEFS.filter((c) => !hiddenCols.has(c.key)),
    [hiddenCols],
  );

  // ── 필터 ──
  const [filters, setFilters] = useState<Partial<Record<ColKey, string>>>({});
  const hasActiveFilter = Object.values(filters).some((v) => v && v.length > 0);

  const filteredBatches = useMemo(() => {
    if (!hasActiveFilter) return sortedBatches;
    return sortedBatches.filter((batch) =>
      visibleCols.every((col) => {
        const filterVal = filters[col.key];
        if (!filterVal) return true;
        const cellVal = getCellValueStatic(col, batch, batchNumbers);
        return cellVal.toLowerCase().includes(filterVal.toLowerCase());
      }),
    );
  }, [sortedBatches, filters, visibleCols, batchNumbers, hasActiveFilter]);

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

  function getCellValue(col: (typeof COL_DEFS)[number], batch: SchedulingBatch): string {
    return getCellValueStatic(col, batch, batchNumbers);
  }

  // ── 인라인 편집 ──
  const updateBatch = useSchedulingReviewStore((s) => s.updateBatch);
  const [editingCell, setEditingCell] = useState<EditingCell | null>(null);
  const [editValue, setEditValue] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

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
    const numericId = parseInt(batchId.replace("batch-", ""), 10);
    if (isNaN(numericId)) { setEditingCell(null); return; }
    const isNumericField = field === "unit_count" || field === "length_per_unit_m";
    const finalValue = isNumericField ? Number(editValue) || 0 : editValue;
    await updateBatch(numericId, field, finalValue);
    setEditingCell(null);
  }, [editingCell, editValue, updateBatch]);

  const cancelEdit = useCallback(() => setEditingCell(null), []);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Enter") { e.preventDefault(); commitEdit(); }
      else if (e.key === "Escape") cancelEdit();
    },
    [commitEdit, cancelEdit],
  );

  const isCellEditing = useCallback(
    (batchId: string, field: string) =>
      editingCell?.batchId === batchId && editingCell?.field === field,
    [editingCell],
  );

  const totalWidth = visibleCols.reduce((sum, c) => sum + c.width, 0);

  return (
    <div>
      {/* 상단 바: 상태 범례 + 필터 초기화 + 컬럼 설정 */}
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-1.5">
            <span className="inline-block rounded-full" style={{ width: 8, height: 8, backgroundColor: PROCESS_STATUS_COLORS["진행"] }} />
            <span className="text-[10px]" style={{ color: "#6B7280" }}>진행</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="inline-block rounded-full" style={{ width: 8, height: 8, backgroundColor: PROCESS_STATUS_COLORS["대기"] }} />
            <span className="text-[10px]" style={{ color: "#6B7280" }}>대기</span>
          </div>
          {hasActiveFilter && (
            <button
              onClick={() => setFilters({})}
              className="text-[10px] px-2 py-0.5 rounded"
              style={{ backgroundColor: "#FEF2F2", color: "#C41230", border: "1px solid #FECACA" }}
            >
              필터 초기화
            </button>
          )}
        </div>

        {/* 컬럼 설정 버튼 */}
        <div className="relative" ref={colMenuRef}>
          <button
            onClick={() => setColMenuOpen((v) => !v)}
            className="flex items-center gap-1 text-[10px] px-2 py-1 rounded border transition-colors"
            style={{
              borderColor: colMenuOpen ? "#C41230" : "#E5E7EB",
              color: colMenuOpen ? "#C41230" : "#6B7280",
              backgroundColor: colMenuOpen ? "#FEF2F2" : "#FFFFFF",
            }}
          >
            <svg width="11" height="11" viewBox="0 0 16 16" fill="currentColor">
              <path d="M8 4.754a3.246 3.246 0 1 0 0 6.492 3.246 3.246 0 0 0 0-6.492zM5.754 8a2.246 2.246 0 1 1 4.492 0 2.246 2.246 0 0 1-4.492 0z"/>
              <path d="M9.796 1.343c-.527-1.79-3.065-1.79-3.592 0l-.094.319a.873.873 0 0 1-1.255.52l-.292-.16c-1.64-.892-3.433.902-2.54 2.541l.159.292a.873.873 0 0 1-.52 1.255l-.319.094c-1.79.527-1.79 3.065 0 3.592l.319.094a.873.873 0 0 1 .52 1.255l-.16.292c-.892 1.64.901 3.434 2.541 2.54l.292-.159a.873.873 0 0 1 1.255.52l.094.319c.527 1.79 3.065 1.79 3.592 0l.094-.319a.873.873 0 0 1 1.255-.52l.292.16c1.64.893 3.434-.902 2.54-2.541l-.159-.292a.873.873 0 0 1 .52-1.255l.319-.094c1.79-.527 1.79-3.065 0-3.592l-.319-.094a.873.873 0 0 1-.52-1.255l.16-.292c.893-1.64-.902-3.433-2.541-2.54l-.292.159a.873.873 0 0 1-1.255-.52l-.094-.319zm-2.633.283c.246-.835 1.428-.835 1.674 0l.094.319a1.873 1.873 0 0 0 2.693 1.115l.291-.16c.764-.415 1.6.42 1.184 1.185l-.159.292a1.873 1.873 0 0 0 1.116 2.692l.318.094c.835.246.835 1.428 0 1.674l-.319.094a1.873 1.873 0 0 0-1.115 2.693l.16.291c.415.764-.42 1.6-1.185 1.184l-.291-.159a1.873 1.873 0 0 0-2.693 1.116l-.094.318c-.246.835-1.428.835-1.674 0l-.094-.319a1.873 1.873 0 0 0-2.692-1.115l-.292.16c-.764.415-1.6-.42-1.184-1.185l.159-.291A1.873 1.873 0 0 0 1.945 8.93l-.319-.094c-.835-.246-.835-1.428 0-1.674l.319-.094A1.873 1.873 0 0 0 3.06 4.465l-.16-.292c-.415-.764.42-1.6 1.185-1.184l.292.159a1.873 1.873 0 0 0 2.692-1.115l.094-.319z"/>
            </svg>
            컬럼 설정
            {hiddenCols.size > 0 && (
              <span className="ml-0.5 text-[9px] font-bold" style={{ color: "#C41230" }}>
                -{hiddenCols.size}
              </span>
            )}
          </button>

          {colMenuOpen && (
            <div
              className="absolute right-0 mt-1 rounded-lg shadow-lg z-50 py-1"
              style={{ backgroundColor: "#FFFFFF", border: "1px solid #E5E7EB", minWidth: 160, top: "100%" }}
            >
              {COL_DEFS.map((col) => (
                <label
                  key={col.key}
                  className="flex items-center gap-2 px-3 py-1.5 cursor-pointer hover:bg-gray-50"
                >
                  <input
                    type="checkbox"
                    checked={!hiddenCols.has(col.key)}
                    onChange={() => toggleCol(col.key)}
                    className="w-3 h-3 accent-red-700"
                  />
                  <span className="text-[11px]" style={{ color: "#374151" }}>{col.label}</span>
                </label>
              ))}
              {hiddenCols.size > 0 && (
                <>
                  <div style={{ borderTop: "1px solid #F3F4F6", margin: "4px 0" }} />
                  <button
                    onClick={() => setHiddenCols(new Set())}
                    className="w-full text-left px-3 py-1 text-[10px]"
                    style={{ color: "#6B7280" }}
                  >
                    모두 표시
                  </button>
                </>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Table */}
      <div className="rounded-lg overflow-hidden" style={{ border: "1px solid #E5E7EB" }}>
        {sortedBatches.length === 0 ? (
          <div className="flex items-center justify-center py-12 text-xs text-gray-400" style={{ backgroundColor: "#FAFAFA" }}>
            배치 데이터가 없습니다
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table style={{ width: totalWidth, minWidth: "100%", borderCollapse: "collapse", tableLayout: "fixed" }}>
              <colgroup>
                {visibleCols.map((col) => (
                  <col key={col.key} style={{ width: col.width }} />
                ))}
              </colgroup>
              <thead>
                {/* 헤더 행 */}
                <tr style={{ backgroundColor: "#F9FAFB" }}>
                  {visibleCols.map((col, i) => (
                    <th
                      key={col.key}
                      className="text-[10px] font-semibold px-3 py-2"
                      style={{
                        color: "#6B7280",
                        textAlign: col.align as "left" | "right",
                        borderBottom: "1px solid #E5E7EB",
                        borderRight: i < visibleCols.length - 1 ? "1px solid #E5E7EB" : "none",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {col.label}
                    </th>
                  ))}
                </tr>
                {/* 필터 행 */}
                <tr style={{ backgroundColor: "#F3F4F6" }}>
                  {visibleCols.map((col, i) => (
                    <th
                      key={col.key}
                      className="px-1 py-1"
                      style={{
                        borderBottom: "1px solid #E5E7EB",
                        borderRight: i < visibleCols.length - 1 ? "1px solid #E5E7EB" : "none",
                      }}
                    >
                      <input
                        type="text"
                        value={filters[col.key] ?? ""}
                        onChange={(e) =>
                          setFilters((prev) => ({ ...prev, [col.key]: e.target.value }))
                        }
                        placeholder="검색..."
                        className="w-full text-[10px] px-1.5 py-0.5 rounded"
                        style={{
                          border: filters[col.key] ? "1px solid #C41230" : "1px solid #D1D5DB",
                          outline: "none",
                          backgroundColor: "#FFFFFF",
                          textAlign: col.align as "left" | "right",
                        }}
                      />
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {filteredBatches.length === 0 ? (
                  <tr>
                    <td colSpan={visibleCols.length} className="py-8 text-center text-[11px]" style={{ color: "#9CA3AF" }}>
                      필터 결과 없음
                    </td>
                  </tr>
                ) : (
                  filteredBatches.map((batch) => {
                    const isHighlighted = highlightedBatchId === batch.id;
                    const isWipSkipped = batch.notes === "재고 사용";
                    const rowBg = isHighlighted ? "#FEF2F2" : isWipSkipped ? "#F3F4F6" : getRowBg(batch);
                    const hoverBg = isHighlighted ? "#FEE2E2" : isWipSkipped ? "#E5E7EB" : getRowHoverBg(batch);
                    const statusColor = PROCESS_STATUS_COLORS[batch.processStatus] ?? "#E5E7EB";
                    const textColor = isWipSkipped ? "#9CA3AF" : undefined;

                    return (
                      <tr
                        key={batch.id}
                        data-batch-id={batch.id}
                        style={{ backgroundColor: rowBg, transition: "background-color 300ms", outline: isHighlighted ? "2px solid #C41230" : "none", color: textColor }}
                        onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.backgroundColor = hoverBg; }}
                        onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.backgroundColor = isHighlighted ? "#FEF2F2" : isWipSkipped ? "#F3F4F6" : getRowBg(batch); }}
                      >
                        {visibleCols.map((col, colIdx) => {
                          const editable = EDITABLE_KEYS.has(col.key);
                          const currentlyEditing = isCellEditing(batch.id, col.key);

                          return (
                            <td
                              key={col.key}
                              className="px-3"
                              style={{
                                height: 38,
                                borderBottom: "1px solid #E5E7EB",
                                borderRight: colIdx < visibleCols.length - 1 ? "1px solid #F3F4F6" : "none",
                                borderLeft: colIdx === 0 ? `3px solid ${statusColor}` : "none",
                                verticalAlign: "middle",
                                overflow: "hidden",
                                outline: currentlyEditing ? "2px solid #3B82F6" : "none",
                                outlineOffset: -2,
                                cursor: editable ? "text" : "default",
                              }}
                              onDoubleClick={() => {
                                if (!editable) return;
                                if (col.key === "notes" && batch.notes === "재고 사용") return;
                                const raw = batch[col.key as keyof SchedulingBatch];
                                const strVal = raw != null ? String(raw) : "";
                                startEditing(batch.id, col.key, strVal);
                              }}
                            >
                              {currentlyEditing ? (
                                <input
                                  ref={inputRef}
                                  type={col.key === "unit_count" || col.key === "length_per_unit_m" ? "number" : "text"}
                                  value={editValue}
                                  onChange={(e) => setEditValue(e.target.value)}
                                  onBlur={commitEdit}
                                  onKeyDown={handleKeyDown}
                                  className="w-full text-[11px] bg-white px-1 py-0.5 rounded"
                                  style={{ textAlign: col.align as "left" | "right", outline: "none", border: "1px solid #3B82F6" }}
                                />
                              ) : col.key === "batch_label" ? (
                                <span className="block truncate text-[10px] font-medium" style={{ color: "#9CA3AF" }}>
                                  {getBatchLabel(batch)}
                                </span>
                              ) : col.key === "notes" && batch.notes === "재고 사용" ? (
                                <button
                                  onClick={() => onBatchWipClick?.(batch.id)}
                                  className="text-[11px] font-medium px-1.5 py-0.5 rounded transition-colors"
                                  style={{ backgroundColor: "#FEF2F2", color: "#C41230", cursor: onBatchWipClick ? "pointer" : "default" }}
                                >
                                  재고 사용 →
                                </button>
                              ) : (
                                <span className="block truncate text-[11px]" style={{ textAlign: col.align as "left" | "right" }}>
                                  {getCellValue(col, batch) || "\u2014"}
                                </span>
                              )}
                            </td>
                          );
                        })}
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

/** 컴포넌트 외부에서도 사용 가능한 순수 함수 (filteredBatches useMemo에서 사용) */
function getCellValueStatic(
  col: (typeof COL_DEFS)[number],
  batch: SchedulingBatch,
  batchNumbers: Map<string, number>,
): string {
  if (col.key === "batch_label") {
    const num = batchNumbers.get(getBatchGroupKey(batch));
    return num != null ? `배치 ${num}` : "";
  }
  if (col.key === "delivery_date") return formatDeliveryDate(batch.delivery_date);
  const raw = batch[col.key as keyof SchedulingBatch];
  if (typeof raw === "number") return raw.toLocaleString();
  return String(raw ?? "");
}
