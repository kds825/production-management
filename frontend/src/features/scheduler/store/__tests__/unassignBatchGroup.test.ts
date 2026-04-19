import { describe, it, expect, beforeEach, vi } from "vitest";
import { useScheduleStore } from "../scheduleStore";
import type { ScheduleTask } from "../../types";

/**
 * ScheduleTask minimal factory — Task 4.3 unassignBatchGroup action 테스트 전용.
 * action은 batch_group/status/id 외에 snapshot 합성 시 customer/spec/color/volume_m/
 * product/equipment_id/delivery_date/order_id 를 참조하므로 모두 의미있는 값으로 채움.
 */
const makeTask = (
  id: string,
  batch_group: string,
  status: string = "planned",
): ScheduleTask => ({
  id,
  order_id: `ORD-${id}`,
  equipment_id: "EQ-01",
  product: "절연",
  spec: "25SQ",
  core_count: 1,
  color: "흑",
  start: new Date("2026-04-20T08:00:00"),
  end: new Date("2026-04-20T12:00:00"),
  volume_m: 1000,
  line_speed_m_per_min: 10,
  priority: "normal",
  status,
  predecessors: [],
  notes: "",
  changeover_min: 0,
  batch_group,
  customer: "삼성",
  delivery_date: new Date("2026-05-01T00:00:00"),
});

describe("unassignBatchGroup", () => {
  beforeEach(() => {
    // 스토어 초기화 — Zustand setState로 필요한 필드만 리셋
    useScheduleStore.setState({
      tasks: [makeTask("t1", "g1"), makeTask("t2", "g1"), makeTask("t3", "g2")],
      unscheduledItems: [],
      inFlightBatchGroups: new Set<string>(),
      runLabel: null,
    });
    // fetch / toast mock 초기화
    vi.restoreAllMocks();
  });

  it("sends reason in request body and adds snapshot", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () =>
        Promise.resolve({
          batch_group: "g1",
          affected_batches: [1, 2],
          affected_tasks: ["t1", "t2"],
          reason: "자재지연",
          idempotent: false,
        }),
    });
    global.fetch = fetchMock as unknown as typeof fetch;

    await useScheduleStore.getState().unassignBatchGroup("g1", "자재지연");

    // 첫 fetch 호출만 검증 — fireReanalysis는 runLabel이 null이라 호출되지 않음
    expect(fetchMock).toHaveBeenCalled();
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/batch-group/g1/unassign");
    expect(init).toMatchObject({ method: "POST" });
    expect(init.body).toContain('"reason":"자재지연"');

    const s = useScheduleStore.getState();
    expect(s.tasks.map((t) => t.id)).toEqual(["t3"]);
    expect(s.unscheduledItems).toHaveLength(1);
    expect(s.unscheduledItems[0].kind).toBe("batch_group");
    if (s.unscheduledItems[0].kind === "batch_group") {
      expect(s.unscheduledItems[0].group.batch_group).toBe("g1");
      expect(s.unscheduledItems[0].group.unassign_reason).toBe("자재지연");
      // order_count: t1/t2의 order_id 는 모두 고유(ORD-t1, ORD-t2) → 2
      expect(s.unscheduledItems[0].group.order_count).toBe(2);
      // total_length_m: 1000 + 1000
      expect(s.unscheduledItems[0].group.total_length_m).toBe(2000);
    }
    // inFlight 가드는 finally에서 해제됨
    expect(s.inFlightBatchGroups.has("g1")).toBe(false);
  });

  it("rolls back on API failure", async () => {
    global.fetch = vi
      .fn()
      .mockResolvedValue({ ok: false, status: 500 }) as unknown as typeof fetch;

    await useScheduleStore.getState().unassignBatchGroup("g1", "자재지연");

    const s = useScheduleStore.getState();
    expect(s.tasks).toHaveLength(3);
    expect(s.tasks.map((t) => t.id).sort()).toEqual(["t1", "t2", "t3"]);
    expect(s.unscheduledItems).toHaveLength(0);
    expect(s.inFlightBatchGroups.has("g1")).toBe(false);
  });

  it("race guard blocks duplicate in-flight calls", async () => {
    useScheduleStore.setState({
      inFlightBatchGroups: new Set<string>(["g1"]),
    });
    const fetchMock = vi.fn();
    global.fetch = fetchMock as unknown as typeof fetch;

    await useScheduleStore.getState().unassignBatchGroup("g1", "자재지연");

    expect(fetchMock).not.toHaveBeenCalled();
    // tasks는 그대로
    const s = useScheduleStore.getState();
    expect(s.tasks).toHaveLength(3);
  });
});
