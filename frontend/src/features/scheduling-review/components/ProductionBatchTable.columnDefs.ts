/**
 * ProductionBatchTable 의 컬럼 정의 (Task 1.17, F-3).
 *
 * 추출 대상:
 * - COL_DEFS (14 컬럼)
 * - ColKey type
 * - DEFAULT_HIDDEN_COLS
 * - EDITABLE_KEYS
 */

export const COL_DEFS = [
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

export type ColKey = (typeof COL_DEFS)[number]["key"];

/** 탭과 중복되어 기본 숨김 처리할 컬럼 */
export const DEFAULT_HIDDEN_COLS: ColKey[] = ["processGroup"];

export const EDITABLE_KEYS = new Set([
  "color",
  "unit_count",
  "length_per_unit_m",
  "notes",
]);
