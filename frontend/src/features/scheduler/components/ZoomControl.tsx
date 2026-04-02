"use client";

import { useRef } from "react";
import { useScheduleStore } from "../store/scheduleStore";
import type { ZoomLevel } from "../types";

const ZOOM_OPTIONS: { value: ZoomLevel; label: string }[] = [
  { value: "week", label: "주" },
  { value: "day", label: "일" },
  { value: "hour", label: "시간" },
];

const DAY_MS = 24 * 60 * 60 * 1000;
const WEEK_MS = 7 * DAY_MS;

export function ZoomControl() {
  const zoomLevel = useScheduleStore((s) => s.zoomLevel);
  const setZoomLevel = useScheduleStore((s) => s.setZoomLevel);
  const range = useScheduleStore((s) => s.range);
  const setRange = useScheduleStore((s) => s.setRange);
  const dayWidthScale = useScheduleStore((s) => s.dayWidthScale);
  const setDayWidthScale = useScheduleStore((s) => s.setDayWidthScale);
  const dateInputRef = useRef<HTMLInputElement>(null);

  // 이전: 범위를 1주 전으로 이동
  function handlePrev() {
    setRange({ start: range.start - WEEK_MS, end: range.end - WEEK_MS });
  }

  // 다음: 범위를 1주 후로 이동
  function handleNext() {
    setRange({ start: range.start + WEEK_MS, end: range.end + WEEK_MS });
  }

  // 오늘: 현재 범위 스팬을 유지하며 오늘을 중심으로 이동
  function handleToday() {
    const now = new Date();
    now.setHours(0, 0, 0, 0);
    const center = now.getTime();
    const halfSpan = (range.end - range.start) / 2;
    setRange({ start: center - halfSpan, end: center + halfSpan });
  }

  // 캘린더에서 날짜 선택 시 해당 날짜로 이동
  function handleDateChange(e: React.ChangeEvent<HTMLInputElement>) {
    const val = e.target.value;
    if (!val) return;
    const target = new Date(val);
    target.setHours(0, 0, 0, 0);
    const center = target.getTime();
    const halfSpan = (range.end - range.start) / 2;
    setRange({ start: center - halfSpan, end: center + halfSpan });
    e.target.value = "";
  }

  // 확대(+): dayWidthScale을 2배 → 픽셀 밀도 증가, range는 유지 (스크롤로 탐색)
  function handleZoomIn() {
    const next = dayWidthScale * 2;
    if (next > 32) return; // 최대 32배
    setDayWidthScale(next);
  }

  // 축소(-): dayWidthScale을 절반 → 픽셀 밀도 감소, range는 유지
  function handleZoomOut() {
    const next = dayWidthScale / 2;
    if (next < 0.25) return; // 최소 0.25배
    setDayWidthScale(next);
  }

  const btnBase =
    "px-2 py-1.5 text-xs text-gray-600 bg-white hover:bg-gray-50 transition-colors duration-150";
  const divider = "border-l border-gray-200";

  return (
    <div className="flex items-center gap-2">
      {/* 날짜 이동: < 오늘 > 📅 */}
      <div className="flex items-center rounded-md border border-gray-200 overflow-hidden">
        <button onClick={handlePrev} className={btnBase} title="이전 주">
          <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
            <path
              d="M7.5 2.5L4 6l3.5 3.5"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </button>
        <button
          onClick={handleToday}
          className={`${btnBase} ${divider} px-3 font-medium`}
          title="오늘"
        >
          오늘
        </button>
        <button
          onClick={handleNext}
          className={`${btnBase} ${divider}`}
          title="다음 주"
        >
          <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
            <path
              d="M4.5 2.5L8 6l-3.5 3.5"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </button>
        <button
          onClick={() => dateInputRef.current?.showPicker()}
          className={`${btnBase} ${divider}`}
          title="날짜로 이동"
        >
          <svg
            width="12"
            height="12"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <rect x="3" y="4" width="18" height="18" rx="2" ry="2" />
            <line x1="16" y1="2" x2="16" y2="6" />
            <line x1="8" y1="2" x2="8" y2="6" />
            <line x1="3" y1="10" x2="21" y2="10" />
          </svg>
        </button>
        <input
          ref={dateInputRef}
          type="date"
          onChange={handleDateChange}
          className="absolute opacity-0 pointer-events-none"
          style={{ width: 0, height: 0 }}
          tabIndex={-1}
        />
      </div>

      {/* 확대/축소: - + */}
      <div className="flex items-center rounded-md border border-gray-200 overflow-hidden">
        <button
          onClick={handleZoomOut}
          className={btnBase}
          title="축소 (범위 넓히기)"
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
          className={`${btnBase} ${divider}`}
          title="확대 (범위 좁히기)"
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

      {/* 줌 프리셋: 주 일 시간 */}
      <div className="flex rounded-md overflow-hidden border border-gray-200">
        {ZOOM_OPTIONS.map(({ value, label }, idx) => (
          <button
            key={value}
            onClick={() => {
              setZoomLevel(value);
              setDayWidthScale(1.0); // 프리셋 전환 시 scale 초기화
              // 프리셋 클릭 시 각 줌 레벨에 맞는 범위로 재설정
              const today = new Date();
              today.setHours(0, 0, 0, 0);
              const todayMs = today.getTime();
              if (value === "hour") {
                // 시간 뷰: 오늘 06:00 ~ 익일 06:00 (24시간, 근무 시간 중심)
                const HOUR_MS = 60 * 60 * 1000;
                setRange({
                  start: todayMs + 6 * HOUR_MS,
                  end: todayMs + 30 * HOUR_MS,
                });
              } else if (value === "day") {
                // 일 뷰: 오늘 ± 3일 (7일 뷰)
                setRange({
                  start: todayMs - 3 * DAY_MS,
                  end: todayMs + 3 * DAY_MS,
                });
              } else if (value === "week") {
                // 주 뷰: 오늘 ± 14일 (4주 뷰, 기본값)
                setRange({
                  start: todayMs - 14 * DAY_MS,
                  end: todayMs + 14 * DAY_MS,
                });
              }
            }}
            className={[
              "px-3 py-1.5 text-xs font-medium transition-colors duration-150",
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
