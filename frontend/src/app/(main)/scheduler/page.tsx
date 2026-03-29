"use client";

import { useCallback, useMemo, useState, useEffect } from "react";
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
import { useScheduleData } from "@/features/scheduler/hooks/useScheduleData";
import { useScheduleStore } from "@/features/scheduler/store/scheduleStore";
import type { Order, ScheduleTask } from "@/features/scheduler/types";
import { xToTime, SIDEBAR_WIDTH } from "@/features/scheduler/utils/ganttUtils";

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

  // ── 자동배열 상태 ──
  const [autoScheduleLoading, setAutoScheduleLoading] = useState(false);
  const [autoScheduleResult, setAutoScheduleResult] = useState<string | null>(
    null,
  );

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
          `자동배열 완료 (런: ${runLabel}). 페이지를 새로고침하면 결과가 반영됩니다.`,
        );
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
  }, []);

  // 태스크 블록 클릭 → 감사 패널 열기
  const handleTaskClick = useCallback((taskId: string) => {
    setAuditPanel((prev) => ({
      ...prev,
      open: true,
      batchId: taskId,
      data: null,
      loading: true,
      error: null,
    }));
    fetch(`${API_BASE}/audit/explain/${encodeURIComponent(taskId)}`)
      .then(async (res) => {
        if (res.ok) {
          const data: AuditExplanation = await res.json();
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
        const rangeStart = range.start;

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
      }
    },
    [assignOrder, moveTask, zoomLevel, range, isEditMode],
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
            <SchedulerView activeDragGroup={activeDragGroup} />
            <ConstraintAlert />
          </div>

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

      {/* 감사 트레일 패널 — 태스크 블록 클릭 시 하단에 표시 */}
      {auditPanel.open && (
        <div
          className="shrink-0 border-t border-gray-200 bg-white"
          style={{ maxHeight: 220, overflowY: "auto" }}
        >
          {/* 패널 헤더 */}
          <div className="flex items-center justify-between px-4 py-2 border-b border-gray-100 bg-gray-50">
            <span className="text-[11px] font-semibold text-gray-700">
              AI 스케줄링 근거
              {auditPanel.batchId && (
                <span className="ml-2 text-gray-400 font-normal">
                  — {auditPanel.batchId}
                </span>
              )}
            </span>
            <button
              onClick={() => {
                setAuditPanel((prev) => ({ ...prev, open: false }));
                selectTask(null);
              }}
              className="text-gray-400 hover:text-gray-600 text-xs leading-none"
            >
              ✕
            </button>
          </div>

          {/* 패널 본문 */}
          <div className="px-4 py-3">
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
              <p className="text-[11px] text-red-500">{auditPanel.error}</p>
            )}
            {auditPanel.data && !auditPanel.loading && (
              <div className="flex flex-col gap-2">
                {(auditPanel.data.explanation || auditPanel.data.reasoning) && (
                  <p className="text-[11px] text-gray-700 leading-relaxed whitespace-pre-wrap">
                    {auditPanel.data.explanation ?? auditPanel.data.reasoning}
                  </p>
                )}
                {auditPanel.data.scheduled_at && (
                  <p className="text-[10px] text-gray-400">
                    배정 시각:{" "}
                    {new Date(auditPanel.data.scheduled_at).toLocaleString(
                      "ko-KR",
                    )}
                  </p>
                )}
                {auditPanel.data.changed_by && (
                  <p className="text-[10px] text-gray-400">
                    변경자: {auditPanel.data.changed_by}
                  </p>
                )}
                {/* 나머지 필드를 key-value로 표시 */}
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
                      <span className="font-medium">{k}</span>: {String(v)}
                    </p>
                  ))}
              </div>
            )}
          </div>
        </div>
      )}

      {/* 전역 오버레이 UI */}
      <ContextMenu />
      <TaskFormModal />
    </div>
  );
}
