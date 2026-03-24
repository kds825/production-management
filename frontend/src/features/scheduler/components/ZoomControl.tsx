"use client";

/**
 * ZoomControl.tsx
 *
 * 줌 프리셋(주/일/시간) + 확대/축소(+/-) 버튼.
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
  const range = useScheduleStore((s) => s.range);
  const setRange = useScheduleStore((s) => s.setRange);

  // 확대: 현재 범위의 중심 기준으로 범위를 50%로 줄임
  function handleZoomIn() {
    if (!range) return;
    const center = (range.start + range.end) / 2;
    const halfSpan = (range.end - range.start) / 4; // 50%로 줄이기
    const minSpan = 6 * 60 * 60 * 1000; // 최소 6시간
    if (halfSpan * 2 < minSpan) return;
    setRange({ start: center - halfSpan, end: center + halfSpan });
  }

  // 축소: 현재 범위의 중심 기준으로 범위를 200%로 확장
  function handleZoomOut() {
    if (!range) return;
    const center = (range.start + range.end) / 2;
    const halfSpan = range.end - range.start; // 200%로 확장
    const maxSpan = 90 * 24 * 60 * 60 * 1000; // 최대 90일
    if (halfSpan * 2 > maxSpan) return;
    setRange({ start: center - halfSpan, end: center + halfSpan });
  }

  return (
    <div className="flex items-center gap-2">
      {/* 확대/축소 버튼 */}
      <div className="flex items-center rounded-md border border-gray-200 overflow-hidden">
        <button
          onClick={handleZoomOut}
          className="px-2 py-1.5 text-xs text-gray-600 bg-white hover:bg-gray-50 transition-colors border-r border-gray-200"
          title="축소"
        >
          <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
            <path
              d="M2.5 6h7"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
            />
          </svg>
        </button>
        <button
          onClick={handleZoomIn}
          className="px-2 py-1.5 text-xs text-gray-600 bg-white hover:bg-gray-50 transition-colors"
          title="확대"
        >
          <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
            <path
              d="M6 2.5v7M2.5 6h7"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
            />
          </svg>
        </button>
      </div>

      {/* 프리셋 버튼 */}
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
