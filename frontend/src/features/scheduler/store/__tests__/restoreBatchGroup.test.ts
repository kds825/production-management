import { describe, it, expect, beforeEach, vi } from "vitest";

// useScheduleData 모듈을 먼저 mock 해야 store의 static import가 spy를 주입받는다.
// restoreBatchGroup 은 refreshTasks() 를 호출하여 Gantt 재렌더링을 트리거하므로
// 테스트에서 실제 HTTP 요청 대신 no-op 로 대체한다.
vi.mock("../../hooks/useScheduleData", () => ({
  refreshTasks: vi.fn().mockResolvedValue(undefined),
}));

import { useScheduleStore } from "../scheduleStore";
import { refreshTasks } from "../../hooks/useScheduleData";
import type { BatchGroupSnapshot, InboxItem } from "../../types";

/**
 * BatchGroupSnapshot factory — 인박스에 들어있는 unassigned 상태를 재현.
 * Task 4.4 restoreBatchGroup 은 snapshot 자체를 참조하지 않고 batch_group 키만 사용하므로
 * 필수 필드만 의미있게 채움.
 */
const makeSnapshot = (batchGroup: string): BatchGroupSnapshot => ({
  batch_group: batchGroup,
  customer: "삼성",
  spec: "25SQ",
  color: "흑",
  total_length_m: 1000,
  delivery_date: "2026-04-25",
  processes: [{ process: "연선", equipment_group: "연선" }],
  order_count: 1,
  unassign_reason: "자재지연",
});

describe("restoreBatchGroup", () => {
  beforeEach(() => {
    const item: InboxItem = { kind: "batch_group", group: makeSnapshot("g1") };
    useScheduleStore.setState({
      tasks: [],
      unscheduledItems: [item],
      inFlightBatchGroups: new Set<string>(),
      runLabel: null,
    });
    vi.mocked(refreshTasks).mockClear();
  });

  it("200 성공: unscheduledItems 제거 + refreshTasks 호출", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () =>
        Promise.resolve({
          batch_group: "g1",
          restored_tasks: ["t1", "t2", "t3"],
          conflicts: [],
          idempotent: false,
        }),
    }) as unknown as typeof fetch;

    await useScheduleStore.getState().restoreBatchGroup("g1");

    const s = useScheduleStore.getState();
    expect(s.unscheduledItems).toHaveLength(0);
    expect(refreshTasks).toHaveBeenCalledOnce();
    expect(s.inFlightBatchGroups.has("g1")).toBe(false);
  });

  it("409 conflict: 인박스 유지 + refreshTasks 미호출 (warning toast)", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 409,
      json: () =>
        Promise.resolve({
          detail: { conflicts: [{ task_id: "t1" }] },
        }),
    }) as unknown as typeof fetch;

    await useScheduleStore.getState().restoreBatchGroup("g1");

    const s = useScheduleStore.getState();
    expect(s.unscheduledItems).toHaveLength(1);
    expect(refreshTasks).not.toHaveBeenCalled();
    expect(s.inFlightBatchGroups.has("g1")).toBe(false);
  });
});
