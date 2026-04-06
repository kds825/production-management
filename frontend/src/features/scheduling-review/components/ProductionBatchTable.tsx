"use client";

import React, { useMemo, useState, useRef, useEffect, useCallback } from "react";
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
  highlightedBatchIds?: ReadonlySet<string> | null;
  onBatchWipClick?: (batchId: string) => void;
}

const COL_DEFS = [
  { key: "processGroup", label: "구분", align: "left", width: 56 },
  { key: "batch_label", label: "배치", align: "left", width: 60 },
  { key: "processStatus", label: "상태", align: "left", width: 58 },
  { key: "product", label: "품목", align: "left", width: 100 },
  { key: "spec", label: "규격", align: "left", width: 120 },
  { key: "color", label: "외피색상", align: "left", width: 80 },
  { key: "core_colors", label: "선심색상", align: "left", width: 80 },
  { key: "customer", label: "거래처", align: "left", width: 100 },
  { key: "delivery_date", label: "납품일", align: "left", width: 82 },
  { key: "length_per_unit_m", label: "조장(M)", align: "right", width: 72 },
  { key: "unit_count", label: "개수", align: "right", width: 56 },
  { key: "total_length_m", label: "수량(M)", align: "right", width: 72 },
  { key: "convertedQty", label: "환산수량", align: "right", width: 72 },
  { key: "notes", label: "비고", align: "left", width: 90 },
] as const;

/** 탭과 중복되어 기본 숨김 처리할 컬럼 */
const DEFAULT_HIDDEN_COLS: ColKey[] = ["processGroup"];

type ColKey = (typeof COL_DEFS)[number]["key"];

const BATCH_BG_EVEN = "#FFFFFF";
const BATCH_BG_ODD = "#F7F8FA";
const BATCH_BG_EVEN_HOVER = "#FEF9F9";
const BATCH_BG_ODD_HOVER = "#FDF4F4";

const EDITABLE_KEYS = new Set(["color", "unit_count", "length_per_unit_m", "notes"]);

interface EditingCell {
  batchId: string;
  field: string;
}

/** 컬럼별 선택된 값 집합 — undefined면 필터 없음(전체) */
type ColFilters = Partial<Record<ColKey, Set<string>>>;

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

export function ProductionBatchTable({
  title,
  batches,
  processGroup,
  highlightedBatchIds,
  onBatchWipClick,
  showProcessColumn,
}: ProductionBatchTableProps) {
  const batchNumbers = useMemo(() => assignBatchNumbers(batches), [batches]);
  const sortedBatches = useMemo(
    () => sortBatchesByBatchNumber(batches, batchNumbers),
    [batches, batchNumbers],
  );

  // ── 컬럼 표시/숨김 ──
  const [hiddenCols, setHiddenCols] = useState<Set<ColKey>>(new Set(DEFAULT_HIDDEN_COLS));
  const [colMenuOpen, setColMenuOpen] = useState(false);
  const colMenuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!colMenuOpen) return;
    function handleClick(e: MouseEvent) {
      if (colMenuRef.current && !colMenuRef.current.contains(e.target as Node))
        setColMenuOpen(false);
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [colMenuOpen]);

  const toggleCol = useCallback((key: ColKey) => {
    setHiddenCols((prev) => {
      const next = new Set(prev);
      next.has(key) ? next.delete(key) : next.add(key);
      return next;
    });
  }, []);

  const visibleCols = useMemo(
    () => COL_DEFS.filter((c) => !hiddenCols.has(c.key)),
    [hiddenCols],
  );

  // ── 컬럼별 드롭다운 필터 (엑셀 방식) ──
  const [colFilters, setColFilters] = useState<ColFilters>({});
  const [openFilterCol, setOpenFilterCol] = useState<ColKey | null>(null);
  const [filterSearch, setFilterSearch] = useState("");
  const filterMenuRef = useRef<HTMLDivElement>(null);

  // 외부 클릭 시 필터 드롭다운 닫기
  useEffect(() => {
    if (!openFilterCol) return;
    function handleClick(e: MouseEvent) {
      if (filterMenuRef.current && !filterMenuRef.current.contains(e.target as Node))
        setOpenFilterCol(null);
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [openFilterCol]);

  // 드롭다운 열릴 때 검색어 초기화
  useEffect(() => { setFilterSearch(""); }, [openFilterCol]);

  /** 특정 컬럼의 전체 고유값 목록 */
  const uniqueValues = useMemo(() => {
    if (!openFilterCol) return [];
    const col = COL_DEFS.find((c) => c.key === openFilterCol);
    if (!col) return [];
    const vals = new Set(sortedBatches.map((b) => getCellValueStatic(col, b, batchNumbers)));
    return Array.from(vals).sort((a, b) => a.localeCompare(b, "ko"));
  }, [openFilterCol, sortedBatches, batchNumbers]);

  const filteredUniqueValues = useMemo(
    () => uniqueValues.filter((v) => v.toLowerCase().includes(filterSearch.toLowerCase())),
    [uniqueValues, filterSearch],
  );

  const hasActiveFilter = Object.keys(colFilters).length > 0;
  const activeFilterCount = Object.keys(colFilters).length;

  /** 필터 적용된 배치 목록 */
  const filteredBatches = useMemo(() => {
    if (!hasActiveFilter) return sortedBatches;
    return sortedBatches.filter((batch) =>
      (Object.entries(colFilters) as [ColKey, Set<string>][]).every(([key, selected]) => {
        const col = COL_DEFS.find((c) => c.key === key);
        if (!col) return true;
        const val = getCellValueStatic(col, batch, batchNumbers);
        return selected.has(val);
      }),
    );
  }, [sortedBatches, colFilters, batchNumbers, hasActiveFilter]);

  /** 컬럼 필터 드롭다운 내 체크박스 토글 */
  const toggleFilterValue = useCallback((colKey: ColKey, value: string) => {
    setColFilters((prev) => {
      const existing = prev[colKey] ? new Set(prev[colKey]) : new Set(
        COL_DEFS.find((c) => c.key === colKey)
          ? sortedBatches.map((b) => getCellValueStatic(COL_DEFS.find((c) => c.key === colKey)!, b, batchNumbers))
          : []
      );
      existing.has(value) ? existing.delete(value) : existing.add(value);

      // 전체 선택 상태면 필터 제거
      const allVals = new Set(sortedBatches.map((b) => {
        const col = COL_DEFS.find((c) => c.key === colKey)!;
        return getCellValueStatic(col, b, batchNumbers);
      }));
      const isAll = allVals.size === existing.size && [...allVals].every((v) => existing.has(v));

      const next = { ...prev };
      if (isAll) delete next[colKey];
      else next[colKey] = existing;
      return next;
    });
  }, [sortedBatches, batchNumbers]);

  /** 컬럼 필터 전체선택/해제 */
  const toggleAllFilterValues = useCallback((colKey: ColKey, allSelected: boolean) => {
    setColFilters((prev) => {
      const next = { ...prev };
      if (allSelected) {
        delete next[colKey];
      } else {
        const col = COL_DEFS.find((c) => c.key === colKey)!;
        next[colKey] = new Set(
          sortedBatches.map((b) => getCellValueStatic(col, b, batchNumbers))
        );
      }
      return next;
    });
  }, [sortedBatches, batchNumbers]);

  /** 컬럼 필터 초기화 */
  const clearColFilter = useCallback((colKey: ColKey) => {
    setColFilters((prev) => { const next = { ...prev }; delete next[colKey]; return next; });
  }, []);

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

  const startEditing = useCallback((batchId: string, field: string, currentValue: string) => {
    setEditingCell({ batchId, field });
    setEditValue(currentValue);
  }, []);

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

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === "Enter") { e.preventDefault(); commitEdit(); }
    else if (e.key === "Escape") cancelEdit();
  }, [commitEdit, cancelEdit]);

  const isCellEditing = useCallback(
    (batchId: string, field: string) =>
      editingCell?.batchId === batchId && editingCell?.field === field,
    [editingCell],
  );

  function getCellValue(col: (typeof COL_DEFS)[number], batch: SchedulingBatch): string {
    return getCellValueStatic(col, batch, batchNumbers);
  }

  function getWipNoteStyle(notes: string): React.CSSProperties {
    if (notes.startsWith("연선재고"))
      return { backgroundColor: "#ECFDF5", color: "#065F46" }; // 초록
    if (notes.startsWith("절연재고"))
      return { backgroundColor: "#EFF6FF", color: "#1D4ED8" }; // 파랑
    if (notes.startsWith("시스재고"))
      return { backgroundColor: "#F5F3FF", color: "#6D28D9" }; // 보라
    return { backgroundColor: "#FEF2F2", color: "#C41230" };   // 기본 빨강
  }

  function getRowBg(b: SchedulingBatch) {
    const num = batchNumbers.get(getBatchGroupKey(b)) ?? 1;
    return num % 2 === 0 ? BATCH_BG_ODD : BATCH_BG_EVEN;
  }
  function getRowHoverBg(b: SchedulingBatch) {
    const num = batchNumbers.get(getBatchGroupKey(b)) ?? 1;
    return num % 2 === 0 ? BATCH_BG_ODD_HOVER : BATCH_BG_EVEN_HOVER;
  }
  function getBatchLabel(b: SchedulingBatch) {
    const num = batchNumbers.get(getBatchGroupKey(b));
    return num != null ? `배치 ${num}` : "";
  }

  const totalWidth = visibleCols.reduce((sum, c) => sum + c.width, 0);

  return (
    <div>
      {/* 상단 바 */}
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-4">
          {/* 상태 범례 */}
          <div className="flex items-center gap-1.5">
            <span className="inline-block rounded-full" style={{ width: 8, height: 8, backgroundColor: PROCESS_STATUS_COLORS["진행"] }} />
            <span className="text-[10px]" style={{ color: "#6B7280" }}>진행</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="inline-block rounded-full" style={{ width: 8, height: 8, backgroundColor: PROCESS_STATUS_COLORS["대기"] }} />
            <span className="text-[10px]" style={{ color: "#6B7280" }}>대기</span>
          </div>
          {/* 활성 필터 배지 */}
          {hasActiveFilter && (
            <div className="flex items-center gap-1.5">
              <span className="text-[10px] px-2 py-0.5 rounded-full font-medium"
                style={{ backgroundColor: "#FEF2F2", color: "#C41230", border: "1px solid #FECACA" }}>
                필터 {activeFilterCount}개 적용 중 ({filteredBatches.length}/{sortedBatches.length}행)
              </span>
              <button
                onClick={() => setColFilters({})}
                className="text-[10px] px-2 py-0.5 rounded"
                style={{ color: "#6B7280", border: "1px solid #E5E7EB" }}
              >
                전체 해제
              </button>
            </div>
          )}
        </div>

        {/* 컬럼 설정 */}
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
              <path d="M9.796 1.343c-.527-1.79-3.065-1.79-3.592 0l-.094.319a.873.873 0 0 1-1.255.52l-.292-.16c-1.64-.892-3.433.902-2.54 2.541l.159.292a.873.873 0 0 1-.52 1.255l-.319.094c-1.79.527-1.79 3.065 0 3.592l.319.094a.873.873 0 0 1 .52 1.255l-.16.292c-.892 1.64.901 3.434 2.541 2.54l.292-.159a.873.873 0 0 1 1.255.52l.094.319c.527 1.79 3.065 1.79 3.592 0l.094-.319a.873.873 0 0 1 1.255-.52l.292.16c1.64.893 3.434-.902 2.54-2.541l-.159-.292a.873.873 0 0 1 .52-1.255l.319-.094c1.79-.527 1.79-3.065 0-3.592l-.319-.094a.873.873 0 0 1-.52-1.255l.16-.292c.893-1.64-.902-3.433-2.541-2.54l-.292.159a.873.873 0 0 1-1.255-.52l-.094-.319z"/>
            </svg>
            컬럼 설정
            {hiddenCols.size > 0 && (
              <span className="ml-0.5 text-[9px] font-bold" style={{ color: "#C41230" }}>-{hiddenCols.size}</span>
            )}
          </button>
          {colMenuOpen && (
            <div className="absolute right-0 mt-1 rounded-lg shadow-lg z-50 py-1"
              style={{ backgroundColor: "#FFFFFF", border: "1px solid #E5E7EB", minWidth: 160, top: "100%" }}>
              {COL_DEFS.map((col) => (
                <label key={col.key} className="flex items-center gap-2 px-3 py-1.5 cursor-pointer hover:bg-gray-50">
                  <input type="checkbox" checked={!hiddenCols.has(col.key)} onChange={() => toggleCol(col.key)} className="w-3 h-3 accent-red-700" />
                  <span className="text-[11px]" style={{ color: "#374151" }}>{col.label}</span>
                </label>
              ))}
              {hiddenCols.size > 0 && (
                <>
                  <div style={{ borderTop: "1px solid #F3F4F6", margin: "4px 0" }} />
                  <button onClick={() => setHiddenCols(new Set())} className="w-full text-left px-3 py-1 text-[10px]" style={{ color: "#6B7280" }}>
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
                {visibleCols.map((col) => <col key={col.key} style={{ width: col.width }} />)}
              </colgroup>
              <thead>
                <tr style={{ backgroundColor: "#F9FAFB" }}>
                  {visibleCols.map((col, i) => {
                    const isFiltered = !!colFilters[col.key];
                    const isOpen = openFilterCol === col.key;
                    return (
                      <th
                        key={col.key}
                        className="text-[10px] font-semibold px-3 py-2 select-none"
                        style={{
                          color: isFiltered ? "#C41230" : "#6B7280",
                          textAlign: col.align as "left" | "right",
                          borderBottom: "1px solid #E5E7EB",
                          borderRight: i < visibleCols.length - 1 ? "1px solid #E5E7EB" : "none",
                          whiteSpace: "nowrap",
                          cursor: "pointer",
                          backgroundColor: isOpen ? "#FEF2F2" : isFiltered ? "#FFF7F7" : undefined,
                          position: "relative",
                        }}
                        onClick={() => setOpenFilterCol(isOpen ? null : col.key)}
                      >
                        <span className="flex items-center gap-1" style={{ justifyContent: col.align === "right" ? "flex-end" : "flex-start" }}>
                          {col.label}
                          {/* 필터 아이콘 */}
                          <svg width="9" height="9" viewBox="0 0 16 16" fill="currentColor"
                            style={{ color: isFiltered ? "#C41230" : "#D1D5DB", flexShrink: 0 }}>
                            <path d="M1.5 1.5A.5.5 0 0 1 2 1h12a.5.5 0 0 1 .5.5v2a.5.5 0 0 1-.128.334L10 8.692V13.5a.5.5 0 0 1-.342.474l-3 1A.5.5 0 0 1 6 14.5V8.692L1.628 3.834A.5.5 0 0 1 1.5 3.5v-2z"/>
                          </svg>
                        </span>

                        {/* 필터 드롭다운 */}
                        {isOpen && (
                          <div
                            ref={filterMenuRef}
                            className="absolute left-0 mt-1 rounded-lg shadow-xl z-50"
                            style={{
                              top: "100%",
                              backgroundColor: "#FFFFFF",
                              border: "1px solid #E5E7EB",
                              minWidth: 180,
                              maxHeight: 280,
                              display: "flex",
                              flexDirection: "column",
                            }}
                            onClick={(e) => e.stopPropagation()}
                          >
                            {/* 검색 */}
                            <div className="p-2 border-b" style={{ borderColor: "#F3F4F6" }}>
                              <input
                                type="text"
                                value={filterSearch}
                                onChange={(e) => setFilterSearch(e.target.value)}
                                placeholder="값 검색..."
                                autoFocus
                                className="w-full text-[11px] px-2 py-1 rounded"
                                style={{ border: "1px solid #D1D5DB", outline: "none" }}
                              />
                            </div>
                            {/* 전체선택 */}
                            <div className="flex items-center justify-between px-3 py-1.5 border-b" style={{ borderColor: "#F3F4F6" }}>
                              <label className="flex items-center gap-2 cursor-pointer">
                                <input
                                  type="checkbox"
                                  className="w-3 h-3 accent-red-700"
                                  checked={!colFilters[col.key]}
                                  onChange={() => toggleAllFilterValues(col.key, !!colFilters[col.key])}
                                />
                                <span className="text-[11px] font-medium" style={{ color: "#374151" }}>전체</span>
                              </label>
                              {isFiltered && (
                                <button
                                  onClick={() => clearColFilter(col.key)}
                                  className="text-[10px]"
                                  style={{ color: "#C41230" }}
                                >
                                  초기화
                                </button>
                              )}
                            </div>
                            {/* 값 목록 */}
                            <div className="overflow-y-auto" style={{ maxHeight: 200 }}>
                              {filteredUniqueValues.map((val) => {
                                const selected = !colFilters[col.key] || colFilters[col.key]!.has(val);
                                return (
                                  <label
                                    key={val}
                                    className="flex items-center gap-2 px-3 py-1.5 cursor-pointer hover:bg-gray-50"
                                  >
                                    <input
                                      type="checkbox"
                                      className="w-3 h-3 accent-red-700"
                                      checked={selected}
                                      onChange={() => toggleFilterValue(col.key, val)}
                                    />
                                    <span className="text-[11px] truncate" style={{ color: "#374151" }}>
                                      {val || "(빈 값)"}
                                    </span>
                                  </label>
                                );
                              })}
                              {filteredUniqueValues.length === 0 && (
                                <div className="px-3 py-3 text-[10px]" style={{ color: "#9CA3AF" }}>결과 없음</div>
                              )}
                            </div>
                          </div>
                        )}
                      </th>
                    );
                  })}
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
                    const isHighlighted = highlightedBatchIds?.has(batch.id) ?? false;
                    const isWipSkipped = batch.notes.includes("재고 사용");
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
                                if (col.key === "notes" && batch.notes.includes("재고 사용")) return;
                                const raw = batch[col.key as keyof SchedulingBatch];
                                startEditing(batch.id, col.key, raw != null ? String(raw) : "");
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
                              ) : col.key === "notes" && batch.notes.includes("재고 사용") ? (
                                <button
                                  onClick={() => onBatchWipClick?.(batch.id)}
                                  className="text-[11px] font-medium px-1.5 py-0.5 rounded transition-colors"
                                  style={{
                                    ...getWipNoteStyle(batch.notes),
                                    cursor: onBatchWipClick ? "pointer" : "default",
                                  }}
                                >
                                  {batch.notes}
                                </button>
                              ) : (
                                <span className="block truncate text-[11px]" style={{ textAlign: col.align as "left" | "right" }}>
                                  {getCellValue(col, batch) || <span style={{ color: "#CBD5E1" }}>–</span>}
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
