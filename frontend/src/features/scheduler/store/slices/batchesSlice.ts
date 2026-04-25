/**
 * batchesSlice — 간트에 렌더링되는 작업(tasks) + 설비 + 선택/체인/편집/모달/캐스케이드 상태.
 *
 * 책임 (Single Responsibility):
 *   - 도메인 데이터: equipment, tasks, lineSpeedData, runLabel
 *   - 선택 상태: selectedTaskId / selectedChainIds / selectedArrows (체인 하이라이트)
 *   - 편집 라이프사이클: isEditMode, editSnapshot, savedVersions, showSavedToast
 *   - 모달/메뉴 상태: contextMenu, taskFormModal, splitModal
 *   - 드래그 preview / cross-process cascade preview
 *
 * 다른 슬라이스 의존성: get().runLabel(자기 슬라이스), 그리고 일부 액션이 set 으로
 * 다른 슬라이스 필드(예: cascade 상호배타 리셋은 diffSlice 에서 수행) 도 변경 가능.
 */
import type { StateCreator } from "zustand";
import type {
  Equipment,
  ScheduleTask,
  LineSpeedEntry,
  ScheduleVersion,
  ContextMenuState,
  TaskFormModalState,
  CascadePreview,
} from "../../types";
import type { ArrowEdge } from "../../utils/chainGraph";
import { buildChain } from "../../utils/chainGraph";
import {
  API_BASE,
  fireReanalysis,
  cascadePush,
  isPredecessorProcess,
} from "./shared";
import type { ScheduleStore } from "../scheduleStore";

export interface BatchesSlice {
  // ── State ───────────────────────────────────────────────
  equipment: Equipment[];
  tasks: ScheduleTask[];
  lineSpeedData: LineSpeedEntry[];

  selectedTaskId: string | null;
  /**
   * 선택된 블록이 속한 생산 체인 task id 집합.
   * - `null`: 선택 없음 OR orphan (chain size ≤ 1) — R6 에 따라 dim 미적용.
   * - `Set<string>`: 체인 外 블록을 dim 처리할 때 사용.
   */
  selectedChainIds: Set<string> | null;
  /**
   * 선택된 체인의 화살표 엣지(src→dst) 목록. Overlay 가 SVG path 를 그릴 때 사용.
   */
  selectedArrows: ArrowEdge[];

  // 편집 모드 — 기본은 읽기 전용(false)
  isEditMode: boolean;
  // 수정 모드 진입 시 저장된 tasks 스냅샷 — 취소 시 복원용
  editSnapshot: ScheduleTask[] | null;
  // 버전 히스토리
  savedVersions: ScheduleVersion[];
  // 저장 완료 토스트 표시 여부
  showSavedToast: boolean;

  // 드래그 중 cascade preview — task별 시간 오프셋(ms)
  previewOffsets: Record<string, number>;

  // UI 상태
  contextMenu: ContextMenuState | null;
  taskFormModal: TaskFormModalState;

  // 배치 분할 모달
  splitModal: {
    isOpen: boolean;
    batchGroup: string;
    taskId: string;
  };

  // Cross-process cascade preview 상태
  cascadePreview: CascadePreview | null;
  conflictModalOpen: boolean;
  cascadeOriginalTask: { id: string; start: Date; end: Date } | null;

  // 현재 run_label — AI 재분석 트리거에 사용
  runLabel: string | null;

  // ── Actions ─────────────────────────────────────────────
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
  selectTask: (taskId: string | null) => void;
  setLineSpeedData: (data: LineSpeedEntry[]) => void;

  toggleEditMode: () => void;
  discardEdits: () => void;
  saveVersion: (label?: string) => Promise<void>;
  hideSavedToast: () => void;

  openContextMenu: (state: ContextMenuState) => void;
  closeContextMenu: () => void;
  openTaskFormModal: (state: Omit<TaskFormModalState, "isOpen">) => void;
  closeTaskFormModal: () => void;

  setPreviewOffsets: (offsets: Record<string, number>) => void;
  clearPreviewOffsets: () => void;

  openSplitModal: (batchGroup: string, taskId: string) => void;
  closeSplitModal: () => void;

  previewCascade: (
    taskId: string,
    newStart: Date,
    newEnd: Date,
  ) => Promise<CascadePreview | null>;
  applyCascade: (preview: CascadePreview) => Promise<void>;
  cancelCascade: () => void;

  setRunLabel: (runLabel: string | null) => void;
}

export const createBatchesSlice: StateCreator<
  ScheduleStore,
  [["zustand/immer", never]],
  [],
  BatchesSlice
> = (set, get) => ({
  equipment: [],
  tasks: [],
  lineSpeedData: [],
  selectedTaskId: null,
  selectedChainIds: null,
  selectedArrows: [],
  isEditMode: false,
  editSnapshot: null,
  savedVersions: [],
  showSavedToast: false,
  previewOffsets: {},
  contextMenu: null,
  taskFormModal: { isOpen: false, mode: "create" },
  splitModal: { isOpen: false, batchGroup: "", taskId: "" },
  cascadePreview: null,
  conflictModalOpen: false,
  cascadeOriginalTask: null,
  runLabel: null,

  setEquipment: (equipment) => {
    set((state) => {
      state.equipment = equipment;
    });
  },

  setTasks: (tasks) => {
    set((state) => {
      state.tasks = tasks;
    });
  },

  addTask: (task) => {
    set((state) => {
      state.tasks.push(task);
    });
  },

  deleteTask: (taskId) => {
    set((state) => {
      state.tasks = state.tasks.filter((t) => t.id !== taskId);
    });
  },

  // 개별 작업 업데이트 — 변경 후 AI 재분석 트리거
  updateTask: (taskId, updates) => {
    set((state) => {
      const taskIdx = state.tasks.findIndex((t) => t.id === taskId);
      if (taskIdx === -1) return;
      Object.assign(state.tasks[taskIdx], updates);
    });
    fireReanalysis(get().runLabel);
  },

  // 작업 이동 (드래그 앤 드롭) — 겹침 방지 + cascade push + 후공정 연동
  // 선행 공정인 경우 cross-equipment cascade preview 를 비동기로 트리거.
  moveTask: (taskId, newEquipmentId, start, end) => {
    // 이동 전 원본 위치를 저장 (cascade 취소 시 복원용)
    const taskBefore = get().tasks.find((t) => t.id === taskId);
    if (taskBefore) {
      const origStart =
        taskBefore.start instanceof Date
          ? taskBefore.start
          : new Date(taskBefore.start);
      const origEnd =
        taskBefore.end instanceof Date
          ? taskBefore.end
          : new Date(taskBefore.end);
      set((state) => {
        state.cascadeOriginalTask = {
          id: taskId,
          start: origStart,
          end: origEnd,
        };
      });
    }

    set((state) => {
      const taskIdx = state.tasks.findIndex((t) => t.id === taskId);
      if (taskIdx === -1) return;

      const task = state.tasks[taskIdx];
      const timeDelta = start.getTime() - task.start.getTime();
      task.equipment_id = newEquipmentId;
      task.start = start;
      task.end = end;

      // cascade push: 같은 설비의 다른 작업과 겹치면 뒤로 밀기
      cascadePush(state.tasks, taskId, newEquipmentId);

      // 후공정 자동 연동: 같은 order_id 의 후속 공정 작업도 시간 이동
      if (task.order_id && timeDelta !== 0) {
        const successors = state.tasks.filter(
          (t) =>
            t.id !== taskId &&
            t.order_id === task.order_id &&
            t.process_step !== undefined &&
            task.process_step !== undefined &&
            t.process_step > task.process_step,
        );
        for (const succ of successors) {
          const succDuration = succ.end.getTime() - succ.start.getTime();
          // 후공정은 현재 작업 종료 이후에 시작해야 함
          const newSuccStart = new Date(
            Math.max(succ.start.getTime() + timeDelta, end.getTime()),
          );
          succ.start = newSuccStart;
          succ.end = new Date(newSuccStart.getTime() + succDuration);
          cascadePush(state.tasks, succ.id, succ.equipment_id);
        }
      }
    });

    // 선행 공정이면 cross-equipment cascade preview 를 비동기로 트리거
    const updatedTask = get().tasks.find((t) => t.id === taskId);
    if (updatedTask && isPredecessorProcess(updatedTask, get().tasks)) {
      get()
        .previewCascade(taskId, start, end)
        .then((preview) => {
          if (!preview) return;
          if (preview.can_auto_resolve && preview.conflicts.length === 0) {
            // 충돌 없이 자동 해소 가능 → 즉시 적용
            get().applyCascade(preview);
          } else {
            // 충돌 있거나 자동 해소 불가 → 모달 표시
            set((state) => {
              state.conflictModalOpen = true;
            });
          }
        });
    }

    // 블록 이동 후 AI 재분석 트리거 (fire-and-forget)
    fireReanalysis(get().runLabel);
  },

  /**
   * 블록 선택 시 chainGraph.buildChain() 을 단일 호출해 chainIds + arrows 를
   * 동시에 저장 (overlay 측 double BFS 회피 — v3 Architecture).
   *
   * R6 규칙: 체인 크기 ≤ 1 (orphan task) 인 경우 selectedChainIds = null 로 저장해
   * overlay/block dim 경로가 "선택 없음" 과 동일하게 동작하도록 한다.
   *
   * 주의: buildChain 이 반환하는 Set<string> 을 immer draft 내부에서 재구성하지
   * 않고 평면 set({...}) 으로 주입. immer 는 Set 을 deep-freeze 하지 않으므로
   * 외부에서 build 된 Set 을 그대로 저장하는 것이 안전하고 불필요 복제 회피.
   */
  selectTask: (taskId) => {
    if (taskId === null) {
      set({
        selectedTaskId: null,
        selectedChainIds: null,
        selectedArrows: [],
      });
      return;
    }
    const state = get();
    const result = buildChain(taskId, state.tasks, state.equipment);
    // R6: chain size ≤ 1 → dim 생략 신호 = selectedChainIds null
    const chainIds = result.chainIds.size > 1 ? result.chainIds : null;
    set({
      selectedTaskId: taskId,
      selectedChainIds: chainIds,
      selectedArrows: result.arrows,
    });
  },

  setLineSpeedData: (data) => {
    set((state) => {
      state.lineSpeedData = data;
    });
  },

  // 편집 모드 토글 — 읽기 전용 ↔ 수정 모드
  toggleEditMode: () => {
    set((state) => {
      if (!state.isEditMode) {
        // 수정 모드 진입: 현재 tasks 스냅샷 저장
        state.editSnapshot = state.tasks.map((t) => ({ ...t }));
      } else {
        // 수정 모드 종료(저장): 스냅샷 폐기
        state.editSnapshot = null;
      }
      state.isEditMode = !state.isEditMode;
    });
  },

  discardEdits: () => {
    set((state) => {
      if (state.editSnapshot) {
        state.tasks = state.editSnapshot;
        state.editSnapshot = null;
      }
      state.isEditMode = false;
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

  setPreviewOffsets: (offsets) => {
    set((state) => {
      state.previewOffsets = offsets;
    });
  },

  clearPreviewOffsets: () => {
    set((state) => {
      state.previewOffsets = {};
    });
  },

  openSplitModal: (batchGroup, taskId) => {
    set((state) => {
      state.splitModal = { isOpen: true, batchGroup, taskId };
    });
  },

  closeSplitModal: () => {
    set((state) => {
      state.splitModal = { isOpen: false, batchGroup: "", taskId: "" };
    });
  },

  // Cross-process cascade preview — 선행 공정 이동 시 후행 공정 영향 미리보기
  previewCascade: async (taskId, newStart, newEnd) => {
    try {
      const res = await fetch(`${API_BASE}/schedules/cascade-preview`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          task_id: taskId,
          new_start: newStart.toISOString(),
          new_end: newEnd.toISOString(),
        }),
      });
      if (!res.ok) {
        console.warn("[previewCascade] API 오류:", res.status);
        return null;
      }
      const preview: CascadePreview = await res.json();
      set((state) => {
        state.cascadePreview = preview;
      });
      return preview;
    } catch {
      // 백엔드 미연결 시 null 반환 — PoC 단계에서 graceful fallback
      console.warn("[previewCascade] 백엔드 연결 실패");
      return null;
    }
  },

  // Cascade 전체 적용 — bulk update 로 후행 공정 일괄 이동
  applyCascade: async (preview) => {
    try {
      const updates = preview.affected_tasks.map((t) => ({
        task_id: t.task_id,
        new_start: t.new_start,
        new_end: t.new_end,
      }));

      const res = await fetch(`${API_BASE}/schedules/tasks/bulk-update`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ updates }),
      });

      if (res.ok) {
        // 로컬 상태도 즉시 반영하여 간트 차트 갱신
        set((state) => {
          for (const affected of preview.affected_tasks) {
            const taskIdx = state.tasks.findIndex(
              (t) => t.id === affected.task_id,
            );
            if (taskIdx !== -1) {
              state.tasks[taskIdx].start = new Date(affected.new_start);
              state.tasks[taskIdx].end = new Date(affected.new_end);
            }
          }
          state.cascadePreview = null;
          state.conflictModalOpen = false;
          state.cascadeOriginalTask = null;
        });
        // cascade 적용 후 AI 재분석 트리거
        fireReanalysis(get().runLabel);
      } else {
        console.warn("[applyCascade] bulk-update 실패:", res.status);
      }
    } catch {
      console.warn("[applyCascade] 백엔드 연결 실패");
    }
  },

  // Cascade 취소 — 이동된 작업을 원래 위치로 복원
  cancelCascade: () => {
    const { cascadeOriginalTask } = get();
    if (cascadeOriginalTask) {
      set((state) => {
        const taskIdx = state.tasks.findIndex(
          (t) => t.id === cascadeOriginalTask.id,
        );
        if (taskIdx !== -1) {
          state.tasks[taskIdx].start = cascadeOriginalTask.start;
          state.tasks[taskIdx].end = cascadeOriginalTask.end;
        }
        state.cascadePreview = null;
        state.conflictModalOpen = false;
        state.cascadeOriginalTask = null;
      });
    } else {
      set((state) => {
        state.cascadePreview = null;
        state.conflictModalOpen = false;
        state.cascadeOriginalTask = null;
      });
    }
  },

  setRunLabel: (runLabel) => {
    set((state) => {
      state.runLabel = runLabel;
    });
  },
});
