/**
 * ── 간트 드래그/캐스케이드 동작 문서 (Task #7 조사) ──────────────────────
 *
 * 1. 대체설비 블록 이동:
 *    - equipmentMatchesGroup() (SchedulerView.tsx)가 드래그 대상 블록의 설비 그룹을 판별한다.
 *    - 허용 그룹: "연선", "B100", "A100", "A120"
 *    - 같은 그룹 내 설비 간에만 이동이 가능하고, 다른 그룹(예: 연선→B100)으로는 드롭이 차단된다.
 *
 * 2. Cascade Push (겹침 방지):
 *    - cascadePush()는 **같은 설비(equipment_id)** 내에서만 작동한다.
 *    - 이동된 블록 기준 오른쪽 겹침 → forward push, 왼쪽 겹침 → backward push.
 *    - 설비 간(cross-equipment) cascade는 현재 미구현.
 *
 * 3. 선행→후행 자동 연동 (moveTask 내부):
 *    - 같은 order_id의 후속 공정(process_step이 큰 작업)을 timeDelta만큼 이동시킨다.
 *    - 후공정 시작은 선행 공정 종료 이후로 보장 (Math.max 적용).
 *    - 이동 후 후공정 설비 내에서 cascadePush를 재적용한다.
 *    - **제한사항**: cross-equipment 간 cascade는 미구현이므로, 연선 블록을 이동해도
 *      절연/시스 설비의 다른 배치에 대한 연쇄 밀기는 발생하지 않는다.
 */
import { create } from "zustand";
import { immer } from "zustand/middleware/immer";
import { enableMapSet } from "immer";

// Set/Map 을 immer draft 내에서 mutate 하려면 플러그인 활성화가 필요.
// inFlightBatchGroups (Set<string>) 이 Set 이므로 모듈 로드 시 1회 호출.
enableMapSet();
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
  CascadePreview,
  InboxItem,
  BatchGroupSnapshot,
  UnassignReason,
} from "../types";
import {
  getLineSpeed,
  calculateTaskEnd,
  getDefaultRange,
} from "../utils/ganttUtils";
import { useToastStore } from "@/shared/ui/toastStore";
import { refreshTasks } from "../hooks/useScheduleData";

const API_BASE = "http://localhost:8000/api";

/**
 * 선행 공정 여부 판단: process_step이 낮은 작업은 후행 공정이 존재할 수 있다.
 * process_step 값이 있고, 같은 order_id로 더 높은 step이 존재하면 선행 공정이다.
 */
function isPredecessorProcess(
  task: ScheduleTask,
  allTasks: ScheduleTask[],
): boolean {
  if (task.process_step == null || !task.order_id) return false;
  return allTasks.some(
    (t) =>
      t.id !== task.id &&
      t.order_id === task.order_id &&
      t.process_step != null &&
      t.process_step > task.process_step!,
  );
}

/** timestamp 추출 헬퍼 */
function toMs(d: Date | string | number): number {
  if (typeof d === "number") return d;
  if (d instanceof Date) return d.getTime();
  return new Date(d).getTime();
}

/**
 * Cascade push (양방향): 같은 설비에서 작업이 겹치면 밀어냄.
 *
 * 이동된 블록 기준으로:
 * - 오른쪽에 겹치는 블록 → 오른쪽으로 연쇄 밀기 (forward)
 * - 왼쪽에 겹치는 블록 → 왼쪽으로 연쇄 밀기 (backward)
 *
 * 기계는 동시에 하나의 작업만 할 수 있으므로 겹침 = 0이 되어야 한다.
 */
function cascadePush(
  tasks: ScheduleTask[],
  movedTaskId: string,
  equipmentId: string,
): void {
  const indices: number[] = [];
  for (let i = 0; i < tasks.length; i++) {
    if (tasks[i].equipment_id === equipmentId) indices.push(i);
  }
  if (indices.length < 2) return;

  // 시작시간 기준 정렬
  indices.sort((a, b) => toMs(tasks[a].start) - toMs(tasks[b].start));

  // 이동된 블록의 정렬 내 위치 찾기
  const movedPos = indices.findIndex((idx) => tasks[idx].id === movedTaskId);

  // --- Forward push: movedPos부터 오른쪽으로 ---
  for (let i = Math.max(movedPos, 0); i < indices.length - 1; i++) {
    const curr = tasks[indices[i]];
    const next = tasks[indices[i + 1]];
    const currEnd = toMs(curr.end);
    const nextStart = toMs(next.start);
    if (currEnd > nextStart) {
      const dur = toMs(next.end) - nextStart;
      next.start = new Date(currEnd);
      next.end = new Date(currEnd + dur);
    }
  }

  // --- Backward push: movedPos부터 왼쪽으로 ---
  for (let i = Math.min(movedPos, indices.length - 1); i > 0; i--) {
    const curr = tasks[indices[i]];
    const prev = tasks[indices[i - 1]];
    const currStart = toMs(curr.start);
    const prevEnd = toMs(prev.end);
    if (prevEnd > currStart) {
      // prev를 왼쪽으로 밀기: prev.end = curr.start, prev.start = prev.end - duration
      const dur = prevEnd - toMs(prev.start);
      prev.end = new Date(currStart);
      prev.start = new Date(currStart - dur);
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
  /**
   * 미배정 항목 (Task 4.1 union).
   * - kind "order": 기존 단일 수주 카드
   * - kind "batch_group": unassign된 배치 그룹 (Task 5.4에서 렌더링 지원)
   */
  unscheduledItems: InboxItem[];
  /**
   * Task 4.3/4.4에서 사용할 race-condition 가드.
   * 같은 batch_group이 unassign/restore 중복 호출되지 않도록 in-flight 상태를 추적.
   */
  inFlightBatchGroups: Set<string>;

  // 라인 속도 데이터 (API에서 로드)
  lineSpeedData: LineSpeedEntry[];

  // 뷰 상태
  viewFilter: ViewFilter;
  zoomLevel: ZoomLevel;
  range: { start: number; end: number };
  /** +/- 버튼으로 조정하는 픽셀 밀도 배율 (1.0 = 기본값) */
  dayWidthScale: number;

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

  // 배치 분할 모달 상태
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
  setDayWidthScale: (scale: number) => void;

  /**
   * 생산계획등록에서 확정된 배치를 간트 차트에 자동 배치한다.
   * 각 배치를 equipment_group에 맞는 설비에 순차적으로 배치한다.
   * 배치 실패 항목은 unscheduledItems에 "order" kind로 추가한다.
   */
  syncFromPlanRegister: (batches: ProductionBatch[]) => void;
  setUnscheduledItems: (items: InboxItem[]) => void;
  setLineSpeedData: (data: LineSpeedEntry[]) => void;

  /**
   * 수주를 스케줄러에 배정한다.
   * - 라인 속도를 조회하여 종료 시각을 자동 계산한다.
   * - 해당 수주를 unscheduledItems에서 제거하고 tasks에 추가한다.
   */
  assignOrder: (orderId: string, equipmentId: string, startTime: Date) => void;

  // 편집 모드 토글
  toggleEditMode: () => void;

  // 수정 내용을 버리고 수정 모드 종료 (스냅샷으로 복원)
  discardEdits: () => void;

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

  // 드래그 중 cascade preview
  setPreviewOffsets: (offsets: Record<string, number>) => void;
  clearPreviewOffsets: () => void;

  // 배치 분할 모달
  openSplitModal: (batchGroup: string, taskId: string) => void;
  closeSplitModal: () => void;

  // Cross-process cascade
  previewCascade: (
    taskId: string,
    newStart: Date,
    newEnd: Date,
  ) => Promise<CascadePreview | null>;
  applyCascade: (preview: CascadePreview) => Promise<void>;
  cancelCascade: () => void;

  // run_label 설정 — 스케줄러 페이지 초기화 시 호출
  setRunLabel: (runLabel: string | null) => void;

  /**
   * 배치 그룹을 unassign — tasks에서 제거하고 unscheduledItems에 BatchGroupSnapshot을 추가.
   * - 낙관적 업데이트 후 API 실패 시 롤백.
   * - inFlightBatchGroups 가드로 중복 호출 차단.
   */
  unassignBatchGroup: (
    batchGroup: string,
    reason?: UnassignReason,
  ) => Promise<void>;

  /**
   * 배치 그룹을 원래 자리로 복원 (블로커 B4 — Task 4.4).
   * - 200 성공: unscheduledItems에서 제거 + refreshTasks()로 Gantt 재렌더링.
   * - 409 conflict: warning Toast ("원래 자리에 다른 작업이 있습니다") + 인박스 유지.
   * - inFlightBatchGroups 가드로 중복 호출 차단.
   */
  restoreBatchGroup: (batchGroup: string) => Promise<void>;
}

type ScheduleStore = ScheduleState & ScheduleActions;

/**
 * 블록 변경 시 AI 재분석을 비동기로 트리거한다 (fire-and-forget).
 * 응답을 기다리지 않으므로 간트 UX에 영향 없음.
 */
function fireReanalysis(runLabel: string | null): void {
  if (!runLabel) return;
  fetch(
    `${API_BASE}/pipeline/stage2/${encodeURIComponent(runLabel)}/trigger-reanalysis`,
    { method: "POST" },
  ).catch(() => {
    // 백엔드 미연결 시 무시 — PoC 단계에서 graceful 처리
  });
}

export const useScheduleStore = create<ScheduleStore>()(
  immer((set, get) => ({
    // 초기 상태
    equipment: [],
    tasks: [],
    violations: [],
    selectedTaskId: null,
    unscheduledItems: [],
    inFlightBatchGroups: new Set<string>(),
    lineSpeedData: [],
    viewFilter: { filterType: "all", filterValue: [] },
    zoomLevel: "day",
    range: getDefaultRange(7), // day 줌: ±7일 = 2주 뷰
    dayWidthScale: 1.0,
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
    // 선행 공정인 경우 cross-equipment cascade preview를 비동기로 트리거한다.
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

        // 후공정 자동 연동: 같은 order_id의 후속 공정 작업도 시간 이동
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

      // 선행 공정이면 cross-equipment cascade preview를 비동기로 트리거
      const updatedTask = get().tasks.find((t) => t.id === taskId);
      if (updatedTask && isPredecessorProcess(updatedTask, get().tasks)) {
        // 비동기 cascade preview — 결과에 따라 자동 적용 또는 모달 표시
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

    setDayWidthScale: (scale) => {
      set((state) => {
        state.dayWidthScale = scale;
      });
    },

    setUnscheduledItems: (items) => {
      set((state) => {
        state.unscheduledItems = items;
      });
    },

    setLineSpeedData: (data) => {
      set((state) => {
        state.lineSpeedData = data;
      });
    },

    // 수주 → 스케줄 작업 배정
    assignOrder: (orderId, equipmentId, startTime) => {
      const { unscheduledItems, lineSpeedData } = get();
      // union narrowing: "order" kind만 대상으로 삼고 id 매칭
      const orderItem = unscheduledItems.find(
        (i): i is { kind: "order"; order: Order } =>
          i.kind === "order" && i.order.id === orderId,
      );
      if (!orderItem) return;
      const order = orderItem.order;

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
        // "order" kind 중 해당 id만 제거. batch_group kind는 그대로 유지.
        state.unscheduledItems = state.unscheduledItems.filter(
          (i) => !(i.kind === "order" && i.order.id === orderId),
        );

        // cascade push: 새 작업이 기존 작업과 겹치면 뒤로 밀기
        cascadePush(state.tasks, newTask.id, equipmentId);
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

    // 배치 분할 모달 열기
    openSplitModal: (batchGroup, taskId) => {
      set((state) => {
        state.splitModal = { isOpen: true, batchGroup, taskId };
      });
    },

    // 배치 분할 모달 닫기
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

    // Cascade 전체 적용 — bulk update로 후행 공정 일괄 이동
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
        // 실패 수주는 "order" kind로 wrap하여 unscheduledItems에 추가.
        state.unscheduledItems.push(
          ...failedOrders.map((o) => ({ kind: "order" as const, order: o })),
        );
        state.isEditMode = true;

        // 동기화 후 range를 tasks 시간 범위의 앞 2주로 자동 설정
        // 890건 전량 렌더링 시 빈 화면 방지
        if (newTasks.length > 0) {
          const allTasks = state.tasks;
          let minStart = Infinity;
          for (const t of allTasks) {
            const ts =
              t.start instanceof Date
                ? t.start.getTime()
                : new Date(t.start).getTime();
            if (ts < minStart) minStart = ts;
          }
          if (minStart < Infinity) {
            const TWO_WEEKS_MS = 14 * 24 * 60 * 60 * 1000;
            state.range = {
              start: minStart,
              end: minStart + TWO_WEEKS_MS,
            };
          }
        }
      });
    },

    // run_label 설정 — 스케줄러 페이지 로드 시 최신 런 라벨을 저장
    setRunLabel: (runLabel) => {
      set((state) => {
        state.runLabel = runLabel;
      });
    },

    /**
     * 배치 그룹 unassign — 낙관적 업데이트 + API 호출 + 실패 시 롤백.
     *
     * Flow:
     *   1. 가드 체크(중복 in-flight / 빈 타겟 / non-planned 상태 포함 시 즉시 리턴)
     *   2. BatchGroupSnapshot 합성 + tasks → unscheduledItems 이동 (낙관적)
     *   3. POST /pipeline/batch-group/{bg}/unassign with { reason }
     *   4. 성공: success Toast + fireReanalysis
     *      실패: 롤백 + error Toast
     *   5. finally: in-flight guard 해제
     */
    unassignBatchGroup: async (
      batchGroup: string,
      reason: UnassignReason = "기타",
    ) => {
      const state = get();
      if (state.inFlightBatchGroups.has(batchGroup)) return;

      const targets = state.tasks.filter((t) => t.batch_group === batchGroup);
      if (targets.length === 0) return;
      // 진행중/완료 배치는 unassign 불가 — planned 상태만 허용
      if (targets.some((t) => t.status !== "planned")) return;

      // BatchGroupSnapshot 합성 — 프론트 측 즉시 반영용 (서버가 생성한 스냅샷은
      // 다음 reanalysis/refresh 시 덮어써짐)
      const snapshot: BatchGroupSnapshot = {
        batch_group: batchGroup,
        customer: targets[0].customer || "",
        spec: targets[0].spec,
        color: targets[0].color || "",
        total_length_m: targets.reduce((s, t) => s + (t.volume_m || 0), 0),
        delivery_date: targets[0].delivery_date
          ? new Date(targets[0].delivery_date).toISOString()
          : "",
        processes: targets.map((t) => ({
          process: t.product || "",
          equipment_group: t.equipment_id,
        })),
        order_count: new Set(targets.map((t) => t.order_id)).size,
        unassign_reason: reason,
      };

      // 롤백용 원본 스냅샷 (shallow clone 으로 족함 — 내부 Date/primitive 만 사용)
      const rollbackTasks = targets.map((t) => ({ ...t }));

      // 낙관적 업데이트
      set((s) => {
        s.inFlightBatchGroups.add(batchGroup);
        s.tasks = s.tasks.filter((t) => t.batch_group !== batchGroup);
        s.unscheduledItems.push({ kind: "batch_group", group: snapshot });
      });

      try {
        const res = await fetch(
          `${API_BASE}/pipeline/batch-group/${encodeURIComponent(batchGroup)}/unassign`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ reason }),
          },
        );
        if (!res.ok) throw new Error(`HTTP ${res.status}`);

        useToastStore
          .getState()
          .show(`${batchGroup} 미배정으로 이동 (사유: ${reason})`, "success");

        fireReanalysis(get().runLabel);
      } catch (err) {
        console.warn("[unassignBatchGroup] 롤백:", err);
        set((s) => {
          s.tasks.push(...rollbackTasks);
          s.unscheduledItems = s.unscheduledItems.filter(
            (i) =>
              !(i.kind === "batch_group" && i.group.batch_group === batchGroup),
          );
        });
        useToastStore
          .getState()
          .show("미배정 이동 실패. 다시 시도하세요", "error");
      } finally {
        set((s) => {
          s.inFlightBatchGroups.delete(batchGroup);
        });
      }
    },

    /**
     * 배치 그룹을 원래 자리로 복원 (블로커 B4 — Task 4.4).
     *
     * Flow:
     *   1. in-flight 가드 체크
     *   2. POST /pipeline/batch-group/{bg}/restore
     *   3. 200 성공: unscheduledItems에서 해당 batch_group 제거 → refreshTasks()
     *      호출로 서버의 새로 배치된 tasks를 스토어에 반영하여 Gantt 즉시 재렌더링
     *   4. 409 conflict: warning Toast + 인박스 유지 (v2에서 재배치 지원 예정)
     *   5. 기타 실패: error Toast
     *   6. finally: in-flight 가드 해제
     */
    restoreBatchGroup: async (batchGroup: string) => {
      const state = get();
      if (state.inFlightBatchGroups.has(batchGroup)) return;

      set((s) => {
        s.inFlightBatchGroups.add(batchGroup);
      });

      try {
        const res = await fetch(
          `${API_BASE}/pipeline/batch-group/${encodeURIComponent(batchGroup)}/restore`,
          { method: "POST" },
        );

        if (res.status === 409) {
          useToastStore
            .getState()
            .show(
              "원래 자리에 다른 작업이 있습니다. 재배치는 v2에서 지원 예정입니다.",
              "warning",
              6000,
            );
          return;
        }
        if (!res.ok) throw new Error(`HTTP ${res.status}`);

        // 성공: 인박스에서 제거 후 서버 tasks 재조회 (블로커 B4 — Gantt 즉시 반영)
        set((s) => {
          s.unscheduledItems = s.unscheduledItems.filter(
            (i) =>
              !(i.kind === "batch_group" && i.group.batch_group === batchGroup),
          );
        });
        await refreshTasks();

        useToastStore
          .getState()
          .show(`${batchGroup} 원래 자리로 복원 완료`, "success");
        fireReanalysis(get().runLabel);
      } catch (err) {
        console.warn("[restoreBatchGroup] 실패:", err);
        useToastStore.getState().show("복원 실패. 다시 시도하세요", "error");
      } finally {
        set((s) => {
          s.inFlightBatchGroups.delete(batchGroup);
        });
      }
    },
  })),
);
