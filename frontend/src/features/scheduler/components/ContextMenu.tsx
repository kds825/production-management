"use client";

import { useEffect, useRef, useState } from "react";
import {
  PlusIcon,
  MagnifyingGlassPlusIcon,
  PencilIcon,
  DocumentDuplicateIcon,
  ScissorsIcon,
  PlayIcon,
  CheckCircleIcon,
  ArrowUturnLeftIcon,
  ArrowDownTrayIcon,
  TrashIcon,
} from "@heroicons/react/24/outline";
import { useScheduleStore } from "../store/scheduleStore";
import { UnassignConfirmModal } from "./UnassignConfirmModal";
import type { ScheduleTask, UnassignReason } from "../types";

const API_BASE = "http://localhost:8000/api";

/** 컨텍스트 메뉴 — 빈 영역 또는 작업 바 우클릭 시 표시 */
export function ContextMenu() {
  const contextMenu = useScheduleStore((s) => s.contextMenu);
  const closeContextMenu = useScheduleStore((s) => s.closeContextMenu);
  const openTaskFormModal = useScheduleStore((s) => s.openTaskFormModal);
  const openSplitModal = useScheduleStore((s) => s.openSplitModal);
  const deleteTask = useScheduleStore((s) => s.deleteTask);
  const updateTask = useScheduleStore((s) => s.updateTask);
  const tasks = useScheduleStore((s) => s.tasks);

  // Task 5.2 — 미배정으로 이동 모달 상태 (세션 scope)
  const [unassignModal, setUnassignModal] = useState<{
    batchGroup: string;
    tasks: ScheduleTask[];
  } | null>(null);
  // "다시 묻지 않기" 체크 시 true — 컴포넌트 unmount(페이지 이동/새로고침)까지 유지
  const [skipConfirm, setSkipConfirm] = useState(false);

  const menuRef = useRef<HTMLDivElement>(null);

  // 메뉴 외부 클릭 시 닫기
  useEffect(() => {
    if (!contextMenu) return;

    function handleClickOutside(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        closeContextMenu();
      }
    }

    function handleEscape(e: KeyboardEvent) {
      if (e.key === "Escape") closeContextMenu();
    }

    document.addEventListener("mousedown", handleClickOutside);
    document.addEventListener("keydown", handleEscape);
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
      document.removeEventListener("keydown", handleEscape);
    };
  }, [contextMenu, closeContextMenu]);

  // 주의: UnassignConfirmModal 은 contextMenu 와 독립적으로 마운트되어야 한다.
  // "미배정으로 이동" 클릭 시 onClick 핸들러가 먼저 closeContextMenu() 를 호출하여
  // contextMenu=null 상태가 된 뒤 setUnassignModal(...) 을 호출하는데, 여기서
  // 컴포넌트 전체가 return null 로 언마운트되면 setState 가 손실되어 모달이
  // 뜨지 않는다 (Phase 6 E2E 에서 발견된 버그).
  // 따라서 contextMenu 가 닫혀도 modal 상태만 살아 있으면 modal 은 계속 렌더한다.
  if (!contextMenu) {
    return (
      <UnassignConfirmModal
        isOpen={!!unassignModal}
        batchGroup={unassignModal?.batchGroup ?? ""}
        tasks={unassignModal?.tasks ?? []}
        onConfirm={(reason: UnassignReason, dontAskAgain: boolean) => {
          if (unassignModal) {
            void useScheduleStore
              .getState()
              .unassignBatchGroup(unassignModal.batchGroup, reason);
            if (dontAskAgain) setSkipConfirm(true);
            setUnassignModal(null);
          }
        }}
        onCancel={() => setUnassignModal(null)}
      />
    );
  }

  // 뷰포트 경계 보정 (메뉴가 화면 밖으로 나가지 않도록)
  const menuWidth = 160;
  // task 메뉴: 기본 항목 + 상태 변경 섹션(최대 3항목 × 28px + 구분선 8px)
  const menuHeight = contextMenu.type === "task" ? 220 : 88;
  const left = Math.min(contextMenu.x, window.innerWidth - menuWidth - 8);
  const top = Math.min(contextMenu.y, window.innerHeight - menuHeight - 8);

  // 선택된 작업 복사 (복사 기능용)
  const selectedTask = contextMenu.taskId
    ? tasks.find((t) => t.id === contextMenu.taskId)
    : undefined;

  function handleAddTask() {
    closeContextMenu();
    openTaskFormModal({
      mode: "create",
      prefill: {
        equipmentId: contextMenu?.equipmentId,
        start: contextMenu?.clickTime,
      },
    });
  }

  function handleEditTask() {
    if (!contextMenu?.taskId) return;
    closeContextMenu();
    openTaskFormModal({ mode: "edit", taskId: contextMenu.taskId });
  }

  function handleDeleteTask() {
    if (!contextMenu?.taskId) return;
    if (window.confirm("이 작업을 삭제하시겠습니까?")) {
      deleteTask(contextMenu.taskId);
    }
    closeContextMenu();
  }

  function handleCopyTask() {
    if (!selectedTask) return;
    // 복사본 생성: ID를 새로 부여하고 시작 시간을 1일 뒤로 이동
    const newTask = {
      ...selectedTask,
      id: `task-copy-${Date.now()}`,
      start: new Date(selectedTask.start.getTime() + 24 * 60 * 60 * 1000),
      end: new Date(selectedTask.end.getTime() + 24 * 60 * 60 * 1000),
      status: "planned" as const,
    };
    // addTask는 별도 import 없이 store에서 직접 접근
    useScheduleStore.getState().addTask(newTask);
    closeContextMenu();
  }

  function handleSplitBatch() {
    if (!selectedTask?.batch_group) return;
    closeContextMenu();
    openSplitModal(selectedTask.batch_group, selectedTask.id);
  }

  // 낙관적 상태 변경: 즉시 로컬 반영 → API 실패 시 롤백
  async function handleStatusChange(newStatus: string) {
    if (!selectedTask?.batch_id || !contextMenu?.taskId) return;
    closeContextMenu();

    const prevStatus = selectedTask.status;
    updateTask(contextMenu.taskId, { status: newStatus });

    try {
      const res = await fetch(
        `${API_BASE}/pipeline/batch/${selectedTask.batch_id}/status`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ status: newStatus }),
        },
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
    } catch {
      // 실패 시 롤백
      updateTask(contextMenu.taskId, { status: prevStatus });
    }
  }

  const menuItemClass =
    "w-full text-left px-3 py-2 text-xs text-gray-700 hover:bg-red-50 hover:text-red-700 flex items-center gap-2 transition-colors";

  return (
    <>
      <div
        ref={menuRef}
        className="fixed z-50 bg-white rounded-lg shadow-lg border border-gray-200 py-1 min-w-[160px]"
        style={{ left, top }}
        // 컨텍스트 메뉴 자체의 우클릭이 또 다른 컨텍스트 메뉴를 열지 않도록 방지
        onContextMenu={(e) => e.preventDefault()}
      >
        {contextMenu.type === "empty" ? (
          <>
            <button className={menuItemClass} onClick={handleAddTask}>
              <PlusIcon width={14} height={14} aria-hidden />
              작업 추가
            </button>
            <div className="border-t border-gray-100 my-1" />
            <button
              className={menuItemClass}
              onClick={() => {
                // 선택 영역 줌 — 현재는 주(week) 뷰 고정이므로 알림 처리
                closeContextMenu();
              }}
            >
              <MagnifyingGlassPlusIcon width={14} height={14} aria-hidden />
              선택 영역 줌
            </button>
          </>
        ) : (
          <>
            <button className={menuItemClass} onClick={handleEditTask}>
              <PencilIcon width={14} height={14} aria-hidden />
              수정
            </button>
            <button className={menuItemClass} onClick={handleCopyTask}>
              <DocumentDuplicateIcon width={14} height={14} aria-hidden />
              복사
            </button>
            {selectedTask?.batch_group && (
              <button className={menuItemClass} onClick={handleSplitBatch}>
                <ScissorsIcon width={14} height={14} aria-hidden />
                배치 분할
              </button>
            )}
            {/* 상태 변경 섹션 — batch_id가 있는 DB 태스크만 표시 */}
            {selectedTask?.batch_id != null && (
              <>
                <div className="border-t border-gray-100 my-1" />
                <div className="px-3 py-1 text-[9px] text-gray-400 uppercase tracking-wide">
                  상태 변경
                </div>
                {selectedTask.status !== "in_progress" && (
                  <button
                    className={menuItemClass}
                    onClick={() => handleStatusChange("in_progress")}
                  >
                    <PlayIcon width={14} height={14} aria-hidden />
                    진행중으로 변경
                  </button>
                )}
                {selectedTask.status !== "completed" && (
                  <button
                    className={menuItemClass}
                    onClick={() => handleStatusChange("completed")}
                  >
                    <CheckCircleIcon width={14} height={14} aria-hidden />
                    완료로 변경
                  </button>
                )}
                {selectedTask.status !== "planned" && (
                  <button
                    className={menuItemClass}
                    onClick={() => handleStatusChange("planned")}
                  >
                    <ArrowUturnLeftIcon width={14} height={14} aria-hidden />
                    계획으로 되돌리기
                  </button>
                )}
              </>
            )}
            {/* Task 5.2 — 배치 관리 섹션: 미배정으로 이동 (B3 연결) */}
            {selectedTask?.batch_id != null && selectedTask.batch_group && (
              <>
                <div className="border-t border-gray-100 my-1" />
                <div className="px-3 py-1 text-[9px] text-gray-400 uppercase tracking-wide">
                  배치 관리
                </div>
                {(() => {
                  const groupTasks = tasks.filter(
                    (t) => t.batch_group === selectedTask.batch_group,
                  );
                  const hasNonPlanned = groupTasks.some(
                    (t) => t.status !== "planned",
                  );
                  const hasWipMatched = groupTasks.some(
                    (t) => t.wip_matched_id != null,
                  );
                  const disabled = hasNonPlanned || hasWipMatched;
                  const tooltip = hasNonPlanned
                    ? "진행중인 공정 포함 — 먼저 계획으로 되돌리세요"
                    : hasWipMatched
                      ? "WIP 매칭된 묶음은 이동할 수 없습니다"
                      : "이 묶음을 미배정 작업으로 이동";
                  return (
                    <button
                      type="button"
                      className={menuItemClass}
                      disabled={disabled}
                      aria-disabled={disabled}
                      title={tooltip}
                      onClick={() => {
                        if (disabled) return;
                        const bg = selectedTask.batch_group!;
                        closeContextMenu();
                        if (skipConfirm) {
                          void useScheduleStore
                            .getState()
                            .unassignBatchGroup(bg, "기타");
                        } else {
                          setUnassignModal({
                            batchGroup: bg,
                            tasks: groupTasks,
                          });
                        }
                      }}
                    >
                      <ArrowDownTrayIcon width={14} height={14} aria-hidden />
                      미배정으로 이동
                    </button>
                  );
                })()}
              </>
            )}
            <div className="border-t border-gray-100 my-1" />
            <button
              className={`${menuItemClass} text-red-600 hover:bg-red-50`}
              onClick={handleDeleteTask}
            >
              <TrashIcon width={14} height={14} aria-hidden />
              삭제
            </button>
          </>
        )}
      </div>
      {/* Task 5.2 — 미배정으로 이동 확인 모달 */}
      <UnassignConfirmModal
        isOpen={!!unassignModal}
        batchGroup={unassignModal?.batchGroup ?? ""}
        tasks={unassignModal?.tasks ?? []}
        onConfirm={(reason: UnassignReason, dontAskAgain: boolean) => {
          if (unassignModal) {
            void useScheduleStore
              .getState()
              .unassignBatchGroup(unassignModal.batchGroup, reason);
            if (dontAskAgain) setSkipConfirm(true);
            setUnassignModal(null);
          }
        }}
        onCancel={() => setUnassignModal(null)}
      />
    </>
  );
}
