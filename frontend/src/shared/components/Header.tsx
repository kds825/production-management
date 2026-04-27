"use client";

import Image from "next/image";

import { BellIcon } from "@/features/scheduler/components/decision/BellIcon";

interface HeaderProps {
  onAddTask?: () => void;
  isEditMode?: boolean;
  onToggleEditMode?: () => void;
  onDiscardEdits?: () => void;
}

export function Header({
  onAddTask,
  isEditMode = false,
  onToggleEditMode,
  onDiscardEdits,
}: HeaderProps) {
  return (
    <header className="h-14 bg-white border-b border-gray-200 flex items-center justify-between px-6 sticky top-0 z-50">
      <div className="flex items-center gap-3">
        <Image
          src="/kbi-group-logo.jpg"
          alt="KBI GROUP"
          width={72}
          height={36}
          className="object-contain"
        />
        <div className="h-6 w-px bg-gray-200" />
        <h1
          className="text-sm font-semibold tracking-tight"
          style={{ color: "var(--kbi-brown)", letterSpacing: "-0.02em" }}
        >
          생산계획 스케줄러
        </h1>
      </div>
      <div className="flex items-center gap-3">
        {/* 작업 추가 버튼 — 편집 모드에서만 활성화, 읽기 전용에서는 흐리게 표시 */}
        <button
          onClick={isEditMode ? onAddTask : undefined}
          disabled={!isEditMode}
          className="px-3 py-1.5 text-xs font-medium rounded-md transition-all"
          style={{
            backgroundColor: isEditMode
              ? "var(--color-brand-primary)"
              : "var(--color-border-default)",
            color: isEditMode
              ? "var(--color-text-inverse)"
              : "var(--color-text-tertiary)",
            cursor: isEditMode ? "pointer" : "not-allowed",
          }}
          title={isEditMode ? "작업 추가" : "수정 모드에서 사용 가능합니다"}
        >
          + 작업 추가
        </button>

        {/* 수정하기 / 저장하기 토글 버튼 */}
        <button
          onClick={onToggleEditMode}
          className="px-3 py-1.5 text-xs font-medium rounded-md transition-all border"
          style={
            isEditMode
              ? {
                  backgroundColor: "var(--color-brand-primary)",
                  color: "var(--color-text-inverse)",
                  borderColor: "var(--color-brand-primary)",
                  cursor: "pointer",
                }
              : {
                  backgroundColor: "var(--bg-surface)",
                  color: "var(--kbi-brown)",
                  borderColor: "var(--kbi-brown)",
                  cursor: "pointer",
                }
          }
        >
          {isEditMode ? "저장하기" : "수정하기"}
        </button>

        {/* 취소 버튼 — 수정 모드일 때만 표시 */}
        {isEditMode && (
          <button
            onClick={onDiscardEdits}
            className="px-3 py-1.5 text-xs font-medium rounded-md transition-all border"
            style={{
              backgroundColor: "var(--bg-surface)",
              color: "var(--color-text-secondary)",
              borderColor: "var(--neutral-300)",
              cursor: "pointer",
            }}
            title="변경 사항을 저장하지 않고 수정 모드를 종료합니다"
          >
            취소
          </button>
        )}

        <BellIcon />
        <span className="text-[9px] text-gray-300 px-1.5 py-0.5">v0.1</span>
      </div>
    </header>
  );
}
