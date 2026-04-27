/**
 * EditWarningModal — 읽기 전용 모드에서 드롭/이동 시도 시 안내하는 portal 모달.
 *
 * 책임 (Week 7 Task 7B.1 분리):
 *   - 모달 열림 상태 + 닫기 / "수정하기" 액션 두 가지 콜백만 받음.
 *   - 부모(SchedulerPage) 가 isEditMode 분기에서 setShowEditWarning(true) 만 호출하면 됨.
 *
 * Portal 사용: body 레벨에 띄워야 z-index 우선권 확보 + 페이지 본문 overflow:hidden 영향 회피.
 */
"use client";

import { createPortal } from "react-dom";

interface EditWarningModalProps {
  isOpen: boolean;
  onClose: () => void;
  /** "수정하기" 클릭 시 — 일반적으로 store.toggleEditMode 를 위임 */
  onEnableEditMode: () => void;
}

export function EditWarningModal({
  isOpen,
  onClose,
  onEnableEditMode,
}: EditWarningModalProps) {
  if (!isOpen || typeof document === "undefined") return null;

  return createPortal(
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
      onClick={onClose}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          backgroundColor: "var(--bg-surface)",
          borderRadius: 8,
          padding: "28px 32px",
          boxShadow: "0 8px 32px rgba(0,0,0,0.18)",
          border: "1px solid var(--color-border-default)",
          minWidth: 420,
          maxWidth: 500,
        }}
      >
        <p
          className="text-sm font-semibold mb-2"
          style={{ color: "var(--color-text-primary)" }}
        >
          수정 모드를 활성화해주세요
        </p>
        <p className="text-xs text-gray-500 mb-5 leading-relaxed">
          작업을 배정하거나 이동하려면 우측 상단의 &quot;수정하기&quot; 버튼을
          먼저 눌러주세요.
        </p>
        <div className="flex justify-end gap-2">
          <button
            onClick={onClose}
            className="px-4 py-2 text-xs font-medium rounded-md transition-colors"
            style={{
              border: "1px solid var(--color-border-default)",
              color: "var(--color-text-secondary)",
            }}
          >
            닫기
          </button>
          <button
            onClick={() => {
              onClose();
              onEnableEditMode();
            }}
            className="px-4 py-2 text-xs font-medium rounded-md text-white transition-colors"
            style={{ backgroundColor: "var(--color-brand-primary)" }}
          >
            수정하기
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
