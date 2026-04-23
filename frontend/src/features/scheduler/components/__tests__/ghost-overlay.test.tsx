/**
 * Task 22 — Gantt ghost overlay contract.
 *
 * 프로젝트 테스트 환경 제약:
 * - vitest `environment: "node"` 이며 JSDOM / RTL 미도입.
 * - `SchedulerView` 는 zustand store 와 ResizeObserver / 실제 DOM 측정에 강하게
 *   결합되어 있어 SSR 스냅샷으로는 렌더 자체가 의미 없는 빈 트리로 전락.
 * - 따라서 본 파일은 SchedulerView 에서 export 한 **순수 헬퍼** `getProposalFor`
 *   의 계약을 단위 검증하고, 실제 DOM 레벨(ghost data-testid, focus outline)
 *   렌더 검증은 Playwright E2E (Task 25) 로 위임한다.
 *
 * 왜 이 레벨에서 stop:
 * - `getProposalFor` 는 push/pull 병합 순서와 "없으면 null" 계약이 ghost 렌더의
 *   유일한 결정 지점이다. 이 함수가 정확하면 ghost 렌더 로직도 정상.
 */
import { describe, it, expect } from "vitest";
import { getProposalFor } from "../SchedulerView";
import type {
  CascadePreviewResponse,
  PushEntry,
} from "../../api/cascade.types";

function mkEntry(id: string, overrides: Partial<PushEntry> = {}): PushEntry {
  return {
    task_id: id,
    equipment_code: "EQ1",
    batch_label: `PO-${id}`,
    old_start: "2026-04-20T10:00:00",
    old_end: "2026-04-20T12:00:00",
    new_start: "2026-04-20T12:00:00",
    new_end: "2026-04-20T14:00:00",
    reason: "same_equipment_conflict",
    ...overrides,
  };
}

function mkPreview(
  over: Partial<CascadePreviewResponse> = {},
): CascadePreviewResponse {
  return {
    request_id: "r1",
    summary: "",
    pushes: [],
    pulls: [],
    unresolved: [],
    can_auto_resolve: true,
    iter_count: 0,
    truncated: false,
    ...over,
  };
}

describe("getProposalFor — Task 22 ghost overlay helper", () => {
  it("push 배열에서 일치하는 task_id 를 찾는다", () => {
    const preview = mkPreview({ pushes: [mkEntry("A"), mkEntry("B")] });
    const found = getProposalFor("B", preview);
    expect(found?.task_id).toBe("B");
  });

  it("pull 배열도 검색 대상 — push 에 없으면 pull 에서 찾는다", () => {
    const preview = mkPreview({
      pushes: [mkEntry("A")],
      pulls: [mkEntry("C", { reason: "successor_slack_available" })],
    });
    const found = getProposalFor("C", preview);
    expect(found?.task_id).toBe("C");
    expect(found?.reason).toBe("successor_slack_available");
  });

  it("push 가 pull 보다 우선한다 (같은 task_id 가 양쪽에 있으면 push 반환)", () => {
    const preview = mkPreview({
      pushes: [mkEntry("X", { new_start: "2026-04-20T15:00:00" })],
      pulls: [
        mkEntry("X", {
          reason: "successor_slack_available",
          new_start: "2026-04-20T08:00:00",
        }),
      ],
    });
    const found = getProposalFor("X", preview);
    expect(found?.new_start).toBe("2026-04-20T15:00:00");
  });

  it("매칭이 없으면 null 을 반환한다 (ghost 미렌더 분기)", () => {
    const preview = mkPreview({ pushes: [mkEntry("A")] });
    expect(getProposalFor("Z", preview)).toBeNull();
  });

  it("preview 가 null/undefined 면 null 을 반환 — 모달 닫힌 상태", () => {
    expect(getProposalFor("A", null)).toBeNull();
    expect(getProposalFor("A", undefined)).toBeNull();
  });
});
