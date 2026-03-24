"use client";

/**
 * ZoomControl.tsx
 *
 * 타임라인 줌 레벨(주 / 일 / 시간)을 선택하는 버튼 그룹.
 * 활성 버튼은 KBI Red(#C41230)로 강조 표시한다.
 */

import { useScheduleStore } from "../store/scheduleStore";
import type { ZoomLevel } from "../types";

const ZOOM_OPTIONS: { value: ZoomLevel; label: string }[] = [
  { value: "week", label: "주" },
  { value: "day", label: "일" },
  { value: "hour", label: "시간" },
];

export function ZoomControl() {
  const zoomLevel = useScheduleStore((s) => s.zoomLevel);
  const setZoomLevel = useScheduleStore((s) => s.setZoomLevel);

  return (
    <div className="flex items-center gap-1.5">
      <span className="text-[10px] font-semibold text-gray-500 uppercase tracking-wide">
        줌
      </span>
      <div className="flex rounded-md overflow-hidden border border-gray-200">
        {ZOOM_OPTIONS.map(({ value, label }, idx) => (
          <button
            key={value}
            onClick={() => setZoomLevel(value)}
            className={[
              "px-3 py-1.5 text-xs font-medium transition-colors",
              idx > 0 ? "border-l border-gray-200" : "",
              zoomLevel === value
                ? "text-white"
                : "text-gray-700 bg-white hover:bg-gray-50",
            ].join(" ")}
            style={zoomLevel === value ? { backgroundColor: "#C41230" } : {}}
          >
            {label}
          </button>
        ))}
      </div>
    </div>
  );
}
