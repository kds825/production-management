/**
 * useSchedulerDnd — 스케줄러 페이지의 @dnd-kit 핸들러 응집.
 *
 * 책임 (Week 7 Task 7B.1 분리):
 *   - activeDrag 상태 관리 + activeDragGroup/Sq/Material 파생값.
 *   - sensors (PointerSensor 5px 활성화).
 *   - handleDragStart / handleDragMove (cascade preview offsets) / handleDragEnd
 *     (4개 분기: inbox / batch_group / order / task|batch_group_task).
 *
 * 페이지는 hook 의 반환값을 DndContext 에 그대로 spread 한다 — DnD 도메인 로직과
 * 페이지의 시각/모달 상태가 분리되어 page.tsx 가 ≤500 줄에 수렴.
 */
"use client";

import { useCallback, useMemo, useState } from "react";
import {
  PointerSensor,
  useSensor,
  useSensors,
  type DragStartEvent,
  type DragEndEvent,
  type DragMoveEvent,
} from "@dnd-kit/core";

import { equipmentMatchesGroup } from "../components/SchedulerView";
import { useScheduleStore } from "../store/scheduleStore";
import { xToTime } from "../utils/ganttUtils";
import { useToastStore } from "@/shared/ui/toastStore";
import type { Order, ScheduleTask } from "../types";

/** 드래그 중인 아이템 정보 — DragOverlay 미리보기용 */
export interface ActiveDragItem {
  type: "order" | "task";
  order?: Order;
  task?: ScheduleTask;
}

interface UseSchedulerDndOptions {
  /** task → 단일 재배정 시 호출되는 cascade v2 commit (페이지에서 useScheduleChangeWithCascade 결과 전달) */
  cascadeCommit: (input: {
    task_id: string;
    new_start: string;
    new_end: string;
    new_equipment_code?: string | null;
  }) => void;
  /** batch_group 드롭 시 호출되는 onDropToEquipment (페이지에서 useBatchGroupDrag 결과 전달) */
  bgDropToEquipment: (
    bgLabel: string,
    args: {
      equipmentCode: string;
      anchorStart: Date;
      isOrigin: boolean;
    },
  ) => void | Promise<void>;
  /** read-only 모드에서 드롭 시도 시 노출할 경고 모달 트리거 */
  onShowEditWarning: () => void;
  /** inbox 드롭 시 UnassignConfirmModal 을 띄울 때 호출 (skip 옵션 있으면 즉시 unassign) */
  onInboxDrop: (batchGroup: string) => void;
  /** "이 세션에서 다시 묻지 않기" 가 체크되어 있는지 — true 면 즉시 unassign */
  skipConfirmFromDrag: boolean;
  /** 블록 변경 후 AI explain 캐시 무효화 (영향받는 배치 재분석 필요) */
  onResetExplainCache: () => void;
  /** 선행 공정 이동 시 후행 공정 영향 경고 토스트 트리거 */
  onPredecessorMoveWarning: (msg: string) => void;
}

export interface UseSchedulerDndResult {
  // sensors / DragOverlay 데이터
  sensors: ReturnType<typeof useSensors>;
  activeDrag: ActiveDragItem | null;
  // SchedulerView 에 전달할 비호환 행 표시용 파생값
  activeDragGroup: string | null;
  activeDragSq: number | null;
  activeDragMaterial: string | null;
  // DndContext 핸들러
  onDragStart: (event: DragStartEvent) => void;
  onDragMove: (event: DragMoveEvent) => void;
  onDragEnd: (event: DragEndEvent) => void;
}

export function useSchedulerDnd({
  cascadeCommit,
  bgDropToEquipment,
  onShowEditWarning,
  onInboxDrop,
  skipConfirmFromDrag,
  onResetExplainCache,
  onPredecessorMoveWarning,
}: UseSchedulerDndOptions): UseSchedulerDndResult {
  const tasks = useScheduleStore((s) => s.tasks);
  const equipment = useScheduleStore((s) => s.equipment);
  const range = useScheduleStore((s) => s.range);
  const isEditMode = useScheduleStore((s) => s.isEditMode);
  const assignOrder = useScheduleStore((s) => s.assignOrder);
  const setPreviewOffsets = useScheduleStore((s) => s.setPreviewOffsets);
  const clearPreviewOffsets = useScheduleStore((s) => s.clearPreviewOffsets);

  const [activeDrag, setActiveDrag] = useState<ActiveDragItem | null>(null);

  // 드래그 중인 아이템의 equipment_group 추론 — SchedulerView 로 전달하여 비호환 행 비활성화
  const activeDragGroup = useMemo<string | null>(() => {
    if (!activeDrag) return null;
    if (activeDrag.type === "order" && activeDrag.order) {
      return activeDrag.order.equipment_group ?? null;
    }
    if (activeDrag.type === "task" && activeDrag.task) {
      const eq = equipment.find((e) => e.id === activeDrag.task!.equipment_id);
      if (!eq) return null;
      for (const g of ["연선", "B100", "A100", "A120"]) {
        if (equipmentMatchesGroup(eq, g)) return g;
      }
    }
    return null;
  }, [activeDrag, equipment]);

  // 드래그 중인 아이템의 SQ (mm²) 추출 — spec 문자열에서 "400SQ" → 400 파싱
  const activeDragSq = useMemo<number | null>(() => {
    if (!activeDrag) return null;
    const spec =
      activeDrag.type === "order"
        ? activeDrag.order?.spec
        : activeDrag.task?.spec;
    if (!spec) return null;
    const match = spec.match(/(\d+)\s*SQ/i);
    return match ? parseInt(match[1], 10) : null;
  }, [activeDrag]);

  // 드래그 중인 아이템의 도체 재질 (CU | AL) 추출
  const activeDragMaterial = useMemo<string | null>(() => {
    if (!activeDrag) return null;
    if (activeDrag.type === "task" && activeDrag.task?.material) {
      return activeDrag.task.material;
    }
    // Order 타입에 material 이 없으므로 null 반환 — 배치 기반 task 만 재질 검증.
    return null;
  }, [activeDrag]);

  // 드래그 감도 — 5px 이동 후 드래그 시작
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
  );

  const onDragStart = useCallback((event: DragStartEvent) => {
    const data = event.active.data.current;
    if (!data) return;

    if (data.type === "order") {
      // equipment_group 이 drag data 에 있으면 order 객체에 merge 하여 activeDragGroup 계산에 활용
      const order = data.order as Order;
      const enrichedOrder: Order = data.equipment_group
        ? { ...order, equipment_group: data.equipment_group as string }
        : order;
      setActiveDrag({ type: "order", order: enrichedOrder });
    } else if (data.type === "task") {
      setActiveDrag({ type: "task", task: data.task as ScheduleTask });
    }
  }, []);

  // 드래그 중 — cascade preview 계산
  const onDragMove = useCallback(
    (event: DragMoveEvent) => {
      if (!isEditMode) return;
      const activeData = event.active.data.current;
      const over = event.over;
      if (!activeData || !over?.data?.current) {
        clearPreviewOffsets();
        return;
      }
      if (over.data.current.type !== "equipment-row") {
        clearPreviewOffsets();
        return;
      }
      if (activeData.type !== "task") {
        clearPreviewOffsets();
        return;
      }

      const task = activeData.task as ScheduleTask;
      const targetEqId = over.data.current.equipmentId as string;

      // 현재 드래그 위치에서 task 의 예상 start/end 계산
      const MS_PER_DAY = 24 * 60 * 60 * 1000;
      const totalDays = Math.max((range.end - range.start) / MS_PER_DAY, 1);
      const overWidth = over.rect?.width || 800;
      const dynamicDayWidth = overWidth / totalDays;

      const taskStartTs =
        task.start instanceof Date
          ? task.start.getTime()
          : new Date(task.start).getTime();
      const taskEndTs =
        task.end instanceof Date
          ? task.end.getTime()
          : new Date(task.end).getTime();
      const durationMs = taskEndTs - taskStartTs;
      const deltaMs = (event.delta.x / dynamicDayWidth) * MS_PER_DAY;
      const previewStart = taskStartTs + deltaMs;
      const previewEnd = previewStart + durationMs;

      // 같은 설비의 다른 task 들에 대해 cascade offset 계산
      const sameMachine = tasks
        .filter((t) => t.id !== task.id && t.equipment_id === targetEqId)
        .map((t) => ({
          id: t.id,
          start:
            t.start instanceof Date
              ? t.start.getTime()
              : new Date(t.start).getTime(),
          end:
            t.end instanceof Date ? t.end.getTime() : new Date(t.end).getTime(),
        }))
        .sort((a, b) => a.start - b.start);

      const offsets: Record<string, number> = {};

      // Forward push: 드래그 블록의 end 이후에 겹치는 블록을 오른쪽으로
      let fwdBoundary = previewEnd;
      for (const other of sameMachine) {
        if (other.start >= previewStart && fwdBoundary > other.start) {
          const pushMs = fwdBoundary - other.start;
          offsets[other.id] = pushMs; // 양수 = 오른쪽
          fwdBoundary = fwdBoundary + (other.end - other.start);
        }
      }

      // Backward push: 드래그 블록의 start 이전에 겹치는 블록을 왼쪽으로
      const reverseSorted = [...sameMachine].reverse();
      let bwdBoundary = previewStart;
      for (const other of reverseSorted) {
        if (other.end <= previewEnd && other.end > bwdBoundary) {
          const pushMs = other.end - bwdBoundary;
          offsets[other.id] = -pushMs; // 음수 = 왼쪽
          bwdBoundary = bwdBoundary - (other.end - other.start);
        }
      }

      if (Object.keys(offsets).length > 0) {
        setPreviewOffsets(offsets);
      } else {
        clearPreviewOffsets();
      }
    },
    [isEditMode, tasks, range, setPreviewOffsets, clearPreviewOffsets],
  );

  // 드래그 종료 — 4 분기 (inbox / batch_group / order / task|batch_group_task)
  const onDragEnd = useCallback(
    (event: DragEndEvent) => {
      setActiveDrag(null);
      clearPreviewOffsets();

      const { active, over } = event;
      if (!over) return;

      // 수정 모드가 아니면 경고 모달 표시
      if (!isEditMode) {
        onShowEditWarning();
        return;
      }

      const activeData = active.data.current;
      const overData = over.data.current;
      if (!activeData) return;

      // --- 분기 1: inbox dropzone 드롭 ---
      if (over.id === "inbox-dropzone") {
        if (
          activeData.type === "task" ||
          activeData.type === "batch_group_task"
        ) {
          const task = activeData.task as ScheduleTask | undefined;
          const bg = task?.batch_group;
          if (!bg) {
            useToastStore
              .getState()
              .show(
                "batch_group 에 속하지 않은 task 는 미배정 이동 불가",
                "warning",
              );
            return;
          }
          if (skipConfirmFromDrag) {
            void useScheduleStore.getState().unassignBatchGroup(bg, "기타");
            return;
          }
          onInboxDrop(bg);
        }
        return;
      }

      if (!overData) return;

      // 드롭 대상이 설비 행인지 확인
      if (overData.type !== "equipment-row") return;

      const targetEquipmentId = overData.equipmentId as string;

      // 동적 dayWidth 계산 (컨테이너 너비 기반)
      const MS_PER_DAY = 24 * 60 * 60 * 1000;
      const totalDays = Math.max((range.end - range.start) / MS_PER_DAY, 1);

      // --- 분기 2: BatchGroupCard → equipment-row 드롭 ---
      if (activeData.type === "batch_group") {
        const bgLabel = (
          activeData.group as { batch_group?: string } | undefined
        )?.batch_group;
        if (!bgLabel) return;
        let anchorStart: Date;
        const overRect = over.rect;
        if (event.delta && overRect) {
          const activatorEvent = event.activatorEvent as MouseEvent;
          if (activatorEvent) {
            const dropClientX = activatorEvent.clientX + event.delta.x;
            const relativeX = dropClientX - overRect.left;
            const dynamicDayWidth = overRect.width / totalDays;
            const timestamp = xToTime(relativeX, range.start, dynamicDayWidth);
            anchorStart = new Date(timestamp);
          } else {
            anchorStart = new Date();
          }
        } else {
          anchorStart = new Date();
        }
        void bgDropToEquipment(bgLabel, {
          equipmentCode: targetEquipmentId,
          anchorStart,
          isOrigin: false,
        });
        return;
      }

      // --- 분기 3: 수주를 스케줄러에 드롭 ---
      if (activeData.type === "order") {
        const order = activeData.order as Order;
        const rangeStart = range.start;

        let startTime: Date;
        if (event.delta) {
          const overRect = over.rect;
          if (overRect) {
            const activatorEvent = event.activatorEvent as MouseEvent;
            if (activatorEvent) {
              const dropClientX = activatorEvent.clientX + event.delta.x;
              const relativeX = dropClientX - overRect.left;
              const dynamicDayWidth = overRect.width / totalDays;
              const timestamp = xToTime(relativeX, rangeStart, dynamicDayWidth);
              startTime = new Date(timestamp);
            } else {
              startTime = new Date();
            }
          } else {
            startTime = new Date();
          }
        } else {
          startTime = new Date();
        }

        assignOrder(order.id, targetEquipmentId, startTime);
        return;
      }

      // --- 분기 4: 기존 task / batch_group_task → 단일 task 재배정 (cascade 경유) ---
      if (
        activeData.type === "task" ||
        activeData.type === "batch_group_task"
      ) {
        const task = activeData.task as ScheduleTask;

        const taskStartTs =
          task.start instanceof Date
            ? task.start.getTime()
            : new Date(task.start).getTime();
        const taskEndTs =
          task.end instanceof Date
            ? task.end.getTime()
            : new Date(task.end).getTime();
        const durationMs = taskEndTs - taskStartTs;

        const overWidth = over.rect?.width || 800;
        const dynamicDayWidth = overWidth / totalDays;
        const deltaMs = (event.delta.x / dynamicDayWidth) * MS_PER_DAY;
        const newStartTs = taskStartTs + deltaMs;

        const newStart = new Date(newStartTs);
        const newEnd = new Date(newStartTs + durationMs);

        // Date → naive ISO ("YYYY-MM-DDTHH:mm:ss"): backend TZ-guard 가 'Z'/'+HH:MM' 을 422 로 reject.
        // 로컬 타임존을 KST 로 간주하는 프로젝트 전제 아래, UTC 오프셋을 제거한 wall-clock 값을 전송.
        const toNaiveIso = (d: Date): string => {
          const tzOffsetMin = d.getTimezoneOffset();
          const local = new Date(d.getTime() - tzOffsetMin * 60_000);
          return local.toISOString().slice(0, 19);
        };
        cascadeCommit({
          task_id: task.id,
          new_start: toNaiveIso(newStart),
          new_end: toNaiveIso(newEnd),
          new_equipment_code: targetEquipmentId,
        });

        // 블록 변경 → AI explain 캐시 무효화 (영향받는 배치 재분석 필요)
        onResetExplainCache();

        // 선행 공정 이동 경고: 연선→절연→시스 체인에서 후행 공정이 있으면 경고 표시
        const bg = (task.batch_group || "").toLowerCase();
        const eq = equipment.find((e) => e.id === task.equipment_id);
        const processType = eq?.process_type?.toLowerCase() ?? "";
        const isStranding =
          bg.startsWith("연선") || processType === "stranding";
        const isInsulation =
          bg.startsWith("저압절연") ||
          bg.startsWith("고압절연") ||
          bg.startsWith("b100");
        if (isStranding || isInsulation) {
          const successorLabel = isStranding ? "절연/시스" : "시스";
          onPredecessorMoveWarning(
            `선행 공정 이동 시 후행 공정(${successorLabel})에 영향을 줄 수 있습니다`,
          );
        }
      }
    },
    [
      isEditMode,
      tasks,
      range,
      equipment,
      assignOrder,
      cascadeCommit,
      bgDropToEquipment,
      skipConfirmFromDrag,
      onShowEditWarning,
      onInboxDrop,
      onResetExplainCache,
      onPredecessorMoveWarning,
      clearPreviewOffsets,
    ],
  );

  return {
    sensors,
    activeDrag,
    activeDragGroup,
    activeDragSq,
    activeDragMaterial,
    onDragStart,
    onDragMove,
    onDragEnd,
  };
}
