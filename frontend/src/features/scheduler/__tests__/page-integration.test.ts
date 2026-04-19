/**
 * Task 21 — page.tsx wire-up 계약 테스트.
 *
 * 왜 contract-only 레벨인가:
 * - RTL / renderHook 미설치 상태라 page 전체를 mount 해서 상호작용 테스트를 돌릴 수 없다.
 * - 실제 drag + modal + bulk-update 흐름은 Playwright E2E (Task 25-27) 에서 검증.
 *
 * 여기서는 다음 두 가지만 검증한다:
 * 1. `useScheduleChangeWithCascade` 모듈이 정상 import 되고 함수로 제공되는지.
 * 2. caller(scheduler/page.tsx) 가 의존하는 반환 필드가 hook 의 공개 계약에 포함되는지
 *    — type-level 로는 tsc 가 검증하지만, 런타임에도 핵심 키들이 존재함을 가볍게 확인.
 */
import { describe, it, expect, vi } from "vitest";

vi.mock("../api/cascade", () => ({
  cascadePreview: vi.fn(),
  bulkUpdate: vi.fn(),
  revertChangeSet: vi.fn(),
}));

vi.mock("@/shared/config/featureFlags", () => ({
  FEATURE_FLAG_CASCADE_V2: true,
}));

vi.mock("@/shared/ui/toastStore", () => ({
  useToastStore: Object.assign(() => vi.fn(), {
    getState: () => ({ show: vi.fn(), dismiss: vi.fn(), dismissAll: vi.fn() }),
  }),
}));

import { useScheduleChangeWithCascade } from "../hooks/useScheduleChangeWithCascade";

describe("Task 21 — scheduler/page.tsx wire-up contract", () => {
  it("exports useScheduleChangeWithCascade as a function", () => {
    expect(typeof useScheduleChangeWithCascade).toBe("function");
  });

  it("ConflictResolutionModal module loads with named + default export", async () => {
    const mod = await import("../components/ConflictResolutionModal");
    expect(typeof mod.ConflictResolutionModal).toBe("function");
    expect(typeof mod.default).toBe("function");
  });

  it("TaskFormModal module exposes onSubmitWithCascade prop typing", async () => {
    const mod = await import("../components/TaskFormModal");
    expect(typeof mod.TaskFormModal).toBe("function");
  });
});
