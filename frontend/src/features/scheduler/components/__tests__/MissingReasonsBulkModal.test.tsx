/**
 * MissingReasonsBulkModal — pure helper 계약 + view contract 테스트.
 *
 * 프로젝트 테스트 환경 제약 (TaskFormModal.test.tsx 참고):
 *   - vitest `environment: "node"`, JSDOM/RTL 미도입.
 *   - useEffect 가 돌지 않으므로 모달의 fetch 흐름은 E2E (Playwright/browse) 담당.
 *
 * 따라서 본 파일은:
 *   1) 모달이 export 한 pure helper (summarize / format / bulk-apply / pending)
 *      를 단위 검증.
 *   2) view contract — open=false 면 null, open=true 면 헤더가 렌더되는지 확인
 *      (renderToStaticMarkup 으로 markup 문자열 검증).
 */
import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

import type { MissingReasonItem } from "../../api/missingReasons";
import {
  MissingReasonsBulkModal,
  RowState,
  applyBulkReasonToRows,
  computePendingUpdates,
  formatTime,
  shortChangeSet,
  summarizeTaskChange,
} from "../MissingReasonsBulkModal";

describe("shortChangeSet", () => {
  it("36자 uuid 를 앞 8자로 줄인다", () => {
    expect(shortChangeSet("abcdef01-2345-6789-abcd-ef0123456789")).toBe(
      "abcdef01",
    );
  });

  it("8자 이하 입력은 그대로 반환", () => {
    expect(shortChangeSet("short")).toBe("short");
    expect(shortChangeSet("12345678")).toBe("12345678");
  });
});

describe("formatTime", () => {
  it("ISO datetime 에서 HH:MM 부분만 추출", () => {
    expect(formatTime("2026-04-25T14:30:00")).toBe("14:30");
  });

  it("null/undefined/빈 문자열은 빈 문자열로", () => {
    expect(formatTime(null)).toBe("");
    expect(formatTime(undefined)).toBe("");
    expect(formatTime("")).toBe("");
  });

  it("ISO 가 아닌 입력은 그대로 반환 (fallback)", () => {
    expect(formatTime("garbage")).toBe("garbage");
  });
});

describe("summarizeTaskChange", () => {
  function mkItem(over: Partial<MissingReasonItem> = {}): MissingReasonItem {
    return {
      change_set_id: "cs-1",
      created_at: "2026-04-25T08:00:00",
      tasks: [],
      ...over,
    };
  }

  it("task 가 없으면 '영향 task 없음' 으로 압축", () => {
    const s = summarizeTaskChange(mkItem());
    expect(s.label).toContain("영향 task 없음");
    expect(s.before).toBe("");
    expect(s.after).toBe("");
  });

  it("batch_group 이 있으면 그것을 label 우선", () => {
    const s = summarizeTaskChange(
      mkItem({
        tasks: [
          {
            task_id: "100",
            batch_group: "bg-A-001",
            equipment_code: "EX-B100",
            before: {
              start: "2026-04-25T08:00:00",
              end: "2026-04-25T16:00:00",
              equipment_code: "EX-B100",
            },
            after: {
              start: "2026-04-25T09:00:00",
              end: "2026-04-25T17:00:00",
              equipment_code: "EX-B100",
            },
          },
        ],
      }),
    );
    expect(s.label).toBe("bg-A-001");
    expect(s.before).toBe("08:00–16:00");
    expect(s.after).toBe("09:00–17:00");
  });

  it("batch_group 이 null 이면 task id + equipment 로 fallback", () => {
    const s = summarizeTaskChange(
      mkItem({
        tasks: [
          {
            task_id: "200",
            batch_group: null,
            equipment_code: "EX-B100",
            before: null,
            after: null,
          },
        ],
      }),
    );
    expect(s.label).toBe("task 200 · EX-B100");
  });

  it("task 가 여러 개면 첫 task 만 label + '외 N건'", () => {
    const s = summarizeTaskChange(
      mkItem({
        tasks: [
          {
            task_id: "1",
            batch_group: "bg-A",
            equipment_code: null,
            before: null,
            after: null,
          },
          {
            task_id: "2",
            batch_group: "bg-B",
            equipment_code: null,
            before: null,
            after: null,
          },
          {
            task_id: "3",
            batch_group: "bg-C",
            equipment_code: null,
            before: null,
            after: null,
          },
        ],
      }),
    );
    expect(s.label).toBe("bg-A 외 2건");
  });
});

describe("applyBulkReasonToRows", () => {
  it("선택된 행에만 reason 일괄 적용", () => {
    const before: Record<string, RowState> = {
      a: { selected: true, reason: null },
      b: { selected: false, reason: null },
      c: { selected: true, reason: "현장 긴급" },
    };
    const after = applyBulkReasonToRows(before, "납기 변경");
    expect(after.a).toEqual({ selected: true, reason: "납기 변경" });
    // 미선택 행은 건드리지 않음 — 운영자가 명시적으로 제외한 행 보존.
    expect(after.b).toEqual({ selected: false, reason: null });
    // 선택된 행은 기존 reason 도 덮어쓴다 (일괄 적용 의도).
    expect(after.c).toEqual({ selected: true, reason: "납기 변경" });
  });

  it("새 객체를 반환 (immutability)", () => {
    const before: Record<string, RowState> = {
      a: { selected: true, reason: null },
    };
    const after = applyBulkReasonToRows(before, "납기 변경");
    expect(after).not.toBe(before);
    expect(after.a).not.toBe(before.a);
  });
});

describe("computePendingUpdates", () => {
  it("선택 + reason 둘 다 있는 행만 payload 로 포함", () => {
    const rows: Record<string, RowState> = {
      a: { selected: true, reason: "납기 변경" },
      b: { selected: false, reason: "현장 긴급" }, // 미선택 → 제외
      c: { selected: true, reason: null }, // 사유 미정 → 제외
      d: { selected: true, reason: "자재 부족" },
    };
    const updates = computePendingUpdates(rows);
    expect(updates).toEqual([
      { change_set_id: "a", reason: "납기 변경" },
      { change_set_id: "d", reason: "자재 부족" },
    ]);
  });

  it("빈 입력은 빈 배열", () => {
    expect(computePendingUpdates({})).toEqual([]);
  });
});

describe("MissingReasonsBulkModal — view contract", () => {
  it("open=false 면 null 렌더", () => {
    const html = renderToStaticMarkup(
      <MissingReasonsBulkModal open={false} onClose={() => {}} />,
    );
    expect(html).toBe("");
  });

  it("open=true 면 dialog role + 한국어 헤더 노출", () => {
    const html = renderToStaticMarkup(
      <MissingReasonsBulkModal open={true} onClose={() => {}} />,
    );
    expect(html).toContain('role="dialog"');
    expect(html).toContain("사유 미기록");
    // useEffect 가 안 돌아 loading 상태로 시작 — "불러오는 중..." 가 노출되어야 함.
    expect(html).toContain("불러오는 중");
  });
});
