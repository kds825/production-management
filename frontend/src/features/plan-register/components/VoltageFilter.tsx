"use client";

import { useState } from "react";
import { usePlanRegisterStore } from "../store/planRegisterStore";
import type { EquipmentGroup } from "../types";

const EQUIPMENT_GROUPS: EquipmentGroup[] = ["연선", "B100", "A100", "A120"];

export function VoltageFilter() {
  const {
    voltageFilter,
    equipmentFilter,
    setVoltageFilter,
    setEquipmentFilter,
  } = usePlanRegisterStore();

  const [showHighVoltageTooltip, setShowHighVoltageTooltip] = useState(false);

  return (
    <div className="flex items-center gap-4 mb-3">
      {/* 전압 레벨 탭 */}
      <div className="flex items-center gap-0.5">
        {(["전체보기", "고압", "저압"] as const).map((tab) => {
          const isDisabled = tab === "고압";
          const filterValue =
            tab === "전체보기" ? "전체" : (tab as "고압" | "저압");
          const isActive =
            voltageFilter === filterValue ||
            (tab === "전체보기" && voltageFilter === "전체");
          const isActiveTab =
            tab === "전체보기"
              ? voltageFilter === "전체"
              : voltageFilter === tab;

          return (
            <div key={tab} className="relative">
              <button
                disabled={isDisabled}
                onClick={() => {
                  if (!isDisabled) {
                    const val =
                      tab === "전체보기" ? "전체" : (tab as "고압" | "저압");
                    setVoltageFilter(val);
                    if (val === "저압") {
                      setEquipmentFilter("연선");
                    } else {
                      setEquipmentFilter(null);
                    }
                  }
                }}
                onMouseEnter={() =>
                  isDisabled && setShowHighVoltageTooltip(true)
                }
                onMouseLeave={() => setShowHighVoltageTooltip(false)}
                className="text-[11px] font-medium px-3 py-1.5 rounded-md transition-colors"
                style={{
                  backgroundColor: isActiveTab
                    ? "var(--kbi-brown)"
                    : "transparent",
                  color: isDisabled
                    ? "var(--neutral-300)"
                    : isActiveTab
                      ? "var(--color-text-inverse)"
                      : "var(--color-text-secondary)",
                  cursor: isDisabled ? "not-allowed" : "pointer",
                  border: isActiveTab
                    ? "1px solid var(--kbi-brown)"
                    : "1px solid var(--color-border-default)",
                }}
              >
                {tab}
              </button>

              {/* 고압 비활성 툴팁 */}
              {isDisabled && showHighVoltageTooltip && (
                <div
                  className="absolute bottom-full left-1/2 -translate-x-1/2 mb-1.5 px-2 py-1 rounded text-[10px] text-white whitespace-nowrap z-50"
                  style={{ backgroundColor: "var(--neutral-text-primary)" }}
                >
                  준비 중
                  <div
                    className="absolute top-full left-1/2 -translate-x-1/2 w-0 h-0"
                    style={{
                      borderLeft: "4px solid transparent",
                      borderRight: "4px solid transparent",
                      borderTop: "4px solid var(--neutral-text-primary)",
                    }}
                  />
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* 저압 장비 서브탭 */}
      {voltageFilter === "저압" && (
        <div
          className="flex items-center gap-0.5 pl-3"
          style={{ borderLeft: "1px solid var(--color-border-default)" }}
        >
          {EQUIPMENT_GROUPS.map((group) => {
            const isActive = equipmentFilter === group;
            return (
              <button
                key={group}
                onClick={() => setEquipmentFilter(group)}
                className="text-[11px] font-medium px-2.5 py-1 rounded transition-colors"
                style={{
                  backgroundColor: isActive
                    ? "var(--kbi-red-tint-5)"
                    : "transparent",
                  color: isActive
                    ? "var(--color-brand-primary)"
                    : "var(--color-text-secondary)",
                  border: isActive
                    ? "1px solid #FECACA"
                    : "1px solid transparent",
                }}
              >
                {group}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
