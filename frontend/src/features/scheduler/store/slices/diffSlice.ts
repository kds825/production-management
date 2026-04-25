/**
 * diffSlice — Gantt 버전 diff overlay (compareMode) 상태.
 *
 * 진입: scheduler/page.tsx 의 [비교 모드] 토글이 enableCompareMode(before, after)
 * 를 호출하면 async fetch 로 /runs/compare 응답을 받아 diffResponse 에 저장.
 * SchedulerView 가 buildDiffIndex(diffResponse) 로 색인하여 GanttTaskBlock 의
 * diffOverlay/ghostReason/dimmed prop 을 분기 렌더.
 *
 * Cascade preview 와는 상호배타: enableCompareMode 활성화 시 batches 슬라이스의
 * cascade 상태(cascadePreview/conflictModalOpen/cascadeOriginalTask/previewOffsets)
 * 를 즉시 리셋하여 두 overlay 가 동시에 그려지지 않도록 한다 (스펙 §설계결정).
 *
 * 에러 상태에서도 enabled 는 true 를 유지해 상단 pill 에 빨간 toast 를 띄울 수
 * 있게 하고, 사용자가 × 클릭 시 closeCompareMode 로 닫는다.
 */
import type { StateCreator } from "zustand";
import type { RunCompareResponse } from "../../types/diff";
import { API_BASE } from "./shared";
import type { ScheduleStore } from "../scheduleStore";

interface CompareModeState {
  enabled: boolean;
  beforeRunLabel: string | null;
  afterRunLabel: string | null;
  diffResponse: RunCompareResponse | null;
  loading: boolean;
  error: string | null;
  filters: { added: boolean; moved: boolean; removed: boolean };
}

const initialCompareMode: CompareModeState = {
  enabled: false,
  beforeRunLabel: null,
  afterRunLabel: null,
  diffResponse: null,
  loading: false,
  error: null,
  // removed 는 기본 OFF — 노이즈 최소화 (스펙 §2 필터 초기값)
  filters: { added: true, moved: true, removed: false },
};

export interface DiffSlice {
  /**
   * Gantt 버전 diff overlay 상태 (compareMode).
   * cascade preview 와 시각/상태 모두 상호배타.
   */
  compareMode: CompareModeState;

  /**
   * compareMode 활성화 — /runs/compare 를 fetch 해 diffResponse 저장.
   * cascade 상호배타: 호출 시 batches 슬라이스의 cascade 상태도 리셋한다.
   */
  enableCompareMode: (before: string, after: string) => Promise<void>;
  /**
   * 필터 pill 토글 — added/moved/removed 중 하나의 가시성을 반전.
   */
  toggleCompareFilter: (category: "added" | "moved" | "removed") => void;
  /**
   * compareMode OFF — initialCompareMode 로 리셋.
   * 필터 기본값(added=ON, moved=ON, removed=OFF) 도 함께 복귀.
   */
  closeCompareMode: () => void;
}

export const createDiffSlice: StateCreator<
  ScheduleStore,
  [["zustand/immer", never]],
  [],
  DiffSlice
> = (set) => ({
  compareMode: initialCompareMode,

  enableCompareMode: async (before: string, after: string) => {
    set((state) => {
      state.compareMode.enabled = true;
      state.compareMode.loading = true;
      state.compareMode.error = null;
      state.compareMode.beforeRunLabel = before;
      state.compareMode.afterRunLabel = after;
      // 신규 fetch 시작 시 과거 diffResponse 는 비워 leaky render 방지
      state.compareMode.diffResponse = null;
      // cascade 상호배타 리셋 (batches 슬라이스 필드)
      state.cascadePreview = null;
      state.conflictModalOpen = false;
      state.cascadeOriginalTask = null;
      state.previewOffsets = {};
    });

    try {
      const url = `${API_BASE}/pipeline/runs/compare?before=${encodeURIComponent(before)}&after=${encodeURIComponent(after)}`;
      const res = await fetch(url);
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }
      const body: RunCompareResponse = await res.json();
      set((state) => {
        state.compareMode.diffResponse = body;
        state.compareMode.loading = false;
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : "비교 요청 실패";
      console.warn("[enableCompareMode] 실패:", err);
      set((state) => {
        state.compareMode.error = message;
        state.compareMode.loading = false;
        // enabled 는 true 유지 — UI 에서 에러 표시 후 사용자가 닫게 한다
      });
    }
  },

  toggleCompareFilter: (category) => {
    set((state) => {
      state.compareMode.filters[category] =
        !state.compareMode.filters[category];
    });
  },

  closeCompareMode: () => {
    set((state) => {
      state.compareMode = { ...initialCompareMode };
    });
  },
});
