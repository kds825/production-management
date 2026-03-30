"use client";

import { useEffect, useRef } from "react";
import { useScheduleStore } from "../store/scheduleStore";

/** 컨텍스트 메뉴 — 빈 영역 또는 작업 바 우클릭 시 표시 */
export function ContextMenu() {
  const contextMenu = useScheduleStore((s) => s.contextMenu);
  const closeContextMenu = useScheduleStore((s) => s.closeContextMenu);
  const openTaskFormModal = useScheduleStore((s) => s.openTaskFormModal);
  const openSplitModal = useScheduleStore((s) => s.openSplitModal);
  const deleteTask = useScheduleStore((s) => s.deleteTask);
  const tasks = useScheduleStore((s) => s.tasks);

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

  if (!contextMenu) return null;

  // 뷰포트 경계 보정 (메뉴가 화면 밖으로 나가지 않도록)
  const menuWidth = 160;
  const menuHeight = contextMenu.type === "task" ? 120 : 88;
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

  const menuItemClass =
    "w-full text-left px-3 py-2 text-xs text-gray-700 hover:bg-red-50 hover:text-red-700 flex items-center gap-2 transition-colors";

  return (
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
            <span style={{ color: "#C41230" }}>+</span>
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
            <span>🔍</span>
            선택 영역 줌
          </button>
        </>
      ) : (
        <>
          <button className={menuItemClass} onClick={handleEditTask}>
            <span>✏️</span>
            수정
          </button>
          <button className={menuItemClass} onClick={handleCopyTask}>
            <span>📋</span>
            복사
          </button>
          {selectedTask?.batch_group && (
            <button className={menuItemClass} onClick={handleSplitBatch}>
              <span style={{ color: "#C41230" }}>&#x2702;</span>
              배치 분할
            </button>
          )}
          <div className="border-t border-gray-100 my-1" />
          <button
            className={`${menuItemClass} text-red-600 hover:bg-red-50`}
            onClick={handleDeleteTask}
          >
            <span>🗑️</span>
            삭제
          </button>
        </>
      )}
    </div>
  );
}
