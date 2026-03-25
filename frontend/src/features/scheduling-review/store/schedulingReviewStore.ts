import { create } from "zustand";
import { immer } from "zustand/middleware/immer";
import type { ProcessGroup } from "@/shared/constants/processGroups";
import type { SchedulingBatch, WipItem, AiInsight, AiSummary } from "../types";
import {
  MOCK_YEONEO_BATCHES,
  MOCK_INSULATION_BATCHES,
  MOCK_SHEATH_BATCHES,
  MOCK_YEONEO_WIP,
  MOCK_INSULATION_WIP,
  MOCK_AI_INSIGHTS,
  MOCK_AI_SUMMARY,
} from "../mock/mockSchedulingData";

interface SchedulingReviewState {
  // 공정별 배치 데이터
  yeonseoBatches: SchedulingBatch[];
  insulationBatches: SchedulingBatch[];
  sheatBatches: SchedulingBatch[];

  // WIP 데이터
  yeonaeoWip: WipItem[];
  insulationWip: WipItem[];

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
  calculateBatches: () => Promise<void>;
  setActiveTab: (tab: ProcessGroup) => void;
  reset: () => void;
}

type SchedulingReviewStore = SchedulingReviewState & SchedulingReviewActions;

const initialState: SchedulingReviewState = {
  yeonseoBatches: [],
  insulationBatches: [],
  sheatBatches: [],
  yeonaeoWip: [],
  insulationWip: [],
  isCalculating: false,
  isCalculated: false,
  aiInsights: [],
  aiSummary: null,
  activeTab: "연선",
};

export const useSchedulingReviewStore = create<SchedulingReviewStore>()(
  immer((set) => ({
    ...initialState,

    loadFromPlanRegister: () => {
      // Mock 데이터를 공정별로 로드
      set((state) => {
        state.yeonseoBatches = [...MOCK_YEONEO_BATCHES];
        state.insulationBatches = [...MOCK_INSULATION_BATCHES];
        state.sheatBatches = [...MOCK_SHEATH_BATCHES];
        state.yeonaeoWip = [...MOCK_YEONEO_WIP];
        state.insulationWip = [...MOCK_INSULATION_WIP];
      });
    },

    calculateBatches: async () => {
      set((state) => {
        state.isCalculating = true;
      });

      // AI 계산 시뮬레이션 (2초 딜레이)
      await new Promise((resolve) => setTimeout(resolve, 2000));

      set((state) => {
        state.isCalculating = false;
        state.isCalculated = true;
        state.aiInsights = [...MOCK_AI_INSIGHTS];
        state.aiSummary = { ...MOCK_AI_SUMMARY };
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
        yeonaeoWip: [],
        insulationWip: [],
        aiInsights: [],
      }));
    },
  })),
);
