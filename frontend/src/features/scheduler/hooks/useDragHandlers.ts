"use client";

import { useCallback } from "react";
import type { DragEndEvent, ResizeEndEvent } from "dnd-timeline";
import { useScheduleStore } from "../store/scheduleStore";

/**
 * dnd-timeline 드래그/리사이즈 이벤트 핸들러
 * - onDragEnd: 아이템을 새 위치(row + span)로 이동
 * - onResizeEnd: 아이템의 span(시작/끝 시간)만 변경
 */
export function useDragHandlers() {
  const moveTask = useScheduleStore((s) => s.moveTask);
  const updateTask = useScheduleStore((s) => s.updateTask);

  const onDragEnd = useCallback(
    (event: DragEndEvent) => {
      // dnd-timeline이 주입한 getSpanFromDragEvent로 새 span 추출
      const updatedSpan =
        event.active.data.current?.getSpanFromDragEvent?.(event);
      if (!updatedSpan) return;

      const activeItemId = String(event.active.id);

      // over?.id가 새 rowId(설비 id)
      const newRowId = event.over?.id ? String(event.over.id) : null;
      if (!newRowId) return;

      // span.start/end는 숫자(timestamp) → Date 변환
      const start = new Date(updatedSpan.start);
      const end = new Date(updatedSpan.end);

      moveTask(activeItemId, newRowId, start, end);
    },
    [moveTask],
  );

  const onResizeEnd = useCallback(
    (event: ResizeEndEvent) => {
      const updatedSpan =
        event.active.data.current?.getSpanFromResizeEvent?.(event);
      if (!updatedSpan) return;

      const activeItemId = String(event.active.id);
      const start = new Date(updatedSpan.start);
      const end = new Date(updatedSpan.end);

      updateTask(activeItemId, { span: { start, end } });
    },
    [updateTask],
  );

  return { onDragEnd, onResizeEnd };
}
