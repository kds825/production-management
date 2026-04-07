"use client";

import { useMemo } from "react";
import { useScheduleStore } from "../store/scheduleStore";
import type { ViewFilterType } from "../types";

/** 저압 설비 — 저압 연선/절연/시스/연합/T·P */
const LV_EQUIPMENT: string[] = [
  "ST-T6B0",
  "ST-54BO1",
  "CA-12BO",
  "CA-4BO",
  "CA-LU",
  "EX-B100",
  "SH-A100",
  "SH-A120",
  "TP-1",
  "TP-2",
  "TP-GD",
];

/** 고압 설비 — 고압 연선(AL6BO)/절연(CV)/시스(A150,B150) */
const HV_EQUIPMENT: string[] = [
  "ST-AL6BO",
  "ST-54BO2",
  "ST-54BO3",
  "ST-30BO",
  "EX-CV1",
  "EX-CV2",
  "SH-B100",
  "SH-A150",
];

const PROCESS_LABELS: Record<string, string> = {
  신선: "신선",
  연선: "연선",
  저압절연: "저압절연",
  고압절연: "고압절연",
  연합: "연합",
  저압시스: "저압시스",
  고압시스: "고압시스",
};

export function ViewFilter() {
  const viewFilter = useScheduleStore((s) => s.viewFilter);
  const setViewFilter = useScheduleStore((s) => s.setViewFilter);
  const equipment = useScheduleStore((s) => s.equipment);

  const activeType = viewFilter.filterType;

  const isLvOnly =
    activeType === "voltage" &&
    LV_EQUIPMENT.every((id) => viewFilter.filterValue.includes(id));

  const isHvOnly =
    activeType === "voltage" &&
    HV_EQUIPMENT.every((id) => viewFilter.filterValue.includes(id));

  const processOptions = useMemo(() => {
    const types = [...new Set(equipment.map((eq) => eq.process_type))].filter(
      (t) => t !== "신선",
    );
    return types.map((t) => ({ label: PROCESS_LABELS[t] ?? t, value: t }));
  }, [equipment]);

  function setAll() {
    setViewFilter({ filterType: "all", filterValue: [] });
  }

  function setLvOnly() {
    if (isLvOnly) return setAll();
    setViewFilter({ filterType: "voltage", filterValue: LV_EQUIPMENT });
  }

  function setHvOnly() {
    if (isHvOnly) return setAll();
    setViewFilter({ filterType: "voltage", filterValue: HV_EQUIPMENT });
  }

  function setProcess() {
    setViewFilter({ filterType: "process", filterValue: [] });
  }

  function toggleProcess(processType: string) {
    const current = viewFilter.filterValue ?? [];
    const next = current.includes(processType)
      ? current.filter((v) => v !== processType)
      : [...current, processType];
    setViewFilter({ filterType: "process", filterValue: next });
  }

  const btn = (active: boolean, color?: string) =>
    [
      "px-3 py-1.5 text-xs font-medium rounded-md transition-colors border",
      active
        ? "text-white border-transparent"
        : "text-gray-600 bg-white border-gray-200 hover:bg-gray-50",
    ].join(" ");

  return (
    <div
      className="flex items-center gap-2 px-4 py-2 bg-white flex-wrap"
      style={{ minHeight: 44 }}
    >
      <span className="text-[10px] font-semibold text-gray-500 uppercase tracking-wide mr-1">
        필터
      </span>

      {/* 전체 / 저압만 / 고압만 / 공정별 */}
      <button
        onClick={setAll}
        className={btn(activeType === "all")}
        style={
          activeType === "all" && !isLvOnly && !isHvOnly
            ? { backgroundColor: "#C41230" }
            : {}
        }
      >
        전체
      </button>

      <button
        onClick={setLvOnly}
        className={btn(isLvOnly)}
        style={isLvOnly ? { backgroundColor: "#1565C0" } : {}}
      >
        저압만
      </button>

      <button
        onClick={setHvOnly}
        className={btn(isHvOnly)}
        style={isHvOnly ? { backgroundColor: "#C41230" } : {}}
      >
        고압만
      </button>

      <div className="w-px h-4 bg-gray-200" />

      <button
        onClick={setProcess}
        className={btn(activeType === "process")}
        style={activeType === "process" ? { backgroundColor: "#4A2C2A" } : {}}
      >
        공정별
      </button>

      {/* 공정별 서브 필터 */}
      {activeType === "process" && (
        <>
          <div className="w-px h-4 bg-gray-300" />
          <div className="flex flex-wrap gap-1">
            {processOptions.map((opt) => {
              const selected = (viewFilter.filterValue ?? []).includes(
                opt.value,
              );
              return (
                <button
                  key={opt.value}
                  onClick={() => toggleProcess(opt.value)}
                  className={[
                    "px-2 py-0.5 text-[10px] rounded border transition-colors",
                    selected
                      ? "text-white border-transparent"
                      : "text-gray-600 bg-white border-gray-200 hover:bg-gray-50",
                  ].join(" ")}
                  style={selected ? { backgroundColor: "#4A2C2A" } : {}}
                >
                  {opt.label}
                </button>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
