/**
 * filtersSlice — 간트 뷰 필터/줌/시간 범위/픽셀 밀도 + 제약 위반 표시.
 *
 * 책임 (Single Responsibility):
 *   - viewFilter: 사용자 필터(공정/설비 등) — ViewFilterType 으로 분기
 *   - zoomLevel + range + dayWidthScale: 시간축 표시 범위/밀도
 *   - violations: 제약 위반 리스트 (UI 알림 배너에서 소비)
 *
 * 별도 슬라이스로 분리한 이유: 데이터 슬라이스(orders/batches)와 라이프사이클이 다르며,
 * 다른 슬라이스 액션과의 간섭이 거의 없어 독립적으로 변할 수 있다.
 */
import type { StateCreator } from "zustand";
import type {
  ConstraintViolation,
  ZoomLevel,
  ViewFilterType,
} from "../../types";
import { getDefaultRange } from "../../utils/ganttUtils";
import type { ScheduleStore } from "../scheduleStore";

interface ViewFilter {
  filterType: ViewFilterType;
  filterValue: string[];
}

export interface FiltersSlice {
  // ── State ───────────────────────────────────────────────
  violations: ConstraintViolation[];
  viewFilter: ViewFilter;
  zoomLevel: ZoomLevel;
  range: { start: number; end: number };
  /** +/- 버튼으로 조정하는 픽셀 밀도 배율 (1.0 = 기본값) */
  dayWidthScale: number;

  // ── Actions ─────────────────────────────────────────────
  setViolations: (violations: ConstraintViolation[]) => void;
  setViewFilter: (filter: Partial<ViewFilter>) => void;
  setZoomLevel: (level: ZoomLevel) => void;
  setRange: (range: { start: number; end: number }) => void;
  setDayWidthScale: (scale: number) => void;
}

export const createFiltersSlice: StateCreator<
  ScheduleStore,
  [["zustand/immer", never]],
  [],
  FiltersSlice
> = (set) => ({
  violations: [],
  viewFilter: { filterType: "all", filterValue: [] },
  zoomLevel: "day",
  range: getDefaultRange(7), // day 줌: ±7일 = 2주 뷰
  dayWidthScale: 1.0,

  setViolations: (violations) => {
    set((state) => {
      state.violations = violations;
    });
  },

  setViewFilter: (filter) => {
    set((state) => {
      Object.assign(state.viewFilter, filter);
    });
  },

  setZoomLevel: (level) => {
    set((state) => {
      state.zoomLevel = level;
    });
  },

  setRange: (range) => {
    set((state) => {
      state.range = range;
    });
  },

  setDayWidthScale: (scale) => {
    set((state) => {
      state.dayWidthScale = scale;
    });
  },
});
