"use client";

import { useMemo, useState, useRef, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
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

interface SchedulingResultTableProps {
  yeonseoBatches: SchedulingBatch[];
  insulationBatches: SchedulingBatch[];
  sheatBatches: SchedulingBatch[];
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
  { key: "processStatus", label: "공정상태", align: "left", width: 64 },
  { key: "convertedQty", label: "환산수량", align: "right", width: 80 },
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

/** CRUD 모드 */
type CrudMode = "view" | "edit" | "add" | "delete";

interface EditingCell {
  batchId: string;
  field: string;
}

/** 새 행 추가 시 사용할 임시 ID 생성 */
let tempIdCounter = 0;
function generateTempId(): string {
  tempIdCounter += 1;
  return `new-${Date.now()}-${tempIdCounter}`;
}

export function SchedulingResultTable({
  yeonseoBatches,
  insulationBatches,
  sheatBatches,
  activeTab,
  onTabChange,
}: SchedulingResultTableProps) {
  const router = useRouter();
  const updateBatch = useSchedulingReviewStore((s) => s.updateBatch);

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

  // 배치번호 기준 오름차순 정렬 (납기 빠른 그룹 -> 색상 순)
  const sortedBatches = useMemo(
    () => sortBatchesByBatchNumber(activeBatches, batchNumbers),
    [activeBatches, batchNumbers],
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

  // ── CRUD 상태 ──
  const [crudMode, setCrudMode] = useState<CrudMode>("view");
  const [hasChanges, setHasChanges] = useState(false);

  // ── 수정 모드: 인라인 편집 ──
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
      if (crudMode !== "edit" && crudMode !== "add") return;
      setEditingCell({ batchId, field });
      setEditValue(currentValue);
    },
    [crudMode],
  );

  const commitEdit = useCallback(async () => {
    if (!editingCell) return;
    const { batchId, field } = editingCell;

    // 새로 추가된 행인 경우 로컬 상태만 업데이트
    if (batchId.startsWith("new-")) {
      setAddedRows((prev) =>
        prev.map((row) => {
          if (row.id !== batchId) return row;
          const isNumericField =
            field === "unit_count" || field === "length_per_unit_m";
          const finalValue = isNumericField
            ? Number(editValue) || 0
            : editValue;
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          const updated = { ...row, [field]: finalValue } as any;
          if (field === "unit_count" || field === "length_per_unit_m") {
            updated.total_length_m =
              (updated.length_per_unit_m || 0) * (updated.unit_count || 1);
          }
          return updated as SchedulingBatch;
        }),
      );
      setHasChanges(true);
      setEditingCell(null);
      return;
    }

    // 기존 배치 — API PATCH
    const numericId = parseInt(batchId.replace("batch-", ""), 10);
    if (isNaN(numericId)) {
      setEditingCell(null);
      return;
    }

    const isNumericField =
      field === "unit_count" || field === "length_per_unit_m";
    const finalValue = isNumericField ? Number(editValue) || 0 : editValue;

    await updateBatch(numericId, field, finalValue);
    setHasChanges(true);
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

  const isCellEditing = useCallback(
    (batchId: string, field: string) =>
      editingCell?.batchId === batchId && editingCell?.field === field,
    [editingCell],
  );

  // ── 추가 모드 ──
  const [addedRows, setAddedRows] = useState<SchedulingBatch[]>([]);

  const handleAdd = useCallback(() => {
    const newRow: SchedulingBatch = {
      id: generateTempId(),
      product: "",
      spec: "",
      color: "",
      customer: "",
      delivery_date: "",
      length_per_unit_m: 0,
      unit_count: 0,
      total_length_m: 0,
      equipment_group:
        activeTab === "연선" ? "연선" : activeTab === "절연" ? "B100" : "A120",
      voltage_type: "저압",
      notes: "",
      classification_reason: "",
      processGroup: activeTab,
      processStatus: "대기",
      convertedQty: 0,
    };
    setAddedRows((prev) => [...prev, newRow]);
    setCrudMode("add");
    setHasChanges(true);
  }, [activeTab]);

  // ── 삭제 모드 ──
  const [selectedForDelete, setSelectedForDelete] = useState<Set<string>>(
    new Set(),
  );
  const [deletedIds, setDeletedIds] = useState<Set<string>>(new Set());

  const toggleSelectForDelete = useCallback((batchId: string) => {
    setSelectedForDelete((prev) => {
      const next = new Set(prev);
      if (next.has(batchId)) {
        next.delete(batchId);
      } else {
        next.add(batchId);
      }
      return next;
    });
  }, []);

  const confirmDelete = useCallback(() => {
    setDeletedIds((prev) => {
      const next = new Set(prev);
      selectedForDelete.forEach((id) => next.add(id));
      return next;
    });
    // 추가된 행 중 삭제 대상 제거
    setAddedRows((prev) =>
      prev.filter((row) => !selectedForDelete.has(row.id)),
    );
    setSelectedForDelete(new Set());
    setHasChanges(true);
  }, [selectedForDelete]);

  // ── 확인 (최종 저장) ──
  const handleConfirm = useCallback(() => {
    // PoC: 로컬 상태 변경만 반영 완료 표시
    setHasChanges(false);
    setCrudMode("view");
    setAddedRows([]);
    setDeletedIds(new Set());
    setSelectedForDelete(new Set());
    setEditingCell(null);
  }, []);

  // ── 모드 전환 핸들러 ──
  const handleEditMode = useCallback(() => {
    setCrudMode((prev) => (prev === "edit" ? "view" : "edit"));
    setSelectedForDelete(new Set());
  }, []);

  const handleDeleteMode = useCallback(() => {
    setCrudMode((prev) => (prev === "delete" ? "view" : "delete"));
    setSelectedForDelete(new Set());
    setEditingCell(null);
  }, []);

  // 표시할 배치 목록: 정렬된 배치 + 추가된 행 - 삭제된 행
  const displayBatches = useMemo(() => {
    const filtered = sortedBatches.filter((b) => !deletedIds.has(b.id));
    return [...filtered, ...addedRows];
  }, [sortedBatches, deletedIds, addedRows]);

  // 체크박스 컬럼 포함 시 총 너비
  const checkboxColWidth = 36;
  const totalWidth =
    COL_DEFS.reduce((sum, c) => sum + c.width, 0) +
    (crudMode === "delete" ? checkboxColWidth : 0);

  // 편집 가능 여부 (수정/추가 모드일 때만)
  const isEditable = crudMode === "edit" || crudMode === "add";

  return (
    <section>
      {/* Tab bar + CRUD 버튼 */}
      <div
        className="flex items-center justify-between mb-0"
        style={{ borderBottom: "1px solid #E5E7EB" }}
      >
        {/* 탭 */}
        <div className="flex items-center gap-0">
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

        {/* CRUD 버튼 */}
        <div className="flex items-center gap-1 pr-1">
          <CrudButton
            label="수정"
            isActive={crudMode === "edit"}
            onClick={handleEditMode}
          />
          <CrudButton
            label="추가"
            isActive={crudMode === "add"}
            onClick={handleAdd}
          />
          <CrudButton
            label="삭제"
            isActive={crudMode === "delete"}
            onClick={handleDeleteMode}
          />
          <CrudButton
            label="확인"
            isActive={false}
            onClick={handleConfirm}
            disabled={!hasChanges}
            confirm
          />
        </div>
      </div>

      {/* Table */}
      <div
        className="rounded-b-lg overflow-hidden"
        style={{
          border: "1px solid #E5E7EB",
          borderTop: "none",
        }}
      >
        {displayBatches.length === 0 ? (
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
                {crudMode === "delete" && (
                  <col style={{ width: checkboxColWidth }} />
                )}
                {COL_DEFS.map((col) => (
                  <col key={col.key} style={{ width: col.width }} />
                ))}
              </colgroup>
              <thead>
                <tr style={{ backgroundColor: "#F9FAFB" }}>
                  {crudMode === "delete" && (
                    <th
                      className="text-[10px] font-semibold px-1 py-2"
                      style={{
                        color: "#6B7280",
                        borderBottom: "1px solid #E5E7EB",
                        borderRight: "1px solid #E5E7EB",
                        textAlign: "center",
                        whiteSpace: "nowrap",
                      }}
                    >
                      선택
                    </th>
                  )}
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
                {displayBatches.map((batch) => {
                  const isNewRow = batch.id.startsWith("new-");
                  const isWipSkipped = batch.notes === "재고 사용";
                  const rowBg = isNewRow
                    ? "#FFFBEB"
                    : isWipSkipped
                      ? "#F3F4F6"
                      : getRowBg(batch);
                  const hoverBg = isNewRow
                    ? "#FEF3C7"
                    : isWipSkipped
                      ? "#E5E7EB"
                      : getRowHoverBg(batch);
                  const statusColor =
                    PROCESS_STATUS_COLORS[batch.processStatus] ?? "#E5E7EB";
                  /** 재고 사용 행은 텍스트를 회색으로 -- 간트 차트 미반영 표시 */
                  const textColor = isWipSkipped ? "#9CA3AF" : undefined;

                  const isSelected = selectedForDelete.has(batch.id);

                  return (
                    <tr
                      key={batch.id}
                      style={{
                        backgroundColor: isSelected ? "#FEE2E2" : rowBg,
                        color: textColor,
                      }}
                      onMouseEnter={(e) => {
                        if (!isSelected) {
                          (
                            e.currentTarget as HTMLElement
                          ).style.backgroundColor = hoverBg;
                        }
                      }}
                      onMouseLeave={(e) => {
                        (e.currentTarget as HTMLElement).style.backgroundColor =
                          isSelected
                            ? "#FEE2E2"
                            : isNewRow
                              ? "#FFFBEB"
                              : isWipSkipped
                                ? "#F3F4F6"
                                : getRowBg(batch);
                      }}
                    >
                      {/* 삭제 모드 체크박스 */}
                      {crudMode === "delete" && (
                        <td
                          className="px-1"
                          style={{
                            height: 38,
                            borderBottom: "1px solid #E5E7EB",
                            borderRight: "1px solid #F3F4F6",
                            textAlign: "center",
                            verticalAlign: "middle",
                          }}
                        >
                          <input
                            type="checkbox"
                            checked={isSelected}
                            onChange={() => toggleSelectForDelete(batch.id)}
                            className="rounded"
                            style={{
                              width: 14,
                              height: 14,
                              accentColor: PRIMARY,
                            }}
                          />
                        </td>
                      )}

                      {COL_DEFS.map((col, colIdx) => {
                        const editable =
                          isEditable && EDITABLE_KEYS.has(col.key);
                        // 새 행에서는 규격도 편집 가능
                        const editableForNewRow =
                          isEditable &&
                          isNewRow &&
                          (col.key === "spec" ||
                            col.key === "product" ||
                            col.key === "customer" ||
                            col.key === "delivery_date");
                        const canEdit = editable || editableForNewRow;
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
                              cursor: canEdit ? "text" : "default",
                              backgroundColor:
                                canEdit && !currentlyEditing
                                  ? "#F0F9FF"
                                  : undefined,
                            }}
                            onClick={() => {
                              if (!canEdit) return;
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
                                {isNewRow ? "신규" : getBatchLabel(batch)}
                              </span>
                            ) : col.key === "notes" &&
                              batch.notes === "재고 사용" ? (
                              <span
                                className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold"
                                style={{
                                  backgroundColor: "#DBEAFE",
                                  color: "#1D4ED8",
                                }}
                              >
                                재고 사용
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

      {/* 삭제 모드일 때 하단 삭제 확인 버튼 */}
      {crudMode === "delete" && selectedForDelete.size > 0 && (
        <div className="flex justify-end mt-2">
          <button
            onClick={confirmDelete}
            className="text-[11px] font-medium px-3 py-1.5 rounded-md transition-colors"
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
            선택 항목 삭제 ({selectedForDelete.size}건)
          </button>
        </div>
      )}

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

// ── CRUD 버튼 공용 컴포넌트 ──

function CrudButton({
  label,
  isActive,
  onClick,
  disabled = false,
  confirm = false,
}: {
  label: string;
  isActive: boolean;
  onClick: () => void;
  disabled?: boolean;
  confirm?: boolean;
}) {
  const activeStyle = {
    backgroundColor: confirm ? PRIMARY : isActive ? PRIMARY : "#FFFFFF",
    color: confirm
      ? "#FFFFFF"
      : isActive
        ? "#FFFFFF"
        : disabled
          ? "#D1D5DB"
          : "#374151",
    border: `1px solid ${isActive || confirm ? PRIMARY : "#E5E7EB"}`,
    opacity: disabled ? 0.5 : 1,
    cursor: disabled ? "not-allowed" : "pointer",
  } as const;

  return (
    <button
      onClick={disabled ? undefined : onClick}
      className="text-[11px] font-medium px-2 py-1 rounded transition-colors"
      style={activeStyle}
      onMouseEnter={(e) => {
        if (disabled) return;
        if (!isActive && !confirm) {
          e.currentTarget.style.borderColor = PRIMARY;
          e.currentTarget.style.color = PRIMARY;
        }
      }}
      onMouseLeave={(e) => {
        if (disabled) return;
        if (!isActive && !confirm) {
          e.currentTarget.style.borderColor = "#E5E7EB";
          e.currentTarget.style.color = "#374151";
        }
      }}
    >
      {label}
    </button>
  );
}
