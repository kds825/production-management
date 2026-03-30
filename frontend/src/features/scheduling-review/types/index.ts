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
  totalProductionM: number;
  riskCount: number;
  highlights: string[];
}
