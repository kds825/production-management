import { create } from "zustand";
import { immer } from "zustand/middleware/immer";
import type { ProductionBatch } from "@/features/scheduler/types";
import type { UploadedFile, EquipmentGroup, VoltageType } from "../types";
import { ALL_MOCK_BATCHES } from "../mock/mockBatchData";

interface PlanRegisterState {
  uploadedFile: UploadedFile | null;
  isAnalyzing: boolean;
  isAnalyzed: boolean;
  batches: ProductionBatch[];
  confirmedBatches: ProductionBatch[];
  voltageFilter: VoltageType;
  equipmentFilter: EquipmentGroup | null;
}

interface PlanRegisterActions {
  setUploadedFile: (file: UploadedFile | null) => void;
  setIsAnalyzing: (v: boolean) => void;
  setIsAnalyzed: (v: boolean) => void;
  setBatches: (batches: ProductionBatch[]) => void;
  updateBatch: (id: string, updates: Partial<ProductionBatch>) => void;
  confirmBatches: () => void;
  reset: () => void;
  setVoltageFilter: (v: VoltageType) => void;
  setEquipmentFilter: (v: EquipmentGroup | null) => void;
}

type PlanRegisterStore = PlanRegisterState & PlanRegisterActions;

const initialState: PlanRegisterState = {
  uploadedFile: null,
  isAnalyzing: false,
  isAnalyzed: true,
  batches: ALL_MOCK_BATCHES,
  confirmedBatches: [],
  voltageFilter: "저압",
  equipmentFilter: "연선",
};

export const usePlanRegisterStore = create<PlanRegisterStore>()(
  immer((set, get) => ({
    ...initialState,

    setUploadedFile: (file) => {
      set((state) => {
        state.uploadedFile = file;
        // 새 파일이 올라오면 기존 분석 결과 초기화
        if (file === null) {
          state.isAnalyzed = false;
          state.batches = [];
        }
      });
    },

    setIsAnalyzing: (v) => {
      set((state) => {
        state.isAnalyzing = v;
      });
    },

    setIsAnalyzed: (v) => {
      set((state) => {
        state.isAnalyzed = v;
      });
    },

    setBatches: (batches) => {
      set((state) => {
        state.batches = batches;
      });
    },

    updateBatch: (id, updates) => {
      set((state) => {
        const idx = state.batches.findIndex((b) => b.id === id);
        if (idx === -1) return;
        Object.assign(state.batches[idx], updates);
      });
    },

    confirmBatches: () => {
      const { batches } = get();
      set((state) => {
        state.confirmedBatches = [...batches];
      });
    },

    reset: () => {
      set(() => ({
        ...initialState,
        batches: [...ALL_MOCK_BATCHES],
        confirmedBatches: [],
      }));
    },

    setVoltageFilter: (v) => {
      set((state) => {
        state.voltageFilter = v;
        // 저압 선택 시 기본 장비 필터도 설정
        if (v === "저압" && state.equipmentFilter === null) {
          state.equipmentFilter = "연선";
        }
      });
    },

    setEquipmentFilter: (v) => {
      set((state) => {
        state.equipmentFilter = v;
      });
    },
  })),
);
