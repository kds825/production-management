"use client";

import { createPortal } from "react-dom";
import type { CascadePreview } from "../types";

interface ConflictResolutionModalProps {
  preview: CascadePreview;
  movedTask: { id: string; spec: string; process: string };
  onApply: () => void;
  onCancel: () => void;
}

/** 날짜를 "M/D HH:mm" 형식으로 포맷 */
function fmtDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString("ko-KR", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/**
 * 선행 공정 이동 시 후행 공정에 대한 영향을 표시하고
 * 사용자에게 일괄 적용 또는 취소를 선택하게 하는 모달.
 */
export function ConflictResolutionModal({
  preview,
  movedTask,
  onApply,
  onCancel,
}: ConflictResolutionModalProps) {
  const { affected_tasks, conflicts } = preview;

  const modalContent = (
    <div
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 99999,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        backgroundColor: "rgba(0,0,0,0.4)",
      }}
      onClick={onCancel}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="rounded-lg shadow-xl"
        style={{
          backgroundColor: "#FFFFFF",
          minWidth: 480,
          maxWidth: 600,
          maxHeight: "80vh",
          display: "flex",
          flexDirection: "column",
          border: "1px solid #E5E7EB",
        }}
      >
        {/* 헤더 */}
        <div
          className="flex items-center gap-2 px-5 py-3 rounded-t-lg"
          style={{ backgroundColor: "#4A2C2A" }}
        >
          <svg
            width="16"
            height="16"
            viewBox="0 0 16 16"
            fill="#FCD34D"
            style={{ flexShrink: 0 }}
          >
            <path d="M8 1L1 14h14L8 1zm0 2.5l5.5 9.5h-11L8 3.5zM7.25 7v3.5h1.5V7h-1.5zm0 4.5v1.5h1.5v-1.5h-1.5z" />
          </svg>
          <span className="text-sm font-semibold text-white">
            선행 공정 이동으로 후행 작업에 영향
          </span>
        </div>

        {/* 본문 */}
        <div
          className="px-5 py-4 flex-1 overflow-y-auto"
          style={{ maxHeight: "60vh" }}
        >
          {/* 이동 대상 정보 */}
          <div
            className="flex items-center gap-2 mb-4 px-3 py-2 rounded"
            style={{ backgroundColor: "#FDF2F2", border: "1px solid #F3D5D5" }}
          >
            <div
              className="w-1 h-8 rounded-sm"
              style={{ backgroundColor: "#C41230" }}
            />
            <div className="flex flex-col">
              <span
                className="text-[11px] font-medium"
                style={{ color: "#9CA3AF" }}
              >
                이동 대상
              </span>
              <span
                className="text-sm font-semibold"
                style={{ color: "#1F2937" }}
              >
                {movedTask.process} {movedTask.spec}
              </span>
            </div>
          </div>

          {/* 영향받는 작업 목록 */}
          {affected_tasks.length > 0 && (
            <div className="mb-4">
              <div className="flex items-center gap-1.5 mb-2">
                <span
                  className="text-xs font-semibold"
                  style={{ color: "#1F2937" }}
                >
                  영향받는 작업
                </span>
                <span
                  className="text-[10px] px-1.5 py-0.5 rounded font-medium"
                  style={{ backgroundColor: "#FEF3C7", color: "#92400E" }}
                >
                  {affected_tasks.length}건
                </span>
              </div>
              <div
                className="rounded border"
                style={{ borderColor: "#E5E7EB" }}
              >
                {affected_tasks.map((task, idx) => (
                  <div
                    key={task.task_id}
                    className="flex items-center justify-between px-3 py-2"
                    style={{
                      borderBottom:
                        idx < affected_tasks.length - 1
                          ? "1px solid #F3F4F6"
                          : "none",
                    }}
                  >
                    <div className="flex flex-col gap-0.5">
                      <div className="flex items-center gap-2">
                        <span
                          className="text-[11px] font-semibold"
                          style={{ color: "#1F2937" }}
                        >
                          {task.process}
                        </span>
                        <span
                          className="text-[10px] px-1.5 py-0.5 rounded font-mono"
                          style={{
                            backgroundColor: "#F3F4F6",
                            color: "#6B7280",
                          }}
                        >
                          {task.equipment}
                        </span>
                      </div>
                      <span className="text-[10px] text-gray-400">
                        {task.reason}
                      </span>
                    </div>
                    <div className="flex items-center gap-1 text-[11px]">
                      <span style={{ color: "#9CA3AF" }}>
                        {fmtDate(task.old_start)}
                      </span>
                      <svg
                        width="12"
                        height="12"
                        viewBox="0 0 16 16"
                        fill="#9CA3AF"
                      >
                        <path d="M6 3l5 5-5 5V3z" />
                      </svg>
                      <span
                        className="font-medium"
                        style={{ color: "#C41230" }}
                      >
                        {fmtDate(task.new_start)}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* 충돌 섹션 */}
          {conflicts.length > 0 && (
            <div className="mb-2">
              <div className="flex items-center gap-1.5 mb-2">
                <svg width="12" height="12" viewBox="0 0 16 16" fill="#D97706">
                  <path d="M8 1L1 14h14L8 1zm0 2.5l5.5 9.5h-11L8 3.5zM7.25 7v3.5h1.5V7h-1.5zm0 4.5v1.5h1.5v-1.5h-1.5z" />
                </svg>
                <span
                  className="text-xs font-semibold"
                  style={{ color: "#92400E" }}
                >
                  충돌 {conflicts.length}건
                </span>
              </div>
              <div
                className="rounded border"
                style={{
                  borderColor: "#FCD34D",
                  backgroundColor: "#FFFBEB",
                }}
              >
                {conflicts.map((conflict, idx) => (
                  <div
                    key={`${conflict.task_id}-${conflict.conflict_with}`}
                    className="px-3 py-2"
                    style={{
                      borderBottom:
                        idx < conflicts.length - 1
                          ? "1px solid #FEF3C7"
                          : "none",
                    }}
                  >
                    <div className="flex items-center gap-2 mb-0.5">
                      <span
                        className="text-[11px] font-medium"
                        style={{ color: "#1F2937" }}
                      >
                        {conflict.equipment}
                      </span>
                      <span
                        className="text-[10px] px-1.5 py-0.5 rounded"
                        style={{
                          backgroundColor: "#FEE2E2",
                          color: "#DC2626",
                        }}
                      >
                        {conflict.overlap_min}분 겹침
                      </span>
                    </div>
                    <p className="text-[10px] text-gray-500">
                      {conflict.task_id} 이동 시 {conflict.equipment}에서 기존
                      작업({conflict.conflict_with})과 겹침
                      {conflict.resolution === "push_forward" &&
                        " -- 기존 작업을 뒤로 밀어서 해소"}
                    </p>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* 하단 버튼 영역 */}
        <div
          className="flex justify-end gap-2 px-5 py-3 border-t"
          style={{ borderColor: "#E5E7EB" }}
        >
          <button
            onClick={onCancel}
            className="px-4 py-2 text-xs font-medium rounded-md transition-colors"
            style={{
              border: "1px solid #E5E7EB",
              color: "#6B7280",
              backgroundColor: "#FFFFFF",
            }}
          >
            취소
          </button>
          <button
            onClick={onApply}
            className="px-4 py-2 text-xs font-medium rounded-md text-white transition-colors"
            style={{ backgroundColor: "#C41230" }}
          >
            전체 적용
          </button>
        </div>
      </div>
    </div>
  );

  if (typeof document === "undefined") return null;
  return createPortal(modalContent, document.body);
}
