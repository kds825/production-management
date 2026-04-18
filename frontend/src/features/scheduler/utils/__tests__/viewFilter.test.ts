import { describe, it, expect } from "vitest";
import { filterEquipmentByView, taskMatchesViewFilter } from "../viewFilter";
import type { Equipment, ScheduleTask } from "../../types";

function eq(id: string, process_type: string): Equipment {
  return {
    id,
    name: id,
    process_type,
    capabilities: [],
    capacity_tons_per_month: 0,
    status: "active",
  } as Equipment;
}

function task(id: string, equipment_id: string): ScheduleTask {
  return {
    id,
    equipment_id,
    start: new Date(),
    end: new Date(),
    status: "planned",
  } as ScheduleTask;
}

describe("filterEquipmentByView", () => {
  const base: Equipment[] = [
    eq("ST-T6B0", "연선"),
    eq("EX-B100", "저압절연"),
    eq("EX-CV1", "고압절연"),
    eq("SH-A100", "저압시스"),
    eq("SH-A150", "고압시스"),
    eq("DRAW-1", "신선"), // 신선은 항상 숨김
  ];

  it("all 필터 — 신선 제외 전체", () => {
    const r = filterEquipmentByView(base, "all", []);
    expect(r.map((e) => e.id)).not.toContain("DRAW-1");
    expect(r.length).toBe(5);
  });

  it("voltage 필터 (저압만) — filterValue 의 id 만", () => {
    const r = filterEquipmentByView(base, "voltage", [
      "ST-T6B0",
      "EX-B100",
      "SH-A100",
    ]);
    expect(r.map((e) => e.id).sort()).toEqual([
      "EX-B100",
      "SH-A100",
      "ST-T6B0",
    ]);
  });

  it("voltage 필터 빈 배열 — all 과 동등", () => {
    const r = filterEquipmentByView(base, "voltage", []);
    expect(r.length).toBe(5);
  });

  it("process 필터 — filterValue 의 process_type 만", () => {
    const r = filterEquipmentByView(base, "process", ["고압절연", "고압시스"]);
    expect(r.map((e) => e.id).sort()).toEqual(["EX-CV1", "SH-A150"]);
  });

  it("process 필터 빈 배열 — all 과 동등", () => {
    const r = filterEquipmentByView(base, "process", []);
    expect(r.length).toBe(5);
  });

  it("신선 설비는 어떤 필터에서도 제외", () => {
    expect(
      filterEquipmentByView(base, "all", []).find((e) => e.id === "DRAW-1"),
    ).toBeUndefined();
    expect(
      filterEquipmentByView(base, "voltage", ["DRAW-1"]).find(
        (e) => e.id === "DRAW-1",
      ),
    ).toBeUndefined();
    expect(
      filterEquipmentByView(base, "process", ["신선"]).find(
        (e) => e.id === "DRAW-1",
      ),
    ).toBeUndefined();
  });
});

describe("taskMatchesViewFilter", () => {
  it("task.equipment_id 가 set 에 있으면 true", () => {
    const t = task("T1", "EX-B100");
    expect(taskMatchesViewFilter(t, new Set(["EX-B100", "SH-A100"]))).toBe(
      true,
    );
  });

  it("task.equipment_id 가 set 에 없으면 false", () => {
    const t = task("T1", "EX-CV1");
    expect(taskMatchesViewFilter(t, new Set(["EX-B100"]))).toBe(false);
  });

  it("빈 set 은 어떤 task 도 통과시키지 않음", () => {
    const t = task("T1", "EX-B100");
    expect(taskMatchesViewFilter(t, new Set())).toBe(false);
  });
});
