"use client";

import { useState } from "react";
import { useScheduleStore } from "../store/scheduleStore";
import type { ViewFilterType } from "../types";

// 공정 목록 (process_type 기준)
const PROCESS_OPTIONS: { label: string; value: string }[] = [
  { label: "신선", value: "drawing" },
  { label: "연선", value: "stranding" },
  { label: "절연", value: "lv_insulation" },
  { label: "고압절연", value: "hv_insulation" },
  { label: "테이핑", value: "taping" },
  { label: "연합", value: "cabling" },
  { label: "시스", value: "lv_jacketing" },
  { label: "고압시스", value: "hv_jacketing" },
];

/**
 * 전압 분류 필터
 * 공통 설비(신선/연선)는 어떤 뷰에서도 항상 표시한다.
 * LV only: 저압절연, 저압자켓 공정
 * HV only: 고압절연, 고압자켓, 테이핑, 연합 등
 */
const ALWAYS_SHOWN_PROCESS: string[] = ["drawing", "stranding"];
const LV_PROCESS: string[] = ["lv_insulation", "lv_jacketing"];
const HV_PROCESS: string[] = [
  "hv_insulation",
  "hv_jacketing",
  "taping",
  "cabling",
  "neutral_wire",
];

type VoltageMode = "lv" | "hv";

/** ViewFilter: 스케줄러 뷰 필터 컨트롤 */
export function ViewFilter() {
  const viewFilter = useScheduleStore((s) => s.viewFilter);
  const setViewFilter = useScheduleStore((s) => s.setViewFilter);
  const equipment = useScheduleStore((s) => s.equipment);

  // 전압 토글 로컬 상태 (voltage 모드일 때 사용)
  const [voltageMode, setVoltageMode] = useState<VoltageMode>("lv");

  // 현재 filterType
  const activeType = viewFilter.filterType;

  function switchTo(type: ViewFilterType) {
    if (type === "all") {
      setViewFilter({ filterType: "all", filterValue: [] });
    } else if (type === "voltage") {
      // 전압 필터로 전환 — 현재 voltageMode 기준으로 필터값 설정
      applyVoltage(voltageMode);
    } else {
      // 공정별: 기본적으로 전체 공정 표시
      setViewFilter({ filterType: "process", filterValue: [] });
    }
  }

  function handleProcessChange(processType: string) {
    const current = viewFilter.filterValue ?? [];
    const next = current.includes(processType)
      ? current.filter((v) => v !== processType)
      : [...current, processType];
    setViewFilter({ filterType: "process", filterValue: next });
  }

  function applyVoltage(mode: VoltageMode) {
    setVoltageMode(mode);
    // 공통 설비 equipment ids + 해당 전압 공정 equipment ids
    const targetProcesses = [
      ...ALWAYS_SHOWN_PROCESS,
      ...(mode === "lv" ? LV_PROCESS : HV_PROCESS),
    ];
    const ids = equipment
      .filter((eq) => targetProcesses.includes(eq.process_type))
      .map((eq) => eq.id);
    setViewFilter({ filterType: "voltage", filterValue: ids });
  }

  const tabClass = (active: boolean) =>
    [
      "px-3 py-1.5 text-xs font-medium rounded-md transition-colors",
      active
        ? "text-white"
        : "text-gray-600 bg-white border border-gray-200 hover:bg-gray-50",
    ].join(" ");

  return (
    <div
      className="flex items-center gap-3 px-4 py-2 bg-white border-b border-gray-200"
      style={{ minHeight: 44 }}
    >
      <span className="text-[10px] font-semibold text-gray-500 uppercase tracking-wide mr-1">
        필터
      </span>

      {/* 메인 필터 탭 */}
      <div className="flex gap-1">
        {(
          [
            ["all", "전체"],
            ["process", "공정별"],
            ["voltage", "고압/저압"],
          ] as [ViewFilterType, string][]
        ).map(([type, label]) => (
          <button
            key={type}
            onClick={() => switchTo(type)}
            className={tabClass(activeType === type)}
            style={activeType === type ? { backgroundColor: "#C41230" } : {}}
          >
            {label}
          </button>
        ))}
      </div>

      {/* 공정별 서브 필터 */}
      {activeType === "process" && (
        <>
          <div className="w-px h-4 bg-gray-300 mx-1" />
          <div className="flex flex-wrap gap-1">
            {PROCESS_OPTIONS.map((opt) => {
              const isSelected = (viewFilter.filterValue ?? []).includes(
                opt.value,
              );
              return (
                <button
                  key={opt.value}
                  onClick={() => handleProcessChange(opt.value)}
                  className={[
                    "px-2 py-0.5 text-[10px] rounded border transition-colors",
                    isSelected
                      ? "text-white border-transparent"
                      : "text-gray-600 bg-white border-gray-200 hover:bg-gray-50",
                  ].join(" ")}
                  style={isSelected ? { backgroundColor: "#4A2C2A" } : {}}
                >
                  {opt.label}
                </button>
              );
            })}
          </div>
        </>
      )}

      {/* 전압 토글 */}
      {activeType === "voltage" && (
        <>
          <div className="w-px h-4 bg-gray-300 mx-1" />
          <div
            className="flex rounded-lg overflow-hidden border border-gray-200"
            style={{ fontSize: "11px" }}
          >
            <button
              onClick={() => applyVoltage("lv")}
              className={[
                "px-3 py-1 font-medium transition-colors",
                voltageMode === "lv"
                  ? "text-white"
                  : "text-gray-600 bg-white hover:bg-gray-50",
              ].join(" ")}
              style={voltageMode === "lv" ? { backgroundColor: "#1565C0" } : {}}
            >
              저압 (0.6/1kV)
            </button>
            <button
              onClick={() => applyVoltage("hv")}
              className={[
                "px-3 py-1 font-medium transition-colors border-l border-gray-200",
                voltageMode === "hv"
                  ? "text-white"
                  : "text-gray-600 bg-white hover:bg-gray-50",
              ].join(" ")}
              style={voltageMode === "hv" ? { backgroundColor: "#C41230" } : {}}
            >
              고압 (6kV+)
            </button>
          </div>
          <span className="text-[10px] text-gray-400 ml-1">
            * 신선/연선 공통 설비는 항상 표시
          </span>
        </>
      )}
    </div>
  );
}
