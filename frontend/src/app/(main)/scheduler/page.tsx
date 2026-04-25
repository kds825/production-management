"use client";

import { useCallback, useMemo, useState } from "react";
import { DndContext, DragOverlay } from "@dnd-kit/core";

import { Header } from "@/shared/components/Header";
import { CollapsiblePanel } from "@/shared/components/CollapsiblePanel";
import { SchedulerView } from "@/features/scheduler/components/SchedulerView";
import { OrderInbox } from "@/features/scheduler/components/OrderInbox";
import { ConstraintAlert } from "@/features/scheduler/components/ConstraintAlert";
import { OverlapAlertBanner } from "@/features/scheduler/components/OverlapAlertBanner";
import { ContextMenu } from "@/features/scheduler/components/ContextMenu";
import { TaskFormModal } from "@/features/scheduler/components/TaskFormModal";
import { WipUpdateModal } from "@/features/scheduler/components/WipUpdateModal";
import { BatchSplitModal } from "@/features/scheduler/components/BatchSplitModal";
// Task 21 — cascade v2.
// Task 19 에서 Task 16/17 기반으로 교체된 ConflictResolutionModal 을 `useScheduleChangeWithCascade`
// 훅으로 wiring. FEATURE_FLAG off 시 legacyMove 콜백이 store.moveTask 로 fallback.
import { ConflictResolutionModal } from "@/features/scheduler/components/ConflictResolutionModal";
import { useScheduleChangeWithCascade } from "@/features/scheduler/hooks/useScheduleChangeWithCascade";
import { refreshTasks as refreshScheduleTasks } from "@/features/scheduler/hooks/useScheduleData";
import { useBatchGroupDrag } from "@/features/scheduler/hooks/useBatchGroupDrag";
import { useScheduleStore } from "@/features/scheduler/store/scheduleStore";
import type { Order } from "@/features/scheduler/types";
import { UnassignConfirmModal } from "@/features/scheduler/components/UnassignConfirmModal";
import { BatchGroupDragPreviewModal } from "@/features/scheduler/components/BatchGroupDragPreviewModal";

// ── Week 7 Task 7B.1: 페이지 분리 — 응집된 hooks + page-sections ──
import { useSchedulerData } from "@/features/scheduler/hooks/useSchedulerData";
import { useSchedulerKeyboard } from "@/features/scheduler/hooks/useSchedulerKeyboard";
import { useBatchCompareMode } from "@/features/scheduler/hooks/useBatchCompareMode";
import { useAutoSchedule } from "@/features/scheduler/hooks/useAutoSchedule";
import { useBatchInspector } from "@/features/scheduler/hooks/useBatchInspector";
import { useSchedulerDnd } from "@/features/scheduler/hooks/useSchedulerDnd";
import { SchedulerToolbar } from "@/features/scheduler/page-sections/SchedulerToolbar";
import { SchedulerBanners } from "@/features/scheduler/page-sections/SchedulerBanners";
import { LateTasksPanel } from "@/features/scheduler/page-sections/LateTasksPanel";
import { BatchInspector } from "@/features/scheduler/page-sections/BatchInspector";
import { CompareDetailsModal } from "@/features/scheduler/page-sections/CompareDetailsModal";
import { EditWarningModal } from "@/features/scheduler/page-sections/EditWarningModal";

export default function SchedulerPage() {
  // ── 데이터 라이프사이클 hooks ───────────────────────────────────
  const { isLoading, error, wipRunLabel, lateTasks } = useSchedulerData();
  const inspector = useBatchInspector();
  const compare = useBatchCompareMode();
  const autoSched = useAutoSchedule();

  // ── store 셀렉터 ───────────────────────────────────────────────
  const openTaskFormModal = useScheduleStore((s) => s.openTaskFormModal);
  const isEditMode = useScheduleStore((s) => s.isEditMode);
  const toggleEditMode = useScheduleStore((s) => s.toggleEditMode);
  const discardEdits = useScheduleStore((s) => s.discardEdits);
  const saveVersion = useScheduleStore((s) => s.saveVersion);
  const showSavedToast = useScheduleStore((s) => s.showSavedToast);
  const tasks = useScheduleStore((s) => s.tasks);
  const unscheduledItems = useScheduleStore((s) => s.unscheduledItems);

  // 미배정 패널 카운트는 "order" kind 만 (batch_group kind 는 별도 집계)
  const unscheduledOrderItems = useMemo<Order[]>(
    () =>
      unscheduledItems
        .filter((i): i is { kind: "order"; order: Order } => i.kind === "order")
        .map((i) => i.order),
    [unscheduledItems],
  );

  // 비교 모드 상태 셀렉터 (toolbar/modal 에 그대로 전달)
  const compareMode = useScheduleStore((s) => s.compareMode);
  const toggleCompareFilter = useScheduleStore((s) => s.toggleCompareFilter);
  const closeCompareMode = useScheduleStore((s) => s.closeCompareMode);

  // ESC 로 비교 모드 종료
  useSchedulerKeyboard({
    compareModeEnabled: compareMode.enabled,
    onEscapeCompareMode: closeCompareMode,
  });

  // ── cascade v2 훅 ────────────────────────────────────────────
  /**
   * legacyMove 어댑터 — FEATURE_FLAG off 시 store.moveTask 로 fallback.
   * ChangeInput (ISO 문자열) → moveTask(taskId, eqId, Date, Date) 시그니처 정합.
   */
  const moveTask = useScheduleStore((s) => s.moveTask);
  const legacyMoveAdapter = useCallback(
    (input: {
      task_id: string;
      new_start: string;
      new_end: string;
      new_equipment_code?: string | null;
    }) => {
      const currentTask = tasks.find((t) => t.id === input.task_id);
      const targetEquipmentId =
        input.new_equipment_code ?? currentTask?.equipment_id ?? "";
      moveTask(
        input.task_id,
        targetEquipmentId,
        new Date(input.new_start),
        new Date(input.new_end),
      );
    },
    [moveTask, tasks],
  );

  const cascade = useScheduleChangeWithCascade({
    legacyMove: legacyMoveAdapter,
    onCommitted: async () => {
      await refreshScheduleTasks();
    },
  });

  // Task 4.3 — batch_group 드래그 orchestration 훅.
  const bgDrag = useBatchGroupDrag({
    onCommitted: async () => {
      await refreshScheduleTasks();
    },
  });

  // ── 페이지 레벨 보조 상태 ─────────────────────────────────────────
  // Task 4.3 — inbox 드롭 UnassignConfirmModal 상태.
  const [unassignFromDragState, setUnassignFromDragState] = useState<{
    batchGroup: string;
  } | null>(null);
  const [skipConfirmFromDrag, setSkipConfirmFromDrag] = useState(false);

  // Task 22 — ConflictResolutionModal row hover → 간트 블록 focus-ring 하이라이트.
  const [hoveredTaskId, setHoveredTaskId] = useState<string | null>(null);

  // SM재고 실적 모달
  const [showWipModal, setShowWipModal] = useState(false);

  // 선행 공정 이동 경고 토스트 (4초 자동 dismiss)
  const [predecessorToast, setPredecessorToast] = useState<string | null>(null);

  // 납기 초과 패널 가시성
  const [showLatePanel, setShowLatePanel] = useState(false);

  // CollapsiblePanel 애니메이션 상태
  const [panelAnimating, setPanelAnimating] = useState(false);

  // 수정 모드 경고 모달 가시성
  const [showEditWarning, setShowEditWarning] = useState(false);

  // ── DnD 핸들러 hook (sensors / drag state / handlers) ─────────────
  const dnd = useSchedulerDnd({
    cascadeCommit: cascade.commit,
    bgDropToEquipment: bgDrag.onDropToEquipment,
    onShowEditWarning: () => setShowEditWarning(true),
    onInboxDrop: (bg) => setUnassignFromDragState({ batchGroup: bg }),
    skipConfirmFromDrag,
    onResetExplainCache: inspector.resetExplainCache,
    onPredecessorMoveWarning: (msg) => {
      setPredecessorToast(msg);
      setTimeout(() => setPredecessorToast(null), 4000);
    },
  });

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
        onDiscardEdits={discardEdits}
      />

      {/* 겹침 경고 배너 — Stage 2 overlap_alert 응답 시에만 노출, 헤더 바로 아래 최상단 */}
      {autoSched.overlapAlert && (
        <OverlapAlertBanner
          message={autoSched.overlapAlert}
          onDismiss={() => autoSched.setOverlapAlert(null)}
        />
      )}

      {/* 뷰 필터 + 동기화 + 줌 컨트롤 + 자동배열 + 비교 모드 + 납기초과 + SM재고 */}
      <SchedulerToolbar
        autoScheduleLoading={autoSched.autoScheduleLoading}
        onAutoSchedule={autoSched.handleAutoSchedule}
        compareModeEnabled={compareMode.enabled}
        compareModeLoading={compareMode.loading}
        compareModeError={compareMode.error}
        diffResponse={compareMode.diffResponse}
        diffFilters={compareMode.filters}
        onToggleCompareMode={compare.handleToggleCompareMode}
        onToggleCompareFilter={toggleCompareFilter}
        onOpenCompareList={() => compare.setCompareOpen(true)}
        lateTaskCount={lateTasks.length}
        showLatePanel={showLatePanel}
        onToggleLatePanel={() => setShowLatePanel((v) => !v)}
        isEditMode={isEditMode}
        wipRunLabel={wipRunLabel}
        onOpenWipModal={() => setShowWipModal(true)}
      />

      {/* 자동배열 결과 / 로딩·에러 / 편집 모드 / 저장 토스트 / 선행 공정 경고 */}
      <SchedulerBanners
        autoScheduleResult={autoSched.autoScheduleResult}
        onDismissAutoSchedule={() => autoSched.setAutoScheduleResult(null)}
        isLoading={isLoading}
        error={error}
        isEditMode={isEditMode}
        showSavedToast={showSavedToast}
        predecessorToast={predecessorToast}
        onDismissPredecessor={() => setPredecessorToast(null)}
      />

      {/* 납기 초과 패널 */}
      {showLatePanel && (
        <LateTasksPanel
          lateTasks={lateTasks}
          onClose={() => setShowLatePanel(false)}
        />
      )}

      {/* 메인 콘텐츠 영역 — DndContext 가 OrderInbox 와 SchedulerView 를 모두 감싼다 */}
      <DndContext
        sensors={dnd.sensors}
        onDragStart={dnd.onDragStart}
        onDragMove={dnd.onDragMove}
        onDragEnd={dnd.onDragEnd}
      >
        <main className="flex-1 overflow-hidden flex flex-col">
          {/* 간트 차트 + 우측 상세 패널 */}
          <div className="flex-1 overflow-hidden flex flex-row min-h-0">
            <div
              className="flex-1 overflow-hidden flex flex-col p-3 gap-2 min-w-0"
              style={{ minHeight: 300 }}
            >
              <SchedulerView
                activeDragGroup={dnd.activeDragGroup}
                activeDragSq={dnd.activeDragSq}
                activeDragMaterial={dnd.activeDragMaterial}
                previewOverlay={cascade.modalState?.preview ?? null}
                focusedTaskId={hoveredTaskId}
              />
              <ConstraintAlert />
            </div>

            {/* 수주 상세 + 감사 트레일 패널 — 간트 차트 우측 */}
            <BatchInspector
              auditPanel={inspector.auditPanel}
              setAuditPanel={inspector.setAuditPanel}
              batchGroupOrders={inspector.batchGroupOrders}
              batchGroupLoading={inspector.batchGroupLoading}
              processFlow={inspector.processFlow}
              onNavigateToProcessBatch={inspector.navigateToProcessBatch}
            />
          </div>

          {/* 하단 미배정 수주 패널 */}
          <CollapsiblePanel
            title="미배정 작업"
            count={unscheduledOrderItems.length}
            defaultExpanded={unscheduledOrderItems.length > 0}
            onAnimatingChange={setPanelAnimating}
          >
            <OrderInbox isAnimating={panelAnimating} />
          </CollapsiblePanel>
        </main>

        {/* 드래그 오버레이 — 드래그 중인 아이템 미리보기 */}
        <DragOverlay dropAnimation={null}>
          {dnd.activeDrag?.type === "order" && dnd.activeDrag.order && (
            <div
              className="px-3 py-2 rounded-lg shadow-xl text-white text-xs font-semibold"
              style={{
                backgroundColor: "#C41230",
                minWidth: 120,
                pointerEvents: "none",
              }}
            >
              {dnd.activeDrag.order.product} {dnd.activeDrag.order.spec}
              <br />
              <span className="text-white/80 text-[9px]">
                {dnd.activeDrag.order.total_length_m.toLocaleString()}m
              </span>
            </div>
          )}
          {dnd.activeDrag?.type === "task" && dnd.activeDrag.task && (
            <div
              className="px-3 py-2 rounded-lg shadow-xl text-white text-xs font-semibold"
              style={{
                backgroundColor: "#4A2C2A",
                minWidth: 120,
                pointerEvents: "none",
              }}
            >
              {dnd.activeDrag.task.product} {dnd.activeDrag.task.spec}
              <br />
              <span className="text-white/80 text-[9px]">
                {dnd.activeDrag.task.volume_m.toLocaleString()}m
              </span>
            </div>
          )}
        </DragOverlay>
      </DndContext>

      {/* 수정 모드 경고 모달 */}
      <EditWarningModal
        isOpen={showEditWarning}
        onClose={() => setShowEditWarning(false)}
        onEnableEditMode={toggleEditMode}
      />

      {/* 전역 오버레이 UI */}
      <ContextMenu />
      {/* Task 21 — TaskFormModal edit 경로에서 cascade 훅 commit 주입. */}
      <TaskFormModal onSubmitWithCascade={cascade.commit} />

      {/* SM재고 실적 모달 */}
      {showWipModal && wipRunLabel && (
        <WipUpdateModal
          runLabel={wipRunLabel}
          onClose={() => setShowWipModal(false)}
        />
      )}

      {/* 배치 분할 모달 */}
      <BatchSplitModal />

      {/* Task 21 — cascade v2 충돌 해소 모달.
          flag on + preview 에 pushes/pulls/unresolved 존재 시에만 open. */}
      {cascade.modalState?.open && (
        <ConflictResolutionModal
          preview={cascade.modalState.preview}
          pullToggle={cascade.pullToggle}
          onPullToggle={cascade.setPullToggle}
          onApply={cascade.applyModal}
          onClose={cascade.closeModal}
          guidanceShown={cascade.modalState.guidanceShown}
          onRowHover={setHoveredTaskId}
        />
      )}

      {/* Task 4.3 — inbox 드롭 미배정 확인 모달 */}
      <UnassignConfirmModal
        isOpen={!!unassignFromDragState}
        batchGroup={unassignFromDragState?.batchGroup ?? ""}
        tasks={
          unassignFromDragState
            ? tasks.filter(
                (t) => t.batch_group === unassignFromDragState.batchGroup,
              )
            : []
        }
        onConfirm={(reason, dontAskAgain) => {
          if (unassignFromDragState) {
            void useScheduleStore
              .getState()
              .unassignBatchGroup(unassignFromDragState.batchGroup, reason);
            if (dontAskAgain) setSkipConfirmFromDrag(true);
            setUnassignFromDragState(null);
          }
        }}
        onCancel={() => setUnassignFromDragState(null)}
      />

      {/* Task 4.3 — BatchGroupCard → equipment-row 드롭 preview 확정 모달 */}
      <BatchGroupDragPreviewModal
        state={bgDrag.modalState}
        onApply={() => void bgDrag.applyModal()}
        onCancel={() => bgDrag.cancelModal()}
      />

      {/* 이전 버전과 비교 모달 — 비교 모드 활성 시 [목록] 버튼으로 오픈 */}
      <CompareDetailsModal
        isOpen={compare.compareOpen}
        onClose={() => compare.setCompareOpen(false)}
      />
    </div>
  );
}
