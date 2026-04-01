import type { ProductionBatch } from "@/features/scheduler/types";
import type {
  ProcessGroup,
  ProcessStatus,
} from "@/shared/constants/processGroups";

export interface SchedulingBatch extends ProductionBatch {
  processGroup: ProcessGroup;
  processStatus: ProcessStatus;
  convertedQty: number;
}

export interface WipItem {
  id: string;
  processGroup: ProcessGroup;
  product: string;
  spec: string;
  color: string;
  stock: number;
  convertedQty: number;
  /** 매칭된 배치 ID — 클릭 시 해당 배치로 스크롤 */
  matchedBatchId?: string;
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
