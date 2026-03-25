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
