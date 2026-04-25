/**
 * ordersSlice — 미배정 인박스(unscheduledItems) + 배치 그룹 라이프사이클.
 *
 * 책임 (Single Responsibility):
 *   - 미배정 항목(InboxItem: order kind / batch_group kind) 보관 및 in-flight 가드
 *   - 수주를 스케줄에 배정 (assignOrder)
 *   - 생산계획등록 → 간트 자동 배치 (syncFromPlanRegister) — 이 액션은 batches 슬라이스의
 *     tasks 도 변경하지만, 시작점은 "미배정 batches 입력" 이므로 orders 슬라이스에 둠.
 *   - 배치 그룹 unassign / restore / 스냅샷 로드
 *
 * 다른 슬라이스 의존성: get() 으로 batches(tasks/equipment/lineSpeedData/runLabel) 조회.
 * 이는 zustand 슬라이스 패턴의 정상 사용법 — 슬라이스는 "전체 store 의 한 단면" 일 뿐이며
 * set/get 모두 store 레벨에서 작동한다.
 */
import type { StateCreator } from "zustand";
import type {
  InboxItem,
  Order,
  ScheduleTask,
  ProductionBatch,
  BatchGroupSnapshot,
  UnassignReason,
} from "../../types";
import { useToastStore } from "@/shared/ui/toastStore";
import { refreshTasks } from "../../hooks/useScheduleData";
import { getLineSpeed, calculateTaskEnd } from "../../utils/ganttUtils";
import { API_BASE, fireReanalysis, cascadePush } from "./shared";
import type { ScheduleStore } from "../scheduleStore";

export interface OrdersSlice {
  // ── State ───────────────────────────────────────────────
  unscheduledItems: InboxItem[];
  /** Task 4.3/4.4 race-condition 가드 */
  inFlightBatchGroups: Set<string>;

  // ── Actions ─────────────────────────────────────────────
  setUnscheduledItems: (items: InboxItem[]) => void;
  /** 수주를 스케줄러에 배정 — 라인 속도 기반 종료 시각 자동 계산 */
  assignOrder: (orderId: string, equipmentId: string, startTime: Date) => void;
  /** 생산계획등록에서 확정된 배치를 간트 차트에 자동 배치 */
  syncFromPlanRegister: (batches: ProductionBatch[]) => void;
  unassignBatchGroup: (
    batchGroup: string,
    reason?: UnassignReason,
  ) => Promise<void>;
  restoreBatchGroup: (batchGroup: string) => Promise<void>;
  loadBatchGroupSnapshots: () => Promise<void>;
}

export const createOrdersSlice: StateCreator<
  ScheduleStore,
  [["zustand/immer", never]],
  [],
  OrdersSlice
> = (set, get) => ({
  unscheduledItems: [],
  inFlightBatchGroups: new Set<string>(),

  setUnscheduledItems: (items) => {
    set((state) => {
      state.unscheduledItems = items;
    });
  },

  assignOrder: (orderId, equipmentId, startTime) => {
    const { unscheduledItems, lineSpeedData } = get();
    // union narrowing: "order" kind만 대상
    const orderItem = unscheduledItems.find(
      (i): i is { kind: "order"; order: Order } =>
        i.kind === "order" && i.order.id === orderId,
    );
    if (!orderItem) return;
    const order = orderItem.order;

    const lineSpeed = getLineSpeed(
      lineSpeedData,
      order.spec,
      order.product,
      order.core_count,
    );
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
      state.unscheduledItems = state.unscheduledItems.filter(
        (i) => !(i.kind === "order" && i.order.id === orderId),
      );
      // cascade push: 새 작업이 기존 작업과 겹치면 뒤로 밀기
      cascadePush(state.tasks, newTask.id, equipmentId);
    });
  },

  syncFromPlanRegister: (batches) => {
    const { equipment, lineSpeedData, tasks: existingTasks } = get();

    // 설비 그룹 → 설비 ID 매핑
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

    // 설비별 마지막 종료 시각 추적
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
      state.unscheduledItems.push(
        ...failedOrders.map((o) => ({ kind: "order" as const, order: o })),
      );
      state.isEditMode = true;

      // 동기화 후 range 를 tasks 의 시작 시각에 맞춰 앞 2주로 자동 설정.
      // 890건 전량 렌더링 시 빈 화면 방지.
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

  /**
   * 배치 그룹 unassign — 낙관적 업데이트 + API 호출 + 실패 시 롤백.
   * 자세한 흐름은 원본 scheduleStore.ts 의 docstring 참고.
   */
  unassignBatchGroup: async (
    batchGroup: string,
    reason: UnassignReason = "기타",
  ) => {
    const state = get();
    if (state.inFlightBatchGroups.has(batchGroup)) return;

    const targets = state.tasks.filter((t) => t.batch_group === batchGroup);
    if (targets.length === 0) return;
    // 진행중/완료 배치는 unassign 불가. planned/scheduled 둘 다 허용.
    if (targets.some((t) => t.status !== "planned" && t.status !== "scheduled"))
      return;

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

    // 롤백용 원본 스냅샷
    const rollbackTasks = targets.map((t) => ({ ...t }));

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
   * 배치 그룹을 원래 자리로 복원 (Task 4.4 / 블로커 B4).
   * 자세한 흐름은 원본 scheduleStore.ts 의 docstring 참고.
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

      // 성공: 인박스에서 제거 후 서버 tasks 재조회 (Gantt 즉시 반영)
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

  /**
   * 서버 batch-group-snapshots 조회 → unscheduledItems 의 batch_group 부분만 멱등 교체.
   * order kind 는 보존, batch_group kind 만 서버 응답으로 덮어씀.
   */
  loadBatchGroupSnapshots: async () => {
    try {
      const res = await fetch(`${API_BASE}/pipeline/batch-group-snapshots`);
      if (!res.ok) return;
      const body = await res.json();
      const groups = (body.groups ?? []) as BatchGroupSnapshot[];
      set((s) => {
        const orderItems = s.unscheduledItems.filter((i) => i.kind === "order");
        const groupItems = groups.map((g) => ({
          kind: "batch_group" as const,
          group: g,
        }));
        s.unscheduledItems = [...orderItems, ...groupItems];
      });
    } catch (err) {
      console.warn("[loadBatchGroupSnapshots] 실패:", err);
    }
  },
});
