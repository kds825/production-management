"use client";

import { useState, useCallback } from "react";
import { usePlanRegisterStore } from "@/features/plan-register/store/planRegisterStore";
import { useScheduleStore } from "../store/scheduleStore";

export function SyncButton() {
  const confirmedBatches = usePlanRegisterStore((s) => s.confirmedBatches);
  const syncFromPlanRegister = useScheduleStore((s) => s.syncFromPlanRegister);

  const [showToast, setShowToast] = useState(false);

  const isDisabled = confirmedBatches.length === 0;

  const handleSync = useCallback(() => {
    if (isDisabled) return;
    syncFromPlanRegister(confirmedBatches);
    setShowToast(true);
    setTimeout(() => setShowToast(false), 3000);
  }, [isDisabled, confirmedBatches, syncFromPlanRegister]);

  return (
    <div className="relative">
      <button
        onClick={handleSync}
        disabled={isDisabled}
        className="flex items-center gap-1.5 text-[11px] font-medium px-3 py-1.5 rounded-md transition-colors"
        style={{
          backgroundColor: isDisabled
            ? "var(--neutral-100)"
            : "var(--kbi-red-tint-5)",
          color: isDisabled
            ? "var(--neutral-300)"
            : "var(--color-brand-primary)",
          border: isDisabled
            ? "1px solid var(--color-border-default)"
            : "1px solid #FECACA",
          cursor: isDisabled ? "not-allowed" : "pointer",
        }}
        onMouseEnter={(e) => {
          if (!isDisabled) {
            e.currentTarget.style.backgroundColor = "var(--kbi-red-tint-12)";
          }
        }}
        onMouseLeave={(e) => {
          if (!isDisabled) {
            e.currentTarget.style.backgroundColor = "var(--kbi-red-tint-5)";
          }
        }}
        title={
          isDisabled ? "생산계획등록 페이지에서 배치를 확정하세요" : undefined
        }
      >
        <svg
          width="12"
          height="12"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M23 4v6h-6" />
          <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" />
        </svg>
        생산계획 동기화
        {confirmedBatches.length > 0 && (
          <span
            className="text-[9px] font-semibold px-1.5 py-0.5 rounded-full text-white"
            style={{ backgroundColor: "var(--color-brand-primary)" }}
          >
            {confirmedBatches.length}
          </span>
        )}
      </button>

      {/* 동기화 완료 토스트 */}
      {showToast && (
        <div
          className="absolute top-full mt-2 left-1/2 -translate-x-1/2 px-3 py-1.5 rounded-md text-[11px] font-medium text-white whitespace-nowrap z-50 shadow-sm"
          style={{ backgroundColor: "#059669" }}
        >
          동기화 완료
        </div>
      )}
    </div>
  );
}
