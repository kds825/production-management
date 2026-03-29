"use client";

import { useMemo, useState } from "react";
import { useScheduleStore } from "../store/scheduleStore";
import type { ViewFilterType } from "../types";

/**
 * 공통 설비(신선/연선)는 전압 필터에서 항상 표시한다.
 * 비즈니스 규칙이므로 상수로 관리한다.
 */
const ALWAYS_SHOWN_PROCESS: string[] = ["drawing", "stranding"];

/**
 * 공장 수동 계획표(3/23~4/10)에 등장하는 저압 설비 7종.
 * 수동 계획표와 1:1 대응되도록 ID를 하드코딩한다.
 * (T8B0, 54B0#1, 12BO, 4BO, B100EXT, A100EXT, A120EXT)
 */
const LOW_VOLTAGE_EQUIPMENT_IDS: string[] = [
  "ST-T6B0", // T8B0
  "ST-54BO1", // 54B0 #1
  "CA-12BO", // 12BO
  "CA-4BO", // 4BO (T/P)
  "EX-B100", // B100EXT
  "SH-A100", // A100EXT
  "SH-A120", // A120EXT
];

/**
 * 공정 유형 한국어 레이블 — best-effort 매핑.
 * 테이블에 없는 공정은 raw string 그대로 표시된다 (폴백).
 */
const PROCESS_LABELS: Record<string, string> = {
  drawing: "신선",
  stranding: "연선",
  lv_insulation: "저압절연",
  hv_insulation: "고압절연",
  taping: "테이핑",
  cabling: "연합",
  lv_jacketing: "저압시스",
  hv_jacketing: "고압시스",
  neutral_wire: "중성선",
};

/**
 * 전압별 공정 분류 — 설비 데이터에서 동적으로 분류하기 위한 기준.
 * LV: 저압절연/저압시스, HV: 고압절연/고압시스/테이핑/연합/중성선
 * 미분류 공정(신선/연선 제외)은 고압(HV) 그룹에 포함된다.
 */
const LV_ONLY_PROCESS: Set<string> = new Set(["lv_insulation", "lv_jacketing"]);

type VoltageMode = "lv" | "hv";

/** ViewFilter: 스케줄러 뷰 필터 컨트롤 */
export function ViewFilter() {
  const viewFilter = useScheduleStore((s) => s.viewFilter);
  const setViewFilter = useScheduleStore((s) => s.setViewFilter);
  const equipment = useScheduleStore((s) => s.equipment);

  // 전압 토글 로컬 상태 (voltage 모드일 때 사용)
  const [voltageMode, setVoltageMode] = useState<VoltageMode>("lv");

  // 저압만 퀵토글 — 수동 계획표 7개 설비만 보이도록 즉시 전환
  const isLvOnly =
    viewFilter.filterType === "voltage" &&
    viewFilter.filterValue.length === LOW_VOLTAGE_EQUIPMENT_IDS.length &&
    LOW_VOLTAGE_EQUIPMENT_IDS.every((id) =>
      viewFilter.filterValue.includes(id),
    );

  function toggleLvOnly() {
    if (isLvOnly) {
      // 이미 저압만 상태 → 전체로 복귀
      setViewFilter({ filterType: "all", filterValue: [] });
    } else {
      // 저압 7개 설비만 표시
      setViewFilter({
        filterType: "voltage",
        filterValue: LOW_VOLTAGE_EQUIPMENT_IDS,
      });
    }
  }

  // 설비 데이터에서 중복 없이 공정 목록을 동적으로 생성한다.
  const processOptions = useMemo(() => {
    const types = [...new Set(equipment.map((eq) => eq.process_type))];
    return types.map((t) => ({ label: PROCESS_LABELS[t] ?? t, value: t }));
  }, [equipment]);

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
    // 설비 데이터에서 공통(항상 표시) + 해당 전압 공정에 해당하는 설비 id를 수집한다.
    // 미등록 공정 타입은 고압(hv) 그룹으로 fallback 처리된다.
    const ids = equipment
      .filter((eq) => {
        if (ALWAYS_SHOWN_PROCESS.includes(eq.process_type)) return true;
        if (mode === "lv") return LV_ONLY_PROCESS.has(eq.process_type);
        // hv: 공통/LV 이외의 모든 공정
        return !LV_ONLY_PROCESS.has(eq.process_type);
      })
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
      className="flex items-center gap-3 px-4 py-2 bg-white"
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

      {/* 저압만 퀵토글 — 수동 계획표 기준 7개 저압 설비만 표시 */}
      <div className="w-px h-4 bg-gray-200 mx-1" />
      <button
        onClick={toggleLvOnly}
        className={[
          "px-3 py-1.5 text-xs font-medium rounded-md transition-colors border",
          isLvOnly
            ? "text-white border-transparent"
            : "text-gray-600 bg-white border-gray-200 hover:bg-gray-50",
        ].join(" ")}
        style={isLvOnly ? { backgroundColor: "#1565C0" } : {}}
        title="수동 계획표 기준 저압 설비 7종만 표시 (T8B0, 54B0#1, 12BO, 4BO, B100EXT, A100EXT, A120EXT)"
      >
        {isLvOnly ? "전체" : "저압만"}
      </button>

      {/* 공정별 서브 필터 — 설비 데이터에서 동적으로 생성 */}
      {activeType === "process" && (
        <>
          <div className="w-px h-4 bg-gray-300 mx-1" />
          <div className="flex flex-wrap gap-1">
            {processOptions.map((opt) => {
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
