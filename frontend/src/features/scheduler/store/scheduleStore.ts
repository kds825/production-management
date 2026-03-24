import { create } from "zustand";
import { immer } from "zustand/middleware/immer";
import type {
  Equipment,
  ScheduleTask,
  ConstraintViolation,
  TimelineRow,
  TimelineItem,
  ZoomLevel,
  ViewFilterType,
} from "../types";

interface ViewFilter {
  filterType: ViewFilterType;
  filterValue: string[];
}

interface ScheduleState {
  // 도메인 데이터
  equipment: Equipment[];
  tasks: ScheduleTask[];
  violations: ConstraintViolation[];
  selectedTaskId: string | null;

  // dnd-timeline 데이터
  rows: TimelineRow[];
  items: TimelineItem[];

  // 뷰 상태
  viewFilter: ViewFilter;
  zoomLevel: ZoomLevel;
}

interface ScheduleActions {
  setEquipment: (equipment: Equipment[]) => void;
  setTasks: (tasks: ScheduleTask[]) => void;
  updateTask: (
    taskId: string,
    updates: Partial<ScheduleTask> & { span?: { start: Date; end: Date } },
  ) => void;
  moveTask: (
    taskId: string,
    newEquipmentId: string,
    start: Date,
    end: Date,
  ) => void;
  setViolations: (violations: ConstraintViolation[]) => void;
  selectTask: (taskId: string | null) => void;
  setViewFilter: (filter: Partial<ViewFilter>) => void;
  setZoomLevel: (level: ZoomLevel) => void;
}

type ScheduleStore = ScheduleState & ScheduleActions;

/** task → TimelineItem 변환 (span은 dnd-timeline이 요구하는 타임스탬프 number) */
function taskToItem(task: ScheduleTask): TimelineItem {
  const start = task.start instanceof Date ? task.start : new Date(task.start);
  const end = task.end instanceof Date ? task.end : new Date(task.end);
  return {
    id: task.id,
    rowId: task.equipment_id,
    span: {
      start: start.getTime(),
      end: end.getTime(),
    },
    data: task,
  };
}

export const useScheduleStore = create<ScheduleStore>()(
  immer((set) => ({
    // 초기 상태
    equipment: [],
    tasks: [],
    violations: [],
    selectedTaskId: null,
    rows: [],
    items: [],
    viewFilter: { filterType: "all", filterValue: [] },
    zoomLevel: "week",

    // 설비 목록 설정 → rows 동기화
    setEquipment: (equipment) => {
      set((state) => {
        state.equipment = equipment;
        state.rows = equipment.map((eq) => ({ id: eq.id }));
      });
    },

    // 작업 목록 설정 → items 동기화
    setTasks: (tasks) => {
      set((state) => {
        state.tasks = tasks;
        state.items = tasks.map(taskToItem);
      });
    },

    // 개별 작업 업데이트 (리사이즈 등)
    updateTask: (taskId, updates) => {
      set((state) => {
        const taskIdx = state.tasks.findIndex((t) => t.id === taskId);
        if (taskIdx === -1) return;

        const task = state.tasks[taskIdx];

        // span 업데이트가 포함된 경우 start/end에 반영
        if ("span" in updates && updates.span) {
          task.start = updates.span.start;
          task.end = updates.span.end;
          // eslint-disable-next-line @typescript-eslint/no-unused-vars
          const { span: _, ...rest } = updates;
          Object.assign(task, rest);
        } else {
          Object.assign(task, updates);
        }

        // items 동기화
        const itemIdx = state.items.findIndex((i) => i.id === taskId);
        if (itemIdx !== -1) {
          state.items[itemIdx] = taskToItem(task);
        }
      });
    },

    // 작업 이동 (드래그 앤 드롭)
    moveTask: (taskId, newEquipmentId, start, end) => {
      set((state) => {
        const taskIdx = state.tasks.findIndex((t) => t.id === taskId);
        if (taskIdx === -1) return;

        const task = state.tasks[taskIdx];
        task.equipment_id = newEquipmentId;
        task.start = start;
        task.end = end;

        const itemIdx = state.items.findIndex((i) => i.id === taskId);
        if (itemIdx !== -1) {
          state.items[itemIdx] = taskToItem(task);
        }
      });
    },

    setViolations: (violations) => {
      set((state) => {
        state.violations = violations;
      });
    },

    selectTask: (taskId) => {
      set((state) => {
        state.selectedTaskId = taskId;
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
  })),
);
