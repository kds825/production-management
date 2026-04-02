import { create } from "zustand";
import { immer } from "zustand/middleware/immer";
import type { ProcessGroup } from "@/shared/constants/processGroups";
import type {
  SchedulingBatch,
  WipItem,
  AiInsight,
  AiSummary,
  OutsourcedOrder,
} from "../types";
import { calcConvertedQty } from "@/shared/utils/batchGrouping";

const API_BASE =
  typeof window !== "undefined" && process.env.NEXT_PUBLIC_API_URL
    ? `${process.env.NEXT_PUBLIC_API_URL}`
    : "http://localhost:8000/api";

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
  order_status: string | null;
  remarks: string | null;
  spec_raw: string;
  voltage: string | null;
  equipment_code: string | null;
  batch_group: string | null;
  core_colors: string | null;
}

/** process_name + batch_group → equipment_group 매핑
 * 저압시스는 batch_group 접두사(A100/A120)로 설비 구분 */
function toEquipmentGroup(
  processName: string,
  batchGroup?: string | null,
): "연선" | "B100" | "A100" | "A120" {
  if (
    processName === "연선" ||
    processName === "신선" ||
    processName === "연합"
  )
    return "연선";
  if (processName.includes("절연")) return "B100";
  if (processName.includes("시스")) {
    // batch_group 접두사로 A100/A120 구분
    if (batchGroup?.startsWith("A100")) return "A100";
    return "A120";
  }
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
  const equipmentGroup = toEquipmentGroup(b.process_name, b.batch_group);
  const isHighVoltage =
    b.voltage != null && !b.voltage.startsWith("0.6") && b.voltage !== "";
  const voltageType = isHighVoltage ? ("고압" as const) : ("저압" as const);
  return {
    id: `batch-${b.batch_id}`,
    product: b.product_group || "",
    spec: b.spec_raw,
    color: b.sheath_color,
    core_colors: b.core_colors || "",
    customer: b.customer_name || "",
    delivery_date: b.due_date,
    length_per_unit_m: b.drum_length_m,
    unit_count: b.drum_count,
    total_length_m: b.drum_length_m * b.drum_count,
    equipment_group: equipmentGroup,
    voltage_type: voltageType,
    notes: b.wip_matched_id ? "재고 사용" : (b.remarks ?? ""),
    classification_reason: (() => {
      const reasons: string[] = [];
      const sq = Math.round(b.sq_mm2);
      reasons.push(`${sq}SQ 동일규격 묶음`);
      if (b.wip_matched_id) reasons.push("재공재고 사용");
      if (b.process_name.includes("시스")) {
        const isA120 = ["흑", "청", "흑/적"].includes(b.sheath_color);
        reasons.push(isA120 ? "흑/청 → A120 배정" : "갈/회 → A100 배정");
      }
      if (isHighVoltage) reasons.push("고압 URD");
      return reasons.join(" | ");
    })(),
    processGroup: toProcessGroup(b.process_name),
    processStatus:
      b.order_status === "진행" ? ("진행" as const) : ("대기" as const),
    convertedQty: calcConvertedQty(b.spec_raw, b.total_length_m),
    batch_group: b.batch_group || undefined,
  };
}

/** 배치 목록에서 로컬 AiSummary를 생성하는 헬퍼 — DRY */
function buildLocalAiSummary(
  batches: SchedulingBatch[],
  overrides?: Partial<AiSummary>,
): AiSummary {
  return {
    totalBatches: batches.length,
    totalGroups: new Set(batches.map((b) => b.batch_group).filter(Boolean))
      .size,
    totalProductionM: batches.reduce((s, b) => s + b.total_length_m, 0),
    riskCount: 0,
    highlights: [],
    source: "fallback",
    ...overrides,
  };
}

/** AI 분석 비동기 상태 — Stage 2 실행 후 백그라운드로 진행됨 */
type AiAnalysisStatus = "idle" | "pending" | "done" | "error";

interface SchedulingReviewState {
  // 공정별 배치 데이터
  yeonseoBatches: SchedulingBatch[];
  insulationBatches: SchedulingBatch[];
  sheatBatches: SchedulingBatch[];

  // 전체 배치 (공정 탭별 필터 전)
  allBatches: SchedulingBatch[];

  // 외주 분류 수주
  outsourcedOrders: OutsourcedOrder[];

  // WIP 데이터
  yeonaeoWip: WipItem[];
  insulationWip: WipItem[];

  // 로딩/에러 상태
  isLoading: boolean;
  loadError: string | null;

  // 데이터 로드 여부 (API에서 배치를 가져왔는지)
  isLoaded: boolean;

  // 계산 상태 — AI 분석 자동 폴링으로 관리 (레거시 호환 유지)
  isCalculating: boolean;
  isCalculated: boolean;
  calcError: string | null;

  // AI 분석 비동기 상태 — Stage 2에서 백그라운드로 시작됨
  aiAnalysisStatus: AiAnalysisStatus;

  // AI 분석 결과
  aiInsights: AiInsight[];
  aiSummary: AiSummary | null;

  // Section 4 탭
  activeTab: ProcessGroup;

  // 현재 로드된 run_label (ai-summary API 호출에 사용)
  runLabel: string | null;
}

interface SchedulingReviewActions {
  loadFromPlanRegister: () => void;
  /** API에서 배치 로드 (run_label 지정) — 로드 후 자동으로 AI 상태 폴링 시작 */
  loadBatchesFromApi: (runLabel: string) => Promise<void>;
  /** 외주 분류 수주 목록 로드 */
  loadOutsourcedOrders: (runLabel: string) => Promise<void>;
  /** 배치 인라인 편집 — PATCH /api/pipeline/batch/{batch_id} */
  updateBatch: (
    batchId: number,
    field: string,
    value: string | number,
  ) => Promise<boolean>;
  /** AI 분석 상태를 폴링하여 완료 시 결과를 반영한다 */
  pollAiStatus: () => Promise<void>;
  /** AI 재분석 트리거 — 블록 변경 후 수동 호출 또는 scheduleStore에서 호출 */
  triggerReanalysis: () => Promise<void>;
  /** 레거시: 수동 AI 분석 실행 (재분석 트리거 래퍼) */
  calculateBatches: () => Promise<void>;
  /** 내부: 비동기 엔드포인트 미지원 시 기존 동기 AI summary API로 폴백 */
  _fetchAiSummaryFallback: () => Promise<void>;
  setActiveTab: (tab: ProcessGroup) => void;
  reset: () => void;
}

type SchedulingReviewStore = SchedulingReviewState & SchedulingReviewActions;

const initialState: SchedulingReviewState = {
  yeonseoBatches: [],
  insulationBatches: [],
  sheatBatches: [],
  allBatches: [],
  outsourcedOrders: [],
  yeonaeoWip: [],
  insulationWip: [],
  isLoading: false,
  loadError: null,
  isLoaded: false,
  isCalculating: false,
  isCalculated: false,
  calcError: null,
  aiAnalysisStatus: "idle",
  aiInsights: [],
  aiSummary: null,
  activeTab: "연선",
  runLabel: null,
};

/** 폴링 간격 (ms) — AI 분석 완료를 기다리는 주기 */
const AI_POLL_INTERVAL_MS = 2000;
/** 폴링 최대 시도 횟수 — 무한 루프 방지 */
const AI_POLL_MAX_ATTEMPTS = 60;

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
        const insulation = batches.filter((b) => b.equipment_group === "B100");
        const sheat = batches.filter(
          (b) => b.equipment_group === "A100" || b.equipment_group === "A120",
        );

        set((state) => {
          state.allBatches = batches;
          state.yeonseoBatches = yeonseo;
          state.insulationBatches = insulation;
          state.sheatBatches = sheat;
          state.isLoading = false;
          state.isLoaded = true;
          state.runLabel = runLabel;
          state.isCalculated = false;
          state.calcError = null;
          state.aiSummary = null;
          state.aiAnalysisStatus = "idle";

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

        // 외주 분류 수주 병렬 로드
        get().loadOutsourcedOrders(runLabel);

        // AI 분석은 사용자가 명시적으로 버튼을 클릭할 때만 실행
        // (자동 폴링 제거 — LLM 비용 절감)
      } catch (err) {
        set((state) => {
          state.isLoading = false;
          state.loadError =
            err instanceof Error ? err.message : "배치 로드 실패";
        });
      }
    },

    loadOutsourcedOrders: async (runLabel: string) => {
      try {
        const res = await fetch(
          `${API_BASE}/pipeline/stage1/${encodeURIComponent(runLabel)}/outsourced`,
        );
        if (res.ok) {
          const data: OutsourcedOrder[] = await res.json();
          set((state) => {
            state.outsourcedOrders = data;
          });
        }
      } catch {
        // 외주 목록 로드 실패는 핵심 기능이 아니므로 무시
      }
    },

    updateBatch: async (
      batchId: number,
      field: string,
      value: string | number,
    ) => {
      // API 필드명 매핑 — 프론트 col.key → 백엔드 필드
      const fieldMap: Record<string, string> = {
        color: "sheath_color",
        unit_count: "drum_count",
        length_per_unit_m: "drum_length_m",
        notes: "remarks",
      };
      const apiField = fieldMap[field] ?? field;

      try {
        const res = await fetch(`${API_BASE}/pipeline/batch/${batchId}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ [apiField]: value }),
        });
        if (!res.ok) return false;

        // 인라인 편집 후 계산 결과 무효화 — 재계산 필요
        set((state) => {
          state.isCalculated = false;
          state.calcError = null;
        });

        // 로컬 상태 업데이트 — 모든 배치 배열을 순회하여 해당 배치 갱신
        const frontField = field as keyof SchedulingBatch;
        set((state) => {
          const updateInList = (list: SchedulingBatch[]) => {
            const idx = list.findIndex((b) => b.id === `batch-${batchId}`);
            if (idx === -1) return;
            // 편집 가능 필드 직접 반영
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            (list[idx] as any)[frontField] = value;
            // drum_count 또는 drum_length_m 변경 시 total_length_m 재계산
            if (field === "unit_count" || field === "length_per_unit_m") {
              list[idx].total_length_m =
                (list[idx].length_per_unit_m || 0) *
                (list[idx].unit_count || 1);
            }
          };
          updateInList(state.allBatches);
          updateInList(state.yeonseoBatches);
          updateInList(state.insulationBatches);
          updateInList(state.sheatBatches);
        });
        return true;
      } catch {
        return false;
      }
    },

    pollAiStatus: async () => {
      const { runLabel, allBatches } = get();

      // runLabel이 없으면 로컬 fallback으로 즉시 완료 처리
      if (!runLabel) {
        const fallbackHighlight = `총 ${allBatches.length}개 배치, ${allBatches.reduce((s, b) => s + b.total_length_m, 0).toLocaleString()}m 생산`;
        set((state) => {
          state.isCalculating = false;
          state.isCalculated = true;
          state.aiAnalysisStatus = "done";
          state.aiInsights = [];
          state.aiSummary = buildLocalAiSummary(allBatches, {
            highlights: [fallbackHighlight],
            source: "fallback",
          });
        });
        return;
      }

      set((state) => {
        state.isCalculating = true;
        state.calcError = null;
        state.aiAnalysisStatus = "pending";
      });

      // 폴링 루프 — AI 분석이 완료되거나 에러가 발생할 때까지 반복
      for (let attempt = 0; attempt < AI_POLL_MAX_ATTEMPTS; attempt++) {
        try {
          const res = await fetch(
            `${API_BASE}/pipeline/stage2/${encodeURIComponent(runLabel)}/ai-status`,
          );
          if (!res.ok) {
            // 엔드포인트 자체가 없는 경우 — 기존 동기 API로 폴백
            await get()._fetchAiSummaryFallback();
            return;
          }

          const data = await res.json();

          if (data.status === "done") {
            const summary = data.summary;
            set((state) => {
              state.isCalculating = false;
              state.isCalculated = true;
              state.aiAnalysisStatus = "done";
              state.aiInsights = summary?.insights || [];
              state.aiSummary = {
                totalBatches: summary?.totalBatches ?? 0,
                totalGroups: summary?.totalGroups ?? 0,
                totalProductionM: summary?.totalProductionM ?? 0,
                riskCount: summary?.riskCount ?? 0,
                highlights: summary?.highlights ?? [],
                source: summary?.source || "llm",
              };
            });
            return;
          }

          if (data.status === "error") {
            set((state) => {
              state.isCalculating = false;
              state.aiAnalysisStatus = "error";
              state.calcError = data.error || "AI 분석 중 오류 발생";
            });
            return;
          }

          // status === "pending" — 대기 후 재시도
          await new Promise((resolve) =>
            setTimeout(resolve, AI_POLL_INTERVAL_MS),
          );
        } catch {
          // 네트워크 오류 — 기존 동기 API로 폴백
          await get()._fetchAiSummaryFallback();
          return;
        }
      }

      // 최대 시도 횟수 초과 — 타임아웃
      set((state) => {
        state.isCalculating = false;
        state.aiAnalysisStatus = "error";
        state.calcError = "AI 분석 시간 초과";
      });
    },

    triggerReanalysis: async () => {
      const { runLabel } = get();
      if (!runLabel) return;

      set((state) => {
        state.isCalculating = true;
        state.isCalculated = false;
        state.calcError = null;
        state.aiAnalysisStatus = "pending";
        state.aiSummary = null;
      });

      try {
        await fetch(
          `${API_BASE}/pipeline/stage2/${encodeURIComponent(runLabel)}/trigger-reanalysis`,
          { method: "POST" },
        );
      } catch {
        // 트리거 실패해도 폴링은 시도 — 이미 백그라운드에서 실행 중일 수 있음
      }

      // 폴링으로 결과 대기
      await get().pollAiStatus();
    },

    /** 레거시: 수동 AI 분석 — 재분석 트리거로 위임 */
    calculateBatches: async () => {
      const { aiAnalysisStatus } = get();
      // 이미 분석 중이면 중복 실행 방지
      if (aiAnalysisStatus === "pending") return;
      await get().triggerReanalysis();
    },

    /** 기존 동기 AI summary API로 직접 요약을 가져온다 (비동기 엔드포인트 미지원 시 폴백) */
    _fetchAiSummaryFallback: async () => {
      const { runLabel, allBatches } = get();
      if (!runLabel) return;

      try {
        const res = await fetch(
          `${API_BASE}/pipeline/stage1/${runLabel}/ai-summary`,
        );
        if (res.ok) {
          const data = await res.json();
          set((state) => {
            state.isCalculating = false;
            state.isCalculated = true;
            state.aiAnalysisStatus = "done";
            state.aiInsights = data.insights || [];
            state.aiSummary = {
              totalBatches: data.totalBatches,
              totalGroups: data.totalGroups || 0,
              totalProductionM: data.totalProductionM,
              riskCount: data.riskCount,
              highlights: data.highlights,
              source: data.source || "llm",
            };
          });
        } else {
          // 동기 API도 실패 — 로컬 fallback
          set((state) => {
            state.isCalculating = false;
            state.isCalculated = true;
            state.aiAnalysisStatus = "done";
            state.aiInsights = [];
            state.aiSummary = buildLocalAiSummary(allBatches, {
              highlights: [
                `총 ${allBatches.length}개 배치, ${allBatches.reduce((s, b) => s + b.total_length_m, 0).toLocaleString()}m 생산`,
              ],
              source: "fallback",
            });
          });
        }
      } catch {
        // 네트워크 완전 실패 — 로컬 fallback
        set((state) => {
          state.isCalculating = false;
          state.isCalculated = true;
          state.aiAnalysisStatus = "done";
          state.aiInsights = [];
          state.aiSummary = buildLocalAiSummary(allBatches, {
            highlights: [`총 ${allBatches.length}개 배치 (AI 분석 연결 실패)`],
            source: "fallback",
          });
        });
      }
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
        outsourcedOrders: [],
        yeonaeoWip: [],
        insulationWip: [],
        aiInsights: [],
      }));
    },
  })),
);
