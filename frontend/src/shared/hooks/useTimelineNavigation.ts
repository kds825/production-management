"use client";

import { useEffect, useRef } from "react";
import { useScheduleStore } from "@/features/scheduler/store/scheduleStore";
import type { ZoomLevel } from "@/features/scheduler/types";

const MS_PER_DAY = 24 * 60 * 60 * 1000;
const MS_PER_HOUR = 60 * 60 * 1000;

// 휠 1틱당 이동할 시간 단위 — 줌 레벨에 따라 결정 (페이지 플립 느낌)
const WHEEL_STEP_MS: Record<ZoomLevel, number> = {
  week: 7 * MS_PER_DAY,
  day: MS_PER_DAY,
  hour: MS_PER_HOUR,
};

// macOS 트랙패드/Magic Mouse 모멘텀이 1 swipe에 30~50개 이벤트를 발사하므로,
// 스로틀로 1 swipe = 1 step 으로 고정. (사용자가 빠른 다중 이동을 원하면 여러 번 굴림)
const WHEEL_THROTTLE_MS = 150;

/**
 * useTimelineNavigation
 *
 * 타임라인 Gantt 영역의 마우스/스크롤 기반 패닝(좌우 이동)을 처리한다.
 * rangeStart를 이동시켜 블록 위치를 재계산하는 방식으로 동작한다.
 *
 * 지원 입력:
 * 1. 가운데 마우스 버튼 드래그
 * 2. 좌클릭 드래그 (data-draggable 속성 없는 빈 영역)
 * 3. Shift+휠 스크롤 또는 트랙패드 수평 제스처 — 줌 단위만큼 페이지 플립
 *
 * @param dayWidth  현재 줌 레벨의 픽셀/일 — 드래그 패닝의 px→ms 변환에 사용
 */
export function useTimelineNavigation(dayWidth: number) {
  const containerRef = useRef<HTMLDivElement>(null);
  const setRange = useScheduleStore((s) => s.setRange);
  const rangeRef = useRef(useScheduleStore.getState().range);
  const zoomLevelRef = useRef(useScheduleStore.getState().zoomLevel);

  // range/zoomLevel이 바뀔 때마다 ref 동기화 (클로저에서 최신값 참조)
  useEffect(() => {
    return useScheduleStore.subscribe((state) => {
      rangeRef.current = state.range;
      zoomLevelRef.current = state.zoomLevel;
    });
  }, []);

  useEffect(() => {
    if (!containerRef.current) return;
    const container: HTMLDivElement = containerRef.current;

    let isPanning = false;
    let startX = 0;
    let startRangeStart = 0;
    let lastWheelAt = 0;

    function isDraggableTarget(target: EventTarget | null): boolean {
      if (!(target instanceof Element)) return false;
      return !!target.closest("[data-draggable]");
    }

    function pxToMs(px: number): number {
      const dw = dayWidth > 0 ? dayWidth : 80;
      return (px / dw) * MS_PER_DAY;
    }

    function onMouseDown(e: MouseEvent) {
      if (e.button === 1) {
        e.preventDefault();
        isPanning = true;
      } else if (e.button === 0 && !isDraggableTarget(e.target)) {
        isPanning = true;
      }

      if (!isPanning) return;
      startX = e.clientX;
      startRangeStart = rangeRef.current.start;
      container.style.cursor = "grabbing";
      container.style.userSelect = "none";
    }

    function onMouseMove(e: MouseEvent) {
      if (!isPanning) return;
      const dx = e.clientX - startX;
      const deltaMs = pxToMs(-dx); // 오른쪽 드래그 → 과거 방향
      const duration = rangeRef.current.end - rangeRef.current.start;
      const newStart = startRangeStart + deltaMs;
      setRange({ start: newStart, end: newStart + duration });
    }

    function onMouseUp() {
      if (!isPanning) return;
      isPanning = false;
      container.style.cursor = "";
      container.style.userSelect = "";
    }

    function onWheel(e: WheelEvent) {
      // 수평 의도가 있을 때만 가로 패닝: Shift+휠 또는 트랙패드 수평 제스처
      if (!(e.shiftKey || Math.abs(e.deltaX) > Math.abs(e.deltaY))) return;
      e.preventDefault();

      const rawDelta = e.shiftKey ? e.deltaY : e.deltaX;
      if (rawDelta === 0) return;

      // 모멘텀 누적 차단: 첫 이벤트만 처리하고 throttle 윈도우 내 후속 이벤트는 무시.
      // 픽셀 누적 방식을 쓰면 dayWidth가 크면 한 swipe로 수십 일 점프하는 문제가 재발.
      const now = e.timeStamp;
      if (now - lastWheelAt < WHEEL_THROTTLE_MS) return;
      lastWheelAt = now;

      const direction = rawDelta > 0 ? 1 : -1;
      const stepMs = WHEEL_STEP_MS[zoomLevelRef.current];
      const { start, end } = rangeRef.current;
      setRange({
        start: start + direction * stepMs,
        end: end + direction * stepMs,
      });
    }

    container.addEventListener("mousedown", onMouseDown);
    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup", onMouseUp);
    container.addEventListener("wheel", onWheel, { passive: false });

    return () => {
      container.removeEventListener("mousedown", onMouseDown);
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup", onMouseUp);
      container.removeEventListener("wheel", onWheel);
    };
  }, [dayWidth, setRange]);

  return containerRef;
}
