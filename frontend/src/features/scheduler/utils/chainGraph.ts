import type { ScheduleTask, Equipment } from "../types";
import { isExcludedProcess, isTerminalProcess } from "../constants/processes";

export interface ArrowEdge {
  fromId: string;
  toId: string;
}

export interface ChainGraphResult {
  chainIds: Set<string>;
  arrows: ArrowEdge[];
  truncated: boolean;
}

const MAX_ARROWS = 500;

export function buildChain(
  clickedTaskId: string,
  tasks: readonly ScheduleTask[],
  equipment: readonly Equipment[],
): ChainGraphResult {
  const taskMap = new Map(tasks.map((t) => [t.id, t]));
  const equipMap = new Map(equipment.map((e) => [e.id, e]));
  const getProcess = (tid: string): string | null => {
    const t = taskMap.get(tid);
    const eq = t && equipMap.get(t.equipment_id);
    return eq?.process_type ?? null;
  };

  const clicked = taskMap.get(clickedTaskId);
  const clickedProc = clicked ? getProcess(clickedTaskId) : null;
  if (!clicked || clickedProc === null) {
    return { chainIds: new Set(), arrows: [], truncated: false };
  }

  const successorIndex = new Map<string, string[]>();
  for (const t of tasks) {
    for (const p of t.predecessors) {
      const arr = successorIndex.get(p);
      if (arr) arr.push(t.id);
      else successorIndex.set(p, [t.id]);
    }
  }

  const isBackwardOnly = isTerminalProcess(clickedProc);
  const chainIds = new Set<string>([clickedTaskId]);
  const frontier: string[] = [clickedTaskId];

  while (frontier.length > 0) {
    const cur = frontier.pop()!;
    const curTask = taskMap.get(cur);
    if (!curTask) continue;
    for (const p of curTask.predecessors) {
      if (chainIds.has(p) || !taskMap.has(p)) continue;
      chainIds.add(p);
      frontier.push(p);
    }
    if (!isBackwardOnly) {
      const succs = successorIndex.get(cur);
      if (succs) {
        for (const s of succs) {
          if (chainIds.has(s)) continue;
          chainIds.add(s);
          frontier.push(s);
        }
      }
    }
  }

  // 신선·equipment 매핑 실패 제거
  for (const id of [...chainIds]) {
    const proc = getProcess(id);
    if (proc === null || isExcludedProcess(proc)) chainIds.delete(id);
  }

  const groupByProcess = new Map<string, string[]>();
  for (const id of chainIds) {
    const proc = getProcess(id);
    if (proc === null) continue;
    const arr = groupByProcess.get(proc);
    if (arr) arr.push(id);
    else groupByProcess.set(proc, [id]);
  }

  // 인접 공정 쌍 수집
  const pairs = new Set<string>();
  for (const dst of chainIds) {
    const d = taskMap.get(dst);
    if (!d) continue;
    const dProc = getProcess(dst);
    if (dProc === null) continue;
    for (const src of d.predecessors) {
      if (!chainIds.has(src)) continue;
      const sProc = getProcess(src);
      if (sProc === null || sProc === dProc) continue;
      pairs.add(`${sProc}||${dProc}`);
    }
  }

  const arrows: ArrowEdge[] = [];
  let truncated = false;
  outer: for (const key of pairs) {
    const [sp, dp] = key.split("||");
    const ss = groupByProcess.get(sp) ?? [];
    const ds = groupByProcess.get(dp) ?? [];
    for (const s of ss)
      for (const d of ds) {
        if (arrows.length >= MAX_ARROWS) {
          truncated = true;
          break outer;
        }
        arrows.push({ fromId: s, toId: d });
      }
  }

  return { chainIds, arrows, truncated };
}

/**
 * S7 #13 "항상 표시" 모드 — 선택 무관 모든 pred→succ 쌍의 arrows + 모든 task ID.
 *
 * 단일 task 선택의 `buildChain` 과 달리 chain 탐색이 없고 전체 그래프를 평탄화.
 * Overlay 가 SVG path 를 그릴 때 좌표 산출에 사용 (chainIds 도 동일 set 으로).
 */
export function buildAllChains(
  tasks: readonly ScheduleTask[],
  equipment: readonly Equipment[],
): ChainGraphResult {
  const taskMap = new Map(tasks.map((t) => [t.id, t]));
  const equipMap = new Map(equipment.map((e) => [e.id, e]));
  const getProcess = (tid: string): string | null => {
    const t = taskMap.get(tid);
    const eq = t && equipMap.get(t.equipment_id);
    return eq?.process_type ?? null;
  };

  const chainIds = new Set<string>();
  const pairs = new Set<string>();
  let truncated = false;

  for (const t of tasks) {
    const tProc = getProcess(t.id);
    if (tProc === null || isExcludedProcess(tProc)) continue;
    for (const predId of t.predecessors) {
      if (!taskMap.has(predId)) continue;
      const pProc = getProcess(predId);
      if (pProc === null || isExcludedProcess(pProc) || pProc === tProc)
        continue;
      chainIds.add(t.id);
      chainIds.add(predId);
      const key = `${predId}→${t.id}`;
      if (pairs.has(key)) continue;
      pairs.add(key);
    }
  }

  const arrows: ArrowEdge[] = [];
  for (const key of pairs) {
    if (arrows.length >= MAX_ARROWS) {
      truncated = true;
      break;
    }
    const [fromId, toId] = key.split("→");
    arrows.push({ fromId, toId });
  }

  return { chainIds, arrows, truncated };
}
