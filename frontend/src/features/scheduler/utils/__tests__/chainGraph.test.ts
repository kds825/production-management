import { describe, it, expect } from "vitest";
import { buildChain } from "../chainGraph";
import type { ScheduleTask, Equipment } from "../../types";

function task(
  id: string,
  equipmentId: string,
  predecessors: string[] = [],
): ScheduleTask {
  return {
    id,
    order_id: `ord-${id}`,
    equipment_id: equipmentId,
    product: "CV",
    spec: "25SQ",
    core_count: 1,
    color: "흑",
    start: new Date("2026-04-20T08:00:00Z"),
    end: new Date("2026-04-20T12:00:00Z"),
    volume_m: 1000,
    line_speed_m_per_min: 10,
    priority: "normal",
    status: "planned",
    predecessors,
    notes: "",
    changeover_min: 0,
  };
}

function equip(id: string, processType: string): Equipment {
  return {
    id,
    name: id,
    process_type: processType,
    capabilities: [],
    capacity_tons_per_month: 100,
    status: "active",
  };
}

describe("buildChain", () => {
  it("단심 3단계", () => {
    const r = buildChain(
      "a",
      [task("a", "e1"), task("b", "e2", ["a"]), task("c", "e3", ["b"])],
      [equip("e1", "연선"), equip("e2", "저압절연"), equip("e3", "저압시스")],
    );
    expect(r.chainIds).toEqual(new Set(["a", "b", "c"]));
    expect(r.arrows).toHaveLength(2);
    expect(r.truncated).toBe(false);
  });

  it("61연선 fan-in (ST 클릭)", () => {
    const r = buildChain(
      "st",
      [
        task("core", "e1"),
        task("st", "e1", ["core"]),
        task("ins", "e2", ["st"]),
      ],
      [equip("e1", "연선"), equip("e2", "저압절연")],
    );
    expect(r.chainIds).toEqual(new Set(["core", "st", "ins"]));
    expect(r.arrows).toHaveLength(2);
    expect(r.arrows).toContainEqual({ fromId: "core", toId: "ins" });
    expect(r.arrows).toContainEqual({ fromId: "st", toId: "ins" });
  });

  it("CORE 클릭 forward 도달", () => {
    const r = buildChain(
      "core",
      [
        task("core", "e1"),
        task("st", "e1", ["core"]),
        task("ins", "e2", ["st"]),
      ],
      [equip("e1", "연선"), equip("e2", "저압절연")],
    );
    expect(r.chainIds.size).toBe(3);
  });

  it("시스 색상 fan-out (연선 클릭)", () => {
    const r = buildChain(
      "s",
      [
        task("s", "e1"),
        task("i", "e2", ["s"]),
        task("h1", "e3", ["i"]),
        task("h2", "e3", ["i"]),
        task("h3", "e3", ["i"]),
      ],
      [equip("e1", "연선"), equip("e2", "저압절연"), equip("e3", "저압시스")],
    );
    expect(r.chainIds.size).toBe(5);
    expect(r.arrows).toHaveLength(4);
  });

  it("시스(흑) 클릭 backward-only — 다른 색상 제외", () => {
    const r = buildChain(
      "h1",
      [
        task("s", "e1"),
        task("i", "e2", ["s"]),
        task("h1", "e3", ["i"]),
        task("h2", "e3", ["i"]),
        task("h3", "e3", ["i"]),
      ],
      [equip("e1", "연선"), equip("e2", "저압절연"), equip("e3", "저압시스")],
    );
    expect(r.chainIds).toEqual(new Set(["s", "i", "h1"]));
    expect(r.arrows).toHaveLength(2);
  });

  it("시스끼리 predecessor 섞여있어도 same-process 숨김 + 모두 포함", () => {
    const r = buildChain(
      "s",
      [
        task("s", "e1"),
        task("i", "e2", ["s"]),
        task("h1", "e3", ["i"]),
        task("h2", "e3", ["h1"]), // fan-out 잔재
      ],
      [equip("e1", "연선"), equip("e2", "저압절연"), equip("e3", "저압시스")],
    );
    expect(r.chainIds.size).toBe(4);
    expect(r.arrows).toHaveLength(3);
    expect(r.arrows).toContainEqual({ fromId: "i", toId: "h1" });
    expect(r.arrows).toContainEqual({ fromId: "i", toId: "h2" });
  });

  it("연합·T/P 5단계", () => {
    const r = buildChain(
      "i",
      [
        task("s", "e1"),
        task("i", "e2", ["s"]),
        task("l", "e3", ["i"]),
        task("tp", "e4", ["l"]),
        task("h", "e5", ["tp"]),
      ],
      [
        equip("e1", "연선"),
        equip("e2", "저압절연"),
        equip("e3", "연합"),
        equip("e4", "T/P"),
        equip("e5", "저압시스"),
      ],
    );
    expect(r.chainIds.size).toBe(5);
    expect(r.arrows).toHaveLength(4);
  });

  it("신선 제외", () => {
    const r = buildChain(
      "s",
      [task("d", "e0"), task("s", "e1", ["d"]), task("i", "e2", ["s"])],
      [equip("e0", "신선"), equip("e1", "연선"), equip("e2", "저압절연")],
    );
    expect(r.chainIds.has("d")).toBe(false);
    expect(r.arrows).toHaveLength(1);
  });

  it("고아 task → chain={self}, arrows=0", () => {
    const r = buildChain("a", [task("a", "e1")], [equip("e1", "연선")]);
    expect(r.chainIds).toEqual(new Set(["a"]));
    expect(r.arrows).toHaveLength(0);
  });

  it("cycle 종료", () => {
    const r = buildChain(
      "a",
      [task("a", "e1", ["b"]), task("b", "e2", ["a"])],
      [equip("e1", "연선"), equip("e2", "저압절연")],
    );
    expect(r.chainIds).toEqual(new Set(["a", "b"]));
  });

  it("저압 vs 고압 분리 — arrow 표시", () => {
    const r = buildChain(
      "low",
      [task("low", "e1"), task("high", "e2", ["low"])],
      [equip("e1", "저압절연"), equip("e2", "고압절연")],
    );
    expect(r.arrows).toEqual([{ fromId: "low", toId: "high" }]);
  });

  it("stale pred id 무시", () => {
    const r = buildChain(
      "a",
      [task("a", "e1", ["ghost"])],
      [equip("e1", "연선")],
    );
    expect(r.chainIds).toEqual(new Set(["a"]));
  });

  it("equipment 매핑 실패 task 제거", () => {
    const r = buildChain(
      "s",
      [task("s", "e1"), task("g", "e-missing", ["s"])],
      [equip("e1", "연선")],
    );
    expect(r.chainIds).toEqual(new Set(["s"]));
  });

  it("클릭 task 없으면 empty", () => {
    const r = buildChain("404", [task("a", "e1")], [equip("e1", "연선")]);
    expect(r.chainIds.size).toBe(0);
  });

  it("stress 100-task < 20ms, truncated=false", () => {
    const tasks: ScheduleTask[] = [];
    const equipment: Equipment[] = [];
    for (let p = 0; p < 5; p++) {
      const proc = ["연선", "저압절연", "연합", "T/P", "저압시스"][p];
      equipment.push(equip(`eq${p}`, proc));
      for (let i = 0; i < 20; i++) {
        const id = `t${p}-${i}`;
        const preds = p === 0 ? [] : [`t${p - 1}-${i}`];
        tasks.push(task(id, `eq${p}`, preds));
      }
    }
    const t0 = performance.now();
    const r = buildChain("t0-0", tasks, equipment);
    expect(performance.now() - t0).toBeLessThan(20);
    expect(r.truncated).toBe(false);
  });
});
