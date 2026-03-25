"use client";

import { useRef, useState, useCallback, useMemo } from "react";
import { useRouter } from "next/navigation";
import { usePlanRegisterStore } from "../store/planRegisterStore";
import { VoltageFilter } from "./VoltageFilter";
import type { ProductionBatch } from "@/features/scheduler/types";

const PRIMARY = "#C41230";

/** 숫자 컬럼 우측 정렬, 나머지 좌측 */
const COL_DEFS = [
  {
    key: "batch_label",
    label: "배치",
    align: "left",
    editable: false,
    width: 64,
  },
  { key: "product", label: "품목", align: "left", editable: false, width: 120 },
  { key: "spec", label: "규격", align: "left", editable: false, width: 130 },
  { key: "color", label: "색상", align: "left", editable: false, width: 90 },
  {
    key: "customer",
    label: "거래처",
    align: "left",
    editable: false,
    width: 120,
  },
  {
    key: "delivery_date",
    label: "납품일",
    align: "left",
    editable: false,
    width: 90,
  },
  {
    key: "length_per_unit_m",
    label: "조장(M)",
    align: "right",
    editable: true,
    width: 80,
  },
  {
    key: "unit_count",
    label: "개수(ea)",
    align: "right",
    editable: true,
    width: 80,
  },
  {
    key: "total_length_m",
    label: "수량(M)",
    align: "right",
    editable: true,
    width: 80,
  },
  { key: "notes", label: "비고", align: "left", editable: true, width: 120 },
  {
    key: "classification_reason",
    label: "분류근거",
    align: "left",
    editable: false,
    width: 200,
  },
  { key: "detail", label: "", align: "center", editable: false, width: 52 },
] as const;

type ColKey = (typeof COL_DEFS)[number]["key"];

/** Batch group key: product + spec + customer + delivery_date */
function getBatchGroupKey(b: ProductionBatch): string {
  return `${b.product}||${b.spec}||${b.customer}||${b.delivery_date}`;
}

/** Assign sequential batch numbers based on first appearance order */
function assignBatchNumbers(batches: ProductionBatch[]): Map<string, number> {
  const map = new Map<string, number>();
  let counter = 1;
  for (const b of batches) {
    const key = getBatchGroupKey(b);
    if (!map.has(key)) {
      map.set(key, counter++);
    }
  }
  return map;
}

const BATCH_BG_EVEN = "#FFFFFF";
const BATCH_BG_ODD = "#F7F8FA";
const BATCH_BG_EVEN_HOVER = "#FEF9F9";
const BATCH_BG_ODD_HOVER = "#FDF4F4";

function formatDeliveryDate(raw: string): string {
  if (raw.length === 8) {
    return `${raw.slice(0, 4)}.${raw.slice(4, 6)}.${raw.slice(6, 8)}`;
  }
  return raw;
}

/** Detail popup modal */
function DetailModal({
  batch,
  onClose,
}: {
  batch: ProductionBatch;
  onClose: () => void;
}) {
  const r = batch.rawData;
  const rows: { label: string; value: string }[] = [
    { label: "수주번호", value: r?.order_number ?? "—" },
    { label: "전압", value: r?.voltage ?? "—" },
    { label: "중성선", value: r?.neutral_wire || "—" },
    { label: "선심색상", value: r?.core_color ?? "—" },
    { label: "제품군", value: r?.product_group ?? "—" },
    {
      label: "원화단가",
      value:
        r?.unit_price_krw != null
          ? r.unit_price_krw.toLocaleString("ko-KR", {
              minimumFractionDigits: 2,
            }) + " 원"
          : "—",
    },
    {
      label: "원화금액",
      value:
        r?.total_price_krw != null
          ? r.total_price_krw.toLocaleString("ko-KR", {
              minimumFractionDigits: 2,
            }) + " 원"
          : "—",
    },
  ];

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ backgroundColor: "rgba(0,0,0,0.35)" }}
      onClick={onClose}
    >
      <div
        className="relative rounded-lg shadow-lg"
        style={{
          backgroundColor: "#FFFFFF",
          border: "1px solid #E5E7EB",
          minWidth: 340,
          maxWidth: 420,
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div
          className="flex items-center justify-between px-5 py-3"
          style={{ borderBottom: "1px solid #E5E7EB" }}
        >
          <span className="text-xs font-semibold" style={{ color: "#111827" }}>
            배치 상세 정보
          </span>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 transition-colors"
            style={{ lineHeight: 1, fontSize: 16 }}
          >
            ×
          </button>
        </div>
        {/* Product summary */}
        <div
          className="px-5 py-2.5"
          style={{
            borderBottom: "1px solid #F3F4F6",
            backgroundColor: "#FAFAFA",
          }}
        >
          <p className="text-[11px] font-medium" style={{ color: "#4A2C2A" }}>
            {batch.product} — {batch.spec}
          </p>
          <p className="text-[10px] text-gray-400 mt-0.5">
            {batch.customer} / {formatDeliveryDate(batch.delivery_date)}
          </p>
        </div>
        {/* Field table */}
        <div className="px-5 py-3">
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <tbody>
              {rows.map(({ label, value }) => (
                <tr key={label} style={{ borderBottom: "1px solid #F3F4F6" }}>
                  <td
                    className="py-1.5 text-[10px] font-medium"
                    style={{
                      color: "#6B7280",
                      width: 100,
                      verticalAlign: "top",
                    }}
                  >
                    {label}
                  </td>
                  <td
                    className="py-1.5 text-[11px]"
                    style={{ color: "#111827", verticalAlign: "top" }}
                  >
                    {value}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="px-5 pb-4 flex justify-end">
          <button
            onClick={onClose}
            className="text-xs font-medium px-3 py-1.5 rounded-md transition-colors"
            style={{ border: "1px solid #E5E7EB", color: "#374151" }}
            onMouseEnter={(e) =>
              (e.currentTarget.style.backgroundColor = "#F9FAFB")
            }
            onMouseLeave={(e) =>
              (e.currentTarget.style.backgroundColor = "transparent")
            }
          >
            닫기
          </button>
        </div>
      </div>
    </div>
  );
}

function CellValue({
  col,
  batch,
  batchLabel,
  editMode,
  onUpdate,
}: {
  col: (typeof COL_DEFS)[number];
  batch: ProductionBatch;
  batchLabel: string;
  editMode: boolean;
  onUpdate: (id: string, key: ColKey, value: string | number) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  if (col.key === "batch_label") {
    return (
      <span
        className="block truncate text-[10px] font-medium"
        style={{ color: "#9CA3AF" }}
      >
        {batchLabel}
      </span>
    );
  }

  if (col.key === "detail") {
    // rendered by the parent row
    return null;
  }

  const rawValue = batch[col.key as keyof ProductionBatch];
  const displayValue =
    col.key === "delivery_date"
      ? formatDeliveryDate(String(rawValue))
      : typeof rawValue === "number"
        ? rawValue.toLocaleString()
        : String(rawValue ?? "");

  const isEditable = col.editable && editMode;

  const startEdit = useCallback(() => {
    if (!isEditable) return;
    setDraft(String(rawValue ?? ""));
    setEditing(true);
    requestAnimationFrame(() => inputRef.current?.select());
  }, [isEditable, rawValue]);

  const commitEdit = useCallback(() => {
    setEditing(false);
    const isNumeric = typeof rawValue === "number";
    const parsed = isNumeric ? Number(draft) : draft;
    if (isNumeric && isNaN(parsed as number)) return;
    onUpdate(batch.id, col.key, parsed);
  }, [draft, rawValue, batch.id, col.key, onUpdate]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Enter") commitEdit();
      if (e.key === "Escape") setEditing(false);
    },
    [commitEdit],
  );

  if (editing && isEditable) {
    return (
      <input
        ref={inputRef}
        autoFocus
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commitEdit}
        onKeyDown={handleKeyDown}
        className="w-full text-[11px] px-1 py-0.5 rounded-sm outline-none bg-white"
        style={{
          border: `1px solid ${PRIMARY}`,
          textAlign: col.align as "left" | "right",
        }}
      />
    );
  }

  return (
    <span
      className="block truncate text-[11px]"
      style={{
        cursor: isEditable ? "text" : "default",
        textAlign: col.align as "left" | "right",
      }}
      onClick={startEdit}
      title={isEditable ? "클릭하여 편집" : undefined}
    >
      {displayValue || "\u2014"}
    </span>
  );
}

export function ScheduleReviewTable() {
  const router = useRouter();
  const {
    isAnalyzed,
    batches,
    voltageFilter,
    equipmentFilter,
    updateBatch,
    confirmBatches,
  } = usePlanRegisterStore();

  const [editMode, setEditMode] = useState(false);
  const [hasEdited, setHasEdited] = useState(false);
  const [detailBatch, setDetailBatch] = useState<ProductionBatch | null>(null);

  const handleUpdate = useCallback(
    (id: string, key: ColKey, value: string | number) => {
      updateBatch(id, { [key]: value });
      setHasEdited(true);
    },
    [updateBatch],
  );

  const handleConfirm = useCallback(() => {
    confirmBatches();
    router.push("/scheduler");
  }, [confirmBatches, router]);

  const handleEditToggle = useCallback(() => {
    if (editMode) {
      // "수정 완료" clicked
      setEditMode(false);
    } else {
      setEditMode(true);
    }
  }, [editMode]);

  // 필터링된 배치 목록
  const filteredBatches = batches.filter((b) => {
    if (voltageFilter === "고압") return b.voltage_type === "고압";
    if (voltageFilter === "저압") {
      if (b.voltage_type !== "저압") return false;
      if (equipmentFilter !== null)
        return b.equipment_group === equipmentFilter;
      return true;
    }
    // 전체
    if (equipmentFilter !== null) return b.equipment_group === equipmentFilter;
    return true;
  });

  // Batch numbering across ALL batches (not just filtered), so numbers stay consistent
  const batchNumbers = useMemo(() => assignBatchNumbers(batches), [batches]);

  // Per-row batch number from filtered list (derived from global numbers)
  function getBatchLabel(b: ProductionBatch): string {
    const num = batchNumbers.get(getBatchGroupKey(b));
    return num != null ? `배치 ${num}` : "";
  }

  // Alternating background by batch group number (odd/even)
  function getRowBg(b: ProductionBatch): string {
    const num = batchNumbers.get(getBatchGroupKey(b)) ?? 1;
    return num % 2 === 0 ? BATCH_BG_ODD : BATCH_BG_EVEN;
  }

  function getRowHoverBg(b: ProductionBatch): string {
    const num = batchNumbers.get(getBatchGroupKey(b)) ?? 1;
    return num % 2 === 0 ? BATCH_BG_ODD_HOVER : BATCH_BG_EVEN_HOVER;
  }

  const totalWidth = COL_DEFS.reduce((sum, c) => sum + c.width, 0);

  return (
    <>
      {detailBatch && (
        <DetailModal batch={detailBatch} onClose={() => setDetailBatch(null)} />
      )}
      <section>
        {/* Section header with action buttons */}
        <div className="flex items-center justify-between mb-3">
          <div>
            <h3 className="text-sm font-semibold" style={{ color: "#111827" }}>
              2. 생산 스케줄링 검토
            </h3>
            {isAnalyzed && batches.length > 0 && (
              <p className="text-[10px] text-gray-400 mt-0.5">
                총 {batches.length}건 분류 완료 &middot; 현재 표시{" "}
                {filteredBatches.length}건
              </p>
            )}
          </div>
          {isAnalyzed && batches.length > 0 && (
            <div className="flex items-center gap-2">
              <button
                onClick={handleEditToggle}
                className="text-xs font-medium px-3 py-1.5 rounded-md transition-colors"
                style={{
                  border: `1px solid ${editMode ? PRIMARY : "#D1D5DB"}`,
                  color: editMode ? PRIMARY : "#374151",
                  backgroundColor: "transparent",
                }}
                onMouseEnter={(e) =>
                  (e.currentTarget.style.backgroundColor = editMode
                    ? "#FFF5F5"
                    : "#F9FAFB")
                }
                onMouseLeave={(e) =>
                  (e.currentTarget.style.backgroundColor = "transparent")
                }
              >
                {editMode ? "수정 완료" : "수정"}
              </button>
              <button
                onClick={handleConfirm}
                disabled={!hasEdited && !editMode}
                className="text-xs font-medium px-3 py-1.5 rounded-md transition-colors"
                style={{
                  backgroundColor: hasEdited || editMode ? PRIMARY : "#E5E7EB",
                  color: hasEdited || editMode ? "#FFFFFF" : "#9CA3AF",
                  cursor: hasEdited || editMode ? "pointer" : "not-allowed",
                }}
                onMouseEnter={(e) => {
                  if (hasEdited || editMode)
                    e.currentTarget.style.backgroundColor = "#A01028";
                }}
                onMouseLeave={(e) => {
                  if (hasEdited || editMode)
                    e.currentTarget.style.backgroundColor = PRIMARY;
                }}
              >
                배치 확정
              </button>
            </div>
          )}
        </div>

        {/* 필터 */}
        {isAnalyzed && <VoltageFilter />}

        {/* 테이블 */}
        <div
          className="rounded-lg overflow-hidden"
          style={{ border: "1px solid #E5E7EB" }}
        >
          {!isAnalyzed ? (
            <div
              className="flex items-center justify-center py-12 text-xs text-gray-400"
              style={{ backgroundColor: "#FAFAFA" }}
            >
              파일을 업로드하면 배치 결과가 표시됩니다
            </div>
          ) : filteredBatches.length === 0 ? (
            <div
              className="flex items-center justify-center py-12 text-xs text-gray-400"
              style={{ backgroundColor: "#FAFAFA" }}
            >
              해당 필터 조건에 맞는 배치가 없습니다
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
                          textAlign: col.align as "left" | "right" | "center",
                          borderBottom: "1px solid #E5E7EB",
                          borderRight:
                            i < COL_DEFS.length - 1
                              ? "1px solid #E5E7EB"
                              : "none",
                          whiteSpace: "nowrap",
                        }}
                      >
                        {col.label}
                        {/* Show 편집 badge only in edit mode for editable columns */}
                        {editMode && col.editable && (
                          <span
                            className="ml-1 text-[9px] font-normal"
                            style={{ color: PRIMARY }}
                          >
                            편집
                          </span>
                        )}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {filteredBatches.map((batch) => {
                    const rowBg = getRowBg(batch);
                    const hoverBg = getRowHoverBg(batch);
                    return (
                      <tr
                        key={batch.id}
                        style={{ backgroundColor: rowBg }}
                        onMouseEnter={(e) => {
                          (
                            e.currentTarget as HTMLElement
                          ).style.backgroundColor = hoverBg;
                        }}
                        onMouseLeave={(e) => {
                          (
                            e.currentTarget as HTMLElement
                          ).style.backgroundColor = rowBg;
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
                              verticalAlign: "middle",
                              overflow: "hidden",
                            }}
                          >
                            {col.key === "detail" ? (
                              <button
                                onClick={() => setDetailBatch(batch)}
                                className="text-[10px] font-medium px-1.5 py-0.5 rounded transition-colors"
                                style={{
                                  border: "1px solid #E5E7EB",
                                  color: "#6B7280",
                                  backgroundColor: "transparent",
                                }}
                                onMouseEnter={(e) => {
                                  e.currentTarget.style.backgroundColor =
                                    "#F3F4F6";
                                  e.currentTarget.style.color = "#374151";
                                }}
                                onMouseLeave={(e) => {
                                  e.currentTarget.style.backgroundColor =
                                    "transparent";
                                  e.currentTarget.style.color = "#6B7280";
                                }}
                              >
                                상세
                              </button>
                            ) : (
                              <CellValue
                                col={col}
                                batch={batch}
                                batchLabel={getBatchLabel(batch)}
                                editMode={editMode}
                                onUpdate={handleUpdate}
                              />
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
      </section>
    </>
  );
}
