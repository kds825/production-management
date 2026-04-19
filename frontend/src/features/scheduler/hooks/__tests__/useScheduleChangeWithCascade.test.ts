/**
 * useScheduleChangeWithCascade — 2-Phase flow 계약 테스트.
 *
 * 왜 contract-only 레벨인가:
 * - 프로젝트에 @testing-library/react / renderHook 가 미설치.
 * - React 환경 없이 hook 의 useState/useCallback 을 "실행" 하는 단위 테스트는
 *   react internals 에서 invariant 오류를 발생시킨다.
 * - 실제 2-Phase 흐름(플래그 on/off, empty preview, retry, undo)은
 *   Playwright E2E (Task 25-27) 에서 검증.
 *
 * 여기서는 모듈이 로드 가능하고 hook/상수 계약이 유지되는지만 확인한다.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("../../api/cascade", () => ({
  cascadePreview: vi.fn(),
  bulkUpdate: vi.fn(),
  revertChangeSet: vi.fn(),
}));

// 실제 클래스 export 유지 — 훅 내부의 `instanceof BulkUpdateError` 가 필요로 함.
vi.mock("../../api/cascade.types", async () => {
  const actual = await vi.importActual<
    typeof import("../../api/cascade.types")
  >("../../api/cascade.types");
  return actual;
});

vi.mock("@/shared/config/featureFlags", () => ({
  FEATURE_FLAG_CASCADE_V2: true,
}));

vi.mock("@/shared/ui/toastStore", () => ({
  useToastStore: Object.assign(() => vi.fn(), {
    getState: () => ({ show: vi.fn(), dismiss: vi.fn(), dismissAll: vi.fn() }),
  }),
}));

import {
  useScheduleChangeWithCascade,
  RETRY_THRESHOLD,
  UNDO_TOAST_DURATION_MS,
} from "../useScheduleChangeWithCascade";

describe("useScheduleChangeWithCascade contract", () => {
  beforeEach(() => vi.clearAllMocks());

  it("module exports hook function", () => {
    expect(typeof useScheduleChangeWithCascade).toBe("function");
  });

  it("retry threshold is 3 (spec: 자동 재시도 상한)", () => {
    expect(RETRY_THRESHOLD).toBe(3);
  });

  it("undo toast duration is 90 seconds (spec: Undo grace window)", () => {
    expect(UNDO_TOAST_DURATION_MS).toBe(90_000);
  });

  // Note: hook 실행 단위 테스트는 @testing-library/react 없이 불가능.
  // Playwright E2E 에서 실제 flow 검증 (Task 25-27).
});
