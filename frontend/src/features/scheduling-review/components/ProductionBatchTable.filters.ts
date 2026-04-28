/**
 * ProductionBatchTable 의 필터 / 셀 값 추출 helper (Task 1.17, F-3).
 *
 * 추출 대상:
 * - ColFilters type
 * - getCellValueStatic
 */

import type { SchedulingBatch } from "../types";
import {
  getBatchGroupKey,
  formatDeliveryDate,
} from "@/shared/utils/batchGrouping";
import type { COL_DEFS, ColKey } from "./ProductionBatchTable.columnDefs";

/** 컬럼별 선택된 값 집합 — undefined면 필터 없음(전체) */
export type ColFilters = Partial<Record<ColKey, Set<string>>>;

export function getCellValueStatic(
  col: (typeof COL_DEFS)[number],
  batch: SchedulingBatch,
  batchNumbers: Map<string, number>,
): string {
  if (col.key === "batch_label") {
    const num = batchNumbers.get(getBatchGroupKey(batch));
    return num != null ? `배치 ${num}` : "";
  }
  if (col.key === "delivery_date")
    return formatDeliveryDate(batch.delivery_date);
  const raw = batch[col.key as keyof SchedulingBatch];
  if (typeof raw === "number") return raw.toLocaleString();
  return String(raw ?? "");
}
