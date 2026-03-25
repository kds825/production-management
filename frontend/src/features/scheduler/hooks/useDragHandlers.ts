"use client";

/**
 * useDragHandlers.ts
 *
 * 이 파일은 dnd-timeline 제거 이후 사용되지 않는다.
 * 드래그 핸들링 로직은 page.tsx의 DndContext onDragEnd로 이전되었다.
 * 기존 import가 존재할 수 있으므로 빈 폴백 export를 유지한다.
 */

export function useDragHandlers() {
  return {
    onDragEnd: () => {},
    onResizeEnd: () => {},
  };
}
