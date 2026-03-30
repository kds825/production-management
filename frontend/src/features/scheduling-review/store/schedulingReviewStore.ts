import { create } from "zustand";
import { immer } from "zustand/middleware/immer";
import type { ProcessGroup } from "@/shared/constants/processGroups";
import type { SchedulingBatch, WipItem, AiInsight, AiSummary } from "../types";
import { calcConvertedQty } from "@/shared/utils/batchGrouping";

const API_BASE = "http://localhost:8000/api";

/** 백엔드 /api/pipeline/stage1/{run_label}/batches 응답 항목 */
interface ApiBatch {
  batch_id: number;
  process_name: string;
  sq_mm2: number;
  core_count: number;
  sheath_color: string;
  total_length_m: number;
  drum_count: number;
  drum_length_m: number;
  customer_name: string;
  due_date: string;
  product_group: string;
  sales_order_id: string;
  wip_matched_id: number | null;
  status: string;
  remarks: string | null;
  spec_raw: string;
  voltage: string | null;
  equipment_code: string | null;
  batch_group: string | null;
}

/** process_name → equipment_group 매핑
 * DB process_name: 연선, 신선, 연합, 고압절연, 저압절연, 고압시스, 저압시스
 * → 3개 그룹으로 분류하여 scheduling-review 섹션에 매핑 */
function toEquipmentGroup(
  processName: string,
): "연선" | "B100" | "A100" | "A120" {
  if (
    processName === "연선" ||
    processName === "신선" ||
    processName === "연합"
  )
    return "연선";
  if (processName.includes("절연")) return "B100";
  if (processName.includes("시스")) return "A120";
  // 장비 그룹 코드 직접 매핑 (레거시 호환)
  if (processName === "B100") return "B100";
  if (processName === "A100") return "A100";
  if (processName === "A120") return "A120";
  return "A120";
}

/** process_name → ProcessGroup 매핑 */
function toProcessGroup(processName: string): ProcessGroup {
  if (
    processName === "연선" ||
    processName === "신선" ||
    processName === "연합"
  )
    return "연선";
  if (processName.includes("절연")) return "절연";
  if (processName.includes("시스")) return "시스";
  return processName as ProcessGroup;
}

/** API 배치 → 프론트엔드 SchedulingBatch 변환 */
function toBatch(b: ApiBatch): SchedulingBatch {
  const equipmentGroup = toEquipmentGroup(b.process_name);
  const voltageType =
    b.voltage === "고압" ? ("고압" as const) : ("저압" as const);
  return {
    id: `batch-${b.batch_id}`,
    product: b.product_group || "",
    spec: b.spec_raw,
    color: b.sheath_color,
    customer: b.customer_name || "",
    delivery_date: b.due_date,
    length_per_unit_m: b.drum_length_m,
    unit_count: b.drum_count,
    total_length_m: b.total_length_m,
    equipment_group: equipmentGroup,
    voltage_type: voltageType,
    notes: b.wip_matched_id ? "재고 사용" : (b.remarks ?? ""),
    classification_reason: "",
    processGroup: toProcessGroup(b.process_name),
    processStatus: "진행",
    convertedQty: calcConvertedQty(b.spec_raw, b.total_length_m),
    batch_group: b.batch_group || undefined,
  };
}

interface SchedulingReviewState {
  // 공정별 배치 데이터
  yeonseoBatches: SchedulingBatch[];
  insulationBatches: SchedulingBatch[];
  sheatBatches: SchedulingBatch[];

  // 전체 배치 (공정 탭별 필터 전)
  allBatches: SchedulingBatch[];

  // WIP 데이터
  yeonaeoWip: WipItem[];
  insulationWip: WipItem[];

  // 로딩/에러 상태
  isLoading: boolean;
  loadError: string | null;

  // 계산 상태
  isCalculating: boolean;
  isCalculated: boolean;

  // AI 분석 결과
  aiInsights: AiInsight[];
  aiSummary: AiSummary | null;

  // Section 4 탭
  activeTab: ProcessGroup;
}

interface SchedulingReviewActions {
  loadFromPlanRegister: () => void;
  /** API에서 배치 로드 (run_label 지정) */
  loadBatchesFromApi: (runLabel: string) => Promise<void>;
  calculateBatches: () => Promise<void>;
  setActiveTab: (tab: ProcessGroup) => void;
  reset: () => void;
}

type SchedulingReviewStore = SchedulingReviewState & SchedulingReviewActions;

const initialState: SchedulingReviewState = {
  yeonseoBatches: [],
  insulationBatches: [],
  sheatBatches: [],
  allBatches: [],
  yeonaeoWip: [],
  insulationWip: [],
  isLoading: false,
  loadError: null,
  isCalculating: false,
  isCalculated: false,
  aiInsights: [],
  aiSummary: null,
  activeTab: "연선",
};

export const useSchedulingReviewStore = create<SchedulingReviewStore>()(
  immer((set, get) => ({
    ...initialState,

    loadFromPlanRegister: () => {
      // 레거시 — API 로드 전 호환용 (no-op)
    },

    loadBatchesFromApi: async (runLabel: string) => {
      set((state) => {
        state.isLoading = true;
        state.loadError = null;
      });

      try {
        const res = await fetch(
          `${API_BASE}/pipeline/stage1/${encodeURIComponent(runLabel)}/batches`,
        );
        if (!res.ok) {
          const detail = await res.text();
          throw new Error(detail || `HTTP ${res.status}`);
        }
        const data: ApiBatch[] = await res.json();
        const batches = data.map(toBatch);

        // 공정별 분류
        const yeonseo = batches.filter((b) => b.equipment_group === "연선");
        const insulation = batches.filter(
          (b) => b.equipment_group === "B100" || b.equipment_group === "A100",
        );
        const sheat = batches.filter((b) => b.equipment_group === "A120");

        set((state) => {
          state.allBatches = batches;
          state.yeonseoBatches = yeonseo;
          state.insulationBatches = insulation;
          state.sheatBatches = sheat;
          state.isLoading = false;

          // WIP 매칭된 항목을 WIP 리스트로 표시
          const wipItems: WipItem[] = batches
            .filter((b) => b.notes === "재고 사용")
            .map((b) => ({
              id: `wip-${b.id}`,
              matchedBatchId: b.id,
              processGroup: b.processGroup,
              product: b.product,
              spec: b.spec,
              color: b.color,
              stock: b.total_length_m,
              convertedQty: b.convertedQty,
            }));

          state.yeonaeoWip = wipItems.filter((w) => w.processGroup === "연선");
          state.insulationWip = wipItems.filter(
            (w) => w.processGroup !== "연선",
          );
        });
      } catch (err) {
        set((state) => {
          state.isLoading = false;
          state.loadError =
            err instanceof Error ? err.message : "배치 로드 실패";
        });
      }
    },

    calculateBatches: async () => {
      set((state) => {
        state.isCalculating = true;
      });

      // AI 계산 시뮬레이션 (2초 딜레이)
      await new Promise((resolve) => setTimeout(resolve, 2000));

      const { allBatches } = get();

      set((state) => {
        state.isCalculating = false;
        state.isCalculated = true;
        state.aiInsights = [];
        state.aiSummary = {
          totalBatches: allBatches.length,
          totalProductionM: allBatches.reduce(
            (sum, b) => sum + b.total_length_m,
            0,
          ),
          riskCount: 0,
          highlights: [
            `총 ${allBatches.length}개 배치, ${allBatches.reduce((s, b) => s + b.total_length_m, 0).toLocaleString()}m 생산`,
          ],
        };
      });
    },

    setActiveTab: (tab) => {
      set((state) => {
        state.activeTab = tab;
      });
    },

    reset: () => {
      set(() => ({
        ...initialState,
        yeonseoBatches: [],
        insulationBatches: [],
        sheatBatches: [],
        allBatches: [],
        yeonaeoWip: [],
        insulationWip: [],
        aiInsights: [],
      }));
    },
  })),
);
