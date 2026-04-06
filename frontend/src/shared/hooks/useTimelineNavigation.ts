"use client";

import { useEffect, useRef } from "react";
import { useScheduleStore } from "@/features/scheduler/store/scheduleStore";

const MS_PER_DAY = 24 * 60 * 60 * 1000;

/**
 * useTimelineNavigation
 *
 * 타임라인 Gantt 영역의 마우스/스크롤 기반 패닝(좌우 이동)을 처리한다.
 * rangeStart를 이동시켜 블록 위치를 재계산하는 방식으로 동작한다.
 *
 * 지원 입력:
 * 1. 가운데 마우스 버튼 드래그
 * 2. 좌클릭 드래그 (data-draggable 속성 없는 빈 영역)
 * 3. Shift+휠 스크롤 또는 트랙패드 수평 제스처
 *
 * @param dayWidth  현재 줌 레벨의 픽셀/일 — 픽셀을 ms로 변환할 때 사용
 */
export function useTimelineNavigation(dayWidth: number) {
  const containerRef = useRef<HTMLDivElement>(null);
  const setRange = useScheduleStore((s) => s.setRange);
  const rangeRef = useRef(useScheduleStore.getState().range);

  // range가 바뀔 때마다 ref 동기화 (클로저에서 최신값 참조)
  useEffect(() => {
    return useScheduleStore.subscribe((state) => {
      rangeRef.current = state.range;
    });
  }, []);

  useEffect(() => {
    if (!containerRef.current) return;
    const container: HTMLDivElement = containerRef.current;

    let isPanning = false;
    let startX = 0;
    let startRangeStart = 0;

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
      // Shift+휠 또는 트랙패드 수평 제스처 (deltaX != 0)
      if (e.shiftKey || Math.abs(e.deltaX) > Math.abs(e.deltaY)) {
        e.preventDefault();
        const delta = e.shiftKey ? e.deltaY : e.deltaX;
        const deltaMs = pxToMs(delta);
        const { start, end } = rangeRef.current;
        setRange({ start: start + deltaMs, end: end + deltaMs });
      }
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
