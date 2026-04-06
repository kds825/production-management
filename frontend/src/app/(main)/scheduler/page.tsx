"use client";

import { useCallback, useMemo, useRef, useState, useEffect } from "react";
import { createPortal } from "react-dom";
import {
  DndContext,
  DragOverlay,
  PointerSensor,
  useSensor,
  useSensors,
  type DragStartEvent,
  type DragEndEvent,
  type DragMoveEvent,
} from "@dnd-kit/core";

import { Header } from "@/shared/components/Header";
import { CollapsiblePanel } from "@/shared/components/CollapsiblePanel";
import {
  SchedulerView,
  equipmentMatchesGroup,
} from "@/features/scheduler/components/SchedulerView";
import { ViewFilter } from "@/features/scheduler/components/ViewFilter";
import { OrderInbox } from "@/features/scheduler/components/OrderInbox";
import { ConstraintAlert } from "@/features/scheduler/components/ConstraintAlert";
import { ContextMenu } from "@/features/scheduler/components/ContextMenu";
import { TaskFormModal } from "@/features/scheduler/components/TaskFormModal";
import { ZoomControl } from "@/features/scheduler/components/ZoomControl";
import { SyncButton } from "@/features/scheduler/components/SyncButton";
import { WipUpdateModal } from "@/features/scheduler/components/WipUpdateModal";
import { BatchSplitModal } from "@/features/scheduler/components/BatchSplitModal";
import { ConflictResolutionModal } from "@/features/scheduler/components/ConflictResolutionModal";
import { useScheduleData } from "@/features/scheduler/hooks/useScheduleData";
import { useScheduleStore } from "@/features/scheduler/store/scheduleStore";
import type { Order, ScheduleTask } from "@/features/scheduler/types";
import { xToTime, computeTimeBreakdown } from "@/features/scheduler/utils/ganttUtils";

/** 드래그 중인 아이템 정보 */
interface ActiveDragItem {
  type: "order" | "task";
  order?: Order;
  task?: ScheduleTask;
}

const API_BASE = "http://localhost:8000/api";

/** 감사 설명 응답 (GET /api/audit/explain/{batch_id}) */
interface AuditExplanation {
  batch_id: string;
  explanation?: string;
  reasoning?: string;
  scheduled_at?: string;
  changed_by?: string;
  [key: string]: unknown;
}

/** 배치 그룹 내 개별 수주 (GET /api/pipeline/batch-group/{batch_group}/orders) */
interface BatchGroupOrder {
  batch_id: number;
  /** -1: 그룹 헤더(틀단위 집계), 0: 61연선 코어, 1+: 개별 수주 배치 */
  batch_seq: number;
  sales_order_id: string;
  spec_raw: string;
  sheath_color: string;
  customer_name: string;
  due_date: string;
  drum_length_m: number;
  drum_count: number;
  total_length_m: number;
  wip_matched_id: number | null;
  product_group: string;
  status: string;
}

export default function SchedulerPage() {
  const { isLoading, error } = useScheduleData();
  const openTaskFormModal = useScheduleStore((s) => s.openTaskFormModal);
  const isEditMode = useScheduleStore((s) => s.isEditMode);
  const toggleEditMode = useScheduleStore((s) => s.toggleEditMode);
  const saveVersion = useScheduleStore((s) => s.saveVersion);
  const showSavedToast = useScheduleStore((s) => s.showSavedToast);
  const assignOrder = useScheduleStore((s) => s.assignOrder);
  const moveTask = useScheduleStore((s) => s.moveTask);
  const setPreviewOffsets = useScheduleStore((s) => s.setPreviewOffsets);
  const clearPreviewOffsets = useScheduleStore((s) => s.clearPreviewOffsets);
  const tasks = useScheduleStore((s) => s.tasks);
  const equipment = useScheduleStore((s) => s.equipment);
  const zoomLevel = useScheduleStore((s) => s.zoomLevel);
  const range = useScheduleStore((s) => s.range);
  const unscheduledOrders = useScheduleStore((s) => s.unscheduledOrders);
  const cascadePreview = useScheduleStore((s) => s.cascadePreview);
  const conflictModalOpen = useScheduleStore((s) => s.conflictModalOpen);
  const cascadeOriginalTask = useScheduleStore((s) => s.cascadeOriginalTask);
  const applyCascade = useScheduleStore((s) => s.applyCascade);
  const cancelCascade = useScheduleStore((s) => s.cancelCascade);
  const setRunLabel = useScheduleStore((s) => s.setRunLabel);

  // ── SM재고 실적 모달 상태 ──
  const [showWipModal, setShowWipModal] = useState(false);
  const [wipRunLabel, setWipRunLabel] = useState<string | null>(null);

  // 최신 runLabel 조회 (모달 열기 시 사용) + scheduleStore에도 저장 (AI 재분석 트리거용)
  useEffect(() => {
    fetch(`${API_BASE}/pipeline/runs`)
      .then((r) => (r.ok ? r.json() : []))
      .then((runs: Array<{ run_label: string }>) => {
        if (runs.length > 0) {
          setWipRunLabel(runs[0].run_label);
          setRunLabel(runs[0].run_label);
        }
      })
      .catch(() => {});
  }, [setRunLabel]);

  // ── 선행 공정 이동 경고 토스트 ──
  const [predecessorToast, setPredecessorToast] = useState<string | null>(null);

  // ── 자동배열 상태 ──
  const [autoScheduleLoading, setAutoScheduleLoading] = useState(false);
  const [autoScheduleResult, setAutoScheduleResult] = useState<string | null>(
    null,
  );

  // ── 배치 그룹 수주 목록 상태 ──
  const [batchGroupOrders, setBatchGroupOrders] = useState<BatchGroupOrder[]>(
    [],
  );
  const [batchGroupLoading, setBatchGroupLoading] = useState(false);

  // ── 감사 트레일 패널 상태 ──
  const [auditPanel, setAuditPanel] = useState<{
    open: boolean;
    batchId: string | null;
    data: AuditExplanation | null;
    loading: boolean;
    error: string | null;
  }>({ open: false, batchId: null, data: null, loading: false, error: null });

  // 자동배열 실행 — 최신 런 라벨을 먼저 조회한 뒤 stage2 호출
  const handleAutoSchedule = useCallback(async () => {
    setAutoScheduleLoading(true);
    setAutoScheduleResult(null);
    try {
      // 최신 런 라벨 조회
      const runsRes = await fetch(`${API_BASE}/pipeline/runs`);
      let runLabel: string | null = null;
      if (runsRes.ok) {
        const runs: Array<{ run_label: string }> = await runsRes.json();
        if (runs.length > 0) runLabel = runs[0].run_label;
      }
      if (!runLabel) {
        setAutoScheduleResult(
          "실행 가능한 런이 없습니다. 먼저 Stage 1을 실행하세요.",
        );
        return;
      }
      // scheduleStore에 runLabel 저장 (AI 재분석 트리거용)
      setRunLabel(runLabel);
      const res = await fetch(`${API_BASE}/pipeline/stage2`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          run_label: runLabel,
          base_date:
            typeof window !== "undefined"
              ? localStorage.getItem("plan_base_date") || undefined
              : undefined,
        }),
      });
      if (res.ok) {
        setAutoScheduleResult(
          `자동배열 완료 (런: ${runLabel}). 새로고침 중...`,
        );
        // 1초 후 자동 새로고침
        setTimeout(() => window.location.reload(), 1000);
      } else {
        const text = await res.text();
        setAutoScheduleResult(`오류: ${res.status} — ${text.slice(0, 120)}`);
      }
    } catch {
      setAutoScheduleResult(
        `연결 실패: 백엔드 서버(localhost:8000)를 확인하세요.`,
      );
    } finally {
      setAutoScheduleLoading(false);
    }
  }, [setRunLabel]);

  // AI explain 캐시 — 같은 batch_id 재클릭 시 LLM 재호출 방지
  const explainCache = useRef<Record<string, AuditExplanation>>({});

  // 태스크 블록 클릭 → 감사 패널 열기 (캐시 우선)
  const handleTaskClick = useCallback((taskId: string) => {
    const numericId = taskId.replace(/\D/g, "");

    // 캐시 히트: LLM 호출 없이 즉시 표시
    if (explainCache.current[numericId]) {
      setAuditPanel({
        open: true,
        batchId: taskId,
        data: explainCache.current[numericId],
        loading: false,
        error: null,
      });
      return;
    }

    setAuditPanel({
      open: true,
      batchId: taskId,
      data: null,
      loading: true,
      error: null,
    });
    fetch(`${API_BASE}/audit/explain/${numericId}`)
      .then(async (res) => {
        if (res.ok) {
          const data: AuditExplanation = await res.json();
          // 캐시에 저장
          explainCache.current[numericId] = data;
          setAuditPanel((prev) => ({
            ...prev,
            loading: false,
            data,
          }));
        } else {
          setAuditPanel((prev) => ({
            ...prev,
            loading: false,
            error: `응답 오류: ${res.status}`,
          }));
        }
      })
      .catch(() => {
        setAuditPanel((prev) => ({
          ...prev,
          loading: false,
          error: "감사 정보를 가져올 수 없습니다.",
        }));
      });
  }, []);

  // selectedTaskId 변경 시 감사 패널 자동 열기
  const selectedTaskId = useScheduleStore((s) => s.selectedTaskId);
  const selectTask = useScheduleStore((s) => s.selectTask);
  useEffect(() => {
    if (!selectedTaskId) {
      setAuditPanel((prev) => ({ ...prev, open: false }));
      return;
    }
    handleTaskClick(selectedTaskId);
  }, [selectedTaskId, handleTaskClick]);

  // selectedTaskId 변경 시 batch_group 수주 목록 조회
  useEffect(() => {
    if (!selectedTaskId) {
      setBatchGroupOrders([]);
      return;
    }
    const selectedTask = tasks.find((t) => t.id === selectedTaskId);
    const bg = selectedTask?.batch_group;
    if (!bg) {
      setBatchGroupOrders([]);
      return;
    }
    setBatchGroupLoading(true);
    fetch(`${API_BASE}/pipeline/batch-group/${encodeURIComponent(bg)}/orders`)
      .then(async (res) => {
        if (res.ok) {
          const data: BatchGroupOrder[] = await res.json();
          setBatchGroupOrders(data);
        } else {
          setBatchGroupOrders([]);
        }
      })
      .catch(() => {
        setBatchGroupOrders([]);
      })
      .finally(() => {
        setBatchGroupLoading(false);
      });
  }, [selectedTaskId, tasks]);

  const [activeDrag, setActiveDrag] = useState<ActiveDragItem | null>(null);

  // 드래그 중인 아이템의 equipment_group 추론 — SchedulerView로 전달하여 비호환 행을 비활성화
  const activeDragGroup = useMemo<string | null>(() => {
    if (!activeDrag) return null;
    if (activeDrag.type === "order" && activeDrag.order) {
      return activeDrag.order.equipment_group ?? null;
    }
    if (activeDrag.type === "task" && activeDrag.task) {
      // 기존 task는 equipment_id로 설비를 찾아 그룹 추론
      const eq = equipment.find((e) => e.id === activeDrag.task!.equipment_id);
      if (!eq) return null;
      // equipment_group 키를 순서대로 시도
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
    // task에 material 필드가 있으면 우선 사용
    if (activeDrag.type === "task" && activeDrag.task?.material) {
      return activeDrag.task.material;
    }
    // order의 경우 product_group 등으로 추론 (rawData 존재 시)
    // 현재 Order 타입에 material이 없으므로 null 반환 — 배치 기반 task만 재질 검증
    return null;
  }, [activeDrag]);

  const [panelAnimating, setPanelAnimating] = useState(false);
  const [showEditWarning, setShowEditWarning] = useState(false);

  // 드래그 감도 설정 — 5px 이동 후 드래그 시작
  const sensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: { distance: 5 },
    }),
  );

  const handleAddTask = useCallback(() => {
    openTaskFormModal({ mode: "create" });
  }, [openTaskFormModal]);

  // 수정하기/저장하기 토글
  const handleToggleEditMode = useCallback(async () => {
    if (isEditMode) {
      await saveVersion();
    } else {
      toggleEditMode();
    }
  }, [isEditMode, saveVersion, toggleEditMode]);

  // 드래그 시작
  const handleDragStart = useCallback((event: DragStartEvent) => {
    const data = event.active.data.current;
    if (!data) return;

    if (data.type === "order") {
      // equipment_group이 drag data에 있으면 order 객체에 merge하여 activeDragGroup 계산에 활용
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
  const handleDragMove = useCallback(
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

      // 현재 드래그 위치에서 task의 예상 start/end 계산
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

      // 같은 설비의 다른 task들에 대해 cascade offset 계산
      // 대상: 현재 드래그 대상 설비에 있는 task + 드래그 전 설비에 있던 task 중 targetEqId와 같은 것
      const sameMachine = tasks
        .filter((t) => {
          if (t.id === task.id) return false;
          // 이미 targetEqId에 있거나, 드래그로 targetEqId에 올 예정인 것
          return t.equipment_id === targetEqId;
        })
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

  // 드래그 종료
  const handleDragEnd = useCallback(
    (event: DragEndEvent) => {
      setActiveDrag(null);
      clearPreviewOffsets();

      const { active, over } = event;
      if (!over) return;

      // 수정 모드가 아니면 경고 모달 표시
      if (!isEditMode) {
        setShowEditWarning(true);
        return;
      }

      const activeData = active.data.current;
      const overData = over.data.current;
      if (!activeData || !overData) return;

      // 드롭 대상이 설비 행인지 확인
      if (overData.type !== "equipment-row") return;

      const targetEquipmentId = overData.equipmentId as string;

      // 동적 dayWidth 계산 (컨테이너 너비 기반)
      const MS_PER_DAY = 24 * 60 * 60 * 1000;
      const totalDays = Math.max((range.end - range.start) / MS_PER_DAY, 1);

      // --- 수주를 스케줄러에 드롭 ---
      if (activeData.type === "order") {
        const order = activeData.order as Order;
        const rangeStart = range.start;

        // 드롭 위치에서 시간을 계산
        // delta.x가 드래그 시작점으로부터의 이동 거리이므로,
        // over 요소의 위치를 기반으로 시작 시각을 결정
        // 기본 동작: 현재 시각으로 배정
        let startTime: Date;

        if (event.delta) {
          // 드롭 위치의 x를 기반으로 시간 계산
          // over 요소(설비 행)의 rect를 사용하여 상대 위치 결정
          const overRect = over.rect;
          if (overRect) {
            const activatorEvent = event.activatorEvent as MouseEvent;
            if (activatorEvent) {
              const dropClientX = activatorEvent.clientX + event.delta.x;
              const relativeX = dropClientX - overRect.left;
              // overRect.width가 타임라인 영역 너비 — dayWidth를 동적 계산
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

      // --- 기존 작업을 다른 설비로 이동 ---
      if (activeData.type === "task") {
        const task = activeData.task as ScheduleTask;

        // 현재 작업의 duration을 유지한 채 새 위치로 이동
        const taskStartTs =
          task.start instanceof Date
            ? task.start.getTime()
            : new Date(task.start).getTime();
        const taskEndTs =
          task.end instanceof Date
            ? task.end.getTime()
            : new Date(task.end).getTime();
        const durationMs = taskEndTs - taskStartTs;

        // delta.x로 시간 이동량 계산 (동적 dayWidth)
        const overWidth = over.rect?.width || 800;
        const dynamicDayWidth = overWidth / totalDays;
        const deltaMs = (event.delta.x / dynamicDayWidth) * MS_PER_DAY;
        const newStartTs = taskStartTs + deltaMs;

        const newStart = new Date(newStartTs);
        const newEnd = new Date(newStartTs + durationMs);

        moveTask(task.id, targetEquipmentId, newStart, newEnd);

        // 블록 변경 → AI explain 캐시 무효화 (영향받는 배치 재분석 필요)
        explainCache.current = {};

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
          setPredecessorToast(
            `선행 공정 이동 시 후행 공정(${successorLabel})에 영향을 줄 수 있습니다`,
          );
          setTimeout(() => setPredecessorToast(null), 4000);
        }
      }
    },
    [assignOrder, moveTask, zoomLevel, range, isEditMode, equipment],
  );

  return (
    <div
      className="flex flex-col h-full overflow-hidden"
      style={{ backgroundColor: "#FAFAFA" }}
    >
      {/* 상단 헤더 */}
      <Header
        onAddTask={handleAddTask}
        isEditMode={isEditMode}
        onToggleEditMode={handleToggleEditMode}
      />

      {/* 뷰 필터 + 동기화 + 줌 컨트롤 + 자동배열 */}
      <div className="flex items-center bg-white border-b border-gray-200">
        <div className="flex-1">
          <ViewFilter />
        </div>
        <div className="px-4 py-2 shrink-0 border-l border-gray-200">
          <SyncButton />
        </div>
        <div className="px-4 py-2 shrink-0 border-l border-gray-200">
          <ZoomControl />
        </div>
        {/* 자동배열 버튼 */}
        <div className="px-3 py-2 shrink-0 border-l border-gray-200">
          <button
            onClick={handleAutoSchedule}
            disabled={autoScheduleLoading}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded text-[11px] font-medium text-white transition-opacity disabled:opacity-50"
            style={{ backgroundColor: "#C41230" }}
            title="최신 런에 대해 Stage 2 자동배열 실행"
          >
            {autoScheduleLoading ? (
              <span
                className="inline-block w-3 h-3 border-2 border-white border-t-transparent rounded-full"
                style={{ animation: "spin 1s linear infinite" }}
              />
            ) : (
              <svg
                width="12"
                height="12"
                viewBox="0 0 16 16"
                fill="currentColor"
              >
                <path d="M8 2a6 6 0 100 12A6 6 0 008 2zm0 1.5a4.5 4.5 0 110 9 4.5 4.5 0 010-9zm-.75 2v3.19l2.47 1.43.75-1.3L8.75 7.5V5.5h-1.5z" />
              </svg>
            )}
            자동배열
          </button>
        </div>
        {/* SM재고 실적 버튼 — 편집모드일 때만 표시 */}
        {isEditMode && (
          <div className="px-3 py-2 shrink-0 border-l border-gray-200">
            <button
              onClick={() => setShowWipModal(true)}
              disabled={!wipRunLabel}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded text-[11px] font-medium text-white transition-opacity disabled:opacity-50"
              style={{ backgroundColor: "#C41230" }}
              title="SM재고 실적 업데이트"
            >
              <svg
                width="12"
                height="12"
                viewBox="0 0 16 16"
                fill="currentColor"
              >
                <path d="M2 3h12v2H2V3zm0 4h12v2H2V7zm0 4h8v2H2v-2z" />
              </svg>
              SM재고 실적
            </button>
          </div>
        )}
      </div>

      {/* 자동배열 결과 알림 */}
      {autoScheduleResult && (
        <div
          className="flex items-start justify-between gap-2 px-4 py-2 border-b text-[11px]"
          style={{
            backgroundColor:
              autoScheduleResult.startsWith("오류") ||
              autoScheduleResult.startsWith("연결")
                ? "#FEF2F2"
                : "#F0FDF4",
            borderColor:
              autoScheduleResult.startsWith("오류") ||
              autoScheduleResult.startsWith("연결")
                ? "#FECACA"
                : "#BBF7D0",
            color:
              autoScheduleResult.startsWith("오류") ||
              autoScheduleResult.startsWith("연결")
                ? "#B91C1C"
                : "#15803D",
          }}
        >
          <span>{autoScheduleResult}</span>
          <button
            onClick={() => setAutoScheduleResult(null)}
            className="shrink-0 text-gray-400 hover:text-gray-600"
          >
            ✕
          </button>
        </div>
      )}

      {/* 로딩 / 에러 배너 */}
      {isLoading && (
        <div className="flex items-center justify-center gap-2 py-1.5 bg-blue-50 border-b border-blue-200">
          <div
            className="w-3 h-3 border-2 border-blue-400 border-t-transparent rounded-full"
            style={{ animation: "spin 1s linear infinite" }}
          />
          <span className="text-xs text-blue-600">
            스케줄 데이터 로드 중...
          </span>
        </div>
      )}

      {error && (
        <div className="flex items-center gap-2 px-4 py-1.5 bg-red-50 border-b border-red-200">
          <span className="text-xs text-red-600">{error}</span>
          <span className="text-xs text-gray-400">
            -- 백엔드 서버 연결을 확인하세요 (localhost:8000)
          </span>
        </div>
      )}

      {/* 편집 모드 표시 배너 */}
      {isEditMode && (
        <div className="flex items-center justify-center gap-2 py-1 bg-amber-50 border-b border-amber-200">
          <div
            className="w-2 h-2 rounded-full animate-pulse"
            style={{ backgroundColor: "#C41230" }}
          />
          <span className="text-xs font-medium" style={{ color: "#4A2C2A" }}>
            수정 모드 -- 작업 바를 드래그하여 이동하거나 하단 패널의 수주를
            드래그하여 배정하세요. 완료 후 저장하기를 클릭하세요.
          </span>
        </div>
      )}

      {/* 저장 완료 토스트 */}
      {showSavedToast && (
        <div
          className="fixed bottom-6 right-6 z-50 flex items-center gap-2 px-4 py-3 rounded-lg shadow-lg text-white text-sm font-medium"
          style={{ backgroundColor: "#16A34A" }}
        >
          <span>저장 완료 -- 새 버전이 생성되었습니다.</span>
        </div>
      )}

      {/* 선행 공정 이동 경고 토스트 */}
      {predecessorToast && (
        <div
          className="fixed bottom-6 left-1/2 z-50 flex items-center gap-2 px-4 py-3 rounded-lg shadow-lg text-sm font-medium"
          style={{
            backgroundColor: "#FFFBEB",
            color: "#92400E",
            border: "1px solid #FCD34D",
            transform: "translateX(-50%)",
          }}
        >
          <svg
            width="14"
            height="14"
            viewBox="0 0 16 16"
            fill="currentColor"
            style={{ color: "#D97706", flexShrink: 0 }}
          >
            <path d="M8 1L1 14h14L8 1zm0 2.5l5.5 9.5h-11L8 3.5zM7.25 7v3.5h1.5V7h-1.5zm0 4.5v1.5h1.5v-1.5h-1.5z" />
          </svg>
          <span>{predecessorToast}</span>
          <button
            onClick={() => setPredecessorToast(null)}
            className="ml-2 text-amber-400 hover:text-amber-600"
          >
            ✕
          </button>
        </div>
      )}

      {/* 메인 콘텐츠 영역 — DndContext가 OrderInbox와 SchedulerView를 모두 감싼다 */}
      <DndContext
        sensors={sensors}
        onDragStart={handleDragStart}
        onDragMove={handleDragMove}
        onDragEnd={handleDragEnd}
      >
        <main className="flex-1 overflow-hidden flex flex-col">
          {/* 간트 차트 + 제약 조건 */}
          <div
            className="flex-1 overflow-hidden flex flex-col p-3 gap-2"
            style={{ minHeight: 300 }}
          >
            <SchedulerView
              activeDragGroup={activeDragGroup}
              activeDragSq={activeDragSq}
              activeDragMaterial={activeDragMaterial}
            />
            <ConstraintAlert />
          </div>

          {/* 수주 상세 + 감사 트레일 패널 — 간트 차트 바로 아래, 미배정 작업 위 */}
          {auditPanel.open &&
            (() => {
              const selectedTask = selectedTaskId
                ? tasks.find((t) => t.id === selectedTaskId)
                : null;
              const selectedEquipment = selectedTask
                ? equipment.find((e) => e.id === selectedTask.equipment_id)
                : null;

              return (
                <div
                  className="shrink-0 border-t bg-white"
                  style={{
                    maxHeight: 240,
                    overflowY: "auto",
                    borderColor: "#E5E7EB",
                  }}
                >
                  {/* 패널 헤더 */}
                  <div
                    className="flex items-center justify-between px-4 py-2 border-b"
                    style={{
                      backgroundColor: "#FDF2F2",
                      borderColor: "#F3D5D5",
                    }}
                  >
                    <div className="flex items-center gap-2">
                      <div
                        className="w-1 h-4 rounded-sm"
                        style={{ backgroundColor: "#C41230" }}
                      />
                      <span
                        className="text-[11px] font-semibold"
                        style={{ color: "#4A2C2A" }}
                      >
                        {batchGroupOrders.filter((o) => o.batch_seq >= 1)
                          .length > 1
                          ? "배치 그룹 수주 목록"
                          : "수주 상세 정보"}
                      </span>
                      {selectedTask?.batch_group && (
                        <span
                          className="text-[10px] font-mono px-1.5 py-0.5 rounded"
                          style={{
                            backgroundColor: "#F3E8E8",
                            color: "#C41230",
                          }}
                        >
                          {selectedTask.batch_group}
                        </span>
                      )}
                    </div>
                    <button
                      onClick={() => {
                        setAuditPanel((prev) => ({ ...prev, open: false }));
                        selectTask(null);
                      }}
                      className="text-gray-400 hover:text-gray-600 text-xs leading-none px-1"
                    >
                      ✕
                    </button>
                  </div>

                  {/* 작업 요약 + 배치 그룹 수주 테이블 */}
                  {selectedTask && (
                    <div
                      className="px-4 py-3 border-b"
                      style={{ borderColor: "#F3F4F6" }}
                    >
                      {/* 작업 요약 1행 */}
                      <div className="grid grid-cols-7 gap-x-4 gap-y-1 mb-2">
                        <div className="flex flex-col gap-0.5">
                          <span className="text-[9px] font-medium text-gray-400 uppercase tracking-wider">
                            규격
                          </span>
                          <span
                            className="text-[11px] font-semibold"
                            style={{ color: "#1F2937" }}
                          >
                            {selectedTask.spec || "-"}
                          </span>
                        </div>
                        <div className="flex flex-col gap-0.5">
                          <span className="text-[9px] font-medium text-gray-400 uppercase tracking-wider">
                            배정 설비
                          </span>
                          <span
                            className="text-[11px] font-semibold"
                            style={{ color: "#1F2937" }}
                          >
                            {selectedEquipment?.name ||
                              selectedTask.equipment_id ||
                              "-"}
                          </span>
                        </div>
                        <div className="flex flex-col gap-0.5">
                          <span className="text-[9px] font-medium text-gray-400 uppercase tracking-wider">
                            총 길이
                          </span>
                          <span
                            className="text-[11px] font-semibold"
                            style={{ color: "#1F2937" }}
                          >
                            {selectedTask.volume_m
                              ? `${selectedTask.volume_m.toLocaleString()}m`
                              : "-"}
                          </span>
                        </div>
                        <div className="flex flex-col gap-0.5">
                          <span className="text-[9px] font-medium text-gray-400 uppercase tracking-wider">
                            작업 기간
                          </span>
                          <span className="text-[11px] text-gray-600">
                            {new Date(selectedTask.start).toLocaleString(
                              "ko-KR",
                              {
                                month: "numeric",
                                day: "numeric",
                                hour: "2-digit",
                                minute: "2-digit",
                              },
                            )}
                            {" ~ "}
                            {new Date(selectedTask.end).toLocaleString(
                              "ko-KR",
                              {
                                month: "numeric",
                                day: "numeric",
                                hour: "2-digit",
                                minute: "2-digit",
                              },
                            )}
                          </span>
                        </div>
                        <div className="flex flex-col gap-0.5">
                          <span className="text-[9px] font-medium text-gray-400 uppercase tracking-wider">
                            선속
                          </span>
                          <span className="text-[11px] text-gray-600">
                            {selectedTask.line_speed_m_per_min
                              ? `${selectedTask.line_speed_m_per_min}m/min`
                              : "-"}
                          </span>
                        </div>
                        <div className="flex flex-col gap-0.5">
                          <span className="text-[9px] font-medium text-gray-400 uppercase tracking-wider">
                            우선순위
                          </span>
                          <span
                            className="text-[11px] font-medium"
                            style={{
                              color:
                                selectedTask.priority === "critical"
                                  ? "#DC2626"
                                  : selectedTask.priority === "urgent"
                                    ? "#D97706"
                                    : "#6B7280",
                            }}
                          >
                            {selectedTask.priority === "critical"
                              ? "긴급"
                              : selectedTask.priority === "urgent"
                                ? "우선"
                                : "일반"}
                          </span>
                        </div>
                        <div className="flex flex-col gap-0.5">
                          <span className="text-[9px] font-medium text-gray-400 uppercase tracking-wider">
                            수주 건수
                          </span>
                          <span
                            className="text-[11px] font-semibold"
                            style={{ color: "#C41230" }}
                          >
                            {batchGroupOrders.filter((o) => o.batch_seq >= 1)
                              .length > 0
                              ? `${batchGroupOrders.filter((o) => o.batch_seq >= 1).length}건`
                              : "-"}
                          </span>
                        </div>
                      </div>

                      {/* 배치 그룹 수주 목록 테이블 */}
                      {batchGroupLoading ? (
                        <div className="flex items-center gap-2 py-2 text-[11px] text-gray-400">
                          <span
                            className="inline-block w-3 h-3 border-2 border-gray-300 border-t-transparent rounded-full"
                            style={{ animation: "spin 1s linear infinite" }}
                          />
                          수주 목록 로드 중...
                        </div>
                      ) : batchGroupOrders.length > 0 ? (
                        (() => {
                          // batch_seq=-1: 그룹 헤더(틀단위 집계), batch_seq>=1: 개별 수주
                          const headerBatch = batchGroupOrders.find(
                            (o) => o.batch_seq === -1,
                          );
                          const orderRows = batchGroupOrders.filter(
                            (o) => o.batch_seq >= 1,
                          );
                          const displayRows =
                            orderRows.length > 0 ? orderRows : batchGroupOrders;
                          // 총 생산지시 틀 수: 헤더 있으면 헤더의 drum_count, 없으면 개별 합계
                          const totalLots = headerBatch
                            ? headerBatch.drum_count
                            : displayRows.reduce((s, o) => s + o.drum_count, 0);
                          const totalQty = displayRows.reduce(
                            (s, o) => s + o.total_length_m,
                            0,
                          );
                          const wipCount = displayRows.filter(
                            (o) => o.wip_matched_id,
                          ).length;
                          return (
                            <div className="overflow-x-auto">
                              <table className="w-full text-[11px]">
                                <thead>
                                  <tr
                                    style={{
                                      backgroundColor: "#F9FAFB",
                                      borderBottom: "1px solid #E5E7EB",
                                    }}
                                  >
                                    <th className="text-left py-1 px-2 font-semibold text-gray-500 text-[10px] uppercase tracking-wider">
                                      수주번호
                                    </th>
                                    <th className="text-left py-1 px-2 font-semibold text-gray-500 text-[10px] uppercase tracking-wider">
                                      거래처
                                    </th>
                                    <th className="text-left py-1 px-2 font-semibold text-gray-500 text-[10px] uppercase tracking-wider">
                                      품명
                                    </th>
                                    <th className="text-left py-1 px-2 font-semibold text-gray-500 text-[10px] uppercase tracking-wider">
                                      규격
                                    </th>
                                    <th className="text-left py-1 px-2 font-semibold text-gray-500 text-[10px] uppercase tracking-wider">
                                      색상
                                    </th>
                                    <th className="text-left py-1 px-2 font-semibold text-gray-500 text-[10px] uppercase tracking-wider">
                                      납기
                                    </th>
                                    <th className="text-right py-1 px-2 font-semibold text-gray-500 text-[10px] uppercase tracking-wider">
                                      드럼
                                    </th>
                                    <th className="text-right py-1 px-2 font-semibold text-gray-500 text-[10px] uppercase tracking-wider">
                                      총 길이
                                    </th>
                                    <th className="text-center py-1 px-2 font-semibold text-gray-500 text-[10px] uppercase tracking-wider">
                                      WIP
                                    </th>
                                  </tr>
                                </thead>
                                <tbody>
                                  {displayRows.map((order) => (
                                    <tr
                                      key={order.batch_id}
                                      className="hover:bg-gray-50 transition-colors"
                                      style={{
                                        borderBottom: "1px solid #F3F4F6",
                                      }}
                                    >
                                      <td className="py-1 px-2 font-mono text-gray-700">
                                        {order.sales_order_id || "-"}
                                      </td>
                                      <td className="py-1 px-2 text-gray-700">
                                        {order.customer_name || "-"}
                                      </td>
                                      <td className="py-1 px-2 text-gray-600">
                                        {order.product_group || "-"}
                                      </td>
                                      <td className="py-1 px-2 text-gray-600">
                                        {order.spec_raw}
                                      </td>
                                      <td className="py-1 px-2 text-gray-600">
                                        {order.sheath_color || "-"}
                                      </td>
                                      <td
                                        className="py-1 px-2"
                                        style={{
                                          color:
                                            order.due_date &&
                                            new Date(
                                              order.due_date,
                                            ).getTime() < Date.now()
                                              ? "#DC2626"
                                              : "#4B5563",
                                        }}
                                      >
                                        {order.due_date
                                          ? new Date(
                                              order.due_date,
                                            ).toLocaleDateString("ko-KR")
                                          : "-"}
                                      </td>
                                      <td className="py-1 px-2 text-right text-gray-600">
                                        {order.drum_count} x{" "}
                                        {order.drum_length_m.toLocaleString()}m
                                      </td>
                                      <td className="py-1 px-2 text-right font-medium text-gray-700">
                                        {order.total_length_m.toLocaleString()}
                                        m
                                      </td>
                                      <td className="py-1 px-2 text-center">
                                        {order.wip_matched_id ? (
                                          <span
                                            className="inline-block px-1.5 py-0.5 rounded text-[9px] font-medium"
                                            style={{
                                              backgroundColor: "#DCFCE7",
                                              color: "#16A34A",
                                            }}
                                          >
                                            매칭
                                          </span>
                                        ) : (
                                          <span className="text-gray-300">
                                            -
                                          </span>
                                        )}
                                      </td>
                                    </tr>
                                  ))}
                                </tbody>
                                {/* 합계 행 — 연선은 헤더의 lot_count(생산지시 틀 수) 표시 */}
                                <tfoot>
                                  <tr
                                    style={{
                                      borderTop: "2px solid #E5E7EB",
                                      backgroundColor: "#FDF2F2",
                                    }}
                                  >
                                    <td
                                      colSpan={6}
                                      className="py-1 px-2 font-semibold"
                                      style={{ color: "#C41230" }}
                                    >
                                      합계 {displayRows.length}건
                                      {headerBatch && (
                                        <span className="ml-2 text-[10px] font-normal text-gray-500">
                                          (생산지시 {totalLots}틀 /{" "}
                                          {headerBatch.drum_length_m.toLocaleString()}
                                          m×{totalLots})
                                        </span>
                                      )}
                                    </td>
                                    <td className="py-1 px-2 text-right font-medium text-gray-500">
                                      {totalLots}틀
                                    </td>
                                    <td
                                      className="py-1 px-2 text-right font-semibold"
                                      style={{ color: "#C41230" }}
                                    >
                                      {totalQty.toLocaleString()}m
                                    </td>
                                    <td className="py-1 px-2 text-center text-[10px] text-gray-400">
                                      {wipCount}건
                                    </td>
                                  </tr>
                                </tfoot>
                              </table>
                            </div>
                          );
                        })()
                      ) : (
                        /* batch_group가 없거나 API 실패 시 기존 단일 수주 정보 표시 */
                        <div className="grid grid-cols-6 gap-x-4 gap-y-1">
                          <div className="flex flex-col gap-0.5">
                            <span className="text-[9px] font-medium text-gray-400 uppercase tracking-wider">
                              수주번호
                            </span>
                            <span className="text-[11px] text-gray-700">
                              {selectedTask.order_id || "-"}
                            </span>
                          </div>
                          <div className="flex flex-col gap-0.5">
                            <span className="text-[9px] font-medium text-gray-400 uppercase tracking-wider">
                              거래처
                            </span>
                            <span className="text-[11px] text-gray-700">
                              {selectedTask.customer || "-"}
                            </span>
                          </div>
                          <div className="flex flex-col gap-0.5">
                            <span className="text-[9px] font-medium text-gray-400 uppercase tracking-wider">
                              제품군
                            </span>
                            <span className="text-[11px] text-gray-700">
                              {selectedTask.product || "-"}
                            </span>
                          </div>
                          <div className="flex flex-col gap-0.5">
                            <span className="text-[9px] font-medium text-gray-400 uppercase tracking-wider">
                              색상
                            </span>
                            <span className="text-[11px] text-gray-700">
                              {selectedTask.color || "-"}
                            </span>
                          </div>
                          <div className="flex flex-col gap-0.5">
                            <span className="text-[9px] font-medium text-gray-400 uppercase tracking-wider">
                              납기
                            </span>
                            <span
                              className="text-[11px]"
                              style={{
                                color: selectedTask.delivery_date
                                  ? new Date(
                                      selectedTask.delivery_date,
                                    ).getTime() < Date.now()
                                    ? "#DC2626"
                                    : "#4B5563"
                                  : "#9CA3AF",
                              }}
                            >
                              {selectedTask.delivery_date
                                ? new Date(
                                    selectedTask.delivery_date,
                                  ).toLocaleDateString("ko-KR")
                                : "-"}
                            </span>
                          </div>
                          <div className="flex flex-col gap-0.5">
                            <span className="text-[9px] font-medium text-gray-400 uppercase tracking-wider">
                              길이
                            </span>
                            <span className="text-[11px] text-gray-700">
                              {selectedTask.volume_m
                                ? `${selectedTask.volume_m.toLocaleString()}m`
                                : "-"}
                            </span>
                          </div>
                        </div>
                      )}
                    </div>
                  )}

                  {/* 작업 시간 구성 */}
                  {selectedTask && (() => {
                    const startTs = new Date(selectedTask.start).getTime();
                    const endTs = new Date(selectedTask.end).getTime();
                    const setupMin = (selectedTask as { setup_time_min?: number }).setup_time_min ?? selectedTask.changeover_min ?? 0;
                    const colorChangeMin = (selectedTask as { color_change_min?: number }).color_change_min ?? 0;
                    const tb = computeTimeBreakdown(startTs, endTs, setupMin + colorChangeMin);
                    const totalHrs = ((endTs - startTs) / (60 * 60 * 1000)).toFixed(1);
                    return (
                      <div className="px-4 py-2 border-t" style={{ borderColor: "#F3F4F6" }}>
                        <div className="flex items-center gap-1.5 mb-1.5">
                          <svg width="10" height="10" viewBox="0 0 16 16" fill="#9CA3AF">
                            <path d="M8 1a7 7 0 100 14A7 7 0 008 1zm-.75 4v4.25l3 1.75.75-1.3-2.5-1.45V5h-1.25z" />
                          </svg>
                          <span className="text-[10px] font-medium text-gray-400">작업 시간 구성</span>
                        </div>
                        <div className="flex flex-wrap gap-x-5 gap-y-1 text-[11px]">
                          <span><span className="text-gray-400">총 기간</span> <span className="font-medium text-gray-700">{totalHrs}h</span></span>
                          <span><span className="text-gray-400">실제 작업</span> <span className="font-medium" style={{ color: "#C41230" }}>{tb.actualWork.toFixed(1)}h</span></span>
                          {tb.weekendHrs > 0 && <span><span className="text-gray-400">주말 휴무</span> <span className="text-gray-600">{tb.weekendHrs}h</span></span>}
                          <span><span className="text-gray-400">일일 부동</span> <span className="text-gray-600">{tb.dailyIdleHrs}h</span></span>
                          {setupMin > 0 && <span><span className="text-gray-400">규격교체</span> <span className="text-gray-600">{setupMin}분</span></span>}
                          {colorChangeMin > 0 && <span><span className="text-gray-400">색상교체</span> <span className="text-gray-600">{colorChangeMin}분</span></span>}
                        </div>
                      </div>
                    );
                  })()}

                  {/* AI 스케줄링 근거 */}
                  <div className="px-4 py-2">
                    <div className="flex items-center gap-1.5 mb-1.5">
                      <svg
                        width="10"
                        height="10"
                        viewBox="0 0 16 16"
                        fill="#9CA3AF"
                      >
                        <path d="M8 1a7 7 0 100 14A7 7 0 008 1zm0 2a1 1 0 110 2 1 1 0 010-2zm-1 4h2v5H7V7z" />
                      </svg>
                      <span className="text-[10px] font-medium text-gray-400">
                        AI 스케줄링 근거
                      </span>
                    </div>
                    {auditPanel.loading && (
                      <div className="flex items-center gap-2 text-[11px] text-gray-400">
                        <span
                          className="inline-block w-3 h-3 border-2 border-gray-300 border-t-transparent rounded-full"
                          style={{ animation: "spin 1s linear infinite" }}
                        />
                        AI 설명 로드 중...
                      </div>
                    )}
                    {auditPanel.error && !auditPanel.loading && (
                      <p className="text-[11px] text-gray-400 italic">
                        스케줄링 근거를 불러올 수 없습니다.
                      </p>
                    )}
                    {auditPanel.data && !auditPanel.loading && (
                      <div className="flex flex-col gap-1">
                        {(auditPanel.data.explanation ||
                          auditPanel.data.reasoning) && (
                          <p className="text-[11px] text-gray-600 leading-relaxed whitespace-pre-wrap">
                            {auditPanel.data.explanation ??
                              auditPanel.data.reasoning}
                          </p>
                        )}
                        {auditPanel.data.scheduled_at && (
                          <p className="text-[10px] text-gray-400">
                            배정 시각:{" "}
                            {new Date(
                              auditPanel.data.scheduled_at,
                            ).toLocaleString("ko-KR")}
                          </p>
                        )}
                        {auditPanel.data.changed_by && (
                          <p className="text-[10px] text-gray-400">
                            변경자: {auditPanel.data.changed_by}
                          </p>
                        )}
                        {Object.entries(auditPanel.data)
                          .filter(
                            ([k]) =>
                              ![
                                "batch_id",
                                "explanation",
                                "reasoning",
                                "scheduled_at",
                                "changed_by",
                              ].includes(k),
                          )
                          .map(([k, v]) => (
                            <p key={k} className="text-[10px] text-gray-500">
                              <span className="font-medium">{k}</span>:{" "}
                              {String(v)}
                            </p>
                          ))}
                      </div>
                    )}
                  </div>
                </div>
              );
            })()}

          {/* 하단 미배정 수주 패널 */}
          <CollapsiblePanel
            title="미배정 작업"
            count={unscheduledOrders.length}
            defaultExpanded={unscheduledOrders.length > 0}
            onAnimatingChange={setPanelAnimating}
          >
            <OrderInbox isAnimating={panelAnimating} />
          </CollapsiblePanel>
        </main>

        {/* 드래그 오버레이 — 드래그 중인 아이템 미리보기 */}
        <DragOverlay dropAnimation={null}>
          {activeDrag?.type === "order" && activeDrag.order && (
            <div
              className="px-3 py-2 rounded-lg shadow-xl text-white text-xs font-semibold"
              style={{
                backgroundColor: "#C41230",
                minWidth: 120,
                pointerEvents: "none",
              }}
            >
              {activeDrag.order.product} {activeDrag.order.spec}
              <br />
              <span className="text-white/80 text-[9px]">
                {activeDrag.order.total_length_m.toLocaleString()}m
              </span>
            </div>
          )}
          {activeDrag?.type === "task" && activeDrag.task && (
            <div
              className="px-3 py-2 rounded-lg shadow-xl text-white text-xs font-semibold"
              style={{
                backgroundColor: "#4A2C2A",
                minWidth: 120,
                pointerEvents: "none",
              }}
            >
              {activeDrag.task.product} {activeDrag.task.spec}
              <br />
              <span className="text-white/80 text-[9px]">
                {activeDrag.task.volume_m.toLocaleString()}m
              </span>
            </div>
          )}
        </DragOverlay>
      </DndContext>

      {/* 수정 모드 경고 모달 — Portal로 body 레벨에 렌더링 */}
      {showEditWarning &&
        typeof document !== "undefined" &&
        createPortal(
          <div
            style={{
              position: "fixed",
              inset: 0,
              zIndex: 99999,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              backgroundColor: "rgba(0,0,0,0.3)",
            }}
            onClick={() => setShowEditWarning(false)}
          >
            <div
              onClick={(e) => e.stopPropagation()}
              style={{
                backgroundColor: "#FFFFFF",
                borderRadius: 8,
                padding: "28px 32px",
                boxShadow: "0 8px 32px rgba(0,0,0,0.18)",
                border: "1px solid #E5E7EB",
                minWidth: 420,
                maxWidth: 500,
              }}
            >
              <p
                className="text-sm font-semibold mb-2"
                style={{ color: "#1F2937" }}
              >
                수정 모드를 활성화해주세요
              </p>
              <p className="text-xs text-gray-500 mb-5 leading-relaxed">
                작업을 배정하거나 이동하려면 우측 상단의 "수정하기" 버튼을 먼저
                눌러주세요.
              </p>
              <div className="flex justify-end gap-2">
                <button
                  onClick={() => setShowEditWarning(false)}
                  className="px-4 py-2 text-xs font-medium rounded-md transition-colors"
                  style={{
                    border: "1px solid #E5E7EB",
                    color: "#6B7280",
                  }}
                >
                  닫기
                </button>
                <button
                  onClick={() => {
                    setShowEditWarning(false);
                    toggleEditMode();
                  }}
                  className="px-4 py-2 text-xs font-medium rounded-md text-white transition-colors"
                  style={{ backgroundColor: "#C41230" }}
                >
                  수정하기
                </button>
              </div>
            </div>
          </div>,
          document.body,
        )}

      {/* 전역 오버레이 UI */}
      <ContextMenu />
      <TaskFormModal />

      {/* SM재고 실적 모달 */}
      {showWipModal && wipRunLabel && (
        <WipUpdateModal
          runLabel={wipRunLabel}
          onClose={() => setShowWipModal(false)}
        />
      )}

      {/* 배치 분할 모달 */}
      <BatchSplitModal />

      {/* Cross-process cascade 충돌 해소 모달 */}
      {conflictModalOpen &&
        cascadePreview &&
        (() => {
          const movedTaskId = cascadeOriginalTask?.id ?? "";
          const movedTaskData = tasks.find((t) => t.id === movedTaskId);
          const movedEquip = movedTaskData
            ? equipment.find((e) => e.id === movedTaskData.equipment_id)
            : null;
          return (
            <ConflictResolutionModal
              preview={cascadePreview}
              movedTask={{
                id: movedTaskId,
                spec: movedTaskData?.spec ?? "",
                process:
                  movedEquip?.process_type ?? movedTaskData?.batch_group ?? "",
              }}
              onApply={() => {
                applyCascade(cascadePreview);
                explainCache.current = {}; // cascade 적용 → 캐시 무효화
              }}
              onCancel={cancelCascade}
            />
          );
        })()}
    </div>
  );
}
