import { describe, it, expect, beforeEach, vi } from "vitest";

// store 의 static import (refreshTasks) 가 실제 네트워크 호출을 타지 않도록 차단.
// selectTask 경로에서는 사용되지 않지만 setState 전후 다른 액션 호출 가능성에 대비.
vi.mock("../../hooks/useScheduleData", () => ({
  refreshTasks: vi.fn().mockResolvedValue(undefined),
}));

import { useScheduleStore } from "../scheduleStore";
import type { ScheduleTask, Equipment } from "../../types";

function mkTask(id: string, eq: string, preds: string[] = []): ScheduleTask {
  return {
    id,
    order_id: `o-${id}`,
    equipment_id: eq,
    product: "CV",
    spec: "25SQ",
    core_count: 1,
    color: "흑",
    start: new Date(),
    end: new Date(),
    volume_m: 100,
    line_speed_m_per_min: 10,
    priority: "normal",
    status: "planned",
    predecessors: preds,
    notes: "",
    changeover_min: 0,
  };
}
function mkEq(id: string, p: string): Equipment {
  return {
    id,
    name: id,
    process_type: p,
    capabilities: [],
    capacity_tons_per_month: 100,
    status: "active",
  };
}

describe("store selectedChainIds / selectedArrows", () => {
  beforeEach(() => {
    useScheduleStore.setState({
      tasks: [mkTask("s", "e1"), mkTask("i", "e2", ["s"])],
      equipment: [mkEq("e1", "연선"), mkEq("e2", "저압절연")],
      selectedTaskId: null,
      selectedChainIds: null,
      selectedArrows: [],
    });
  });

  it("selectTask(id) 가 chainIds + arrows 계산해 저장", () => {
    useScheduleStore.getState().selectTask("s");
    const s = useScheduleStore.getState();
    expect(s.selectedTaskId).toBe("s");
    expect(s.selectedChainIds?.has("s")).toBe(true);
    expect(s.selectedChainIds?.has("i")).toBe(true);
    expect(s.selectedArrows).toEqual([{ fromId: "s", toId: "i" }]);
  });

  it("selectTask(null) 은 chainIds/arrows 둘 다 리셋", () => {
    useScheduleStore.getState().selectTask("s");
    useScheduleStore.getState().selectTask(null);
    const s = useScheduleStore.getState();
    expect(s.selectedTaskId).toBeNull();
    expect(s.selectedChainIds).toBeNull();
    expect(s.selectedArrows).toEqual([]);
  });

  it("고아 task (chain size 1) → selectedChainIds=null (R6)", () => {
    useScheduleStore.setState({
      tasks: [mkTask("orphan", "e1")],
      equipment: [mkEq("e1", "연선")],
    });
    useScheduleStore.getState().selectTask("orphan");
    const s = useScheduleStore.getState();
    expect(s.selectedTaskId).toBe("orphan");
    expect(s.selectedChainIds).toBeNull(); // dim 생략
    expect(s.selectedArrows).toEqual([]);
  });
});
