"use client";

import { useState } from "react";
import { useScheduleStore } from "../store/scheduleStore";
import type { ConstraintViolation } from "../types";

/** 위반 유형 한국어 레이블 */
const VIOLATION_TYPE_LABELS: Record<ConstraintViolation["type"], string> = {
  overlap: "작업 중복",
  equipment_capability: "설비 능력 초과",
  precedence: "선행 작업 위반",
  delivery: "납기 초과",
  process_route: "공정 경로 오류",
};

/** 심각도별 스타일 */
const SEVERITY_STYLES: Record<
  ConstraintViolation["severity"],
  { bg: string; border: string; text: string; icon: string; badgeBg: string }
> = {
  error: {
    bg: "#FEF2F2",
    border: "#FECACA",
    text: "#B91C1C",
    icon: "⛔",
    badgeBg: "#DC2626",
  },
  warning: {
    bg: "#FFFBEB",
    border: "#FDE68A",
    text: "#92400E",
    icon: "⚠️",
    badgeBg: "#F59E0B",
  },
};

interface ViolationItemProps {
  violation: ConstraintViolation;
  taskName?: string;
}

function ViolationItem({ violation, taskName }: ViolationItemProps) {
  const style = SEVERITY_STYLES[violation.severity];
  const selectTask = useScheduleStore((s) => s.selectTask);

  function handleClick() {
    // 해당 작업으로 포커스 이동
    selectTask(violation.task_id);
  }

  return (
    <div
      className="flex items-start gap-2 px-3 py-2 rounded-lg cursor-pointer hover:opacity-80 transition-opacity"
      style={{
        backgroundColor: style.bg,
        border: `1px solid ${style.border}`,
      }}
      onClick={handleClick}
      title="클릭하여 작업 강조표시"
    >
      <span className="flex-shrink-0 text-xs mt-0.5">{style.icon}</span>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5 mb-0.5">
          <span
            className="text-[9px] font-semibold px-1.5 py-0.5 rounded text-white flex-shrink-0"
            style={{ backgroundColor: style.badgeBg }}
          >
            {VIOLATION_TYPE_LABELS[violation.type]}
          </span>
          {taskName && (
            <span
              className="text-[9px] text-gray-500 truncate"
              title={taskName}
            >
              {taskName}
            </span>
          )}
        </div>
        <p className="text-[10px]" style={{ color: style.text }}>
          {violation.message}
        </p>
      </div>
    </div>
  );
}

/** ConstraintAlert: 하단 제약 조건 위반 패널 */
export function ConstraintAlert() {
  const violations = useScheduleStore((s) => s.violations);
  const tasks = useScheduleStore((s) => s.tasks);
  const [isCollapsed, setIsCollapsed] = useState(false);

  // violations를 severity 기준으로 정렬 (error 우선)
  const sorted = [...violations].sort((a, b) => {
    if (a.severity === "error" && b.severity !== "error") return -1;
    if (b.severity === "error" && a.severity !== "error") return 1;
    return 0;
  });

  const errorCount = violations.filter((v) => v.severity === "error").length;
  const warningCount = violations.filter(
    (v) => v.severity === "warning",
  ).length;

  // 위반이 없으면 조용히 숨김
  if (violations.length === 0) return null;

  return (
    <div
      className="border-t border-gray-200 bg-white flex flex-col"
      style={{ maxHeight: isCollapsed ? 40 : 200 }}
    >
      {/* 패널 헤더 */}
      <div
        className="flex items-center justify-between px-4 py-2 border-b border-gray-100 cursor-pointer hover:bg-gray-50 transition-colors"
        style={{ minHeight: 40 }}
        onClick={() => setIsCollapsed((prev) => !prev)}
      >
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold text-gray-700">
            제약 조건 알림
          </span>
          {errorCount > 0 && (
            <span className="text-[10px] font-semibold text-white bg-red-600 rounded-full px-1.5 py-0.5">
              오류 {errorCount}
            </span>
          )}
          {warningCount > 0 && (
            <span className="text-[10px] font-semibold text-white bg-amber-500 rounded-full px-1.5 py-0.5">
              경고 {warningCount}
            </span>
          )}
        </div>
        <button
          className="text-gray-400 hover:text-gray-600 transition-colors text-xs"
          aria-label={isCollapsed ? "펼치기" : "접기"}
        >
          {isCollapsed ? "▲ 펼치기" : "▼ 접기"}
        </button>
      </div>

      {/* 위반 목록 */}
      {!isCollapsed && (
        <div className="flex-1 overflow-y-auto p-2 flex flex-col gap-1.5">
          {sorted.map((v, idx) => {
            const task = tasks.find((t) => t.id === v.task_id);
            const taskName = task ? `${task.product} (${task.id})` : v.task_id;
            return (
              <ViolationItem key={idx} violation={v} taskName={taskName} />
            );
          })}
        </div>
      )}
    </div>
  );
}
