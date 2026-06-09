/**
 * shared — 슬라이스 간 공통으로 쓰는 상수/순수 함수.
 *
 * 왜 분리: 같은 헬퍼를 ordersSlice / batchesSlice 양쪽에서 import 해야 하는데,
 * 슬라이스 파일 간 직접 import 는 의존 방향이 모호해지므로 무상태 유틸은 별도 모듈로 격리.
 */
import type { ScheduleTask } from "../../types";

export const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

/**
 * 블록 변경 시 AI 재분석을 비동기로 트리거 (fire-and-forget).
 * 응답을 기다리지 않으므로 간트 UX 에 영향 없음.
 */
export function fireReanalysis(runLabel: string | null): void {
  if (!runLabel) return;
  fetch(
    `${API_BASE}/pipeline/stage2/${encodeURIComponent(runLabel)}/trigger-reanalysis`,
    { method: "POST" },
  ).catch(() => {
    // 백엔드 미연결 시 무시 — PoC 단계에서 graceful 처리
  });
}

/** timestamp 추출 헬퍼 */
function toMs(d: Date | string | number): number {
  if (typeof d === "number") return d;
  if (d instanceof Date) return d.getTime();
  return new Date(d).getTime();
}

/**
 * Cascade push (양방향): 같은 설비에서 작업이 겹치면 밀어냄.
 *
 * 이동된 블록 기준으로:
 * - 오른쪽에 겹치는 블록 → 오른쪽으로 연쇄 밀기 (forward)
 * - 왼쪽에 겹치는 블록 → 왼쪽으로 연쇄 밀기 (backward)
 *
 * 기계는 동시에 하나의 작업만 할 수 있으므로 겹침 = 0 이 되어야 한다.
 */
export function cascadePush(
  tasks: ScheduleTask[],
  movedTaskId: string,
  equipmentId: string,
): void {
  const indices: number[] = [];
  for (let i = 0; i < tasks.length; i++) {
    if (tasks[i].equipment_id === equipmentId) indices.push(i);
  }
  if (indices.length < 2) return;

  // 시작시간 기준 정렬
  indices.sort((a, b) => toMs(tasks[a].start) - toMs(tasks[b].start));

  // 이동된 블록의 정렬 내 위치 찾기
  const movedPos = indices.findIndex((idx) => tasks[idx].id === movedTaskId);

  // --- Forward push: movedPos 부터 오른쪽으로 ---
  for (let i = Math.max(movedPos, 0); i < indices.length - 1; i++) {
    const curr = tasks[indices[i]];
    const next = tasks[indices[i + 1]];
    const currEnd = toMs(curr.end);
    const nextStart = toMs(next.start);
    if (currEnd > nextStart) {
      const dur = toMs(next.end) - nextStart;
      next.start = new Date(currEnd);
      next.end = new Date(currEnd + dur);
    }
  }

  // --- Backward push: movedPos 부터 왼쪽으로 ---
  for (let i = Math.min(movedPos, indices.length - 1); i > 0; i--) {
    const curr = tasks[indices[i]];
    const prev = tasks[indices[i - 1]];
    const currStart = toMs(curr.start);
    const prevEnd = toMs(prev.end);
    if (prevEnd > currStart) {
      // prev 를 왼쪽으로 밀기: prev.end = curr.start, prev.start = prev.end - duration
      const dur = prevEnd - toMs(prev.start);
      prev.end = new Date(currStart);
      prev.start = new Date(currStart - dur);
    }
  }
}

/**
 * 선행 공정 여부 판단: process_step 이 낮은 작업은 후행 공정이 존재할 수 있다.
 * process_step 값이 있고, 같은 order_id 로 더 높은 step 이 존재하면 선행 공정이다.
 */
export function isPredecessorProcess(
  task: ScheduleTask,
  allTasks: ScheduleTask[],
): boolean {
  if (task.process_step == null || !task.order_id) return false;
  return allTasks.some(
    (t) =>
      t.id !== task.id &&
      t.order_id === task.order_id &&
      t.process_step != null &&
      t.process_step > task.process_step!,
  );
}
