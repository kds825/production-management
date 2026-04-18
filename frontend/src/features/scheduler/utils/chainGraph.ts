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
