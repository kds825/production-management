"use client";

/**
 * RangeSelector.tsx
 *
 * 타임라인 빈 영역에서 마우스 드래그로 시간 범위를 선택하는 오버레이 컴포넌트.
 * - mousedown → 선택 시작
 * - mousemove → 반투명 회색 오버레이 확장
 * - mouseup   → 선택 완료, store에 prefill 데이터 저장 후 컨텍스트 메뉴 표시
 *
 * 부모(TimelineRowContainer)의 타임라인 div에 마운트된다.
 * valueToPixels / pixelsToValue를 통해 픽셀 ↔ 타임스탬프 변환에
 * useTimelineContext()를 활용한다.
 */

import { useState, useCallback, useRef } from "react";
import { useTimelineContext } from "dnd-timeline";
import { useScheduleStore } from "../store/scheduleStore";

interface SelectionState {
  startX: number; // 컨테이너 기준 픽셀
  currentX: number;
}

interface RangeSelectorProps {
  equipmentId: string;
}

export function RangeSelector({ equipmentId }: RangeSelectorProps) {
  const { pixelsToValue, sidebarWidth } = useTimelineContext();
  const openContextMenu = useScheduleStore((s) => s.openContextMenu);
  const openTaskFormModal = useScheduleStore((s) => s.openTaskFormModal);

  const containerRef = useRef<HTMLDivElement>(null);
  const [selection, setSelection] = useState<SelectionState | null>(null);
  const isDragging = useRef(false);

  const toTimestamp = useCallback(
    (clientX: number): number => {
      const containerLeft =
        containerRef.current?.getBoundingClientRect().left ?? 0;
      // sidebarWidth는 TimelineContext 좌표계에서 이미 제거된 상태
      const pixelOffset = clientX - containerLeft;
      return pixelsToValue(pixelOffset);
    },
    [pixelsToValue],
  );

  const handleMouseDown = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    // 우클릭이나 이미 작업 요소 클릭은 무시
    if (e.button !== 0) return;
    const target = e.target as HTMLElement;
    // task 아이템 위에서는 범위 선택 시작하지 않음
    if (
      target.closest("[data-draggable]") ||
      target.closest("[data-task-item]")
    )
      return;

    isDragging.current = true;
    const containerLeft =
      containerRef.current?.getBoundingClientRect().left ?? 0;
    const startX = e.clientX - containerLeft;

    setSelection({ startX, currentX: startX });
    e.preventDefault();
  }, []);

  const handleMouseMove = useCallback(
    (e: React.MouseEvent<HTMLDivElement>) => {
      if (!isDragging.current || !selection) return;
      const containerLeft =
        containerRef.current?.getBoundingClientRect().left ?? 0;
      const currentX = e.clientX - containerLeft;
      setSelection((prev) => (prev ? { ...prev, currentX } : null));
    },
    [selection],
  );

  const handleMouseUp = useCallback(
    (e: React.MouseEvent<HTMLDivElement>) => {
      if (!isDragging.current || !selection) return;
      isDragging.current = false;

      const containerLeft =
        containerRef.current?.getBoundingClientRect().left ?? 0;
      const endX = e.clientX - containerLeft;
      const minX = Math.min(selection.startX, endX);
      const maxX = Math.max(selection.startX, endX);

      // 최소 10px 이상 드래그해야 범위 선택으로 인식
      if (maxX - minX > 10) {
        const startTs = pixelsToValue(minX);
        const endTs = pixelsToValue(maxX);

        openContextMenu({
          x: e.clientX,
          y: e.clientY,
          type: "empty",
          equipmentId,
          clickTime: new Date(startTs),
        });

        openTaskFormModal({
          mode: "create",
          prefill: {
            equipmentId,
            start: new Date(startTs),
            end: new Date(endTs),
          },
        });
      }

      setSelection(null);
    },
    [selection, pixelsToValue, equipmentId, openContextMenu, openTaskFormModal],
  );

  const handleMouseLeave = useCallback(() => {
    if (isDragging.current) {
      isDragging.current = false;
      setSelection(null);
    }
  }, []);

  // 오버레이 rect 계산
  const overlayStyle = selection
    ? {
        left: Math.min(selection.startX, selection.currentX),
        width: Math.abs(selection.currentX - selection.startX),
      }
    : null;

  return (
    <div
      ref={containerRef}
      style={{
        position: "absolute",
        inset: 0,
        zIndex: 2,
        cursor: isDragging.current ? "col-resize" : "crosshair",
        // 작업 아이템 이벤트가 정상 전달되도록 포인터 이벤트는 투명하게 두되
        // 빈 영역 클릭만 처리한다.
        pointerEvents: "none",
      }}
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onMouseLeave={handleMouseLeave}
    >
      {/* 선택 범위 오버레이 */}
      {overlayStyle && (
        <div
          style={{
            position: "absolute",
            top: 0,
            bottom: 0,
            left: overlayStyle.left,
            width: overlayStyle.width,
            backgroundColor: "rgba(156, 163, 175, 0.25)",
            border: "1px solid rgba(107, 114, 128, 0.4)",
            pointerEvents: "none",
          }}
        />
      )}
    </div>
  );
}
