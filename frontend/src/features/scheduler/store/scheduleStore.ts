import { create } from "zustand";
import { immer } from "zustand/middleware/immer";
import type {
  Equipment,
  Order,
  ScheduleTask,
  ConstraintViolation,
  ZoomLevel,
  ViewFilterType,
  ContextMenuState,
  TaskFormModalState,
  ScheduleVersion,
  LineSpeedEntry,
  ProductionBatch,
} from "../types";
import {
  getLineSpeed,
  calculateTaskEnd,
  getDefaultRange,
} from "../utils/ganttUtils";

/**
 * Cascade push: 같은 설비에서 작업이 겹치면 뒤에 있는 작업을 연쇄적으로 밀어냄.
 * 기계는 동시에 하나의 작업만 처리할 수 있으므로, 겹침(overlap)이 발생하면
 * 뒤쪽 작업을 앞 작업의 end 시점으로 밀어낸다.
 *
 * @param tasks - 전체 작업 목록 (immer draft)
 * @param movedTaskId - 방금 이동/추가된 작업 ID
 * @param equipmentId - 해당 설비 ID
 */
function cascadePush(
  tasks: ScheduleTask[],
  movedTaskId: string,
  equipmentId: string,
): void {
  // 같은 설비의 작업만 필터 + 시작시간 순 정렬
  const sameMachineTasks = tasks
    .filter((t) => t.equipment_id === equipmentId)
    .sort((a, b) => {
      const aStart =
        a.start instanceof Date
          ? a.start.getTime()
          : new Date(a.start).getTime();
      const bStart =
        b.start instanceof Date
          ? b.start.getTime()
          : new Date(b.start).getTime();
      return aStart - bStart;
    });

  if (sameMachineTasks.length < 2) return;

  // 정렬된 순서대로 순회하며 겹침 해소
  for (let i = 0; i < sameMachineTasks.length - 1; i++) {
    const current = sameMachineTasks[i];
    const next = sameMachineTasks[i + 1];

    const currentEnd =
      current.end instanceof Date
        ? current.end.getTime()
        : new Date(current.end).getTime();
    const nextStart =
      next.start instanceof Date
        ? next.start.getTime()
        : new Date(next.start).getTime();
    const nextEnd =
      next.end instanceof Date
        ? next.end.getTime()
        : new Date(next.end).getTime();

    // 겹침 발생: current.end > next.start
    if (currentEnd > nextStart) {
      const duration = nextEnd - nextStart;
      // next를 current.end로 밀어냄 (duration 유지)
      const realNext = tasks.find((t) => t.id === next.id);
      if (realNext) {
        realNext.start = new Date(currentEnd);
        realNext.end = new Date(currentEnd + duration);
      }
    }
  }
}

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
  unscheduledOrders: Order[];

  // 라인 속도 데이터 (API에서 로드)
  lineSpeedData: LineSpeedEntry[];

  // 뷰 상태
  viewFilter: ViewFilter;
  zoomLevel: ZoomLevel;
  range: { start: number; end: number };

  // 편집 모드 — 기본은 읽기 전용(false)
  isEditMode: boolean;

  // 버전 히스토리
  savedVersions: ScheduleVersion[];

  // 저장 완료 토스트 표시 여부
  showSavedToast: boolean;

  // UI 상태
  contextMenu: ContextMenuState | null;
  taskFormModal: TaskFormModalState;
}

interface ScheduleActions {
  setEquipment: (equipment: Equipment[]) => void;
  setTasks: (tasks: ScheduleTask[]) => void;
  addTask: (task: ScheduleTask) => void;
  deleteTask: (taskId: string) => void;
  updateTask: (taskId: string, updates: Partial<ScheduleTask>) => void;
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
  setRange: (range: { start: number; end: number }) => void;

  /**
   * 생산계획등록에서 확정된 배치를 간트 차트에 자동 배치한다.
   * 각 배치를 equipment_group에 맞는 설비에 순차적으로 배치한다.
   * 배치 실패 항목은 unscheduledOrders에 추가한다.
   */
  syncFromPlanRegister: (batches: ProductionBatch[]) => void;
  setUnscheduledOrders: (orders: Order[]) => void;
  setLineSpeedData: (data: LineSpeedEntry[]) => void;

  /**
   * 수주를 스케줄러에 배정한다.
   * - 라인 속도를 조회하여 종료 시각을 자동 계산한다.
   * - 해당 수주를 unscheduledOrders에서 제거하고 tasks에 추가한다.
   */
  assignOrder: (orderId: string, equipmentId: string, startTime: Date) => void;

  // 편집 모드 토글
  toggleEditMode: () => void;

  // 현재 스케줄을 버전으로 저장
  saveVersion: (label?: string) => Promise<void>;

  // 저장 완료 토스트 숨기기
  hideSavedToast: () => void;

  // 컨텍스트 메뉴
  openContextMenu: (state: ContextMenuState) => void;
  closeContextMenu: () => void;

  // 작업 폼 모달
  openTaskFormModal: (state: Omit<TaskFormModalState, "isOpen">) => void;
  closeTaskFormModal: () => void;
}

type ScheduleStore = ScheduleState & ScheduleActions;

export const useScheduleStore = create<ScheduleStore>()(
  immer((set, get) => ({
    // 초기 상태
    equipment: [],
    tasks: [],
    violations: [],
    selectedTaskId: null,
    unscheduledOrders: [],
    lineSpeedData: [],
    viewFilter: { filterType: "all", filterValue: [] },
    zoomLevel: "week",
    range: getDefaultRange(),
    isEditMode: false,
    savedVersions: [],
    showSavedToast: false,
    contextMenu: null,
    taskFormModal: { isOpen: false, mode: "create" },

    // 설비 목록 설정
    setEquipment: (equipment) => {
      set((state) => {
        state.equipment = equipment;
      });
    },

    // 작업 목록 설정
    setTasks: (tasks) => {
      set((state) => {
        state.tasks = tasks;
      });
    },

    // 단일 작업 추가
    addTask: (task) => {
      set((state) => {
        state.tasks.push(task);
      });
    },

    // 작업 삭제
    deleteTask: (taskId) => {
      set((state) => {
        state.tasks = state.tasks.filter((t) => t.id !== taskId);
      });
    },

    // 개별 작업 업데이트
    updateTask: (taskId, updates) => {
      set((state) => {
        const taskIdx = state.tasks.findIndex((t) => t.id === taskId);
        if (taskIdx === -1) return;
        Object.assign(state.tasks[taskIdx], updates);
      });
    },

    // 작업 이동 (드래그 앤 드롭) — 겹침 방지 + cascade push
    moveTask: (taskId, newEquipmentId, start, end) => {
      set((state) => {
        const taskIdx = state.tasks.findIndex((t) => t.id === taskId);
        if (taskIdx === -1) return;

        const task = state.tasks[taskIdx];
        task.equipment_id = newEquipmentId;
        task.start = start;
        task.end = end;

        // cascade push: 같은 설비의 다른 작업과 겹치면 뒤로 밀기
        cascadePush(state.tasks, taskId, newEquipmentId);
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

    setRange: (range) => {
      set((state) => {
        state.range = range;
      });
    },

    setUnscheduledOrders: (orders) => {
      set((state) => {
        state.unscheduledOrders = orders;
      });
    },

    setLineSpeedData: (data) => {
      set((state) => {
        state.lineSpeedData = data;
      });
    },

    // 수주 → 스케줄 작업 배정
    assignOrder: (orderId, equipmentId, startTime) => {
      const { unscheduledOrders, lineSpeedData } = get();
      const order = unscheduledOrders.find((o) => o.id === orderId);
      if (!order) return;

      // 라인 속도 조회
      const lineSpeed = getLineSpeed(
        lineSpeedData,
        order.spec,
        order.product,
        order.core_count,
      );

      // 종료 시각 계산
      const endTime = calculateTaskEnd(
        startTime,
        order.total_length_m,
        lineSpeed,
      );

      const newTask: ScheduleTask = {
        id: `TASK-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
        order_id: order.id,
        equipment_id: equipmentId,
        product: order.product,
        spec: order.spec,
        core_count: order.core_count,
        color: order.color,
        start: startTime,
        end: endTime,
        volume_m: order.total_length_m,
        line_speed_m_per_min: lineSpeed,
        priority: order.priority,
        status: "planned",
        delivery_date: order.delivery_date
          ? new Date(order.delivery_date)
          : undefined,
        predecessors: [],
        notes: "",
        changeover_min: 0,
      };

      set((state) => {
        state.tasks.push(newTask);
        state.unscheduledOrders = state.unscheduledOrders.filter(
          (o) => o.id !== orderId,
        );

        // cascade push: 새 작업이 기존 작업과 겹치면 뒤로 밀기
        cascadePush(state.tasks, newTask.id, equipmentId);
      });
    },

    // 편집 모드 토글 — 읽기 전용 ↔ 수정 모드
    toggleEditMode: () => {
      set((state) => {
        state.isEditMode = !state.isEditMode;
      });
    },

    // 현재 스케줄을 버전으로 저장하고 읽기 전용 모드로 전환
    saveVersion: async (label = "") => {
      const { tasks } = get();
      const now = new Date();
      const versionLabel =
        label ||
        `저장 ${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")} ${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;

      const newVersion: ScheduleVersion = {
        id: `v-${Date.now()}`,
        label: versionLabel,
        created_at: now,
        // 깊은 복사하여 현재 상태 스냅샷 보존
        tasks: JSON.parse(JSON.stringify(tasks)),
      };

      // 백엔드에 버전 저장 시도 (실패해도 로컬 상태는 저장)
      try {
        await fetch("/api/schedules/versions", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            label: versionLabel,
            tasks,
            created_at: now.toISOString(),
          }),
        });
      } catch {
        // PoC 단계에서는 백엔드 연결 실패를 무시하고 로컬 저장만 진행
        console.warn("[saveVersion] 백엔드 연결 실패 — 로컬 버전만 저장됨");
      }

      set((state) => {
        state.savedVersions.push(newVersion);
        // 저장 완료 후 읽기 전용 모드로 전환
        state.isEditMode = false;
        state.showSavedToast = true;
      });

      // 3초 후 토스트 자동 숨김
      setTimeout(() => {
        get().hideSavedToast();
      }, 3000);
    },

    hideSavedToast: () => {
      set((state) => {
        state.showSavedToast = false;
      });
    },

    openContextMenu: (menuState) => {
      set((state) => {
        state.contextMenu = menuState;
      });
    },

    closeContextMenu: () => {
      set((state) => {
        state.contextMenu = null;
      });
    },

    openTaskFormModal: (modalState) => {
      set((state) => {
        state.taskFormModal = { ...modalState, isOpen: true };
      });
    },

    closeTaskFormModal: () => {
      set((state) => {
        state.taskFormModal = { isOpen: false, mode: "create" };
      });
    },

    // 생산계획등록 배치를 간트 차트에 자동 배치
    syncFromPlanRegister: (batches) => {
      const { equipment, lineSpeedData, tasks: existingTasks } = get();

      // 설비 그룹 → 설비 ID 매핑 (이름에 포함된 키워드로 매핑)
      const equipmentGroupMap: Record<string, string[]> = {};
      for (const eq of equipment) {
        const name = eq.name.toLowerCase();
        for (const group of ["연선", "b100", "a100", "a120"]) {
          if (name.includes(group.toLowerCase())) {
            if (!equipmentGroupMap[group]) equipmentGroupMap[group] = [];
            equipmentGroupMap[group].push(eq.id);
          }
        }
      }

      // 설비별 마지막 종료 시각 추적 (기존 작업 기준)
      const equipmentEndTimes: Record<string, number> = {};
      for (const task of existingTasks) {
        const endTs =
          task.end instanceof Date
            ? task.end.getTime()
            : new Date(task.end).getTime();
        if (
          !equipmentEndTimes[task.equipment_id] ||
          endTs > equipmentEndTimes[task.equipment_id]
        ) {
          equipmentEndTimes[task.equipment_id] = endTs;
        }
      }

      const newTasks: ScheduleTask[] = [];
      const failedOrders: Order[] = [];
      const now = Date.now();

      for (const batch of batches) {
        const groupKey = batch.equipment_group;
        const candidateEquipIds = equipmentGroupMap[groupKey];

        if (!candidateEquipIds || candidateEquipIds.length === 0) {
          // 매핑 실패 → 미배정
          failedOrders.push({
            id: `ORD-${now}-${Math.random().toString(36).slice(2, 6)}`,
            order_number: batch.id,
            product: batch.product,
            spec: batch.spec,
            core_count: 0,
            color: batch.color,
            customer: batch.customer,
            delivery_date: batch.delivery_date,
            total_length_m: batch.total_length_m,
            priority: "normal",
            equipment_group: batch.equipment_group,
          });
          continue;
        }

        // 가장 빨리 비는 설비에 배치
        let bestEquipId = candidateEquipIds[0];
        let bestEndTime = equipmentEndTimes[bestEquipId] || now;
        for (const eqId of candidateEquipIds) {
          const endTime = equipmentEndTimes[eqId] || now;
          if (endTime < bestEndTime) {
            bestEndTime = endTime;
            bestEquipId = eqId;
          }
        }

        const lineSpeed = getLineSpeed(
          lineSpeedData,
          batch.spec,
          batch.product,
          0,
        );
        const startTime = new Date(Math.max(bestEndTime, now));
        const endTime = calculateTaskEnd(
          startTime,
          batch.total_length_m,
          lineSpeed,
        );

        const task: ScheduleTask = {
          id: `SYNC-${now}-${Math.random().toString(36).slice(2, 6)}`,
          order_id: batch.id,
          equipment_id: bestEquipId,
          product: batch.product,
          spec: batch.spec,
          core_count: 0,
          color: batch.color,
          start: startTime,
          end: endTime,
          volume_m: batch.total_length_m,
          line_speed_m_per_min: lineSpeed,
          priority: "normal",
          status: "planned",
          delivery_date: batch.delivery_date
            ? new Date(batch.delivery_date)
            : undefined,
          predecessors: [],
          notes: batch.notes || "",
          changeover_min: 0,
        };

        newTasks.push(task);
        equipmentEndTimes[bestEquipId] = endTime.getTime();
      }

      set((state) => {
        state.tasks.push(...newTasks);
        state.unscheduledOrders.push(...failedOrders);
        state.isEditMode = true;
      });
    },
  })),
);
