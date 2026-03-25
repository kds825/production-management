"use client";

import { useEffect, useRef } from "react";

/**
 * useTimelineNavigation
 *
 * 타임라인 Gantt 영역의 마우스/스크롤 기반 패닝(좌우 이동)을 처리한다.
 * scrollLeft를 직접 조작하여 store.range를 건드리지 않는다.
 *
 * 지원 입력:
 * 1. 가운데 마우스 버튼 드래그
 * 2. 좌클릭 드래그 (data-draggable 속성 없는 빈 영역)
 * 3. Shift+휠 스크롤 또는 트랙패드 수평 제스처
 */
export function useTimelineNavigation() {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    // containerRef.current is non-null after the guard above; captured in a
    // typed const so closures below do not need repeated null checks.
    const container: HTMLDivElement = containerRef.current;

    // 패닝 상태
    let isPanning = false;
    let startX = 0;
    let startScrollLeft = 0;

    function isDraggableTarget(target: EventTarget | null): boolean {
      if (!(target instanceof Element)) return false;
      return !!target.closest("[data-draggable]");
    }

    function onMouseDown(e: MouseEvent) {
      // 가운데 마우스 버튼 (button=1) 또는 좌클릭(button=0) on non-draggable
      if (e.button === 1) {
        e.preventDefault();
        isPanning = true;
      } else if (e.button === 0 && !isDraggableTarget(e.target)) {
        isPanning = true;
      }

      if (!isPanning) return;

      startX = e.clientX;
      startScrollLeft = container.scrollLeft;
      container.style.cursor = "grabbing";
      container.style.userSelect = "none";
    }

    function onMouseMove(e: MouseEvent) {
      if (!isPanning) return;
      const dx = e.clientX - startX;
      container.scrollLeft = startScrollLeft - dx;
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
        container.scrollLeft += delta;
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
  }, []);

  return containerRef;
}
