import type { ProductionBatch } from "@/features/scheduler/types";
import type {
  ProcessGroup,
  ProcessStatus,
} from "@/shared/constants/processGroups";

export interface SchedulingBatch extends ProductionBatch {
  processGroup: ProcessGroup;
  processStatus: ProcessStatus;
  convertedQty: number;
  /** wip_inventory.length_m × count — WIP 재고 실수량 (null이면 WIP 미매칭) */
  wip_total_length_m?: number | null;
  /** wip_inventory.core_colors — WIP 재고 선심색상 */
  wip_core_colors?: string | null;
  /** 매칭된 WIP 재고 ID (wip_inventory.wip_id) — 다중 배치→WIP 연결에 사용 */
  wip_matched_id?: number | null;
}

export interface WipItem {
  id: string;
  /** wip_inventory.wip_id */
  wip_id: number;
  processGroup: ProcessGroup;
  /** wip_inventory.process_stage */
  process_stage: string;
  product: string;
  spec: string;
  core: string | null;
  color: string;
  /** wip_inventory.length_m — 1드럼 기준 길이 */
  stock: number;
  /** wip_inventory.count — 드럼 수 */
  count: number;
  /** wip_inventory.total_length_m */
  total_length_m: number;
  convertedQty: number;
  status: string;
  voltage_class: string;
  /** 매칭된 production_batch.batch_id (단일) */
  matchedBatchId?: string;
  /** 이 WIP을 사용하는 모든 production_batch.id 목록 (1:N) */
  matchedBatchIds?: string[];
  /** 매칭된 production_batch.batch_group */
  matchedBatchGroup?: string;
}

export interface AiInsight {
  batchId: string;
  reasoning: string;
  riskLevel: "low" | "medium" | "high";
  riskDescription?: string;
  wipSuggestion?: string;
}

export interface AiSummary {
  totalBatches: number;
  totalGroups: number;
  totalProductionM: number;
  riskCount: number;
  highlights: string[];
  source?: "rule-based" | "llm" | "fallback";
}

/** 외주 분류된 수주 항목 — sales_order에서 is_outsourced=True인 행 */
export interface OutsourcedOrder {
  order_id: string;
  order_line: number;
  product_group: string;
  spec_raw: string;
  sheath_color: string;
  customer_name: string;
  due_date: string;
  total_length_m: number;
  drum_length_m: number;
  drum_count: number;
  voltage: string;
  order_status: string;
  reason: string;
}
